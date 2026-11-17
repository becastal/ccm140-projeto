import os
import re
import json
import random
import zmq
from time import time, sleep
from google.protobuf import text_format
import mensagens_pb2

nome = os.environ["NOME"]
arquivo = f"dados/{nome}.jsonl"

usuarios = {}   # usuario -> timestamp do ultimo login
canais = []
mensagens = 0

relogio = 0     # relogio logico
recebidas = 0   # mensagens de clientes recebidas (heartbeat a cada 10)

# relogio fisico: todos os containers usam a hora do mesmo computador, entao
# cada servidor comeca com um desvio aleatorio para a sincronizacao aparecer
desvio = random.uniform(-5, 5)


def hora():
    return time() + desvio


# recupera o que ja estava salvo no disco
if os.path.exists(arquivo):
    with open(arquivo) as f:
        for linha in f:
            evento = json.loads(linha)
            if evento["ev"] == "login":
                usuarios[evento["usuario"]] = evento["timestamp"]
            elif evento["ev"] == "canal":
                canais.append(evento["canal"])
            elif evento["ev"] == "msg":
                mensagens += 1
print(f"{nome}: {len(usuarios)} usuarios, {len(canais)} canais e {mensagens} mensagens carregados do disco", flush=True)
print(f"{nome}: relogio fisico comeca com desvio de {desvio:+.3f}s", flush=True)


def salva(evento):
    with open(arquivo, "a") as f:
        f.write(json.dumps(evento) + "\n")


def mostra(msg):
    return text_format.MessageToString(msg, as_one_line=True)


nome_valido = re.compile(r"^[a-z0-9_-]{1,20}$")

context = zmq.Context()
socket = context.socket(zmq.REP)
socket.connect("tcp://broker:5556")

pub = context.socket(zmq.PUB)
pub.connect("tcp://proxy:5557")


# REQ para a referencia; se nao responder em 2s, recria o socket e devolve None
referencia = None


def pede_referencia(req):
    global referencia, relogio
    if referencia is None:
        referencia = context.socket(zmq.REQ)
        referencia.setsockopt(zmq.RCVTIMEO, 2000)
        referencia.setsockopt(zmq.LINGER, 0)
        referencia.connect("tcp://referencia:5559")

    req.nome = nome
    relogio += 1
    req.relogio = relogio
    req.timestamp = hora()
    print(f"enviado   -> referencia: {mostra(req)}", flush=True)
    referencia.send(req.SerializeToString())
    try:
        rep = mensagens_pb2.Controle()
        rep.ParseFromString(referencia.recv())
    except zmq.Again:
        print("referencia nao respondeu", flush=True)
        referencia.close()
        referencia = None
        return None
    relogio = max(relogio, rep.relogio)
    print(f"recebido  <- referencia: {mostra(rep)}", flush=True)
    return rep


rank = None
while rank is None:
    rep = pede_referencia(mensagens_pb2.Controle(tipo="rank"))
    if rep is not None:
        rank = rep.rank
    else:
        sleep(1)
print(f"{nome}: meu rank e {rank}", flush=True)
pede_referencia(mensagens_pb2.Controle(tipo="list"))


def heartbeat():
    global desvio
    inicio = time()
    rep = pede_referencia(mensagens_pb2.Controle(tipo="heartbeat"))
    if rep is None or rep.status != "ok":
        return
    # Cristian: a hora certa chegou com metade do tempo de ida e volta de atraso
    ida_e_volta = time() - inicio
    antes = desvio
    desvio = rep.hora + ida_e_volta / 2 - time()
    print(f"relogio ajustado pela referencia: desvio {antes:+.3f}s -> {desvio:+.3f}s", flush=True)
    pede_referencia(mensagens_pb2.Controle(tipo="list"))


while True:
    req = mensagens_pb2.Requisicao()
    req.ParseFromString(socket.recv())
    relogio = max(relogio, req.relogio)
    print(f"recebido  <- {mostra(req)}", flush=True)

    rep = mensagens_pb2.Resposta()
    rep.status = "ok"

    if req.tipo == "login":
        if not nome_valido.match(req.usuario):
            rep.status = "erro"
            rep.erro = "nome de usuario invalido"
        else:
            usuarios[req.usuario] = req.timestamp
            salva({"ev": "login", "usuario": req.usuario, "timestamp": req.timestamp, "relogio": req.relogio})

    elif req.tipo == "canal":
        if not nome_valido.match(req.canal):
            rep.status = "erro"
            rep.erro = "nome de canal invalido"
        elif req.canal in canais:
            rep.status = "erro"
            rep.erro = "canal ja existe"
        else:
            canais.append(req.canal)
            salva({"ev": "canal", "canal": req.canal, "usuario": req.usuario, "timestamp": req.timestamp, "relogio": req.relogio})

    elif req.tipo == "listar":
        rep.canais.extend(canais)

    elif req.tipo == "publicar":
        if req.canal not in canais:
            rep.status = "erro"
            rep.erro = "canal nao existe"
        elif req.texto == "":
            rep.status = "erro"
            rep.erro = "mensagem vazia"
        else:
            publicacao = mensagens_pb2.Publicacao()
            publicacao.canal = req.canal
            publicacao.usuario = req.usuario
            publicacao.texto = req.texto
            publicacao.timestamp = hora()
            relogio += 1
            publicacao.relogio = relogio
            print(f"publicado => {mostra(publicacao)}", flush=True)
            pub.send_multipart([req.canal.encode(), publicacao.SerializeToString()])
            salva({"ev": "msg", "canal": req.canal, "usuario": req.usuario, "texto": req.texto, "timestamp": req.timestamp, "relogio": req.relogio})

    else:
        rep.status = "erro"
        rep.erro = f"tipo desconhecido: {req.tipo}"

    relogio += 1
    rep.relogio = relogio
    rep.timestamp = hora()
    print(f"enviado   -> {mostra(rep)}", flush=True)
    socket.send(rep.SerializeToString())

    recebidas += 1
    if recebidas % 10 == 0:
        heartbeat()
