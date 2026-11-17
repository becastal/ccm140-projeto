import os
import re
import json
import random
import threading
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
recebidas = 0   # mensagens de clientes recebidas (heartbeat a cada 10, relogio a cada 15)
trava = threading.Lock()

coordenador = None
precisa_eleicao = False
espera_anuncio = 0     # ate quando esperar o anuncio do novo coordenador

# relogio fisico: todos os containers usam a hora do mesmo computador, entao
# cada servidor comeca com um desvio aleatorio para a sincronizacao aparecer
desvio = random.uniform(-5, 5)


def hora():
    return time() + desvio


def incrementa():
    global relogio
    with trava:
        relogio += 1
        return relogio


def atualiza(recebido):
    global relogio
    with trava:
        relogio = max(relogio, recebido)


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
reservados = ["servers", "replica"]

context = zmq.Context()
socket = context.socket(zmq.REP)
socket.connect("tcp://broker:5556")

pub = context.socket(zmq.PUB)
pub.connect("tcp://proxy:5557")

sub = context.socket(zmq.SUB)
sub.connect("tcp://proxy:5558")
sub.setsockopt_string(zmq.SUBSCRIBE, "servers")


# REQ com tempo limite: cria o socket, manda, espera a resposta e fecha.
# Devolve None se ninguem responder.
def pede(endereco, req, espera=1000):
    req.nome = nome
    req.relogio = incrementa()
    req.timestamp = hora()
    print(f"enviado   -> {endereco}: {mostra(req)}", flush=True)

    s = context.socket(zmq.REQ)
    s.setsockopt(zmq.RCVTIMEO, espera)
    s.setsockopt(zmq.LINGER, 0)
    s.connect(f"tcp://{endereco}")
    s.send(req.SerializeToString())
    try:
        rep = mensagens_pb2.Controle()
        rep.ParseFromString(s.recv())
    except zmq.Again:
        print(f"{endereco} nao respondeu", flush=True)
        return None
    finally:
        s.close()

    atualiza(rep.relogio)
    print(f"recebido  <- {endereco}: {mostra(rep)}", flush=True)
    return rep


def pede_referencia(req):
    return pede("referencia:5559", req, 2000)


def pede_servidor(outro, req):
    return pede(f"{outro}:5560", req)


# atende os pedidos dos outros servidores (relogio e eleicao) em outra thread
def atende_servidores():
    global precisa_eleicao
    s = context.socket(zmq.REP)
    s.bind("tcp://*:5560")
    while True:
        req = mensagens_pb2.Controle()
        req.ParseFromString(s.recv())
        atualiza(req.relogio)
        print(f"recebido  <- {req.nome}: {mostra(req)}", flush=True)

        rep = mensagens_pb2.Controle()
        rep.tipo = req.tipo
        rep.nome = nome
        rep.status = "ok"
        if req.tipo == "relogio":
            rep.hora = hora()
        elif req.tipo == "eleicao":
            # alguem com rank menor pediu eleicao: respondo OK e faco a minha
            precisa_eleicao = True
        else:
            rep.status = "erro"

        rep.relogio = incrementa()
        rep.timestamp = hora()
        print(f"enviado   -> {req.nome}: {mostra(rep)}", flush=True)
        s.send(rep.SerializeToString())


threading.Thread(target=atende_servidores, daemon=True).start()

rank = None
while rank is None:
    rep = pede_referencia(mensagens_pb2.Controle(tipo="rank"))
    if rep is not None:
        rank = rep.rank
    else:
        sleep(1)
print(f"{nome}: meu rank e {rank}", flush=True)


def servidores_ativos():
    rep = pede_referencia(mensagens_pb2.Controle(tipo="list"))
    if rep is None:
        return []
    return list(rep.servidores)


def heartbeat():
    pede_referencia(mensagens_pb2.Controle(tipo="heartbeat"))


# eleicao do valentao (bully): pergunta para todos com rank maior;
# se ninguem responder, eu sou o coordenador
def eleicao():
    global precisa_eleicao, coordenador, espera_anuncio
    precisa_eleicao = False
    print(f"{nome}: comecando eleicao (meu rank e {rank})", flush=True)

    alguem_maior = False
    for s in servidores_ativos():
        if s.rank > rank:
            rep = pede_servidor(s.nome, mensagens_pb2.Controle(tipo="eleicao"))
            if rep is not None and rep.status == "ok":
                alguem_maior = True

    if alguem_maior:
        # algum servidor maior vai assumir; espero o anuncio dele
        espera_anuncio = time() + 5
        return

    coordenador = nome
    espera_anuncio = 0
    anuncio = mensagens_pb2.Controle(tipo="coordenador", nome=nome, rank=rank)
    anuncio.relogio = incrementa()
    anuncio.timestamp = hora()
    print(f"{nome}: fui eleito coordenador", flush=True)
    print(f"publicado => [servers] {mostra(anuncio)}", flush=True)
    pub.send_multipart([b"servers", anuncio.SerializeToString()])


# Berkeley: o coordenador pergunta a hora de todos, calcula a media das
# diferencas e acerta o proprio relogio; os outros pedem a hora para ele
def sincroniza():
    global desvio, coordenador, precisa_eleicao
    if coordenador is None:
        precisa_eleicao = True
        return

    if coordenador == nome:
        diferencas = {nome: 0.0}
        for s in servidores_ativos():
            if s.nome == nome:
                continue
            inicio = time()
            rep = pede_servidor(s.nome, mensagens_pb2.Controle(tipo="relogio"))
            if rep is None:
                continue
            ida_e_volta = time() - inicio
            diferencas[s.nome] = rep.hora + ida_e_volta / 2 - hora()
        media = sum(diferencas.values()) / len(diferencas)
        antes = desvio
        desvio += media
        texto = ", ".join(f"{n} {d:+.3f}s" for n, d in diferencas.items())
        print(f"berkeley: diferencas [{texto}], media {media:+.3f}s, meu desvio {antes:+.3f}s -> {desvio:+.3f}s", flush=True)
        return

    inicio = time()
    rep = pede_servidor(coordenador, mensagens_pb2.Controle(tipo="relogio"))
    if rep is None:
        print(f"coordenador {coordenador} nao respondeu", flush=True)
        coordenador = None
        precisa_eleicao = True
        return
    ida_e_volta = time() - inicio
    antes = desvio
    desvio = rep.hora + ida_e_volta / 2 - time()
    print(f"relogio acertado com o coordenador {coordenador}: desvio {antes:+.3f}s -> {desvio:+.3f}s", flush=True)


precisa_eleicao = True

poller = zmq.Poller()
poller.register(socket, zmq.POLLIN)
poller.register(sub, zmq.POLLIN)

while True:
    socks = dict(poller.poll(500))

    if socks.get(sub) == zmq.POLLIN:
        topico, dados = sub.recv_multipart()
        anuncio = mensagens_pb2.Controle()
        anuncio.ParseFromString(dados)
        atualiza(anuncio.relogio)
        print(f"recebido  <= [servers] {mostra(anuncio)}", flush=True)
        if anuncio.rank < rank:
            # um menor se elegeu, mas eu estou vivo: faco nova eleicao
            precisa_eleicao = True
        else:
            coordenador = anuncio.nome
            espera_anuncio = 0
            print(f"{nome}: coordenador agora e {coordenador}", flush=True)

    if espera_anuncio and time() > espera_anuncio:
        print(f"{nome}: ninguem se anunciou como coordenador", flush=True)
        espera_anuncio = 0
        precisa_eleicao = True

    if precisa_eleicao:
        eleicao()

    if socks.get(socket) != zmq.POLLIN:
        continue

    req = mensagens_pb2.Requisicao()
    req.ParseFromString(socket.recv())
    atualiza(req.relogio)
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
        if not nome_valido.match(req.canal) or req.canal in reservados:
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
            publicacao.relogio = incrementa()
            print(f"publicado => {mostra(publicacao)}", flush=True)
            pub.send_multipart([req.canal.encode(), publicacao.SerializeToString()])
            salva({"ev": "msg", "canal": req.canal, "usuario": req.usuario, "texto": req.texto, "timestamp": req.timestamp, "relogio": req.relogio})

    else:
        rep.status = "erro"
        rep.erro = f"tipo desconhecido: {req.tipo}"

    rep.relogio = incrementa()
    rep.timestamp = hora()
    print(f"enviado   -> {mostra(rep)}", flush=True)
    socket.send(rep.SerializeToString())

    recebidas += 1
    if recebidas % 10 == 0:
        heartbeat()
    if recebidas % 15 == 0:
        sincroniza()
