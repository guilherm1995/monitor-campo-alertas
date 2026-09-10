# -*- coding: utf-8 -*-
"""Confere que o aviso de sessao vencida chega no privado -- e so uma vez.

Roda sem rede e sem WhatsApp: `python teste_aviso_sessao.py` sai com 0 quando
o privado acompanha o grupo, e com 1 quando ele viraria lembrete de hora em
hora -- que e o defeito que este teste existe para pegar.
"""
import os
import sys
import tempfile
import importlib
import time

PASTA = tempfile.mkdtemp(prefix='ensaio_aviso_')
os.environ['BOT_PV_CONHECIDOS_ARQUIVO'] = os.path.join(PASTA, 'pv.json')

import ofs_extracao as ofs
import painel_resultados as painel

ofs.lembrar_pv('34132138688546@lid')
ofs.lembrar_pv('167456932937786@lid')

enviados = []
painel.enviar_texto = lambda msg, destino=None: enviados.append(
    (destino or 'GRUPO', str(msg)[:40]))


def rodar_ciclo():
    """Repete o que o ramo de sessao vencida faz, sem baixar nada."""
    saiu = painel._avisar_falha('grupo: sessao vencida')
    if saiu:
        painel._avisar_no_privado(painel.AVISO_PV_CAIU)
    return saiu


erros = []

# 1a hora: sai no grupo e nos dois privados
del enviados[:]
rodar_ciclo()
destinos = [d for d, _ in enviados]
if destinos != ['GRUPO', '167456932937786@lid', '34132138688546@lid']:
    erros.append('1a hora: %s' % destinos)
print('1a vez        :', destinos)

# 2a hora, sem esperar: nao sai nada, nem no grupo nem no privado
del enviados[:]
painel._estado_aviso['falhando'] = True
rodar_ciclo()
print('logo depois   :', [d for d, _ in enviados] or 'nada')
if enviados:
    erros.append('repetiu cedo demais: %s' % [d for d, _ in enviados])

# passada a janela: sai de novo, nos dois lugares
del enviados[:]
painel._estado_aviso['ultimo_aviso'] = time.time() - painel.INTERVALO_AVISO_SEG - 1
rodar_ciclo()
destinos = [d for d, _ in enviados]
print('passada a hora:', destinos)
if 'GRUPO' not in destinos or '34132138688546@lid' not in destinos:
    erros.append('nao repetiu depois da janela: %s' % destinos)

# sem privado conhecido: o grupo ainda recebe
del enviados[:]
os.environ['BOT_PV_CONHECIDOS_ARQUIVO'] = os.path.join(PASTA, 'nao_existe.json')
importlib.reload(ofs)
painel.ofs_extracao = ofs
painel._estado_aviso['ultimo_aviso'] = time.time() - painel.INTERVALO_AVISO_SEG - 1
rodar_ciclo()
destinos = [d for d, _ in enviados]
print('sem privado   :', destinos)
if destinos != ['GRUPO']:
    erros.append('sem privado conhecido: %s' % destinos)

# ---- a volta: agradece so a quem soube da queda
del enviados[:]
painel._estado_aviso['falhando'] = True
painel._avisar_volta()
destinos = [d for d, _ in enviados]
print('voltou        :', destinos)
if destinos != ['GRUPO']:
    erros.append('a volta sem privado conhecido: %s' % destinos)

# com privado conhecido, a volta chega la tambem
del enviados[:]
os.environ['BOT_PV_CONHECIDOS_ARQUIVO'] = os.path.join(PASTA, 'pv.json')
importlib.reload(ofs)
painel.ofs_extracao = ofs
painel._estado_aviso['falhando'] = True
painel._avisar_volta()
destinos = [d for d, _ in enviados]
textos = [t for _, t in enviados]
print('voltou com PV :', destinos)
if 'GRUPO' not in destinos or '34132138688546@lid' not in destinos:
    erros.append('a volta nao chegou no privado: %s' % destinos)
if not any('obrigadu' in t for t in textos):
    erros.append('a volta nao usou a mensagem da operacao: %s' % textos)

# sem ter caido antes, nada e enviado
del enviados[:]
painel._estado_aviso['falhando'] = False
painel._avisar_volta()
print('voltou sem cair:', [d for d, _ in enviados] or 'nada')
if enviados:
    erros.append('agradeceu sem ter caido: %s' % [d for d, _ in enviados])

print()
if erros:
    print('ERROS:')
    for e in erros:
        print(' -', e)
else:
    print('CONFERE: o privado sai junto do grupo, e so quando o grupo sai')
sys.exit(1 if erros else 0)
