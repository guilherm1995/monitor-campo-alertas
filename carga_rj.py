# -*- coding: utf-8 -*-
"""O recorte do Sul RJ para a prévia da carga.

O motor é o `carga_litoral`: contagem, turnos, tipos, capa e lista são os
mesmos das duas regionais, e precisam continuar sendo -- é a mesma pergunta
sendo feita. Aqui mora só o que o Rio tem de diferente.

POR QUE A SIGLA DA WORKZONE, E NÃO A CIDADE
-------------------------------------------
No Litoral a rota se decide por cidade + bairro, porque a divisa da cidade não
é a divisa da rota: São Sebastião se parte em três e Bertioga entra por bairro.

No Rio não é assim, e a base diz isso. Levantado em 23/09/2026 sobre o
`OFS GERAL.csv` (3.334 linhas do Rio): o campo `Cidade` bate com a cidade da
sigla da workzone em **97,2%** das linhas -- 94 divergências no total. Não há
bairro a separar, há um campo digitado à mão que erra de vez em quando.

A sigla é mais firme que esse texto, e é o mesmo critério que já define a
regional nas garantias e nas improdutivas. Então ela decide as duas coisas:
QUEM entra na prévia (sigla na lista do Rio) e COM QUE NOME aparece (a cidade
da sigla, não a escrita na linha). Quem escolheu foi o dono, em 23/09/2026:
"o recorte por siglas está bom, mas com os nomes das cidades".

A consequência é desejada: a atividade que chega com `Cidade = CORDEIRO` mas
workzone de Volta Redonda aparece como **Volta Redonda**, que é a rota que a
atende. No Litoral a mesma ideia já vale para o Canto do Mar, que é São
Sebastião no papel e Caraguatatuba na rota.

O BALDE AQUI NÃO DIZ A ROTA
---------------------------
No Litoral cada balde é uma localidade, e o nome dele é o nome do lugar. No Rio
existem só dois -- `RESENDE` e `TRES RIOS` -- e o de Resende recebe atividade de
quase toda a regional: na mesma medição, 223 de Volta Redonda, 203 de Barra
Mansa, 186 de Resende e 75 de Vassouras estavam nele.

Por isso a lista abaixo serve só para MARCAR `no_balde` em cada linha, como no
Litoral -- e aqui, mais do que lá, ela não deve virar agrupamento de tela:
agrupar o Rio por balde juntaria quatro cidades numa linha só.
"""

from __future__ import annotations

import re

import garantias_lista
from carga_litoral import Recorte, achatar

# 'SP-000CGT-WZ006' -> 'CGT'. Mesma leitura que o resto do bot já faz da
# workzone; está aqui porque é o que transforma a linha em cidade.
_SIGLA = re.compile(r'-0*([A-Z]{2,6})-WZ')

# Os dois baldes do Rio, como o OFS escreve na coluna "Recurso". Explícitos
# pela mesma razão do Litoral: adivinhar "isto parece nome de lugar" erraria no
# dia em que entrasse um técnico chamado Resende.
BALDES_RJ = (
    'RESENDE',
    'TRES RIOS',
)

# As siglas da regional e o nome que cada uma mostra. Nenhuma das duas mora
# aqui: `garantias_lista` já é dona das duas coisas, e uma segunda cópia
# divergiria na primeira cidade nova -- que entraria na garantia e não na
# prévia, sem ninguém notar.
SIGLAS_RJ = frozenset(garantias_lista.RJ)


def sigla_da_workzone(chave):
    """'RJ-000VRD-WZ003' -> 'VRD'. None quando a chave não tem sigla."""
    achado = _SIGLA.search(achatar(chave))
    return achado.group(1) if achado else None


def cidade_do_rj(cidade_ofs, endereco, workzone):
    """O nome da cidade que a visão usa, ou None quando a linha não é nossa.

    `cidade_ofs` e `endereco` são ignorados de propósito -- ver o cabeçalho.
    Eles continuam na assinatura porque é a assinatura do `Recorte`, e o
    Litoral precisa deles.
    """
    sigla = sigla_da_workzone(workzone)
    if sigla is None or sigla not in SIGLAS_RJ:
        return None
    # Sigla conhecida pela regional mas sem nome em CIDADES seria uma cidade
    # nova entrando pela porta dos fundos. Mostra a sigla em vez de sumir com
    # a atividade: prévia a menos é erro calado, sigla estranha na capa é
    # erro que alguém vê e me conta.
    return garantias_lista.CIDADES.get(sigla, sigla)


RECORTE_RJ = Recorte(
    chave='rj',
    nome='Sul RJ',
    cidade=cidade_do_rj,
    baldes=BALDES_RJ,
    colunas_exigidas=('Chave Workzone',),
)
