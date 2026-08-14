# Lista de garantias nos grupos regionais

> Criada em **13/08/2026**, a pedido da operação: a lista que só existia na
> tela do site passou a chegar sozinha nos grupos de roteirização.

---

## 1. O que chega, onde e quando

De **hora em hora, das 7h às 19h**, cada grupo regional recebe duas
mensagens:

1. uma **imagem** com a tabela — as mesmas colunas da tela de Garantias;
2. os **números de contrato em texto**, agrupados por cidade.

**Região sem garantia nenhuma recebe só um texto**, sem imagem — tabela
sem linha é imagem de nada, e pesa o mesmo no grupo:

```
🛠️ GARANTIAS EM ABERTO — Sul RJ
✅ Nenhuma garantia em aberto.
Conferido às 13/08/2026 14:00 · CAMPO lido às 13:58
```

Mas **manda** — não cala. No grupo, mensagem que não chega não se
distingue de bot parado, e é justamente quando não há pendência que
alguém precisa poder confiar que não há mesmo. O horário da conferência
vai no texto por isso: é o que separa "conferi agora e está limpo" de
"faz três horas que não sei".

| Região | Grupo |
|---|---|
| Litoral Norte SP | `<grupo do litoral>` |
| Sul RJ | `<grupo do RJ>` |

São 13 envios por dia por grupo (7h, 8h, … 19h).

### Por que duas mensagens

A imagem se lê de relance, mas **não se copia**. Quem precisa consultar o
contrato no Autenticador ou no OFS teria de digitar o número olhando a foto — e
é assim que se erra um dígito. O texto existe só para isso: um contrato
por linha, porque no WhatsApp tocar e segurar copia a linha inteira.

Tudo o mais fica na imagem. Repetir status, aging e técnico no texto só
faria a mensagem virar parede.

---

## 2. De onde vem a lista (e por que não é a do site)

O site cruza duas planilhas: `chamados_abertos_field_service.xlsx` com a
`base OFS ok.xlsx`. Funciona para a tela, mas o "em aberto" dali vale a
data em que **alguém exportou** aquele arquivo.

Para uma lista que vai sozinha ao grupo de hora em hora isso não serve. Um
grupo de roteirização **age** sobre o que lê: mandar contrato já resolvido
custa deslocamento de técnico.

Então aqui o "em aberto" vem do CAMPO, ao vivo:

> **garantia** = uma O.S. que o próprio bot **já notificou** como garantia
> **e** que a última varredura **ainda viu aberta no CAMPO**.

As duas metades já existiam e não foram criadas para isto:

| | |
|---|---|
| as notificadas | `reparos_avaliados.json` (`notificado: true`) |
| as ainda abertas | `reparos_abertos_conhecidos()`, publicado ao fim de cada varredura completa |

A interseção é a lista. **Nenhuma planilha no caminho** — não há nada que
alguém precise lembrar de exportar, e uma O.S. que fecha some da lista
sozinha, sem ninguém dar baixa.

> ⚠️ Antes da primeira varredura completa o bot **não manda nada** e
> registra no log. Uma lista vazia no grupo se leria como "nenhuma garantia
> pendente", que é o oposto de "ainda não sei".

---

## 3. As colunas

| Coluna | De onde vem |
|---|---|
| Contrato | do registro da garantia |
| Contato | telefones colhidos do chamado no CAMPO |
| Status | consulta ao **Autenticador** na hora do envio (ONLINE/OFFLINE) |
| Aging | dias entre o serviço anterior e a abertura do reparo |
| Serviço | `IRR` (reparo), `IFI` (ativação), `IFI de MDE` (mudança) |
| Técnico (OFS) | quem executou o serviço **anterior** |
| Bairro | do chamado no CAMPO |

Uma consulta só ao Autenticador cobre as duas regiões: ela leva ~35s e depende
da VPN, então uma por região dobraria o custo pela mesma resposta. Se o
Autenticador não responder, a lista sai mesmo assim, com o status em `—`.

### O técnico é do serviço original, e não muda

Quem executou o reparo ou a ativação que gerou a garantia **já executou**.
É fato fixo. Por isso o técnico é apurado **uma vez e gravado** no
registro, junto do aging e do tipo do serviço — nunca recalculado na hora
de montar a lista.

> ⚠️ Recalcular seria pior do que inútil. `verificar_garantia_reparo`
> devolve o **primeiro** serviço do contrato que cai na janela, e a Base
> OFS é substituída a cada envio pelo site. Com outra base, outro serviço
> do mesmo contrato pode casar primeiro — e a linha sairia com o `aging`
> gravado de um serviço e o técnico de outro. Duas verdades na mesma
> linha, sem nada denunciando.

O técnico passou a ser gravado em 13/08/2026; antes era usado na mensagem
e descartado. As garantias anteriores são preenchidas por
`preencher_tecnico_ofs_faltante`, que **confere se o serviço reencontrado
é o mesmo** (mesmo tipo e mesmo aging) antes de aceitar o nome. Não
batendo, fica `N/D` — técnico errado é pior que técnico ausente.

---

## 4. Os JIDs dos grupos: aprendidos por escuta

Um JID é um número opaco (`120363...@g.us`). Ele **não** é digitado à mão
nem adivinhado pelo nome do grupo — o nome tem emoji, acento e travessão, e
muda sem aviso. **A mensagem prova o grupo; o nome não prova nada.**

O procedimento, com o serviço no ar:

**1. Ligue a escuta** (janela de 15 min):

```bash
curl -s -X POST http://127.0.0.1:3939/escuta-grupos -H 'Content-Type: application/json' -d '{"minutos":15}'
```

**2. Mande qualquer mensagem** em cada um dos dois grupos.

**3. Veja o que chegou:**

```bash
curl -s http://127.0.0.1:3939/escuta-grupos | python -m json.tool
```

Cada grupo aparece com `jid`, `nome`, `quando` e `quem`.

**4. Atribua cada JID à sua região:**

```bash
curl -s -X POST http://127.0.0.1:3939/escuta-grupos/atribuir -H 'Content-Type: application/json' -d '{"regiao":"rj","jid":"120363...@g.us"}'
```

Regiões válidas: `litoral` e `rj`. Isso grava em `bot/config.json`:

```json
"gruposRegiao": { "litoral": "...@g.us", "rj": "...@g.us" }
```

**5. Desligue a escuta** (ou espere ela expirar):

```bash
curl -s -X POST http://127.0.0.1:3939/escuta-grupos -H 'Content-Type: application/json' -d '{"minutos":0}'
```

### O que a escuta guarda — e o que não guarda

Enquanto a janela está aberta o serviço enxerga **todo** grupo de que a
conta participa. Por isso ela anota **só metadado**: JID, nome do grupo,
horário e quem mandou. **O texto da mensagem nunca é guardado** — o
objetivo é descobrir um número, não construir um registro de conversas de
grupos que este bot não tem nada que ler.

Pelo mesmo motivo a janela **expira sozinha** (padrão 15 min, teto 120) e
cada janela começa com a lista limpa. Escuta ampla não é estado para ficar
ligado por esquecimento.

### As travas

- **Destino sem JID não cai no grupo principal**: o envio é *recusado* com
  erro. Mandar a lista do Rio para outro grupo é pior do que não mandar,
  porque quem roteia agiria sobre contrato que não é dele.
- **O mesmo JID não serve a duas regiões** (HTTP 409): as duas listas
  chegariam no mesmo grupo e a segunda empurraria a primeira para cima.
- Atribuir é um passo **separado** de escutar. Ver de qual grupo veio a
  mensagem é observação; decidir que aquele grupo é "o do Rio" é decisão —
  e quem decide é quem mandou a mensagem.

> O grupo de sempre (`grupoJid`) **não muda em nada**. Continua recebendo
> todos os alertas e todos os comandos. Os dois grupos regionais recebem
> só esta lista.

---

## 5. Fora de hora

O comando **`/garantias`** (Telegram) ou **`garantias`** (grupo do
WhatsApp) gera e manda na hora, sem esperar a hora cheia.

O agendador **não dispara ao subir** de propósito: o bot reinicia várias
vezes ao dia, e um disparo por reinício encheria os grupos de listas
repetidas fora de hora.

---

## 6. Se a lista parar de chegar

Na ordem:

1. **Chegou em um grupo só?** É JID. Veja `gruposRegiao` em
   `curl -s http://127.0.0.1:3939/status`. Vazio para uma região significa
   que aquele JID nunca foi aprendido — refaça a seção 4. O log do serviço
   avisa no arranque: `Região sem JID: ...`.
2. **Não chegou em nenhum?** Procure no log
   `Lista de garantias enviada para N de 2 região(ões)`. Se a linha não
   aparece de hora em hora, o agendador não está rodando; se aparece com
   `0 de 2`, o envio falhou e o motivo está na linha logo acima.
3. **Chegou "Nenhuma garantia em aberto"?** Pode ser verdade — repare no
   horário do texto para saber de quando é a conferência. Se não for,
   confira o `reparos_avaliados.json` e procure a linha
   `Lista de garantias adiada` (varredura ainda não completou).
4. **Sem status de conexão (tudo `—`)?** É o Autenticador/VPN. O log traz
   `Autenticador indisponível para a lista de garantias`. A lista continua válida.
5. **Faltando uma cidade?** Procure `com unidade fora das regiões conhecidas`
   no log: ou o CAMPO passou a usar uma sigla nova, ou veio digitada errada.

Para desligar o envio automático sem mexer no código:
`GARANTIAS_ENVIO_ATIVO=0`. A janela também se ajusta por ambiente
(`GARANTIAS_HORA_INICIO`, `GARANTIAS_HORA_FIM`).

---

## 7. Onde está cada coisa

| | |
|---|---|
| Regra da lista, regiões e texto | `bot/garantias_lista.py` |
| A imagem | `bot/garantias_render.py` |
| Envio e agendador | `bot/garantias_envio.py` |
| Fonte das notificadas | `bot/dados/reparos_avaliados.json` |
| Fonte das abertas | `bot_campo_monitoramento.py`, `reparos_abertos_conhecidos` |
| Ligação com o bot | mesmo arquivo, `estado_para_lista_garantias` e `gerar_e_enviar_garantias_agora` |
| Técnico das garantias antigas | mesmo arquivo, `preencher_tecnico_ofs_faltante` |
| Aprendizado do JID e roteamento | `bot/index.js` (`anotarGrupoVisto`, `resolverDestino`) |
| A mesma lista na tela | `site/web/fontes/garantias.py` |
