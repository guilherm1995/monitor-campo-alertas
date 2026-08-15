# `/status` — o que o bot fez e em que chão ele pisa

> O bloco da máquina entrou em **14/08/2026**. Antes disso o `/status` contava
> o que o bot tinha feito no dia, mas nada sobre o servidor — e "o bot está
> lento" e "a máquina está sem RAM" são perguntas diferentes.

O comando funciona no grupo do **Telegram** e no do **WhatsApp**.

---

## 1. O que ele responde

Duas partes. A primeira são os contadores do dia:

```
⚙️ Status atual do sistema

CAPEX pendente SUL RJ: 41
CAPEX pendente LITORAL NORTE SP: 27
CAPEX notificadas hoje: 63
Garantias notificadas hoje: 4
Improdutivas notificadas hoje: 2
Erros registrados no LOG hoje: 0
TOTAL de O.S analisadas por minuto: 412.8
```

Os contadores zeram sozinhos na virada do dia e são persistidos em disco — se
o processo reiniciar no meio do turno, o que já foi contado não se perde.

> Com o monitoramento **pausado**, o `/status` avisa antes dos números. Sem
> esse aviso ele devolveria contadores congelados com cara de contador ao
> vivo.

A segunda parte é a máquina:

```
🖥️ Máquina
RAM      1.7 /   7.6 GB  22%
Disco   10.0 /  97.9 GB  11%
Livre    6.0 GB RAM · 83 GB disco
CPU        0%  carga 0.11/8
Uptime máq 13h44 · bot 2h05
Proc      47 em execução

chrome-headless-shell   362 MB
iniciar_site.py         337 MB
bot_campo_monitorament… 194 MB
index.js                173 MB
chrome-headless-shell   130 MB
```

---

## 2. Por que ele existe

É **uma máquina só** para o bot, o site, o Chromium da sessão e a VPN. Quando
alguma coisa fica lenta, a primeira pergunta é qual dos quatro está comendo a
máquina — e até aqui isso só se respondia abrindo SSH.

O bot já vigiava a própria memória (tem teto e reciclagem controlada), mas
isso é o bot olhando para si. O `/status` mostra o resto: quanto sobra de RAM
para o Chromium nascer, quanto sobra de disco para o log e os PNG, e se a
carga está alta por causa de alguém que não é nosso.

---

## 3. As decisões de formato

Parecem detalhe e não são — este bloco é lido no celular, no meio de uma
conversa de grupo.

### Fonte monoespaçada, senão nada alinha

O bloco vai inteiro dentro de crase tripla. É o que dá monoespaçada **nos dois
aplicativos**; sem ela, alinhamento por espaço não sobrevive à renderização.

Um bloco só, e não dois, para não virar duas caixas cinzentas na conversa.

### 36 colunas

Acima disso a bolha do WhatsApp quebra a linha no celular — e o alinhamento
que se pagou para ter vai embora justamente onde ele seria usado.

É por isso que a carga aparece como `carga 0.11/8` e não como as três médias
de 1, 5 e 15 minutos: não cabiam. E `0.11` sozinho não se lê sem saber contra
quantos núcleos ele corre, daí o `/8`.

### Unidade fixa por seção

Máquina em **GB**, processo em **MB** — sempre, mesmo quando o número fica
feio. A versão anterior escolhia a "melhor" unidade para cada valor e saía com
`1.6 GB` numa linha e `378 MB` na seguinte, obrigando quem lê a converter de
cabeça justamente para comparar, que é a única coisa que se faz com esses
números.

### Os campos numéricos têm 5 e 6 colunas

Medida de render conferido, não estimativa: o disco chega a
`131.5 / 208.0`, e um campo curto demais empurra a barra e desalinha
exatamente as duas linhas que existem para ser comparadas.

Pelo mesmo motivo o nome de processo é cortado em 21 caracteres com
reticência. Sem o corte, um nome comprido desloca só a linha dele e a lista
deixa de se ler de relance.

### Processo é identificado pelo script, não pelo nome

`python3` sozinho não diz nada: há quatro deles na máquina. O rótulo sai do
`cmdline`, então aparece `iniciar_site.py` e `bot_campo_monitoramento.py` em
vez de quatro linhas iguais.

---

## 4. Ele não pode derrubar o `/status`

Cada medida vai no **seu próprio `try`**, e o bloco inteiro é opcional. Um
`/status` que estoura por causa de um contador de CPU não responde nem o que a
operação foi perguntar.

- Sem `psutil` instalado, o bloco diz isso em uma linha e o resto sai normal.
- Falhando uma medida só, as outras aparecem.
- Falhando todas, sai `não consegui medir` — e os contadores do dia continuam.

---

## 5. Cuidado ao conferir isso por fora

Medindo a memória do bot pelo terminal, **não** use o maior processo da lista:

```bash
ps --sort=-rss | head -1     # isto pega o SITE, não o bot
```

O site fica em ~340 MB e o bot em ~190 MB. Meça pelo PID do serviço:

```bash
ps -o pid,etime,rss -p $(systemctl show -p MainPID --value campo-bot)
```

Foi assim que uma medição de 14/08/2026 quase virou um alarme falso de
consumo de memória do bot.

---

## 6. Onde está

| | |
|---|---|
| O bloco da máquina | `bot/bot_campo_monitoramento.py`, `montar_bloco_maquina` |
| Rótulo de processo | mesmo arquivo, `_rotulo_processo` |
| Formatadores | mesmo arquivo, `_gb`, `_mb`, `_fmt_duracao` |
| Os contadores do dia | mesmo arquivo, `montar_mensagem_status` |
