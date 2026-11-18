import os
import re
import json
import random
import hashlib
import secrets
import threading
import zmq
from time import time, sleep
from google.protobuf import text_format
import mensagens_pb2

nome = os.environ["NOME"]
arquivo = f"dados/{nome}.jsonl"

usuarios = {}   # usuario -> timestamp do ultimo login
chaves = {}     # usuario -> (g0, g1), chave publica do ZKP
sessoes = {}    # sessao -> usuario
provas_usadas = set()
canais = []
mensagens = 0
eventos = []    # todos os eventos aplicados, na ordem em que chegaram
vistos = set()  # ids dos eventos ja aplicados
contador = 0    # para gerar o id dos eventos criados aqui

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


campos = ["id", "ev", "usuario", "canal", "texto", "timestamp", "relogio", "origem", "g0", "g1", "sessao"]


def para_evento(d):
    e = mensagens_pb2.Evento()
    for c in campos:
        if c in d:
            setattr(e, c, d[c])
    return e


def para_dict(e):
    return {c: getattr(e, c) for c in campos}


# aplica um evento nos dados em memoria; devolve False se ja tinha sido aplicado
def aplica(evento):
    global mensagens
    if evento["id"] in vistos:
        return False
    vistos.add(evento["id"])
    eventos.append(evento)
    if evento["ev"] == "login":
        usuarios[evento["usuario"]] = evento["timestamp"]
        if evento.get("g0"):
            chaves.setdefault(evento["usuario"], (evento["g0"], evento["g1"]))
            sessoes[evento["sessao"]] = evento["usuario"]
    elif evento["ev"] == "canal":
        if evento["canal"] not in canais:
            canais.append(evento["canal"])
    elif evento["ev"] == "msg":
        mensagens += 1
    return True


# recupera o que ja estava salvo no disco
if os.path.exists(arquivo):
    with open(arquivo) as f:
        for linha in f:
            evento = json.loads(linha)
            aplica(evento)
            if evento.get("origem") == nome:
                contador = max(contador, int(evento["id"].split("-")[-1]))
print(f"{nome}: {len(usuarios)} usuarios, {len(canais)} canais e {mensagens} mensagens carregados do disco", flush=True)
print(f"{nome}: relogio fisico comeca com desvio de {desvio:+.3f}s", flush=True)


def salva(evento):
    with open(arquivo, "a") as f:
        f.write(json.dumps(evento) + "\n")


def mostra(msg):
    if isinstance(msg, mensagens_pb2.Requisicao) and len(msg.prova.h) > 0:
        # os 32 compromissos e respostas deixariam a linha enorme
        resumo = mensagens_pb2.Requisicao()
        resumo.CopyFrom(msg)
        del resumo.prova.h[:]
        del resumo.prova.resposta[:]
        return text_format.MessageToString(resumo, as_one_line=True) + f" (+{len(msg.prova.h)} compromissos e respostas)"
    if isinstance(msg, mensagens_pb2.Controle) and len(msg.eventos) > 3:
        # a resposta do sincronizar pode ter milhares de eventos: mostra so os 3 primeiros
        resumo = mensagens_pb2.Controle()
        resumo.CopyFrom(msg)
        del resumo.eventos[3:]
        return text_format.MessageToString(resumo, as_one_line=True) + f" ... ({len(msg.eventos)} eventos)"
    return text_format.MessageToString(msg, as_one_line=True)


# ---------- verificacao da prova de conhecimento zero do login ----------

N = 20
RODADAS = 128
TAMANHO = (N * (N - 1) // 2 + 7) // 8


# bytes -> conjunto de arestas (i, j) com i < j
def grafo(dados):
    arestas = set()
    k = 0
    for i in range(N):
        for j in range(i + 1, N):
            if dados[k // 8] >> (k % 8) & 1:
                arestas.add((i, j))
            k += 1
    return arestas


# troca os rotulos: a aresta (i, j) vira (p[i], p[j])
def permuta(p, arestas):
    return {tuple(sorted((p[i], p[j]))) for i, j in arestas}


# devolve None se a prova estiver certa, ou o motivo do erro
def confere_prova(usuario, prova):
    if prova.n != N or len(prova.h) != RODADAS or len(prova.resposta) != RODADAS:
        return "prova mal formada"
    if any(len(g) != TAMANHO for g in [prova.g0, prova.g1, *prova.h]):
        return "prova mal formada"
    if abs(prova.instante - time() * 1000) > 30000:
        return "prova velha"
    if usuario in chaves and chaves[usuario] != (prova.g0.hex(), prova.g1.hex()):
        return "chave nao confere com a cadastrada"

    # as moedas sao o hash dos compromissos (Fiat-Shamir): o bot nao escolhe
    resumo = hashlib.sha256(usuario.encode() + prova.instante.to_bytes(8, "big") +
                            prova.g0 + prova.g1 + b"".join(prova.h)).digest()
    if resumo in provas_usadas:
        return "prova ja usada"

    g = [grafo(prova.g0), grafo(prova.g1)]
    for i in range(RODADAS):
        moeda = resumo[i // 8] >> (i % 8) & 1
        p = list(prova.resposta[i])
        # moeda 0: p tem que levar g0 em h; moeda 1: p tem que levar g1 em h
        if sorted(p) != list(range(N)) or permuta(p, g[moeda]) != grafo(prova.h[i]):
            return f"prova invalida: rodada {i + 1} falhou"

    provas_usadas.add(resumo)
    return None


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
sub.setsockopt_string(zmq.SUBSCRIBE, "replica")


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


def pede_servidor(outro, req, espera=1000):
    return pede(f"{outro}:5560", req, espera)


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
        elif req.tipo == "sincronizar":
            # servidor que acabou de entrar pede tudo que eu tenho
            rep.eventos.extend(para_evento(e) for e in list(eventos))
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


# replica ativa: todo evento novo e salvo aqui e publicado para os outros
def novo_evento(ev, req, extra={}):
    global contador
    contador += 1
    evento = {"id": f"{nome}-{contador}", "ev": ev, "usuario": req.usuario, "canal": req.canal,
              "texto": req.texto, "timestamp": req.timestamp, "relogio": req.relogio, "origem": nome, **extra}
    aplica(evento)
    salva(evento)
    replica = para_evento(evento)
    print(f"publicado => [replica] {mostra(replica)}", flush=True)
    pub.send_multipart([b"replica", replica.SerializeToString()])


# ao entrar, pega com outro servidor os eventos que perdeu enquanto estava fora
def sincroniza_dados():
    for s in servidores_ativos():
        if s.nome == nome:
            continue
        rep = pede_servidor(s.nome, mensagens_pb2.Controle(tipo="sincronizar"), 5000)
        if rep is None:
            continue
        novos = 0
        for e in rep.eventos:
            evento = para_dict(e)
            if aplica(evento):
                salva(evento)
                novos += 1
        print(f"{nome}: {novos} eventos novos recebidos de {s.nome}", flush=True)
        return


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


sincroniza_dados()
precisa_eleicao = True

poller = zmq.Poller()
poller.register(socket, zmq.POLLIN)
poller.register(sub, zmq.POLLIN)

while True:
    socks = dict(poller.poll(500))

    topico = None
    if socks.get(sub) == zmq.POLLIN:
        topico, dados = sub.recv_multipart()

    if topico == b"replica":
        replica = mensagens_pb2.Evento()
        replica.ParseFromString(dados)
        atualiza(replica.relogio)
        if replica.origem != nome:
            print(f"recebido  <= [replica] {mostra(replica)}", flush=True)
            evento = para_dict(replica)
            if aplica(evento):
                salva(evento)

    elif topico == b"servers":
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
        problema = None
        if not nome_valido.match(req.usuario):
            problema = "nome de usuario invalido"
        else:
            problema = confere_prova(req.usuario, req.prova)
        if problema:
            rep.status = "erro"
            rep.erro = problema
            print(f"login de {req.usuario} recusado: {problema}", flush=True)
        else:
            rep.sessao = secrets.token_hex(8)
            print(f"login de {req.usuario} aceito: prova com {RODADAS} rodadas conferida", flush=True)
            novo_evento("login", req, {"g0": req.prova.g0.hex(), "g1": req.prova.g1.hex(), "sessao": rep.sessao})

    elif req.tipo in ["canal", "publicar"] and sessoes.get(req.sessao) != req.usuario:
        rep.status = "erro"
        rep.erro = "sessao invalida"

    elif req.tipo == "canal":
        if not nome_valido.match(req.canal) or req.canal in reservados:
            rep.status = "erro"
            rep.erro = "nome de canal invalido"
        elif req.canal in canais:
            rep.status = "erro"
            rep.erro = "canal ja existe"
        else:
            novo_evento("canal", req)

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
            novo_evento("msg", req)

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
