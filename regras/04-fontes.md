# AS FONTES: o que cada uma responde, e o que ela NÃO responde

Toda afirmação da operação sai de uma destas fontes. Saber o que cada uma
**não** alcança é mais importante que saber o que ela traz: quase todo erro
grave aqui é uma fonte respondendo uma pergunta que não era dela.

O dossiê abre com a seção `== FONTES DESTE DOSSIÊ ==`, dizendo quais vieram e
quais não. **Fonte marcada como NÃO VEIO não sustenta afirmação nenhuma** --
nem a afirmação negativa. Se o Autenticador não veio, a resposta certa não é "nenhum
contrato está offline"; é que não dá para saber.

## CAMPO -- o backlog

O sistema onde a O.S. nasce e vive enquanto está aberta.

**Responde:** o que está aberto agora, de quem é, de que categoria, há quanto
tempo, para quando está agendado, se tem pacote.

**Não responde:** nada que já foi concluído. O backlog é um retrato do que
falta. Uma O.S. fechada ontem simplesmente não está lá -- e essa é a origem da
resposta errada mais comum, "não consta", para um serviço que foi feito.

## OFS GERAL -- a agenda de hoje por contrato

**Responde:** se o contrato está na agenda de campo de hoje.

**Não responde:** se o serviço foi feito, nem o que aconteceu nele. Estar na
agenda é uma intenção, não um resultado.

É a segunda metade da definição de **Enviado D0** (ver `01-vocabulario.md`).
O CAMPO é a fonte de verdade do backlog; o OFS GERAL só diz se aquele contrato
entrou na agenda de hoje.

## OFS, exportação do dia -- a agenda de campo

A planilha do dia inteiro, atividade por atividade.

**Responde:** quantas atividades, de quem, em que cidade, com que status
(concluído, cancelado, não concluído, pendente, suspenso), a que horas, e o
motivo de encerramento.

**Não responde:** o motivo real de um cancelamento, e nada sobre dias
anteriores.

Duas armadilhas moram aqui e estão em `05-armadilhas.md`: a coluna
`Tipo de Atividade.1` e a diferença entre "não concluído" e "cancelado".

## Base histórica do OFS

O acumulado das exportações. É o que alimenta improdutivas e garantias, e é
o que as buscas do assistente leem.

**Responde:** o histórico de um contrato, a rota de um técnico num dia
passado, quando algo foi feito.

**Não responde:** o que está aberto agora -- para isso é o CAMPO.

## Autenticador

**Responde:** se o contrato está online ou offline neste momento.

**Não responde:** por que está offline, nem há quanto tempo. Só faz sentido
cruzado com reparo aberto.

## Lista de garantias

Reparos que voltaram dentro do prazo do atendimento anterior.

**Responde:** quantas garantias abertas, onde, de que tipo (IRR, IFI,
IFI de MDE), com que aging.

**Não responde:** culpa. Garantia é um fato de prazo, não um juízo sobre o
técnico anterior.

Cobre uma lista de praças **maior** que a do backlog -- ver `02-unidades.md`.

## Improdutivas

Base das visitas improdutivas de origem técnica, dentro de uma janela de dias
(`DIAS_JANELA`, em `improdutivas.py`).

**Responde:** se este contrato já teve visita improdutiva antes.

**Não responde:** nada fora da janela -- e, pior, nada que não estiver no
arquivo exportado. Aumentar a janela sem exportar mais dias não muda nada e
não dá erro nenhum.

## Área de risco

Mapa e lista de ruas marcadas.

**Responde:** se o endereço da O.S. cai em área de risco.

**Não responde:** se é seguro ir hoje. Muda o como, não o se.

## Termômetro de entrantes

**Responde:** quantas O.S. entraram hoje, contra a média histórica.

**Não responde:** por que entraram. Serve para separar "o backlog cresceu
porque entrou muito" de "cresceu porque saiu pouco" -- duas ações diferentes.

## Notificações do bot

O que o próprio bot já avisou no grupo.

**Responde:** se aquela O.S. já foi anunciada, para não anunciar duas vezes.

**Não responde:** se alguém leu ou agiu.
