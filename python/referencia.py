import zmq
from time import time
from google.protobuf import text_format
import mensagens_pb2

relogio = 0
ranks = {}      # nome -> rank (nunca esquece)
vistos = {}     # nome -> hora do ultimo heartbeat (so quem esta ativo)
limite = 30     # segundos sem heartbeat para sair da lista


def mostra(msg):
    return text_format.MessageToString(msg, as_one_line=True)


context = zmq.Context()
socket = context.socket(zmq.REP)
socket.bind("tcp://*:5559")
print("Referencia iniciada na porta 5559", flush=True)

while True:
    req = mensagens_pb2.Controle()
    req.ParseFromString(socket.recv())
    relogio = max(relogio, req.relogio)
    print(f"recebido  <- {mostra(req)}", flush=True)

    for nome in list(vistos):
        if time() - vistos[nome] > limite:
            del vistos[nome]
            print(f"{nome} sem heartbeat ha mais de {limite}s, removido da lista", flush=True)

    rep = mensagens_pb2.Controle()
    rep.tipo = req.tipo
    rep.status = "ok"

    if req.tipo == "rank":
        if req.nome not in ranks:
            ranks[req.nome] = len(ranks) + 1
        vistos[req.nome] = time()
        rep.nome = req.nome
        rep.rank = ranks[req.nome]

    elif req.tipo == "list":
        for nome in sorted(vistos, key=lambda n: ranks[n]):
            servidor = rep.servidores.add()
            servidor.nome = nome
            servidor.rank = ranks[nome]

    elif req.tipo == "heartbeat":
        if req.nome not in ranks:
            rep.status = "erro"
        else:
            vistos[req.nome] = time()

    else:
        rep.status = "erro"

    relogio += 1
    rep.relogio = relogio
    rep.timestamp = time()
    print(f"enviado   -> {mostra(rep)}", flush=True)
    socket.send(rep.SerializeToString())
