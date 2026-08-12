# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
import os

datas = []
binaries = []
hiddenimports = []

# Coleta todos os arquivos do Playwright automaticamente
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# usa SPECPATH em vez de __file__
base_dir = os.path.dirname(os.path.abspath(SPECPATH))

# Pasta chromium (portátil)
chromium_src = os.path.join(base_dir, 'chromium')
if os.path.exists(chromium_src):
    datas.append(('chromium', 'chromium'))

# Pasta chromium headless shell (cópia que criamos)
chromium_headless_src = os.path.join(base_dir, 'chromium_headless_shell-1228')
if os.path.exists(chromium_headless_src):
    datas.append(('chromium_headless_shell-1228', 'chromium_headless_shell-1228'))

# Adiciona os arquivos .py do projeto
# ============ CORRIGIDO: faltavam backlog_reparo.py (importado por
# backlog_envio.py), termometro_render.py e improdutivas.py (os dois
# importados direto por bot_campo_monitoramento.py) -- sem eles empacotados,
# o .exe corre risco de dar ImportError em runtime pros comandos de
# Upgrade/Mudança de Cômodo, termômetro e improdutivas. ============
# backlog_ofs.py entrou depois: cruza o backlog com a agenda do OFS GERAL para
# o "Enviado D0". É importado por backlog_envio.py -- sem ele aqui, o .exe dá
# ImportError na primeira geração de backlog.
for arquivo in ['backlog_render.py', 'backlog_capex.py', 'backlog_conveniencia.py',
                'backlog_envio.py', 'backlog_reparo.py', 'backlog_ofs.py',
                'termometro_render.py', 'improdutivas.py', 'amostra_chamados.py']:
    caminho = os.path.join(base_dir, arquivo)
    if os.path.exists(caminho):
        datas.append((arquivo, '.'))

# NOTA: assets/, dados/ (arquivos estáticos), openconnect/, o serviço
# WhatsApp (index.js/config.json/package.json/node_modules) e
# vpn_sempre_ativa.exe (compilado à parte, ver build_vpn.txt/README) NÃO
# são empacotados aqui -- testamos e confirmamos que o PyInstaller nesse
# modo onefile não embute essas pastas/arquivos por mais que apareçam em
# `datas` (mesma razão pela qual "chromium" acima também nunca funcionou
# de verdade). O padrão real desse projeto é: tudo isso fica ao LADO do
# .exe (o próprio código já assume isso via os.path.dirname(sys.executable),
# igual o chromium/). Depois de compilar, rode `python montar_dist.py` pra
# copiar essas pastas pra dist/ automaticamente.

a = Analysis(
    ['bot_campo_monitoramento.py'],
    pathex=[base_dir],  # CORRIGIDO: adiciona base_dir ao path
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='bot_campo_monitoramento',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # True para ver erros durante o teste
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)