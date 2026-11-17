FROM python:3.13.7-alpine3.21

WORKDIR /app

RUN pip install pyzmq protobuf grpcio-tools

COPY ./mensagens.proto .

RUN python -m grpc_tools.protoc -I. --python_out=. mensagens.proto

CMD ["python", "main.py"]
