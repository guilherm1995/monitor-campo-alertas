# ARMADILHAS

Cada item aqui é um erro que já aconteceu. Todos têm a mesma assinatura: **não
dão erro nenhum**. Devolvem um número calmo e errado, que passa por resposta
apurada. É por isso que estão escritos.

## A coluna `Tipo de Atividade.1`

A exportação do OFS traz **duas** colunas chamadas `Tipo de Atividade`. A
primeira é o regime da atividade e vem inteira como `Normal`. A segunda -- que
o pandas renomeia com o sufixo `.1`, por serem dois cabeçalhos iguais -- é a
que diz Ativação, Reparo, Upgrade.

Ler a primeira devolve tudo `Normal`, zero garantias, e nenhum erro.

**O que vale é `Tipo de Atividade.1`.**

## "Não concluído" não é "cancelado"

São status diferentes no OFS e significam coisas diferentes: a atividade não
concluída ficou pelo caminho; a cancelada foi desmarcada. Somar as duas como
"não deu certo" mistura dois problemas que se resolvem de formas opostas.

## Cada linha do OFS é uma tentativa

O mesmo serviço que voltou três vezes aparece como três atividades. Contar
linhas conta tentativas, não serviços distintos. Quando a pergunta é "quantas
O.S. o técnico fez", o número de linhas superestima.

As buscas do assistente já devolvem as contagens apuradas (por status, por
tipo, total de produtivas) exatamente para ele não contar linhas.

## Almoço e Consulta Médica não são serviço

Aparecem na rota como atividades. Entram na contagem por recurso e fazem
"quantas atividades o fulano tem hoje" crescer sem trabalho nenhum. Ficam de
fora das produtivas (`TIPOS_NAO_PRODUTIVOS`).

## Recurso nem sempre é pessoa

CARAGUATATUBA é uma fila de praça, não um técnico. "O técnico CARAGUATATUBA"
é erro.

## `visto_em` não é "aberto agora"

O carimbo é por dia. Uma prévia de garantias montada por ele conta chamado já
fechado.

## Sigla parecida não é a mesma sigla

SSTBO/SST, BERTN/BERT, BMA/RSD. Comparação por prefixo já produziu rollup
errado em 7 de 47 casos medidos. Ver `02-unidades.md`.

## Bases que se refazem sozinhas

Garantias e improdutivas não são mais planilhas mantidas à mão: são
reconstruídas da base histórica. Uma correção feita na planilha some na
próxima reconstrução.

## Lista truncada em silêncio

O dossiê corta a tabela de O.S. num teto (`TETO_LINHAS_OS`) e, quando corta,
**diz que cortou**. Uma lista truncada sem aviso é a maneira mais fácil de
fazer o modelo responder "não há mais nenhuma" com convicção e estar errado.
Qualquer lista nova que ganhe teto tem de ganhar o aviso junto.

## Datas com ano 0026

Existem linhas com ano `0026` na base. São erro de digitação na origem, não
datas do ano 26. Uma conta de aging sobre elas produz milhares de dias.

## O contrato de teste 1111111

Existe na base e não é cliente. Aparece em contagens se ninguém tirar.

## Recusar sem procurar

A pior de todas, e a única que não é de dados: dizer "não consta" sem ter
feito a busca. Soa **exatamente igual** a uma resposta apurada, e quem lê não
tem como distinguir. Já foi observada duas vezes, com dois modelos diferentes.

A defesa está em três camadas: a instrução manda buscar antes de negar; o
avaliador tem casos que reprovam a negação sem busca; e o código remove a
citação `[BUSCA]` quando nenhuma busca foi feita.

## REPPME: o reparo de PME é reparo, mas o backlog ainda não o conta

O CAMPO separa o reparo do cliente PME numa fila própria, `REPPME`. Ela vem na
busca desde 08/08/2026, mas ficou tempo demais sem ninguém que a tratasse: o
laço da varredura só reconhecia `ES05`.

Em **04/09/2026** isso foi corrigido para a **garantia**: `CODIGOS_REPARO_CAMPO`
passou a valer `['ES05', 'REPPME']`, e o reparo de PME agora entra no alerta e
na lista de garantias em aberto. O caso que revelou a falha foi o contrato
6911438 -- ativação em 03/09, reparo no dia seguinte, nenhum alerta.

**O backlog continua contando só `ES05`** (`CODIGOS_REPARO` em
`backlog_reparo.py`). Então o número de reparo do backlog e a lista de
garantias podem divergir para PME, e isso é intencional até alguém decidir o
contrário: mudar o backlog muda um número que a operação confere todo dia.

Ao mexer nas filas de reparo, rode `python teste_garantia_pme.py`.
