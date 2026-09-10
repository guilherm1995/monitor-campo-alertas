# UNIDADES, CIDADES E REGIÕES

A sigla é a chave. As listas abaixo descrevem o que existe; os valores moram
em `backlog_capex.py` (`REGIOES`) e `garantias_lista.py` (`LITORAL_SP`, `RJ`,
`CIDADES`), e é de lá que o código lê.

## As duas regiões

**LITORAL NORTE SP** e **SUL RJ**. Toda contagem por região sai dessa divisão,
e ela também decide para qual grupo de WhatsApp um alerta vai.

## As siglas e suas cidades

| sigla | cidade | região |
|---|---|---|
| CGT | Caraguatatuba | Litoral Norte SP |
| BASE | Caraguatatuba 1 | Litoral Norte SP |
| SST | São Sebastião | Litoral Norte SP |
| SSTBO | Boiçucanga | Litoral Norte SP |
| IBL | Ilhabela | Litoral Norte SP |
| BERT | Bertioga | Litoral Norte SP |
| BERTN | Bertioga Norte | Litoral Norte SP |
| UTB | Ubatuba | Litoral Norte SP |
| RSD | Resende | Sul RJ |
| MPE | Miguel Pereira | Sul RJ |
| VAS | Vassouras | Sul RJ |
| VRD | Volta Redonda | Sul RJ |
| PNDO | Penedo | Sul RJ |
| VLC | Valença | Sul RJ |
| IZA | Itatiaia | Sul RJ |
| TRS | Três Rios | Sul RJ |
| BMA | Barra Mansa | Sul RJ |
| PORE | Porto Real | Sul RJ |
| COLG | Comendador Levy Gasparian | Sul RJ |
| BPI | Barra do Piraí | Sul RJ |
| PFS | Paty do Alferes | Sul RJ |
| PDS | Paraíba do Sul | Sul RJ |
| PNHE | Pinheiral | Sul RJ |
| CBF | Cabo Frio | Sul RJ |

## O que é monitorado não é o que aparece

Esta é a distinção que mais causa confusão, e ela é **deliberada**:

- **Backlog e CAPEX** cobrem as praças que roteirizamos. A lista está em
  `backlog_capex.REGIOES`.
- **Garantias** cobrem uma lista **maior**, em `garantias_lista.py`, que
  inclui **UTB (Ubatuba)** e **CBF (Cabo Frio)**.

Uma garantia pode aparecer em Ubatuba sem que exista backlog de Ubatuba. Isso
não é defeito: a garantia é acompanhada onde ela cai, o backlog só onde
mandamos equipe. Copiar a lista de garantias de volta para o backlog ligaria
alerta de CAPEX em praça que não roteirizamos.

Ao responder: se a pergunta é de backlog e a sigla não está na lista de
backlog, a resposta certa é que aquela praça não é acompanhada nesse recorte
-- não é "zero O.S.".

## Sigla parecida não é a mesma sigla

**SSTBO não é SST.** **BERTN não é BERT.** **BASE é Caraguatatuba, mas não é
CGT.** Já houve rollup errado por semelhança de prefixo (SSTBO caindo em SST,
BERTN em CGT, BMA em RSD) -- 7 de 47 casos reais medidos. Comparar por
prefixo é o erro; a sigla é exata ou não é.

## Cidade não é unidade

O OFS trabalha por **cidade** (CARAGUATATUBA, SAO SEBASTIAO, ILHABELA, BARRA
MANSA); o CAMPO, por **unidade**. Uma pergunta sobre "Caraguatatuba" pode querer
CGT, BASE, ou as duas. Quando a diferença mudar o número, pergunte de volta.
