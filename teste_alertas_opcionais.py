# -*- coding: utf-8 -*-
"""Confere o /alertas: Upgrade e Mudanca de comodo por regiao. Sem rede.

    python teste_alertas_opcionais.py   -> 0 se a regra vale, 1 se algo fugiu

O QUE ISTO PROTEGE
------------------
Duas coisas que nao levantam excecao nenhuma quando quebram:

1. O CORTE POR HORA. So alerta chamado aberto DEPOIS de alguem ligar. Sem ele,
   ligar despeja no grupo tudo o que ja estava aberto -- eram 32 upgrades e 18
   mudancas de comodo no dia em que isto foi escrito.

2. O ESTADO EM DISCO. Se a leitura falhar e devolver "ligado" por engano, o
   grupo comeca a receber alerta que ninguem pediu; se o arquivo sumir e
   ninguem notar, o alerta que alguem pediu para na surdina. O padrao seguro e
   DESLIGADO, e e o que esta escrito aqui.
"""
import os
import sys
import tempfile

import alertas_opcionais as ao

erros = []


def confere(descricao, obtido, esperado):
    ok = obtido == esperado
    if not ok:
        erros.append('%s: %r, esperava %r' % (descricao, obtido, esperado))
    print('%-56s %-14s %s' % (descricao, obtido, 'ok' if ok else 'ERRO'))


# ---------------------------------------------------- o que o operador digita
print('--- o que o operador digita ---')
ESCRITAS = (
    ('1', ao.LITORAL), ('litoral', ao.LITORAL), ('Litoral Norte', ao.LITORAL),
    ('2', ao.RJ), ('rj', ao.RJ), ('RJ', ao.RJ), ('Sul RJ', ao.RJ),
    ('3', ao.AMBAS), ('ambas', ao.AMBAS), ('as duas regiões', ao.AMBAS),
    ('0', ao.NENHUMA), ('desligar', ao.NENHUMA), ('off', ao.NENHUMA),
    # O que NAO e escolha tem de devolver None: no meio da espera, o grupo
    # continua conversando, e cada frase dessas nao pode virar erro.
    ('bom dia', None), ('', None), ('5', None), ('litoralzinho', None),
)
for texto, esperado in ESCRITAS:
    confere('interpretar(%r)' % texto, ao.interpretar(texto), esperado)


# ------------------------------------------------------------- estado em disco
print()
print('--- o estado em disco ---')
pasta = tempfile.mkdtemp()
CAMINHO = os.path.join(pasta, 'alertas_opcionais.json')

confere('sem arquivo, fica desligado',
        ao.carregar(CAMINHO)['regiao'], ao.NENHUMA)

with open(CAMINHO, 'w', encoding='utf-8') as arq:
    arq.write('{isto nao e json')
confere('arquivo corrompido, fica desligado',
        ao.carregar(CAMINHO)['regiao'], ao.NENHUMA)

ao.gravar(ao.RJ, por='OPERADOR', agora=1000.0, caminho=CAMINHO)
lido = ao.carregar(CAMINHO)
confere('a escolha sobrevive ao arquivo', lido['regiao'], ao.RJ)
confere('o carimbo da hora sobrevive', lido['ativado_em'], 1000.0)
confere('quem escolheu sobrevive', lido['por'], 'OPERADOR')

# Regiao que nao existe nao pode ser gravada: seria um estado que a decisao
# abaixo nao sabe interpretar, e ela devolveria False para sempre.
try:
    ao.gravar('bahia', caminho=CAMINHO)
    erros.append('gravar aceitou uma região que não existe')
    print('região inexistente recusada                              ERRO')
except ValueError:
    print('região inexistente recusada                              ok')


# ------------------------------------------------------------- a decisao
print()
print('--- quem vira alerta ---')
LITORAL_SP = ['CGT', 'BASE', 'SST', 'SSTBO', 'IBL', 'BERT', 'BERTN']
RJ = ['RSD', 'VRD', 'BMA']

LIGADO_EM = 1000.0
DEPOIS = 2000.0
ANTES = 500.0


def decide(regiao, unidade, aberto_em, codigo='UP02'):
    estado = {'regiao': regiao, 'ativado_em': LIGADO_EM, 'por': None}
    return ao.deve_alertar(codigo, unidade, aberto_em, estado, LITORAL_SP, RJ)


CASOS = (
    # (região ligada, unidade, aberto em, esperado, descrição)
    (ao.NENHUMA, 'CGT', DEPOIS, False, 'desligado não alerta nada'),
    (ao.LITORAL, 'CGT', DEPOIS, True, 'litoral ligado, unidade do litoral'),
    (ao.LITORAL, 'VRD', DEPOIS, False, 'litoral ligado, unidade do RJ'),
    (ao.RJ, 'VRD', DEPOIS, True, 'RJ ligado, unidade do RJ'),
    (ao.RJ, 'CGT', DEPOIS, False, 'RJ ligado, unidade do litoral'),
    (ao.AMBAS, 'CGT', DEPOIS, True, 'as duas, unidade do litoral'),
    (ao.AMBAS, 'VRD', DEPOIS, True, 'as duas, unidade do RJ'),
    (ao.AMBAS, 'UTB', DEPOIS, False, 'as duas, unidade de fora'),
    # O corte por hora, que e a razao de o carimbo existir.
    (ao.AMBAS, 'CGT', ANTES, False, 'chamado aberto ANTES de ligar'),
    (ao.AMBAS, 'CGT', LIGADO_EM, True, 'aberto no instante em que ligou'),
    (ao.AMBAS, 'CGT', None, False, 'chamado sem data de abertura'),
)
for regiao, unidade, aberto, esperado, descricao in CASOS:
    confere(descricao, decide(regiao, unidade, aberto), esperado)

# Os codigos: so os dois. Uma ativacao caindo aqui viraria alerta duplicado,
# porque ela ja e avisada pelo caminho do CAPEX.
print()
for codigo, esperado in (('UP02', True), ('ES15', True),
                         ('ES02', False), ('ES05', False), ('ES04', False)):
    confere('a fila %s entra no /alertas' % codigo,
            decide(ao.AMBAS, 'CGT', DEPOIS, codigo=codigo), esperado)

# Sem carimbo (estado escrito à mão, sem `ativado_em`) ninguém alerta: não dá
# para saber o que é novo, e supor que tudo é novo é a enxurrada.
confere('estado sem carimbo de hora',
        ao.deve_alertar('UP02', 'CGT', DEPOIS,
                        {'regiao': ao.AMBAS, 'ativado_em': None},
                        LITORAL_SP, RJ),
        False)

os.remove(CAMINHO)
os.rmdir(pasta)

print()
if erros:
    print('ERROS:')
    for e in erros:
        print(' -', e)
else:
    print('CONFERE: as escolhas, o estado em disco e o corte por hora')
sys.exit(1 if erros else 0)
