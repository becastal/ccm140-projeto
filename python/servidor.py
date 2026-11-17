import os
import re
import json
import zmq
from time import time
from google.protobuf import text_format
import mensagens_pb2

nome = os.environ["NOME"]
arquivo = f"dados/{nome}.jsonl"

usuarios = {}   # usuario -> timestamp do ultimo login
canais = []

# recupera o que ja estava salvo no disco
if os.path.exists(arquivo):
    with open(arquivo) as f:
        for linha in f:
            evento = json.loads(linha)
            if evento["ev"] == "login":
                usuarios[evento["usuario"]] = evento["timestamp"]
            elif evento["ev"] == "canal":
                canais.append(evento["canal"])
print(f"{nome}: {len(usuarios)} usuarios e {len(canais)} canais carregados do disco", flush=True)


def salva(evento):
    with open(arquivo, "a") as f:
        f.write(json.dumps(evento) + "\n")


def mostra(msg):
    return text_format.MessageToString(msg, as_one_line=True)


nome_valido = re.compile(r"^[a-z0-9_-]{1,20}$")

context = zmq.Context()
socket = context.socket(zmq.REP)
socket.connect("tcp://broker:5556")

while True:
    req = mensagens_pb2.Requisicao()
    req.ParseFromString(socket.recv())
    print(f"recebido  <- {mostra(req)}", flush=True)

    rep = mensagens_pb2.Resposta()
    rep.status = "ok"

    if req.tipo == "login":
        if not nome_valido.match(req.usuario):
            rep.status = "erro"
            rep.erro = "nome de usuario invalido"
        else:
            usuarios[req.usuario] = req.timestamp
            salva({"ev": "login", "usuario": req.usuario, "timestamp": req.timestamp})

    elif req.tipo == "canal":
        if not nome_valido.match(req.canal):
            rep.status = "erro"
            rep.erro = "nome de canal invalido"
        elif req.canal in canais:
            rep.status = "erro"
            rep.erro = "canal ja existe"
        else:
            canais.append(req.canal)
            salva({"ev": "canal", "canal": req.canal, "usuario": req.usuario, "timestamp": req.timestamp})

    elif req.tipo == "listar":
        rep.canais.extend(canais)

    else:
        rep.status = "erro"
        rep.erro = f"tipo desconhecido: {req.tipo}"

    rep.timestamp = time()
    print(f"enviado   -> {mostra(rep)}", flush=True)
    socket.send(rep.SerializeToString())
