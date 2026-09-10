# -*- coding: utf-8 -*-
"""Confere que o REPARO DE PME conta como garantia. Sem rede, sem WhatsApp.

    python teste_garantia_pme.py    -> 0 se a regra vale, 1 se alguem a perdeu

O QUE ISTO PROTEGE
------------------
O CAMPO tem duas filas de reparo: ES05 ("REPARO") e REPPME ("REPARO - PME"). Ate
04/09/2026 o laco da varredura tratava so a primeira, e o reparo de PME passava
batido -- sem alerta de garantia e fora da lista de hora em hora. Nada quebrava:
o chamado simplesmente nao existia para o bot.

Erro assim nao levanta excecao e nao aparece no log. Ele so aparece semanas
depois, quando a operacao percebe que uma garantia nunca foi avisada. Por isso
a regra esta escrita aqui.
"""
import os
import sys
from datetime import datetime

# Nao deixa o import mexer em base nenhuma nem sair para a rede.
os.environ.setdefault('BASES_ATUALIZAR', '0')
os.environ.setdefault('CARGA_AUTOMATICA_ATIVA', '0')

import bot_campo_monitoramento as bot          # noqa: E402

erros = []


def confere(descricao, obtido, esperado):
    ok = obtido == esperado
    if not ok:
        erros.append('%s: %r, esperava %r' % (descricao, obtido, esperado))
    print('%-52s %-14s %s' % (descricao, obtido, 'ok' if ok else 'ERRO'))


# ---- as duas filas de reparo do CAMPO
print('filas de reparo:', bot.CODIGOS_REPARO_CAMPO)
for fila in ('ES05', 'REPPME'):
    confere('a fila %s e avaliada como reparo' % fila,
            fila in bot.CODIGOS_REPARO_CAMPO, True)

# Um codigo que NAO e reparo nao pode entrar na lista: se entrasse, ativacao e
# mudanca de endereco virariam "reparo" e cada uma delas seria conferida contra
# a garantia da anterior.
for fila in ('ES02', 'ES04', 'UP02', 'ES15', 'ES06'):
    confere('a fila %s fica de fora' % fila,
            fila in bot.CODIGOS_REPARO_CAMPO, False)


# ---- a regra do prazo, com um historico de mentira no lugar da base
#
# O tipo do servico ANTERIOR vem da Base OFS, que nao escreve o sufixo PME:
# la o tipo e "Ativacao", "Reparo Corretivo" ou "Mudanca de Endereco". Por isso
# a conferencia e por trecho do nome, e por isso o lado da base nao precisou
# mudar quando o REPPME entrou.
class _DataFalsa(object):
    """So o que verificar_garantia_reparo usa de uma data do pandas."""

    def __init__(self, ano, mes, dia):
        self._d = datetime(ano, mes, dia)

    def date(self):
        return self._d.date()


HISTORICO = {
    # o caso real que revelou a falha: ativacao em 03/09, reparo em 04/09
    '6911438': [(_DataFalsa(2026, 9, 3), 'Ativação', 'JOSE DA SILVA')],
    # ativacao velha demais: fora dos 15 dias
    '111': [(_DataFalsa(2026, 8, 1), 'Ativação', 'JOSE DA SILVA')],
    # reparo anterior: prazo maior, 30 dias
    '222': [(_DataFalsa(2026, 8, 20), 'Reparo Corretivo', 'JOSE DA SILVA')],
}

bot._CACHE_BASE_OFS['indice'] = HISTORICO
bot.carregar_base_ofs = lambda: (HISTORICO, {})   # so precisa nao ser None

QUANDO = datetime(2026, 9, 4, 9, 0)
CASOS = (
    ('6911438', True, 'ativação de ontem'),
    ('111', False, 'ativação de 34 dias atrás'),
    ('222', True, 'reparo de 15 dias atrás'),
    ('999', False, 'contrato sem histórico'),
)
print()
for contrato, esperado, descricao in CASOS:
    eh_garantia = bot.verificar_garantia_reparo(contrato, QUANDO)[0]
    confere('%s (%s)' % (contrato, descricao), eh_garantia, esperado)

print()
if erros:
    print('ERROS:')
    for e in erros:
        print(' -', e)
else:
    print('CONFERE: reparo de PME vale garantia, e o prazo continua valendo')
sys.exit(1 if erros else 0)
