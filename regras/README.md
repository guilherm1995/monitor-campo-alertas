# REGRAS DA OPERAÇÃO

Este diretório é a fonte única do que a operação sabe sobre si mesma, escrita
para ser lida por uma inteligência artificial qualquer -- a do `/bot`, a de
quem for programar aqui, ou a próxima que aparecer.

Antes disso, esse conhecimento existia em três lugares que não conversavam: a
instrução do `/bot` dentro do `assistente_ia.py`, o glossário dentro do
`dossie_operacao.py` e os prazos dentro dos `backlog_*.py`. Os três
descreviam a mesma operação, e em 30/08/2026 já discordavam entre si sobre
quanto tempo uma O.S. leva para mudar de balde -- o dossiê dizia 5 dias, o
código do CAPEX usava 7, e o `/bot` repetia o número do dossiê com citação.
Errar assim é pior do que não responder: a citação faz o erro parecer apurado.

## Como está organizado

| arquivo | o que é | quem lê |
|---|---|---|
| `nucleo.md` | as regras de conduta que vão em TODA pergunta do `/bot` | o modelo, a cada pergunta |
| `01-vocabulario.md` | o que cada palavra da operação significa | modelo e gente |
| `02-unidades.md` | siglas, cidades, regiões, e o que é monitorado | modelo e gente |
| `03-categorias-e-prazos.md` | categorias de O.S., códigos de fila e os prazos | modelo e gente |
| `04-fontes.md` | de onde vem cada número, e o que cada fonte NÃO responde | modelo e gente |
| `05-armadilhas.md` | os erros que já aconteceram, para não acontecerem de novo | quem for mexer |
| `06-privacidade.md` | dado de cliente: o que sai, para onde, e o que custa | quem for mexer |
| `07-como-consultar.md` | o passo a passo de CAMPO, Autenticador e OFS: autenticação, pedido, e como cada um falha | quem for mexer |
| `08-onde-achar.md` | mapa de dados: as 34 colunas do OFS, os campos do CAMPO, onde está telefone, endereço e histórico | modelo e gente |
| `09-o-que-o-bot-faz.md` | os alertas, os comandos, o `/bot` e os horários fixos | modelo e gente |
| `10-mapa-do-codigo.md` | onde mexer para mudar cada coisa, nos dois projetos | quem for mexer |
| `11-carga-do-dia-seguinte.md` | a prévia da carga: o balde, os tipos, os status, e São Sebastião partida em três rotas | modelo e gente |
| `12-renovar-a-sessao-do-ofs.md` | como qualquer operador renova a sessão do OFS pela extensão do Chrome, sem o administrador | quem for mexer |

O `nucleo.md` é curto de propósito: ele viaja em toda pergunta e ocupa cota.
Os outros são grandes porque são consultados, não recitados.

Do 01 ao 06 está a operação -- o que as palavras querem dizer, onde ficam as
praças, quais são os prazos, de onde vem cada número. Do 07 ao 10 está o
sistema -- como se fala com cada fonte, onde cada dado mora, o que o bot faz e
onde o código está. O 11 é uma visão inteira, do recorte ao desenho: fica
sozinho porque é a única que a operação descreve como um procedimento, e não
como um conceito, e o 12 é um procedimento de operação: quem faz, de onde, e o
que o servidor recusa.

## A regra sobre as regras

Um fato mora em UM lugar. Se ele já está no código -- um prazo, uma lista de
siglas, um código de fila --, o texto daqui não repete o valor: aponta para a
constante que o define. Copiar o valor para cá criaria a segunda cópia, e a
segunda cópia é exatamente o defeito que este diretório existe para não ter.

Quando um prazo mudar, mude a constante em `backlog_capex.py` ou
`backlog_reparo.py`. O dossiê deriva o rótulo dela, e o texto daqui explica a
regra sem citar o número.

## Como mudar uma regra de conduta

Toda regra do `nucleo.md` nasceu de uma falha observada, e cada uma tem um
caso no `avaliar_assistente.py` que a defende. Ao mexer numa:

1. mude o `nucleo.md`;
2. rode a bateria (`sudo /tmp/rodar_eval.sh` no servidor);
3. se a mudança não tinha caso que a defendesse, escreva o caso.

Regra sem caso que a prove é regra que ninguém vai perceber quando quebrar.
