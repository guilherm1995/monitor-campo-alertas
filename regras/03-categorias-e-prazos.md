# CATEGORIAS DE O.S. E PRAZOS

## As cinco categorias

A categoria vem do **código de fila** da O.S. Os códigos moram em
`backlog_capex.py` e `backlog_reparo.py`.

O reparo tem **duas** filas: `ES05` ("REPARO") e `REPPME` ("REPARO - PME"). É
o mesmo serviço; o que muda é o porte do cliente. Para garantia as duas valem
igual, e é `CODIGOS_REPARO_CAMPO`, no `bot_campo_monitoramento.py`, que diz isso.
No **backlog** de reparo só `ES05` é contado até hoje -- ver a armadilha em
`05-armadilhas.md`.

| categoria | códigos de fila | grupo |
|---|---|---|
| Ativação | ES02, ES02PV, ATV1, ATB2B, ATVPME, ATVPRE | CAPEX |
| Mudança de endereço | ES04 | CAPEX |
| Reparo | ES05, REPPME | reparo |
| Upgrade | UP02 (nome interno "UPGRADE NÃO LÓGICO") | reparo |
| Mudança de cômodo | ES15 | reparo |

Upgrade e mudança de cômodo são os dois únicos tipos com **alerta individual
opcional**: ele fica desligado até alguém pedir `/alertas` no grupo, e vale
por região. Ver `09-o-que-o-bot-faz.md`.

**CAPEX** é cliente novo entrando (ou mudando de casa). **Reparo** é cliente
que já existe e parou de funcionar. As duas coisas competem pelo mesmo
técnico, e por isso aparecem separadas em toda contagem.

## Os prazos NÃO são iguais entre categorias

Este é o ponto que mais gera resposta errada, porque o número parece
comparável e não é. Os limites moram em `LIMITE_BUCKET1_HORAS` e
`LIMITE_BUCKET2_*` dos dois módulos de backlog:

| categoria | balde 1 | balde 2 | balde 3 |
|---|---|---|---|
| Ativação | até 48h | de 48h a 7 dias | acima de 7 dias |
| Mudança de endereço | até 48h | de 48h a 7 dias | acima de 7 dias |
| Reparo | até 24h | de 24h a 48h | acima de 48h |
| Upgrade | até 4 dias | de 4 a 7 dias | acima de 7 dias |
| Mudança de cômodo | até 4 dias | de 4 a 7 dias | acima de 7 dias |

Consequências práticas:

- Um reparo com 3 dias está **estourado**; uma ativação com 3 dias está
  **dentro do prazo**. O mesmo "3 dias" significa coisas opostas.
- "Acima do balde 3" não é uma medida única da operação. Somar o balde 3 de
  reparo com o de ativação produz um número que não quer dizer nada.
- O dossiê escreve o rótulo já derivado do prazo daquela categoria. **Leia o
  rótulo da linha**, não o converta em dias de cabeça.

Até 30/08/2026 o dossiê usava um rótulo fixo -- "até 48h / 2 a 5 dias / acima
de 5 dias" -- para todas as categorias. Nenhum dos três valia para reparo, e o
prazo de ativação já tinha mudado de 5 para 7 dias. O número estava certo e o
nome ao lado dele, errado.

## Rótulos de agendamento

O dossiê classifica cada linha e escreve o rótulo pronto, entre colchetes:

- `[HOJE]`
- `[ATRASADA, deveria ter sido feita há N dia(s)]`
- `[MARCADA PARA O FUTURO, daqui a N dia(s)]`
- `sem agenda`

Os três rótulos não compartilham nenhuma palavra, de propósito. Com "(D+2)" ao
lado de "(VENCIDA há 2 dias)" na linha vizinha, um modelo pequeno leu o número
certo e a palavra errada, e devolveu uma O.S. futura como atrasada.

**Nunca recalcule pela data, e nunca aproveite o rótulo da linha vizinha.**

## Véspera de balde

O dossiê já conta quantas O.S. mudam de balde amanhã, por categoria. É a única
aritmética de calendário que ele faz, e existe justamente para não ser feita
de cabeça. Serve para a pergunta "o que ataco hoje": o que vira amanhã vale
mais que o que já virou.
