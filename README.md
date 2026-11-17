# Projeto CCM140 - troca de mensagens entre bots

Versão simples de um sistema tipo BBS/IRC: bots fazem login, criam canais e publicam mensagens nos canais. Tudo sobe com:

```
docker compose up
```

São 4 servidores e 8 bots, mais o broker, o proxy e o serviço de referência.

## Escolhas

**Linguagens:** Python (servidor, broker, proxy e referência) e C++ (bot).

**Serialização:** Protobuf, o mesmo usado na aula de RPC. As mensagens estão em `mensagens.proto` e são compiladas na criação das imagens (`grpc_tools.protoc` no Python e `protoc` no C++). Toda mensagem leva o `timestamp` do envio.

**Persistência:** cada servidor escreve num arquivo próprio `dados/<servidor>.jsonl`, uma linha por evento (login, canal criado ou mensagem publicada), só acrescentando. Quando o servidor reinicia ele lê o arquivo de novo e recupera os usuários e canais. As mensagens ficam guardadas com canal, usuário, texto e timestamp, então dá para recuperar o histórico inteiro lendo o arquivo.

**Erros:** o servidor responde `status: "erro"` com o motivo em `erro`:
- nome de usuário ou canal inválido (só `a-z`, `0-9`, `_`, `-`, até 20 caracteres);
- canal que já existe;
- publicação em canal que não existe ou mensagem vazia;
- tipo de pedido desconhecido.

Se o login der erro o bot tenta de novo depois de 1 s. Se o servidor não responder em 3 s, o bot reconecta e manda o pedido de novo.

**Publicação:** o bot pede ao servidor (REQ `publicar` com canal e texto), o servidor publica no proxy usando o nome do canal como tópico e responde ok/erro. A publicação vai em duas partes: o nome do canal (tópico) e a `Publicacao` serializada. O proxy usa XSUB na 5557 e XPUB na 5558.

**Bots:** em loop:
1. pedem a lista de canais;
2. se existirem menos de 5 canais, criam um com nome sorteado;
3. se estiverem inscritos em menos de 3 canais, se inscrevem em mais um sorteado;
4. escolhem um canal e mandam 10 mensagens aleatórias, uma por segundo.

Enquanto esperam entre uma mensagem e outra, mostram tudo que chega dos canais inscritos com canal, autor, texto, hora de envio e hora de recebimento.

Como o broker reparte os pedidos entre os servidores (round-robin), cada servidor só conhece os canais que foram criados nele. Por isso aparecem erros `canal nao existe`: isso é resolvido na parte 5 com a réplica.

## Relógios e heartbeat

**Relógio lógico:** bots, servidores e a referência têm um contador. Ele é incrementado antes de cada envio e vai na mensagem (`relogio`). Ao receber, o processo fica com o máximo entre o seu valor e o recebido.

**Serviço de referência** (`python/referencia.py`, REP na porta 5559), mensagens `Controle`:
- `rank`: o servidor manda o nome e recebe o rank. Os ranks são dados em ordem de chegada (1, 2, 3...) e um nome repetido recebe o mesmo rank de antes;
- `list`: devolve nome e rank dos servidores ativos;
- `heartbeat`: cada servidor manda a cada 10 mensagens de clientes recebidas. Quem fica mais de 30 s sem mandar sai da lista (volta quando mandar de novo). A resposta traz a hora certa (`hora`).

**Relógio físico:** como todos os containers usam a hora do mesmo computador, cada servidor começa com um desvio aleatório entre -5 s e +5 s, para a sincronização aparecer. No heartbeat o servidor acerta o relógio com a hora da referência, somando metade do tempo de ida e volta da mensagem (algoritmo de Cristian), e mostra o desvio antes e depois.

## Estrutura

```
mensagens.proto       formato das mensagens
python/broker.py      ROUTER 5555 (bots) / DEALER 5556 (servidores)
python/proxy.py       XSUB 5557 (servidores) / XPUB 5558 (bots)
python/referencia.py  REP 5559
python/servidor.py
cpp/bot.cpp
dados/                arquivos dos servidores
```
