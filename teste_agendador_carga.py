# -*- coding: utf-8 -*-
"""Confere os horarios da previa automatica da carga. Sem rede, sem WhatsApp.

    python teste_agendador_carga.py    -> 0 se todos batem, 1 se algum foge

O QUE ISTO PROTEGE
------------------
Erro de agendamento nao levanta excecao: ele so faz a previa sair na hora
errada, ou nao sair. Quem for mexer nos horarios mexe numa variavel de ambiente
e reinicia -- e so descobre no dia seguinte se errou.

O caso que mais engana e a virada do dia: as 23:59 o proximo tem de ser o
PRIMEIRO horario de amanha, e as 00:01 tem de ser o primeiro de HOJE, sem pular
um dia.
"""
import os
import sys
from datetime import datetime

os.environ.setdefault('CARGA_AUTOMATICA_HORARIOS', '15:40,16:40,18:30')

import bot_campo_monitoramento as bot          # noqa: E402

erros = []


def confere(agora, esperado, horarios=None):
    obtido = bot._proximo_horario_da_carga(agora, horarios)
    texto = obtido.strftime('%d/%m %H:%M') if obtido else 'nenhum'
    if texto != esperado:
        erros.append('%s -> %s, esperava %s'
                     % (agora.strftime('%d/%m %H:%M'), texto, esperado))
    print('%s -> %-12s %s' % (agora.strftime('%d/%m %H:%M'), texto,
                              'ok' if texto == esperado else 'ERRO'))


print('horários lidos:', bot.CARGA_AUTOMATICA_HORARIOS)
print()

# Ao longo do dia, um horário de cada vez.
confere(datetime(2026, 9, 1, 9, 0), '01/09 15:40')
confere(datetime(2026, 9, 1, 15, 39), '01/09 15:40')
confere(datetime(2026, 9, 1, 15, 40), '01/09 16:40')   # o de agora não repete
confere(datetime(2026, 9, 1, 16, 41), '01/09 18:30')
confere(datetime(2026, 9, 1, 18, 30), '02/09 15:40')   # acabaram os de hoje

# A virada do dia, que é onde é fácil pular 24 horas sem querer.
confere(datetime(2026, 9, 1, 23, 59), '02/09 15:40')
confere(datetime(2026, 9, 2, 0, 1), '02/09 15:40')

# Um horário só continua funcionando, e nenhum não estoura.
confere(datetime(2026, 9, 1, 9, 0), '01/09 20:00', horarios=[(20, 0)])
confere(datetime(2026, 9, 1, 9, 0), 'nenhum', horarios=[])

print()
print('lista torta      :', bot._ler_horarios('15:40, , 99:99, 16:40, abc, 18:30, 15:40'))
print('lista vazia      :', bot._ler_horarios(''))
print('fora de ordem    :', bot._ler_horarios('18:30,15:40'))

# Uma vírgula a mais não pode calar a prévia inteira: o que dá para ler, lê.
if bot._ler_horarios('15:40, , 99:99, 16:40, abc, 18:30, 15:40') != [(15, 40), (16, 40), (18, 30)]:
    erros.append('a lista torta não foi limpa como esperado')
if bot._ler_horarios('18:30,15:40') != [(15, 40), (18, 30)]:
    erros.append('a lista fora de ordem não foi ordenada')

# O rodapé de instrução sai só no primeiro envio do dia.
primeiros = [(h, m) == bot.CARGA_AUTOMATICA_HORARIOS[0]
             for h, m in bot.CARGA_AUTOMATICA_HORARIOS]
print('rodapé por envio :', primeiros)
if primeiros != [True] + [False] * (len(bot.CARGA_AUTOMATICA_HORARIOS) - 1):
    erros.append('o rodapé não ficou só no primeiro envio')

print()
if erros:
    print('ERROS:')
    for e in erros:
        print(' -', e)
else:
    print('CONFERE: os três horários, a virada do dia, e o rodapé só no primeiro')
sys.exit(1 if erros else 0)
