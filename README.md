# Projeto CCM140 - troca de mensagens entre bots

Versão simples de um sistema tipo BBS/IRC: bots fazem login, criam canais e publicam mensagens nos canais. Tudo sobe com:

```
docker compose up
```

São 4 servidores e 8 bots, mais o broker, o proxy, o serviço de referência e um bot impostor.

## Escolhas

**Linguagens:** Python (servidor, broker, proxy e referência) e C++ (bot).

**Serialização:** Protobuf, o mesmo usado na aula de RPC. As mensagens estão em `mensagens.proto` e são compiladas na criação das imagens (`grpc_tools.protoc` no Python e `protoc` no C++). Toda mensagem leva o `timestamp` do envio.

**Persistência:** cada servidor escreve num arquivo próprio `dados/<servidor>.jsonl`, uma linha por evento (login, canal criado ou mensagem publicada), só acrescentando. Cada linha tem id, tipo, usuário, canal, texto, timestamp, relógio lógico e o servidor de origem. Quando o servidor reinicia ele lê o arquivo de novo e recupera tudo, e o histórico inteiro de mensagens pode ser lido do arquivo.

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

Como o broker reparte os pedidos entre os servidores (round-robin), sem réplica cada servidor só conheceria os canais criados nele (nas partes 2 a 4 aparecem erros `canal nao existe` por isso). A parte 5 resolve.

## Relógios e heartbeat

**Relógio lógico:** bots, servidores e a referência têm um contador. Ele é incrementado antes de cada envio e vai na mensagem (`relogio`). Ao receber, o processo fica com o máximo entre o seu valor e o recebido.

**Serviço de referência** (`python/referencia.py`, REP na porta 5559), mensagens `Controle`:
- `rank`: o servidor manda o nome e recebe o rank. Os ranks são dados em ordem de chegada (1, 2, 3...) e um nome repetido recebe o mesmo rank de antes;
- `list`: devolve nome e rank dos servidores ativos;
- `heartbeat`: cada servidor manda a cada 10 mensagens de clientes recebidas. Quem fica mais de 30 s sem mandar sai da lista (volta quando mandar de novo).

**Relógio físico:** como todos os containers usam a hora do mesmo computador, cada servidor começa com um desvio aleatório entre -5 s e +5 s, para a sincronização aparecer nos logs.

## Coordenador, Berkeley e eleição

Cada servidor tem uma thread com um REP na porta 5560 que atende os outros servidores (`relogio` e `eleicao`), e guarda na variável `coordenador` o nome do coordenador atual.

**Sincronização (Berkeley), a cada 15 mensagens de clientes:**
- o coordenador pede a hora de todos os servidores ativos, calcula a diferença de cada um para a sua (descontando metade do tempo de ida e volta), tira a média e acerta o próprio relógio com ela;
- os outros pedem a hora correta ao coordenador (REQ `relogio` / REP com `hora`) e acertam o relógio com ela.

O efeito é o mesmo do Berkeley original (todos acabam na média), só que a correção chega aos outros servidores pelo pedido deles, como no diagrama do enunciado. A referência não manda mais a hora no heartbeat.

**Eleição (algoritmo do valentão):** começa quando o servidor sobe, quando o coordenador não responde ao pedido de hora, ou quando chega um pedido de eleição de um servidor de rank menor.
1. O servidor manda `eleicao` para todos os servidores ativos de rank maior (lista da referência).
2. Se algum responder OK, ele espera o anúncio do novo coordenador por 5 s (se não vier, faz outra eleição).
3. Se ninguém responder, ele é o coordenador e publica o próprio nome e rank no tópico `servers`.

Todos os servidores assinam o tópico `servers`. Se chegar o anúncio de um servidor de rank menor que o seu, o servidor faz uma eleição nova (é o que acontece quando os servidores sobem juntos).

Para ver a eleição: `docker stop` no coordenador. O próximo pedido de hora dá tempo esgotado e o servidor vivo de maior rank assume.

## Réplica

O broker reparte os pedidos em round-robin, então cada pedido de escrita (login, criar canal, publicar) chega a um servidor só. Para todos terem todos os dados, foi usada **réplica ativa com atualização por push**, das alternativas vistas na aula de consistência e replicação: o servidor que recebe a escrita propaga a **operação** para os outros, e cada um a aplica nos seus dados e no seu arquivo.

Como foi implementado:
- toda escrita vira um `Evento` (definido no `.proto`) com um id único `<servidor>-<n>`;
- o servidor aplica o evento, salva no seu arquivo e publica no tópico `replica` do proxy (o mesmo proxy dos canais);
- todos os servidores assinam `replica`. Ao receber um evento de outro servidor, aplicam e salvam se o id ainda não foi visto. O id evita aplicar duas vezes;
- **mudança no método:** quem estava fora do ar perde as publicações (o pub/sub não guarda nada). Por isso, ao subir, o servidor pede a outro servidor ativo (REQ `sincronizar` na porta 5560) todos os eventos que ele tem e aplica os que faltam. É uma atualização por pull, só na entrada.

A consistência é **eventual**: por alguns milissegundos um servidor pode ainda não ter um evento que outro já tem. Depois que as escritas param, todos ficam iguais. Se dois servidores criarem o mesmo canal ao mesmo tempo, os dois eventos são aplicados e o canal aparece uma vez só na lista, então o conflito não tem efeito. Cada mensagem guarda o timestamp e o relógio lógico, que servem para ordenar o histórico.

Para conferir: com tudo rodando, os arquivos em `dados/` ficam com os mesmos ids. Dá para fazer `docker stop servidor3`, esperar um pouco e `docker start servidor3`: ele lê o próprio arquivo e pega com outro servidor o que perdeu.

## Extra: login com prova de conhecimento zero

No enunciado o login é só o nome, então qualquer bot poderia dizer que é outro. Aqui o bot prova que é o dono do nome sem mandar nenhum segredo, usando a prova de conhecimento zero por isomorfismo de grafos.

- Cada bot tem um grafo G0 de 20 vértices e um segredo π, uma troca de nomes dos vértices. G1 = π(G0) é o mesmo grafo com os vértices renomeados. G0 e G1 são públicos (`chaves/publicas/<bot>.pub`) e π fica só com o bot (`chaves/privadas/<bot>.sec`).
- Em cada rodada o bot renomeia G0 com uma troca sorteada σ e manda o resultado H. A moeda decide o que ele mostra: com 0 mostra σ (como G0 vira H), com 1 mostra σ∘π⁻¹ (como G1 vira H). Só quem sabe π consegue mostrar o segundo. Cada resposta sozinha é uma troca sorteada e não revela π.
- Quem não sabe π só consegue responder um dos lados, então erra metade das rodadas. São 32 rodadas: chance de 1 em 2³² de passar.
- O enunciado pede que o login seja 1 REQ e 1 REP, e com o broker em round-robin cada mensagem pode ir para um servidor diferente. Por isso as moedas não são sorteadas pelo servidor: elas saem do SHA-256 de (nome, instante, G0, G1, todos os H) (Fiat-Shamir). O bot manda as 32 rodadas numa mensagem só e qualquer servidor confere.
- No primeiro login o servidor guarda G0 e G1 do usuário (e isso é replicado). Depois só aceita a mesma chave. Prova com mais de 30 s ou repetida é recusada.
- O login devolve uma `sessao`, que o bot manda ao criar canal e publicar. Sem sessão válida o pedido é recusado.

O container `impostor` tenta se passar pelo bot1 e é barrado de três jeitos:
1. login com uma chave inventada: `chave nao confere com a cadastrada`;
2. login com a chave pública do bot1, chutando as moedas: `prova invalida: rodada N falhou`;
3. publicar com uma sessão inventada: `sessao invalida`.

Isomorfismo de grafos aleatórios pequenos não é difícil de resolver na prática, então isso é didático. Em sistemas reais a mesma ideia é usada com logaritmo discreto (Schnorr).

Para começar do zero, apague juntos `dados/*.jsonl` e `chaves/*/*.sec`, `chaves/*/*.pub`: se só as chaves sumirem, os servidores recusam a chave nova dos bots.

## Estrutura

```
mensagens.proto       formato das mensagens
python/broker.py      ROUTER 5555 (bots) / DEALER 5556 (servidores)
python/proxy.py       XSUB 5557 (servidores) / XPUB 5558 (bots)
python/referencia.py  REP 5559
python/servidor.py
cpp/bot.cpp           bot e impostor (ALVO=bot1)
dados/                arquivos dos servidores
chaves/               chaves do login (publicas e privadas)
```
