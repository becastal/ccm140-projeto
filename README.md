# Projeto CCM140 - troca de mensagens entre bots

Sistema tipo BBS/IRC: bots fazem login, criam canais e publicam mensagens. Sobe tudo com:

```
docker compose up
```

4 servidores, 8 bots, broker, proxy, referência e um bot impostor.

## Escolhas

- **Linguagens:** Python (servidor, broker, proxy, referência) e C++ (bot).
- **Serialização:** Protobuf (`mensagens.proto`). Toda mensagem tem `timestamp` e `relogio`.
- **Persistência:** cada servidor tem o seu arquivo `dados/<servidor>.jsonl`, uma linha por evento (login, canal, mensagem). Ao reiniciar, o servidor lê o arquivo de volta.
- **Erros:** resposta com `status: "erro"` e o motivo (nome inválido, canal repetido ou inexistente, sessão inválida...).

## Comunicação

- **Bot ↔ servidor:** REQ/REP pelo broker (5555 bots, 5556 servidores), em round-robin.
- **Canais:** o servidor publica no proxy (XSUB 5557, XPUB 5558) com o nome do canal como tópico; os bots assinam até 3 canais.
- **Bots:** listam os canais, criam um se houver menos de 5, assinam um se estiverem em menos de 3, e mandam 10 mensagens, uma por segundo.

## Relógios e heartbeat

- **Relógio lógico:** +1 antes de enviar; ao receber, fica com o maior valor.
- **Referência** (REP 5559): dá o `rank` de cada servidor, devolve a `list` dos ativos e recebe o `heartbeat` (a cada 10 mensagens). Quem passa 30 s sem heartbeat sai da lista.
- **Relógio físico:** cada servidor começa com um desvio aleatório de até 5 s, para a sincronização aparecer.

## Eleição e Berkeley

- Um servidor é o coordenador. A cada 15 mensagens ele calcula a média das horas de todos (Berkeley), e os outros pedem a hora para ele (REQ/REP na 5560).
- **Eleição (valentão):** se o coordenador não responde, o servidor manda `eleicao` aos de rank maior. Se ninguém responde, ele vira coordenador e avisa no tópico `servers`.

## Réplica

Cada escrita vai para um servidor só, por causa do round-robin. Foi usada **réplica ativa por push**:

- toda escrita vira um `Evento` com id único, salvo localmente e publicado no tópico `replica`;
- os outros servidores aplicam o evento, e o id evita duplicar;
- **mudança:** quem estava fora perde as publicações, então ao subir pede tudo a outro servidor (`sincronizar`).

A consistência é eventual: depois de alguns milissegundos todos têm os mesmos dados.

## Extra: login com prova de conhecimento zero

O bot prova que é dono do nome sem revelar o segredo (isomorfismo de grafos):

- G0 e G1 = π(G0) são públicos; só o bot sabe π;
- em cada rodada o bot manda um H (G0 renomeado) e mostra como G0 ou G1 vira H, conforme a moeda. Sem π, só dá para acertar metade;
- são 32 rodadas numa mensagem só: as moedas saem do SHA-256 dos H (Fiat-Shamir), então o login continua sendo 1 REQ + 1 REP;
- o login devolve uma `sessao`, exigida para criar canal e publicar.

O `impostor` tenta entrar como bot1 e é barrado.

Para começar do zero, apague juntos `dados/*.jsonl` e `chaves/*/*.sec`, `chaves/*/*.pub`.

## Estrutura

```
mensagens.proto
python/   broker.py  proxy.py  referencia.py  servidor.py
cpp/      bot.cpp
dados/    arquivos dos servidores
chaves/   chaves do login
```
