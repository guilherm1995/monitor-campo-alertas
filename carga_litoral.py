# -*- coding: utf-8 -*-
"""A prévia da carga do dia seguinte no Litoral Norte.

O QUE É
-------
Todo fim de tarde a operação precisa saber o que cai no colo dos técnicos do
litoral amanhã: quantas atividades, de que tipo, em que cidade, de manhã ou de
tarde. Essa conta sempre foi feita à mão, no Excel, com uma tabela dinâmica
sobre a extração do OFS -- a "capa" -- e a lista detalhada logo abaixo dela.

Este módulo é aquela planilha, escrita uma vez.

AS REGRAS, ditadas pela operação em 31/08/2026
----------------------------------------------
1. A fonte é o OFS, e entra a carga INTEIRA do dia: o que ainda está no balde
   da localidade esperando alguém pegar E o que já está na rota de um técnico.

   Isto mudou em 01/09/2026. A primeira versão contava só o balde, com o
   argumento de que o que já tem dono não é carga a distribuir. A operação
   corrigiu: a prévia serve para saber QUANTO trabalho o dia tem, e metade do
   trabalho já estar distribuído não o faz sumir.

   A divisão entre balde e rota NÃO aparece na prévia, e isso também é regra:
   ela nunca foi o assunto. Existe só para o total não divergir da planilha, e
   mostrá-la pediria atenção para uma decisão que ninguém toma olhando a
   prévia. Os campos `no_balde` e `com_tecnico` seguem calculados porque o
   teste confere os números da planilha contra o subconjunto do balde.

2. Status: entra TUDO menos "cancelado".

3. Tipo: só Ativação, Mudança de Endereço e Reparo. Upgrade/Downgrade,
   Mudança de Cômodo e Clean Up ficam de fora.

4. São Sebastião não conta como uma cidade só. A operação a parte em TRÊS,
   porque a divisa da cidade não é a divisa das rotas: os bairros da ponta
   norte, que são atendidos de CARAGUATATUBA (BAIRROS_DE_CARAGUATATUBA), os
   bairros do TOPO (TOPO_BAIRROS), e todo o resto, que é a COSTA SUL.
   Caraguatatuba e Ilhabela não se partem.

5. Bertioga entra por BAIRRO. Boa parte da Costa Sul chega ao OFS com cidade
   BERTIOGA -- Boiçucanga, Maresias, Juquehy e companhia --, e essas são
   nossas: entram como COSTA SUL. A Bertioga de verdade, em bairro fora da
   lista que atendemos, sai da prévia.

O DETALHE QUE FAZ ISTO SER CÓDIGO, E NÃO UM FILTRO
--------------------------------------------------
O nome do bairro no endereço do OFS é texto livre digitado por gente. O mesmo
bairro aparece como "CIGARRAS", "Praia das Cigarras" e "BALNEARIO CIGARRAS";
"São Francisco" vem com e sem acento; "Topolândia" vem "TOPOLANDIA". Comparar
o texto cru classificaria a maioria como COSTA SUL sem errar nenhum aviso --
o pior tipo de erro, o que sai calado e com cara de resposta.

Por isso a comparação é sempre sobre o texto achatado (maiúsculas, sem acento,
espaços colapsados) e por FRASE INTEIRA dentro do bairro, não por pedaço de
palavra: "CIGARRAS" casa em "BALNEARIO CIGARRAS", mas "OLARIA" não casaria
dentro de uma palavra maior.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

RAIZ = os.path.dirname(os.path.abspath(__file__))
PASTA_DADOS = os.environ.get('OFS_PASTA_DADOS', os.path.join(RAIZ, 'dados'))

# A base da prévia é o OFS GERAL, e não a extração do dia: a prévia é de
# AMANHÃ, e o "OPERACIONAL.csv" traz só a agenda de hoje. O OFS GERAL vai até D+1
# justamente por causa desta visão e da confirmação de agenda.
ARQUIVO_PADRAO = os.environ.get('OFS_GERAL_ARQUIVO',
                                os.path.join(PASTA_DADOS, 'OFS GERAL.csv'))

# ============================== o recorte ====================================

# Os baldes do Litoral Norte, como o OFS escreve o nome deles na coluna
# "Recurso". Balde e técnico ocupam a MESMA coluna: o que diferencia é que o
# nome do balde é o da localidade. A lista é explícita de propósito -- adivinhar
# "isto parece nome de lugar, aquilo parece nome de gente" erraria no dia em que
# entrasse um técnico chamado Bertioga.
#
# Conferido contra a base em 31/08/2026: estes quatro nomes nunca aparecem com
# "Posição na Rota" maior que zero, e nenhum técnico aparece com zero e nome de
# cidade. O balde é isto.
#
# ISTO NÃO É MAIS UM FILTRO. Desde 01/09/2026 a prévia conta a carga inteira, e
# esta lista só serve para MARCAR cada linha com `no_balde`. A marca não vai
# para a tela -- ela existe para o teste poder conferir os números da planilha
# do Excel, que contava só o balde, contra o subconjunto certo.
BALDES_LITORAL = (
    'CARAGUATATUBA',
    'SAO SEBASTIAO',
    'BOICUCANGA',
    'ILHABELA',
)

# Tudo menos cancelado. Escrito como o que FICA DE FORA, e não como uma lista
# do que entra, porque foi assim que a regra foi dita -- e porque uma lista do
# que entra deixaria um status novo do OFS silenciosamente fora da conta.
STATUS_FORA_DA_CARGA = ('CANCELADO',)

# Os três tipos que são carga de campo. A comparação é por PREFIXO achatado:
# "Reparo" pega "Reparo Corretivo" e qualquer outro reparo que o OFS venha a
# nomear, e "Mudança de Endereço" pega a grafia com e sem acento.
TIPOS_DA_CARGA = ('ATIVACAO', 'MUDANCA DE ENDERECO', 'REPARO')

# Os bairros de São Sebastião que a operação chama de TOPO. Todo o resto da
# cidade é COSTA SUL -- inclusive bairro que ninguém previu, o que é a razão de
# a regra ser "lista do topo + resto" e não duas listas.
#
# As variantes estão escritas à mão porque são as que aparecem na base: o mesmo
# lugar vem ora com o "Praia" na frente, ora sem.
TOPO_BAIRROS = (
    'TOPOLANDIA',
    'CENTRO',
    'SAO FRANCISCO',
    'ITATINGA',
    'MORRO DO ABRIGO',
    'VILA AMELIA',
    'PONTAL DA OLARIA',
    'OLARIA',
    'PRAIA DA OLARIA',
    'CIGARRAS',
    'PRAIA DAS CIGARRAS',
    'PRAIA DO ARRASTAO',
    'ARRASTAO',
    'PRAIA DESERTA',
    'VILA INDUSTRIA',
    'VILA INDUSTRIAL',
    'VARADOURO',
    'PRAIA GRANDE',
    # Porto Grande NAO e Praia Grande: sao dois bairros diferentes, e o
    # casamento aqui e por frase inteira, entao um nunca pegaria o outro.
    # Acrescentado em 03/09/2026, pela operacao.
    'PORTO GRANDE',
    'PONTAL DA CRUZ',
)

TOPO = 'TOPO'
COSTA_SUL = 'COSTA SUL'
CARAGUATATUBA = 'CARAGUATATUBA'

# Bairros de Sao Sebastiao que NAO sao nossos.
#
# Ate 03/09/2026 Sao Sebastiao nao tinha essa saida: todo endereco dela caia em
# TOPO, COSTA SUL ou CARAGUATATUBA, porque a cidade inteira era nossa. Bertioga
# ja tinha (ver BAIRROS_DE_BERTIOGA_QUE_ATENDEMOS); agora Sao Sebastiao tambem.
#
# A diferenca entre as duas e o SENTIDO da lista, e ela importa: em Bertioga a
# lista diz o que ENTRA (e o resto sai), aqui ela diz o que SAI (e o resto
# entra). Cada uma foi escrita no sentido que deixa o caso comum ser o padrao --
# quase toda Bertioga nao e nossa, e quase toda Sao Sebastiao e.
BAIRROS_QUE_NAO_ATENDEMOS = (
    'VILA ITAGUA',
)

# Bairros que ficam em Sao Sebastiao no papel e em Caraguatatuba na rota.
#
# Sao a ponta norte da cidade, encostada na divisa: quem atende sai de
# Caraguatatuba, nao do topo nem da costa sul. Regra da operacao, dita em
# 31/08/2026, depois de ver a primeira previa real classificar as tres
# atividades de la como COSTA SUL. A Enseada entrou em 01/09/2026, pela
# mesma razao e pela mesma rota.
#
# Esta lista e conferida ANTES da lista do topo. As duas nao se cruzam hoje,
# mas a ordem tem de ser explicita: no dia em que se cruzarem, o certo e a
# atividade ir para a rota que a atende de verdade.
BAIRROS_DE_CARAGUATATUBA = (
    'CANTO DO MAR',
    # As duas grafias tortas do Canto do Mar que o OFS traz. Não são bairros
    # novos: são o mesmo lugar digitado errado, e sem elas 5 atividades iam
    # para a rota da Costa Sul. Achadas varrendo a base em 03/09/2026.
    #
    # `CANTO DO MA` não colide com `CANTO DO MAR`: o casamento é por frase
    # inteira, e o R logo depois impede o encontro.
    'CANTO DO MA',
    'CANTO O MAR',
    'JARAGUA',
    'ENSEADA',
    # Bairros que são de Caraguatatuba e chegam com o campo Cidade do OFS
    # dizendo São Sebastião. Confirmados pela operação em 03/09/2026.
    #
    # O do Pegorelly denuncia a si mesmo: o logradouro é
    # "RUA DEZ RESIDENCIAL 1 NOVA CARAGUA".
    #
    # `CASA BRANCA` e não `JARDIM CASA BRANCA`: assim pega as duas formas, com
    # e sem o "Jardim" na frente.
    'PEGORELLY',
    'PEGORELLI',
    'CASA BRANCA',
    # Indaia e de Caraguatatuba. Quando ele chega com Cidade = Sao Sebastiao,
    # o campo Cidade e que esta errado -- a atividade e nossa, e a rota e a de
    # Caraguatatuba. Regra da operacao, 03/09/2026.
    #
    # Com Cidade = BERTIOGA ele NAO e nosso, e sai da previa por outro caminho:
    # a lista do que atendemos em Bertioga nao o inclui.
    'INDAIA',
)

# Os bairros que a gente atende quando a atividade sai com cidade BERTIOGA.
#
# Esta lista é a MESMA que o monitoramento já usava para restringir as siglas
# BERT e BERTN, e mora aqui agora para existir uma cópia só: o
# bot_campo_monitoramento.py a importa deste arquivo. Duas listas divergiriam na
# primeira vez que um bairro entrasse em uma e não na outra, e a divergência
# sairia calada dos dois lados.
#
# POR QUE ELA DECIDE A PRÉVIA
# ---------------------------
# Bertioga aparece no OFS por dois motivos diferentes, e só um deles é nosso:
#
#   1. atividade da COSTA SUL que saiu classificada como Bertioga. Boiçucanga,
#      Maresias, Juquehy, Barra do Sahy, Camburi, Baleia, Paúba e companhia são
#      São Sebastião no mapa e Costa Sul na rota, mas chegam com cidade
#      BERTIOGA. Perder essas é perder carga que é nossa;
#
#   2. Bertioga de verdade, em bairro que NÃO atendemos -- São João é o caso
#      que apareceu na primeira prévia real, em 31/08/2026. Contar essas é
#      mandar a rota para um serviço que não é nosso.
#
# Por isso a regra é por bairro e não por cidade: bairro da lista entra como
# COSTA SUL; bairro de fora dela é IGNORADO, e some da prévia.
BAIRROS_DE_BERTIOGA_QUE_ATENDEMOS = (
    'BORACEIA',
    'BALNEARIO MOGIANO',
    'MORADA DA PRAIA',
    'JUQUEHY',
    'JUQUEHI',
    'BARRA DO SAHY',
    'BARRA DO SAHI',
    'CAMBURI',
    'CAMBURY',
    'CAMBURIZINHO',
    'BALEIA',
    'PRAIA DA BALEIA',
    'SITIO VELHO',
    'VILA CARIOCA',
    'NUCLEO VILA CARIOCA',
    'MARESIAS',
    'PAUBA',
    'BOICUCANGA',
)

# As cidades do litoral, achatadas, e o nome com que aparecem na visão.
#
# São Sebastião não aparece com o próprio nome: ela sempre se parte em TOPO ou
# COSTA SUL, pelo bairro.
#
# Bertioga não decide pela cidade: decide pelo bairro, em
# BAIRROS_DE_BERTIOGA_QUE_ATENDEMOS. O que está na lista é COSTA SUL; o que não
# está sai da prévia. Por isso ela vale None aqui, como São Sebastião.
CIDADES_LITORAL = {
    'CARAGUATATUBA': CARAGUATATUBA,
    'ILHABELA': 'ILHABELA',
    'SAO SEBASTIAO': None,      # parte-se pelo bairro, em tres
    'BERTIOGA': None,           # entra pelo bairro, ou não entra
}

# A ordem em que os turnos aparecem na capa. Um turno que a base traga e não
# esteja aqui entra depois destes, na ordem alfabética -- some da visão nunca.
ORDEM_TURNOS = ('Manhã', 'Inicio Manhã', 'Almoço', 'Tarde')

TOTAL = 'Total Geral'


# ============================== texto ========================================

def achatar(texto):
    """Maiúsculas, sem acento, sem espaço sobrando. A forma de comparar."""
    bruto = unicodedata.normalize('NFKD', str(texto or ''))
    sem_acento = ''.join(c for c in bruto if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', sem_acento).strip().upper()


def bairro_do_endereco(endereco):
    """O bairro, tirado do endereço do OFS.

    O formato é `<logradouro>, <número> <bairro>, <cidade> - <UF>`: o bairro é
    o penúltimo pedaço, sem o número que vem grudado na frente dele. Quando o
    endereço não tem essa forma (e alguns não têm), devolve o endereço inteiro
    achatado -- a classificação seguinte procura frases dentro do texto, então
    um bairro "sujo" ainda classifica certo; um bairro vazio nunca.
    """
    partes = [p.strip() for p in str(endereco or '').split(',') if p.strip()]
    if len(partes) < 2:
        return achatar(endereco)
    # O último pedaço é "CIDADE - UF"; o bairro é o de antes.
    bairro = partes[-2]
    # Tira o número da casa, que vem colado no começo: "336 CENTRO" -> "CENTRO".
    bairro = re.sub(r'^\s*[\d.\-/]+\s*', '', bairro)
    return achatar(bairro) or achatar(endereco)


def _frase_dentro(frase, texto):
    """A frase inteira aparece no texto, entre limites de palavra."""
    return re.search(r'(?<![A-Z0-9])%s(?![A-Z0-9])' % re.escape(frase),
                     texto) is not None


def regiao_de_sao_sebastiao(endereco):
    """Em que ROTA o endereço de São Sebastião cai, ou None se não for nosso.

    São três rotas, e não duas. A divisa administrativa da cidade e a divisa
    das rotas não são a mesma linha: a ponta norte de São Sebastião é atendida
    de Caraguatatuba, e contá-la aqui como Costa Sul mandaria as atividades
    para uma rota que não vai passar perto delas.

    A ordem da conferência é regra, não acaso:

    0. o que NÃO é nosso, que sai da prévia (devolve None);
    1. os bairros que são rota de Caraguatatuba;
    2. os bairros do TOPO;
    3. todo o resto, que é COSTA SUL.

    O passo 3 é o que garante que bairro novo nunca some da visão -- ele cai em
    Costa Sul sozinho, em vez de virar uma linha em branco que ninguém percebe.
    O passo 0 é a única exceção a isso, e por ser exceção é uma lista curta e
    explícita.
    """
    bairro = bairro_do_endereco(endereco)
    if not bairro:
        return COSTA_SUL
    for nome in BAIRROS_QUE_NAO_ATENDEMOS:
        if _frase_dentro(nome, bairro):
            return None
    for nome in BAIRROS_DE_CARAGUATATUBA:
        if _frase_dentro(nome, bairro):
            return CARAGUATATUBA
    for nome in TOPO_BAIRROS:
        if _frase_dentro(nome, bairro):
            return TOPO
    return COSTA_SUL


def bertioga_e_nossa(endereco):
    """O endereço de Bertioga é de um bairro que atendemos?

    Bertioga é o único caso em que a resposta certa pode ser "esta linha não
    entra na prévia". Nos outros, o que se decide é PARA ONDE a atividade vai;
    aqui se decide antes se ela é nossa.
    """
    bairro = bairro_do_endereco(endereco)
    if not bairro:
        # Sem bairro legível não dá para afirmar que é nossa, e a regra de
        # Bertioga é de inclusão: só entra o que está na lista.
        return False
    return any(_frase_dentro(nome, bairro)
               for nome in BAIRROS_DE_BERTIOGA_QUE_ATENDEMOS)


def cidade_efetiva(cidade, endereco):
    """O nome que a visão usa, ou None quando a linha não entra na prévia.

    None sai em dois casos bem diferentes, e vale saber a diferença:

    - a cidade não é do Litoral Norte. Nunca foi nossa;
    - a cidade é BERTIOGA e o bairro não está na lista que atendemos. É o caso
      do São João, que apareceu na primeira prévia real de 31/08/2026 e não é
      serviço nosso;
    - a cidade é SÃO SEBASTIÃO e o bairro está em BAIRROS_QUE_NAO_ATENDEMOS.
    """
    chave = achatar(cidade)
    if chave not in CIDADES_LITORAL:
        return None
    if chave == 'BERTIOGA':
        # A cidade que chega como Bertioga é quase sempre Costa Sul mal
        # classificada -- Boiçucanga, Maresias, Juquehy e companhia. O bairro é
        # que separa essas da Bertioga de verdade, que não atendemos.
        return COSTA_SUL if bertioga_e_nossa(endereco) else None
    nome = CIDADES_LITORAL[chave]
    if nome:
        return nome
    return regiao_de_sao_sebastiao(endereco)


def _tipo_da_carga(tipo):
    """O rótulo do tipo se ele é carga de campo; None se não é.

    Devolve o texto ORIGINAL do OFS ("Reparo Corretivo"), não o prefixo: a
    visão que a operação confere no Excel mostra o nome cheio.
    """
    achatado = achatar(tipo)
    for prefixo in TIPOS_DA_CARGA:
        if achatado.startswith(prefixo):
            return str(tipo).strip()
    return None


def _no_balde(recurso):
    return achatar(recurso) in {achatar(b) for b in BALDES_LITORAL}


# ============================== a base =======================================

def _ler(caminho):
    if str(caminho).lower().endswith(('.csv', '.txt')):
        try:
            quadro = pd.read_csv(caminho, dtype=str, keep_default_na=False,
                                 encoding='utf-8-sig')
        except UnicodeDecodeError:
            quadro = pd.read_csv(caminho, dtype=str, keep_default_na=False,
                                 encoding='latin-1')
    else:
        quadro = pd.read_excel(caminho, dtype=str).fillna('')
    quadro.columns = [str(c).strip() for c in quadro.columns]
    return quadro


def _coluna(quadro, *nomes):
    achatadas = {achatar(c): c for c in quadro.columns}
    for nome in nomes:
        achatado = achatar(nome)
        if achatado in achatadas:
            return achatadas[achatado]
    return None


def _mesma_data(valor, alvo):
    """A data da linha é o dia alvo.

    O OFS grava "31/07/26". Aceita também "31/07/2026" e "2026-07-31", porque
    já apareceram os três em bases diferentes e um formato novo não deve
    esvaziar a prévia sem dizer nada.
    """
    texto = str(valor or '').strip()
    if not texto:
        return False
    for formato in ('%d/%m/%y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(texto[:10], formato).date() == alvo
        except ValueError:
            continue
    return False


# ============================== a conta ======================================

def levantar_carga(quando=None, caminho=None, agora=None):
    """Monta a prévia da carga de um dia. Sem argumento, é a de amanhã.

    Devolve um dicionário com TUDO já contado -- capa, lista e totais. Nada
    aqui é deixado "para quem lê somar depois": esta é a mesma regra do dossiê
    do /bot, e pela mesma razão. Contar é de graça aqui e caro lá.
    """
    agora = agora or datetime.now()
    dia = quando or (agora + timedelta(days=1)).date()
    if isinstance(dia, datetime):
        dia = dia.date()
    caminho = caminho or ARQUIVO_PADRAO

    vazio = {
        'data': dia,
        'linhas': [],
        'capa': {},
        'turnos': [],
        'total': 0,
        'no_balde': 0,
        'com_tecnico': 0,
        'por_cidade': {},
        'por_tipo': {},
        'arquivo': caminho,
        'atualizado_em': None,
        'aviso': None,
    }

    if not os.path.exists(caminho):
        vazio['aviso'] = 'a base %s não existe no servidor' % os.path.basename(caminho)
        return vazio

    try:
        quadro = _ler(caminho)
    except Exception:
        logger.exception('Falha ao ler a base da carga em %s', caminho)
        vazio['aviso'] = 'não consegui abrir a base %s' % os.path.basename(caminho)
        return vazio

    vazio['atualizado_em'] = datetime.fromtimestamp(os.path.getmtime(caminho))

    col_data = _coluna(quadro, 'Data')
    col_recurso = _coluna(quadro, 'Recurso')
    col_status = _coluna(quadro, 'Status da Atividade')
    col_cidade = _coluna(quadro, 'Cidade')
    col_endereco = _coluna(quadro, 'Endereço')
    col_nome = _coluna(quadro, 'Nome')
    col_turno = _coluna(quadro, 'Intervalo de Tempo', 'Intervalo')
    # A coluna do tipo é a COM sufixo. A sem sufixo é toda "Normal" na base
    # inteira -- ler a errada devolveria uma prévia vazia sem nenhum erro.
    col_tipo = _coluna(quadro, 'Tipo de Atividade.1')
    col_os = _coluna(quadro, 'Ordem de Serviço')
    col_contrato = _coluna(quadro, 'Número do contrato')

    faltando = [rotulo for rotulo, col in (
        ('Data', col_data), ('Recurso', col_recurso),
        ('Status da Atividade', col_status), ('Cidade', col_cidade),
        ('Endereço', col_endereco), ('Tipo de Atividade.1', col_tipo),
    ) if col is None]
    if faltando:
        vazio['aviso'] = 'a base não tem a(s) coluna(s): ' + ', '.join(faltando)
        return vazio

    fora = {achatar(s) for s in STATUS_FORA_DA_CARGA}
    linhas = []
    # A mesma O.S. pode aparecer duas vezes no mesmo dia quando é repassada de
    # um técnico para outro. Medido em 01/09/2026: zero repetidas naquele dia,
    # mas contar duas vezes inflaria a carga sem ninguém notar, então o seguro
    # fica. Vale só dentro do dia; a mesma O.S. em dias diferentes é outra
    # tentativa, e essas não se cruzam aqui porque a prévia é de um dia só.
    vistas = set()
    for _, linha in quadro.iterrows():
        if not _mesma_data(linha[col_data], dia):
            continue
        if achatar(linha[col_status]) in fora:
            continue
        tipo = _tipo_da_carga(linha[col_tipo])
        if not tipo:
            continue
        cidade = cidade_efetiva(linha[col_cidade], linha[col_endereco])
        if not cidade:
            continue
        identificador = str(linha[col_os]).strip() if col_os else ''
        if identificador:
            if identificador in vistas:
                continue
            vistas.add(identificador)

        turno = str(linha[col_turno]).strip() if col_turno else ''
        recurso = str(linha[col_recurso]).strip()
        linhas.append({
            'nome': str(linha[col_nome]).strip() if col_nome else '',
            'endereco': str(linha[col_endereco]).strip(),
            'cidade': cidade,
            'cidade_ofs': str(linha[col_cidade]).strip(),
            'bairro': bairro_do_endereco(linha[col_endereco]),
            'turno': turno or 'Sem intervalo',
            'tipo': tipo,
            'status': str(linha[col_status]).strip(),
            # `recurso` é o que está na coluna: ou o nome do balde, ou o nome
            # do técnico. `no_balde` diz qual dos dois, para quem lê não
            # precisar conhecer a lista de baldes de cor.
            'recurso': recurso,
            'no_balde': _no_balde(recurso),
            'os': identificador,
            'contrato': str(linha[col_contrato]).strip() if col_contrato else '',
        })

    return _consolidar(vazio, linhas)


def _ordenar_turnos(turnos):
    conhecidos = [t for t in ORDEM_TURNOS if t in turnos]
    resto = sorted(t for t in turnos if t not in ORDEM_TURNOS)
    return conhecidos + resto


def _consolidar(base, linhas):
    """A capa: cidade -> tipo -> turno -> contagem, com os totais já somados."""
    turnos = {l['turno'] for l in linhas}
    # O rótulo do turno vem da base com a grafia da base; a ordem preferida usa
    # a grafia com acento. Casar pelo texto achatado evita duas colunas "Manhã"
    # e "Manha" na mesma capa.
    canonico = {achatar(t): t for t in ORDEM_TURNOS}
    for l in linhas:
        l['turno'] = canonico.get(achatar(l['turno']), l['turno'])
    turnos = _ordenar_turnos({l['turno'] for l in linhas})

    capa = {}
    for l in linhas:
        cidade = capa.setdefault(l['cidade'], {})
        tipo = cidade.setdefault(l['tipo'], {})
        tipo[l['turno']] = tipo.get(l['turno'], 0) + 1

    por_cidade = {}
    por_tipo = {}
    for l in linhas:
        por_cidade[l['cidade']] = por_cidade.get(l['cidade'], 0) + 1
        por_tipo[l['tipo']] = por_tipo.get(l['tipo'], 0) + 1

    base = dict(base)
    base['linhas'] = sorted(
        linhas,
        key=lambda l: (l['cidade'], ORDEM_TURNOS.index(l['turno'])
                       if l['turno'] in ORDEM_TURNOS else 99,
                       l['tipo'], l['nome']))
    base['capa'] = capa
    base['turnos'] = turnos
    base['total'] = len(linhas)
    base['no_balde'] = sum(1 for l in linhas if l['no_balde'])
    base['com_tecnico'] = sum(1 for l in linhas if not l['no_balde'])
    base['por_cidade'] = por_cidade
    base['por_tipo'] = por_tipo
    return base


def total_da_cidade(carga, cidade, turno=None):
    """Quantas atividades a cidade tem, no turno pedido ou no dia todo."""
    soma = 0
    for contagens in (carga['capa'].get(cidade) or {}).values():
        if turno is None:
            soma += sum(contagens.values())
        else:
            soma += contagens.get(turno, 0)
    return soma


def total_do_turno(carga, turno):
    return sum(l['turno'] == turno for l in carga['linhas'])


# ============================== o texto do /bot ==============================

def texto_da_carga(carga, detalhado=False, teto_detalhe=40):
    """A mesma visão em texto, para responder no grupo sem imagem.

    O /carga desenha; o /bot escreve. As duas saem desta mesma conta, de
    propósito: no dia em que divergirem, alguém vai conferir a imagem contra o
    texto e não vai saber qual das duas está certa.
    """
    dia = carga['data'].strftime('%d/%m')
    if carga.get('aviso'):
        return '⚠️ Não consegui montar a prévia de %s: %s.' % (dia, carga['aviso'])
    if not carga['total']:
        return ('📋 *PRÉVIA DA CARGA — %s*\n\nNenhuma atividade no Litoral '
                'Norte para esse dia.' % dia)

    turnos = carga['turnos']
    partes = ['📋 *PRÉVIA DA CARGA — %s (D+1)*' % dia,
              'Litoral Norte, tudo menos cancelado.',
              '']
    partes.append('*%d atividades* no total: %s.' % (
        carga['total'],
        ', '.join('%d %s' % (total_do_turno(carga, t), t.lower())
                  for t in turnos)))
    partes.append('')

    for cidade in sorted(carga['capa']):
        total = total_da_cidade(carga, cidade)
        detalhe_turno = ', '.join(
            '%d %s' % (total_da_cidade(carga, cidade, t), t.lower())
            for t in turnos if total_da_cidade(carga, cidade, t))
        partes.append('*%s* — %d O.S. (%s)' % (cidade, total, detalhe_turno))
        for tipo in sorted(carga['capa'][cidade]):
            contagens = carga['capa'][cidade][tipo]
            soma = sum(contagens.values())
            por_turno = ', '.join('%d %s' % (contagens[t], t.lower())
                                  for t in turnos if contagens.get(t))
            partes.append('  • %s: *%d* (%s)' % (tipo, soma, por_turno))
        partes.append('')

    partes.append('*Total por tipo:* ' + ', '.join(
        '%s %d' % (tipo, quantos)
        for tipo, quantos in sorted(carga['por_tipo'].items())))

    if detalhado:
        partes.append('')
        partes.append('*Lista detalhada:*')
        for l in carga['linhas'][:teto_detalhe]:
            partes.append('• *%s* — %s | %s | %s | %s' % (
                l['nome'], l['endereco'], l['cidade'], l['turno'], l['tipo']))
        se_sobrou = carga['total'] - teto_detalhe
        if se_sobrou > 0:
            partes.append('… e mais %d. A lista inteira sai no */carga*.'
                          % se_sobrou)

    return '\n'.join(partes).strip()


if __name__ == '__main__':      # conferência rápida, fora do bot
    import sys
    caminho = sys.argv[1] if len(sys.argv) > 1 else None
    quando = None
    if len(sys.argv) > 2:
        quando = datetime.strptime(sys.argv[2], '%d/%m/%Y').date()
    resultado = levantar_carga(quando=quando, caminho=caminho)
    print(texto_da_carga(resultado, detalhado=True))
