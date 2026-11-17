# Projeto CCM140 - troca de mensagens entre bots

Versão simples de um sistema tipo BBS/IRC: bots fazem login, listam e criam canais. Tudo sobe com:

```
docker compose up
```

São 4 servidores e 8 bots, mais o broker.

## Escolhas

**Linguagens:** Python (servidor e broker) e C++ (bot).

**Serialização:** Protobuf, o mesmo usado na aula de RPC. As mensagens estão em `mensagens.proto` e são compiladas na criação das imagens (`grpc_tools.protoc` no Python e `protoc` no C++). Toda mensagem leva o `timestamp` do envio.

**Persistência:** cada servidor escreve num arquivo próprio `dados/<servidor>.jsonl`, uma linha por evento (login ou canal criado), só acrescentando. Quando o servidor reinicia ele lê o arquivo de novo e recupera os usuários e canais.

**Erros:** o servidor responde `status: "erro"` com o motivo em `erro`:
- nome de usuário ou canal inválido (só `a-z`, `0-9`, `_`, `-`, até 20 caracteres);
- canal que já existe;
- tipo de pedido desconhecido.

Se o login der erro o bot tenta de novo depois de 1 s. Se o servidor não responder em 3 s, o bot reconecta e manda o pedido de novo.

**Bots:** a cada 3 s pedem a lista de canais e, se existirem menos de 5, criam um com um nome sorteado.

## Estrutura

```
mensagens.proto       formato das mensagens
python/broker.py      ROUTER 5555 (bots) / DEALER 5556 (servidores)
python/servidor.py
cpp/bot.cpp
dados/                arquivos dos servidores
```
