FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y g++ libzmq3-dev cppzmq-dev libprotobuf-dev protobuf-compiler libssl-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ./mensagens.proto .
COPY ./cpp/bot.cpp .

RUN protoc --cpp_out=. mensagens.proto && g++ -O2 -std=c++17 -o bot bot.cpp mensagens.pb.cc -lzmq -lprotobuf -lcrypto

CMD ["./bot"]
