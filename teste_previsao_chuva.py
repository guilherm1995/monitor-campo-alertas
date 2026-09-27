# -*- coding: utf-8 -*-
"""Confere o aviso de chuva da prévia da carga. Sem rede de verdade.

    python teste_previsao_chuva.py    -> 0 se todos batem, 1 se algum foge

O QUE ISTO PROTEGE
-------------------
Três jeitos de o aviso sair errado sem ninguém perceber na hora: (1) turno
sem chuva relevante entrando no aviso mesmo assim (limiar comparado errado),
(2) chuva de manhã contando pro turno da tarde ou vice-versa (janela de
horário trocada), e (3) a resposta de mais de uma cidade do Open-Meteo
(uma lista) sendo lida na ordem errada e a chuva de uma rota grudando na
cidade errada.
"""
import sys
from datetime import date

import previsao_chuva as pc

erros = []


def confere(nome, obtido, esperado):
    ok = obtido == esperado
    print('%-55s %s' % (nome, 'ok' if ok else 'ERRO (%r != %r)' % (obtido, esperado)))
    if not ok:
        erros.append(nome)


# ---------------------------------------------------------------------------
# 1. risco_por_cidade_turno: limiar e janela do turno
# ---------------------------------------------------------------------------

CARGA_FAKE = {
    'data': date(2026, 9, 12),
    # cidade -> tipo -> turno -> contagem, igual a carga_litoral._consolidar
    'capa': {
        'CARAGUATATUBA': {'Reparo Corretivo': {'Manhã': 3, 'Tarde': 2}},
        'ILHABELA': {'Ativação': {'Tarde': 5}},
        'TOPO': {'Reparo Corretivo': {'Manhã': 1}},
    },
}

PREVISAO_FAKE = {
    # Caraguatatuba: manhã seca, tarde molhada -- separa os dois turnos certo.
    'CARAGUATATUBA': {'08': 10, '09': 15, '10': 20, '11': 25,
                       '14': 70, '15': 85, '16': 90, '17': 60},
    # Ilhabela: chuva só de manhã (fora da janela da Tarde, que é o turno
    # que ela tem carga) -- não deve aparecer no aviso.
    'ILHABELA': {'08': 90, '09': 95, '14': 20, '15': 10, '16': 5, '17': 15},
    # TOPO: sem entrada na previsão (falha parcial da consulta) -- tem que
    # ser pulada, não contar como "sem risco".
}

riscos = pc.risco_por_cidade_turno(CARGA_FAKE, PREVISAO_FAKE, limiar=60)

achados = {(r['cidade'], r['turno']) for r in riscos}
confere('Caraguatatuba/Tarde entra (pico 90% >= 60%)',
        ('CARAGUATATUBA', 'Tarde') in achados, True)
confere('Caraguatatuba/Manhã fica de fora (pico 25% < 60%)',
        ('CARAGUATATUBA', 'Manhã') in achados, False)
confere('Ilhabela/Tarde fica de fora (chuva é de manhã, ela só tem carga à tarde)',
        ('ILHABELA', 'Tarde') in achados, False)
confere('TOPO nem aparece (sem previsão -- não é "sem risco", é dado faltando)',
        any(r['cidade'] == 'TOPO' for r in riscos), False)
confere('total de riscos encontrados', len(riscos), 1)

if riscos:
    confere('pico registrado é o máximo da janela (90, não a média)',
            riscos[0]['probabilidade'], 90)
    confere('contagem de O.S. veio de total_da_cidade (2, não o total do dia)',
            riscos[0]['os'], 2)

# limiar mais alto: mesmo o pico de 90% não é páreo pra 95%.
riscos_severos = pc.risco_por_cidade_turno(CARGA_FAKE, PREVISAO_FAKE, limiar=95)
confere('limiar 95%: ninguém passa', len(riscos_severos), 0)

# sem previsão (consulta falhou por inteiro) -> lista vazia, não exceção.
confere('previsão None -> nenhum risco, sem quebrar',
        pc.risco_por_cidade_turno(CARGA_FAKE, None), [])


# ---------------------------------------------------------------------------
# 2. texto_risco_chuva: vazio quando não há risco, ordenado do pior pro melhor
# ---------------------------------------------------------------------------

confere('sem risco -> texto vazio (prévia sai igual a sempre saiu)',
        pc.texto_risco_chuva(CARGA_FAKE, []), '')

dois_riscos = [
    {'cidade': 'ILHABELA', 'turno': 'Tarde', 'probabilidade': 70, 'os': 5},
    {'cidade': 'CARAGUATATUBA', 'turno': 'Tarde', 'probabilidade': 90, 'os': 2},
]
texto = pc.texto_risco_chuva(CARGA_FAKE, dois_riscos)
pos_carag = texto.find('Caraguatatuba')
pos_ilha = texto.find('Ilhabela')
confere('o mais provável (90%) vem antes do menos provável (70%) no texto',
        pos_carag != -1 and pos_ilha != -1 and pos_carag < pos_ilha, True)
confere('a data da carga aparece no texto', '12/09' in texto, True)


# ---------------------------------------------------------------------------
# 3. buscar_chuva_por_cidade: a resposta de VÁRIAS cidades é uma LISTA, na
#    mesma ordem do pedido -- é fácil ler a chuva errada pra cidade errada
#    aqui. Sem rede: troca requests.get por uma função fake.
# ---------------------------------------------------------------------------

class _RespostaFake:
    def __init__(self, corpo):
        self._corpo = corpo

    def raise_for_status(self):
        pass

    def json(self):
        return self._corpo


def _get_fake(url, timeout=None):
    # Duas cidades pedidas, nesta ordem -- a resposta tem que devolver a
    # chuva de cada uma na mesma posição, como o Open-Meteo faz de verdade.
    return _RespostaFake([
        {'hourly': {'time': ['2026-09-12T00:00', '2026-09-12T14:00'],
                    'precipitation_probability': [5, 88]}},
        {'hourly': {'time': ['2026-09-12T00:00', '2026-09-12T14:00'],
                    'precipitation_probability': [40, 12]}},
    ])


pc.requests.get, _original_get = _get_fake, pc.requests.get
try:
    duas_cidades = (('CIDADE_A', -1, -1), ('CIDADE_B', -2, -2))
    resultado = pc.buscar_chuva_por_cidade(date(2026, 9, 12), cidades=duas_cidades)
finally:
    pc.requests.get = _original_get

confere('CIDADE_A pegou o primeiro bloco da lista (88% às 14h, não 12%)',
        (resultado or {}).get('CIDADE_A', {}).get('14'), 88)
confere('CIDADE_B pegou o segundo bloco da lista (12% às 14h, não 88%)',
        (resultado or {}).get('CIDADE_B', {}).get('14'), 12)


# ---------------------------------------------------------------------------
# 4. as duas regionais: cada uma consultada nos SEUS pontos
# ---------------------------------------------------------------------------

confere('litoral e RJ têm tabela de coordenadas',
        sorted(pc.COORDENADAS_POR_REGIAO), ['litoral', 'rj'])

# Toda cidade que a prévia do Rio sabe nomear precisa ter coordenada -- senão
# ela aparece na capa e some do aviso de chuva, calada.
try:
    import garantias_lista
    nomes_rj = {garantias_lista.CIDADES[s] for s in garantias_lista.RJ
                if s in garantias_lista.CIDADES}
    com_ponto = {nome for nome, _, _ in pc.COORDENADAS_RJ}
    confere('nenhuma cidade do RJ ficou sem coordenada',
            sorted(nomes_rj - com_ponto), [])
except Exception as erro_import:
    print('(não deu para conferir contra o garantias_lista: %s)' % erro_import)

# As coordenadas do Rio têm de cair no Rio, não em outro estado nem no mar.
fora = [nome for nome, lat, lon in pc.COORDENADAS_RJ
        if not (-23.5 <= lat <= -20.7 and -44.9 <= lon <= -40.9)]
confere('toda coordenada do RJ cai dentro do estado', fora, [])

# E a regional escolhe a tabela: uma carga do Rio não pode ser consultada nos
# pontos do litoral -- ali nenhuma cidade casaria e o aviso sairia vazio,
# parecendo "não vai chover".
# O aviso está DESLIGADO por padrão desde 23/09/2026 (CHUVA_ATIVA). Os casos
# abaixo conferem a lógica de quando ele estiver ligado, então ligam na mão --
# e o caso do interruptor em si vem logo depois.
pedidos = []
_original_buscar = pc.buscar_chuva_por_cidade
_original_ativa = pc.CHUVA_ATIVA
try:
    pc.CHUVA_ATIVA = True
    pc.buscar_chuva_por_cidade = lambda dia, cidades=None: (
        pedidos.append(tuple(n for n, _, _ in (cidades or ()))) or {})
    pc.linha_de_risco_de_chuva({'data': date(2026, 9, 12), 'capa': {},
                                'regiao': 'rj'})
    pc.linha_de_risco_de_chuva({'data': date(2026, 9, 12), 'capa': {},
                                'regiao': 'litoral'})
    pc.linha_de_risco_de_chuva({'data': date(2026, 9, 12), 'capa': {}})
finally:
    pc.buscar_chuva_por_cidade = _original_buscar
    pc.CHUVA_ATIVA = _original_ativa

confere("regiao 'rj' consulta Volta Redonda", 'Volta Redonda' in pedidos[0], True)
confere("regiao 'rj' NÃO consulta Ilhabela", 'ILHABELA' in pedidos[0], False)
confere("regiao 'litoral' consulta Ilhabela", 'ILHABELA' in pedidos[1], True)
confere('sem regiao vale o litoral, como sempre foi', pedidos[2], pedidos[1])

# Regional sem tabela não inventa: devolve vazio e avisa no log.
_ativa = pc.CHUVA_ATIVA
try:
    pc.CHUVA_ATIVA = True
    confere('regional desconhecida não sai com aviso de chuva',
            pc.linha_de_risco_de_chuva({'data': date(2026, 9, 12), 'capa': {},
                                        'regiao': 'marte'}), '')
finally:
    pc.CHUVA_ATIVA = _ativa

# ---------------------------------------------------------------------------
# 4b. o interruptor: desligado, nem consulta o Open-Meteo
# ---------------------------------------------------------------------------

confere('o padrão de hoje é DESLIGADO', pc.CHUVA_ATIVA, False)

bateu_na_rede = []
_original_buscar = pc.buscar_chuva_por_cidade
try:
    pc.buscar_chuva_por_cidade = lambda *a, **k: bateu_na_rede.append(1)
    desligado = pc.linha_de_risco_de_chuva({'data': date(2026, 9, 12),
                                            'capa': {'CARAGUATATUBA': {}},
                                            'regiao': 'litoral'})
finally:
    pc.buscar_chuva_por_cidade = _original_buscar

confere('desligado -> legenda sai sem a linha de chuva', desligado, '')
confere('desligado -> nem chega a consultar o Open-Meteo', bateu_na_rede, [])

# ---------------------------------------------------------------------------
# 5. o teto de linhas: o Rio pode ter 16 cidades num dia de frente fria
# ---------------------------------------------------------------------------

muitos = [{'cidade': 'Cidade %02d' % i, 'turno': 'Manhã',
           'probabilidade': 100 - i, 'os': 2} for i in range(14)]
texto = pc.texto_risco_chuva({'data': date(2026, 9, 12)}, muitos)
linhas_texto = texto.split('\n')
confere('o aviso lista no máximo o teto + cabeçalho + resumo',
        len(linhas_texto), pc.TETO_LINHAS_CHUVA + 2)
confere('o pior trecho está na primeira linha da lista',
        linhas_texto[1].startswith('Cidade 00'), True)
confere('o que sobrou vira uma linha com a conta',
        linhas_texto[-1], '_e mais 4 trecho(s) acima de 87%, somando 8 O.S._')

# Com poucos trechos, nada muda em relação ao que o litoral já via.
poucos = muitos[:3]
confere('lista curta sai inteira, sem linha de resumo',
        len(pc.texto_risco_chuva({'data': date(2026, 9, 12)}, poucos).split('\n')), 4)


print()
if erros:
    print('ERROS:')
    for e in erros:
        print(' -', e)
else:
    print('CONFERE: limiar, janela do turno, ordenação do texto, leitura '
          'da resposta de várias cidades, e a tabela de cada regional.')
sys.exit(1 if erros else 0)
