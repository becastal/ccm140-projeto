import zmq

context = zmq.Context()

# bots conectam aqui
clientes = context.socket(zmq.ROUTER)
clientes.bind("tcp://*:5555")

# servidores conectam aqui
servidores = context.socket(zmq.DEALER)
servidores.bind("tcp://*:5556")

print("Broker iniciado: clientes 5555, servidores 5556", flush=True)
zmq.proxy(clientes, servidores)

clientes.close()
servidores.close()
context.close()
