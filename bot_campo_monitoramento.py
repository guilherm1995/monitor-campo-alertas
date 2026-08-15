import sys

# ============ ANTES DE QUALQUER OUTRA COISA ============
# Quando chamado com --render-backlog, este executável é só o renderizador de
# PNG de um render pedido pelo processo principal (ver backlog_render.py).
# O desvio precisa vir aqui no topo, antes dos imports pesados e de todo o
# init de módulo: senão o filho abriria o monitor_campo.log e ainda contaria
# erros dele dentro de estatisticas_status.json, sujando os números do site.
if "--render-backlog" in sys.argv:
    from backlog_render import executar_render_cli
    sys.exit(executar_render_cli(sys.argv))

import os
import json
import math
import time
import random
import logging
from logging.handlers import RotatingFileHandler
import re
import unicodedata
import tkinter as tk
import tkinter.font as tkfont
import html
import threading
import queue
import subprocess
import ctypes
import io
from collections import deque
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import requests

# ============ ANTES DOS IMPORTS DO PROJETO ============
# Este arquivo e os módulos abaixo resolvem dados/, logs/, relatorios/, assets/
# e perfil_campo_logistica/ a partir de os.getcwd() -- são 11 pontos espalhados
# por 6 arquivos. Por atalho nunca deu problema: o Explorer já entra na pasta do
# executável antes de rodar. Pelo Agendador de Tarefas, o diretório é
# C:\Windows\System32, e em 06/08/2026 o bot subiu com 0 OS notificadas, sem a
# base OFS, sem node_modules e criando um perfil de navegador vazio lá dentro.
#
# Fixar o diretório aqui conserta os 11 de uma vez e vale para qualquer forma de
# iniciar. Tem que vir ANTES dos imports do projeto: amostra_chamados,
# backlog_envio e backlog_conveniencia montam caminhos já no import -- depois
# deles seria tarde.
#
# A tarefa do Agendador também define a pasta de trabalho ("Iniciar em"), o que
# é redundante com este os.chdir de propósito: o serviço Node (index.js) é
# processo filho e herda o cwd, então convém já nascer certo.
_RAIZ = os.path.dirname(
    sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
)
if os.path.isdir(_RAIZ):
    os.chdir(_RAIZ)

from amostra_chamados import salvar_amostra_chamados, salvar_diagnostico_plano

# ============ MODIFICADO: importar também a nova função ============
from backlog_envio import gerar_e_enviar_backlog, gerar_e_enviar_backlog_tipo, configurar_telegram
import garantias_envio
import garantias_lista
import improdutivas

try:
    import win32gui, win32con
    _WIN32GUI_DISPONIVEL = True
except ImportError:
    _WIN32GUI_DISPONIVEL = False

try:
    import psutil
    _PSUTIL_DISPONIVEL = True
except ImportError:
    _PSUTIL_DISPONIVEL = False

NOMES_PROCESSO_FORTICLIENT = ("forticlient.exe", "fortitray.exe", "fortifw.exe")

try:
    import winsound
    _WINSOUND_DISPONIVEL = True
except ImportError:
    _WINSOUND_DISPONIVEL = False

try:
    # ImageDraw entra pelo Painel de TV: os cards são desenhados em resolução
    # maior e reduzidos, que é como se consegue canto arredondado suavizado
    # (o Canvas do Tk não faz antialiasing em forma nenhuma).
    from PIL import Image, ImageDraw, ImageTk
    _PIL_DISPONIVEL = True
except ImportError:
    _PIL_DISPONIVEL = False

# --- Corrige escala de DPI do Windows (evita janelas com tamanho errado / conteúdo cortado) ---
if sys.platform.startswith('win'):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# --- Lock de instância única (evita duas execuções simultâneas) ---
try:
    import msvcrt
    _PLATAFORMA_LOCK = "windows"
except ImportError:
    import fcntl
    _PLATAFORMA_LOCK = "unix"

try:
    import pandas as pd
    PANDAS_DISPONIVEL = True
except ImportError:
    pd = None
    PANDAS_DISPONIVEL = False

# ================= CONFIGURAÇÕES =================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'SEU_TOKEN_DO_BOT_TELEGRAM')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '-1000000000000')
configurar_telegram(TELEGRAM_TOKEN, CHAT_ID)

# Serviço standalone (Node/Baileys) responsável por enviar os alertas para o
# grupo do WhatsApp (index.js/config.json/package.json na raiz do projeto). Roda na MESMA máquina
# do bot_campo_monitoramento.py (é independente do painel_operacional/index.js, que
# fica em outra máquina cuidando da confirmação com clientes).
WHATSAPP_ALERTA_URL = os.environ.get('WHATSAPP_ALERTA_URL', 'http://127.0.0.1:3939/alerta')
WHATSAPP_ALERTA_ATIVO = os.environ.get('WHATSAPP_ALERTA_ATIVO', '1') != '0'

# Endpoint do serviço Node que devolve (e esvazia) a fila de mensagens recebidas
# no grupo do WhatsApp, usado para o bot interpretar comandos como /autenticador
# direto do grupo (mesma ideia do getUpdates do Telegram, mas via polling HTTP).
WHATSAPP_MENSAGENS_URL = os.environ.get('WHATSAPP_MENSAGENS_URL', 'http://127.0.0.1:3939/mensagens')
WHATSAPP_MENSAGENS_INTERVALO_SEG = float(os.environ.get('WHATSAPP_MENSAGENS_INTERVALO_SEG', '3'))

# Pasta do serviço Node (index.js / config.json / package.json). Por padrão
# fica direto na raiz do projeto, junto com este script (mesma pasta onde
# o bot_campo_monitoramento.py roda) — não numa subpasta.
WHATSAPP_ALERTA_SERVICO_DIR = os.environ.get(
    'WHATSAPP_ALERTA_SERVICO_DIR',
    os.getcwd()
)
WHATSAPP_ALERTA_AUTOSTART = os.environ.get('WHATSAPP_ALERTA_AUTOSTART', '1') != '0'

STATUS_SERVICO_WHATSAPP = {"iniciado": False, "conectado": False, "processo": None}

# Script standalone (vpn_sempre_ativa.py, na raiz do projeto) que mantém a
# VPN OpenConnect conectada e reconecta sozinho em caso de queda. Ele mesmo
# cuida de rodar elevado (pede UAC na primeira vez) -- o bot só dispara ele
# como subprocesso e segue a vida, sem esperar.
VPN_SCRIPT_CAMINHO = os.environ.get(
    'VPN_SCRIPT_CAMINHO',
    os.path.join(os.getcwd(), "vpn_sempre_ativa.py")
)
VPN_AUTOSTART = os.environ.get('VPN_AUTOSTART', '1') != '0'

# Sessão HTTP reaproveitada nas chamadas ao serviço Node (polling + alerta),
# para reusar a mesma conexão via keep-alive em vez de abrir um socket TCP
# novo a cada request -- evita esgotar as portas dinâmicas do Windows em
# execuções longas (WinError 10048).
_SESSAO_WHATSAPP = requests.Session()

# Credenciais do auto.provedor.example
PROVEDOR_AUTO_EMAIL = os.environ.get('PROVEDOR_AUTO_EMAIL', 'operador.ss.operacional@provedor.example')
PROVEDOR_AUTO_SENHA = os.environ.get('PROVEDOR_AUTO_SENHA', 'SUA_SENHA_DO_PORTAL')

# Códigos de serviço alvo para CAPEX (entrantes)
CODIGOS_ALVO = [
    'ES02', 'ES02PV', 'ATV1', 'ATB2B', 'ATVPME', 'ATVPRE',
    'ES04'
]

LITORAL_SP = ['CGT', 'BASE', 'SST', 'SSTBO', 'IBL', 'BERT', 'BERTN']

BAIRROS_COM_RESTRICAO_DE_SIGLA = [
    'BORACEIA', 'BALNEARIO MOGIANO', 'MORADA DA PRAIA', 'JUQUEHY', 'JUQUEHI',
    'BARRA DO SAHY', 'BARRA DO SAHI', 'CAMBURI', 'CAMBURY', 'CAMBURIZINHO',
    'BALEIA', 'PRAIA DA BALEIA', 'SITIO VELHO', 'VILA CARIOCA',
    'NUCLEO VILA CARIOCA', 'MARESIAS', 'PAUBA', 'BOICUCANGA'
]

UNIDADES_RESTRICAO_BAIRRO = {
    'BERT': BAIRROS_COM_RESTRICAO_DE_SIGLA,
    'BERTN': BAIRROS_COM_RESTRICAO_DE_SIGLA,
}

RJ = [
    'RSD', 'MPE', 'VAS', 'VRD',
    'PNDO', 'VLC', 'IZA', 'TRS', 'BMA',
    'PORE', 'COLG', 'BPI', 'PFS', 'PDS',
    'PNHE'
]

# Siglas onde executamos serviço mas que NÃO são nossa área de atendimento.
# Entram só na busca de REPARO (ES05), nunca no alerta de CAPEX: o que se quer
# ali é não perder uma garantia de serviço nosso executado fora da área.
#
# ESTA LISTA É DO VOCABULÁRIO DO CAMPO, NÃO DO DA BASE OFS. Os dois divergem, e
# a divergência é silenciosa. Em 14/08/2026 a base trazia 84 linhas com sigla
# `PBS` (cidade Paraíba do Sul) e o CAMPO NUNCA devolveu esse código -- 0 em
# 7.361 registros; o único daqueles contratos que teve reparo apareceu sob
# `PDS`, que já está em RJ. Pôr `PBS` aqui seria um filtro que nunca casa:
# busca vazia, sem erro nenhum, e a garantia real não notificada.
#
# ANTES DE ACRESCENTAR UMA SIGLA AQUI: confirme que o CAMPO realmente a emite
# (conte `unidade` em dados/reparos_avaliados.json). Que ela apareça na Chave
# Workzone da Base OFS não prova nada -- é o outro dialeto.
SIGLAS_GARANTIA_EXTRA = ['UTB', 'CBF']

# ================= AUTENTICADOR (consulta de sessões) =================
AUTENTICADOR_URL_SAVE = "https://provedor.example/status.php?action=save"
AUTENTICADOR_URL_PROCESSA = "https://provedor.example/processa.php?bg=1"
AUTENTICADOR_URL_LER_CSV = "https://provedor.example/ler_csv.php"

AGUARDANDO_CONTRATO_AUTENTICADOR = {}
TIMEOUT_AGUARDANDO_CONTRATO_AUTENTICADOR_SEG = 5 * 60

# Igual ao AGUARDANDO_CONTRATO_AUTENTICADOR (Telegram), mas chaveado pelo JID de cada
# participante do grupo do WhatsApp -- assim duas pessoas usando /autenticador ao
# mesmo tempo no mesmo grupo não se atrapalham (cada uma tem sua própria
# "espera" independente, em vez de compartilhar o estado do chat inteiro).
AGUARDANDO_CONTRATO_AUTENTICADOR_WHATSAPP = {}

# O /improdutivas mudou de natureza duas vezes em 13/08/2026, e é bom saber
# qual dos três ele é hoje:
#
# 1. ERA um relatório de lote: alguém mandava o comando, ANEXAVA o CSV do OFS
#    no grupo e recebia listas por região. Não tinha memória e não cruzava com
#    nada -- analisava, imprimia e esquecia.
# 2. Foi APOSENTADO, e no lugar dele a mesma classificação de motivos passou a
#    rodar sozinha a cada entrante, contra a base de 30 dias (improdutivas.py e
#    verificar_improdutiva_anterior). Isso continua valendo: é o alerta.
# 3. VOLTOU, com outro corpo, quando ficou claro que alerta não é lista. O
#    alerta é evento -- conta que algo aconteceu e some na conversa do grupo.
#    A pergunta de quem monta roteiro é outra: o que está de pé AGORA. Hoje o
#    comando varre os CAPEX abertos no CAMPO e devolve, por região, os que têm
#    improdutiva recente (montar_lista_improdutivas_abertas).
#
# O comando NÃO espera mais anexo: as bases entram pelo site.

# Guarda a última lista de chamados buscada da API do CAMPO (atualizada a cada
# ciclo do loop de monitoramento). O agendador horário do backlog de CAPEX e
# os comandos /backlog (Telegram) e "backlog" (WhatsApp) usam essa cópia em
# vez de buscar de novo na API -- assim ficam sempre alinhados com o que o
# monitoramento já processou no ciclo mais recente.
LISTA_CHAMADOS_ATUAL = {"dados": None}

# O cache guarda uma PROJEÇÃO do chamado, não o chamado inteiro.
#
# Medido em 08/08/2026: o backlog lê 8 dos 46 campos, e a lista completa de
# ~1850 chamados ocupava 36,9 MB contra 4,6 MB da projeção -- 8x. Pior que o
# tamanho era o pico: no instante de trocar o cache, a lista velha e a nova
# coexistiam, ~74 MB de dicts aninhados de uma vez. Montar e destruir isso a
# cada varredura é o que fragmenta o heap do processo (os objetos SÃO
# liberados -- a contagem de objetos vivos fica estável --, mas o alocador não
# devolve as arenas ao sistema e o pico vai subindo).
#
# Quem processa o chamado CRU continua recebendo o chamado cru: a projeção só
# vale para o que sobrevive à varredura. `extrair_telefones_do_chamado`, por
# exemplo, precisa da estrutura aninhada inteira para achar os contatos.
# nomeCliente e enderecoBairro entraram em 13/08/2026 para o /improdutivas
# consolidado. Não é comodidade: a regra de reincidência casa por contrato OU
# por NOME, e é assim que o alerta individual decide. Uma lista que só casasse
# por contrato apontaria menos casos do que os avisos já enviados no grupo, e
# ninguém saberia dizer qual das duas estava errada. São duas strings curtas
# por chamado -- a projeção existe para descartar `ordemServicos`, que é a
# parte pesada, não para economizar bytes de texto.
CAMPOS_CACHE_BACKLOG = ("id", "fila", "enderecoUnidade", "codigoContrato",
                        "dataAbertura", "dataConclusao", "agendamentoData",
                        "nomeCliente", "enderecoBairro")


def projetar_para_cache(lista_chamados):
    """Versão enxuta da lista, só com o que os consumidores do cache leem.

    `ordemServicos` é a parte pesada do chamado e serve para uma coisa só:
    saber se a última O.S. tem 'pacote' preenchido. Isso vira o booleano
    `tem_pacote` aqui, e a lista aninhada não precisa sobreviver à varredura.
    """
    enxuta = []
    for chamado in lista_chamados or ():
        if not isinstance(chamado, dict):
            continue
        ordens = chamado.get("ordemServicos") or []
        pacote = ordens[-1].get("pacote") if ordens and isinstance(ordens[-1], dict) else None
        registro = {campo: chamado.get(campo) for campo in CAMPOS_CACHE_BACKLOG}
        registro["tem_pacote"] = bool(pacote and str(pacote).strip())
        enxuta.append(registro)
    return enxuta


def obter_lista_chamados_atual():
    return LISTA_CHAMADOS_ATUAL["dados"]


# Tipos aceitos pelos comandos "/backlog <tipo>" (Telegram) e "backlog <tipo>"
# (WhatsApp). Usado tanto pra validar o subtipo digitado quanto pela função
# abaixo, que gera e envia TODOS eles em sequência quando nenhum tipo é
# informado (ex: só "backlog" / "/backlog").
TIPOS_BACKLOG_VALIDOS = ["capex", "reparo", "upgrade", "mudanca_comodo"]


def gerar_e_enviar_backlog_todos_tipos(lista_chamados):
    """Gera e envia o backlog de TODOS os tipos (capex, reparo, upgrade,
    mudanca_comodo), um de cada vez. Rodar em sequência (em vez de disparar
    uma thread por tipo) porque gerar_e_enviar_backlog_tipo já serializa
    tudo internamente via _lock_envio -- ou seja, threads em paralelo aqui
    só ficariam esperando na fila sem ganhar nada, então preferimos um único
    laço simples nesta própria thread."""
    algum_falhou = False
    for tipo in TIPOS_BACKLOG_VALIDOS:
        try:
            ok = gerar_e_enviar_backlog_tipo(lista_chamados, tipo)
            if not ok:
                algum_falhou = True
                logger.warning(f"Backlog completo: falha ao gerar/enviar o tipo '{tipo}'.")
        except Exception:
            algum_falhou = True
            logger.exception(f"Backlog completo: erro inesperado ao gerar/enviar o tipo '{tipo}'.")
    return not algum_falhou

try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

# ================= ORGANIZAÇÃO DE PASTAS =================
# Dados/estado (jsons persistidos, planilha, credencial Google), relatórios
# gerados (PNGs de backlog/termômetro), logs e assets ficam em subpastas
# próprias em vez de soltos na raiz do projeto.
PASTA_DADOS = os.path.join(os.getcwd(), "dados")
PASTA_LOGS = os.path.join(os.getcwd(), "logs")
PASTA_RELATORIOS = os.path.join(os.getcwd(), "relatorios")
PASTA_ASSETS = os.path.join(os.getcwd(), "assets")
for _pasta in (PASTA_DADOS, PASTA_LOGS, PASTA_RELATORIOS, PASTA_ASSETS):
    os.makedirs(_pasta, exist_ok=True)

BASE_OFS_ARQUIVO = os.path.join(PASTA_DADOS, "base OFS ok.xlsx")

# Base das improdutivas -- arquivo SEPARADO da base OFS, de propósito. A base
# OFS é a exportação só de atividades concluídas e é dela que sai a garantia;
# misturar as improdutivas ali significaria mexer numa base crítica que já
# funciona. Esta aqui é a exportação dos últimos 30 dias SEM o filtro de
# status, e serve a uma pergunta só: este entrante já foi improdutiva antes?
# Sobe pelo site, como a outra, e chega aqui espelhada (ver planilhas.py).
BASE_IMPRODUTIVAS_ARQUIVO = os.path.join(PASTA_DADOS, "base improdutivas 30 dias.xlsx")

DIAS_GARANTIA_REPARO = 30
DIAS_GARANTIA_ATIVACAO_MUDANCA = 15
ARQUIVO_REPAROS_AVALIADOS = os.path.join(PASTA_DADOS, "reparos_avaliados.json")

SELECTOR_BOTAO_REFRESH = 'button.button.is-light:has(.fa-sync)'
SELECTOR_BOTAO_CARREGAR_CHAMADOS = 'button:has-text("Carregar chamados")'
SELECTOR_TOKEN_NAO_INFORMADO = 'text=/token n[ãa]o informado/i'
SELECTOR_SERVIDOR_OFFLINE = 'text=/nosso servidor est[áa] offline/i'
SELECTOR_SECAO_ENCERRADA = 'text=/se[çc][ãa]o encerrada/i'
SELECTOR_NENHUM_REGISTRO = 'text=/nenhum registro localizado/i'
SELECTOR_BOT_F = 'text=/BOT-F/i'

TEMPO_MAX_INATIVIDADE_SEG = 360
INTERVALO_BUSCA = 25.0

# 'size' pedido na URL da busca de chamados.
#
# A API TEM TETO DE 300 POR PÁGINA -- pedir acima disso trava a requisição. Por
# isso 290, e por isso a busca PRECISA paginar: em 08/08/2026 os volumes eram 606
# e 1814 chamados, ou seja 3 e 7 páginas. Uma requisição única traria 290 de 1814
# e os outros 84% sumiriam em silêncio, derrubando a contagem de CAPEX e o
# backlog junto.
TAMANHO_PAGINA_BUSCA_DIRETA = 290

# De quanto em quanto tempo a BUSCA PAGINADA completa roda, em SEGUNDOS.
#   0   = desligada
#   25  = toda volta (o laço dorme INTERVALO_BUSCA entre voltas)
#   120 = a cada 2 min  <- valor atual
#   1800= a cada 30 min (o que a operação reclamou; não volte)
#
# Em SEGUNDOS e não em número de voltas de propósito: a duração da volta muda
# conforme o que está ligado. Contar voltas faria o intervalo real variar junto.
#
# ISTO AQUI JÁ FOI 1800, E VOLTAR A ESPAÇAR É A DECISÃO ERRADA. Fica registrado
# o porquê, porque a conclusão é contraintuitiva e custou um dia inteiro.
#
# O que se via em produção:
#   1800s -> +142 MB/h        25s -> +1950 MB/h  (mesmo PID, 17 min, 355->923 MB)
#
# A leitura natural é "cada varredura retém alguns MB, então espace". Está
# errada. Medido em bancada em 08/08/2026 com a carga real (8 páginas, 14,2 MB
# de JSON, ~2300 chamados), 12 rodadas:
#
#   contagem de objetos vivos (gc.get_objects)  186807 -> 186807, IMÓVEL
#   memória privada                             368 -> 405 MB, em dente de serra
#
# Objeto vivo parado com memória privada subindo é FRAGMENTAÇÃO DO ALOCADOR,
# não vazamento de referência. O interpretador pede arenas para os ~2300
# chamados aninhados, libera tudo, mas as arenas não esvaziam por completo e
# não voltam para o SO. Não existe objeto para achar e soltar -- procurar "o
# vazamento" no processamento é caçar o que não existe.
#
# Por etapa, na mesma bancada:
#   json.loads das 8 páginas         +0,2 MB/varredura   0,20s
#   + projetar_para_cache            +2,4 MB/varredura   0,23s
#   + extrair_telefones em todos     -0,2 MB/varredura   1,33s
#
# Três corolários que mudam o desenho:
#   1. O PARSE É DE GRAÇA. Trazer menos JSON da API não economiza memória --
#      só CPU. Foi por isso que a "varredura leve" de 08/08 não tinha como dar
#      certo nem se o volume estimado estivesse correto (e não estava: ES05
#      global devolve 1338, não os 529 que eu chutei, então a leve era 93% de
#      uma completa rodando a cada 120s em vez de 1800s).
#   2. `extrair_telefones_do_chamado` NÃO vaza. Custa 1,3s por varredura, que é
#      problema de CPU, não de memória. O comentário antigo aqui acusava ele,
#      `salvar_amostra_chamados` e `salvar_diagnostico_plano` -- os dois últimos
#      rodam UMA VEZ SÓ (flag `feito` em amostra_chamados.py). Os três estavam
#      inocentes.
#   3. Fragmentação escala com varreduras/hora e é ABSORVÍVEL: ~4 MB/varredura
#      x 144/h = ~580 MB/h, que contra LIMITE_RAM_BOT_MB dá um reinício
#      controlado de ~1 min a cada ~7h. Bate com o observado: o bot rodou
#      exatamente 7 horas em 07/08/2026 com a varredura em toda volta.
#
# Espaçar troca 1 min de reinício por turno por até 30 min de cegueira em TODA
# notificação -- e pior, uma O.S. aberta e concluída dentro da janela nunca é
# notificada, porque quando a varredura roda ela já saiu do filtro
# `dataConclusao IS NULL`. Foi o que a operação sentiu: "as notificações de
# CAPEX diminuíram bastante".
#
# POR QUE 120 E NÃO 25, já que as versões estáveis varriam em toda volta:
# porque o custo por varredura tem uma banda de 10x que eu não consegui fechar,
# e escolher a ponta agressiva dentro dela foi o erro que se repetiu o dia todo.
#
#   bancada limpa                       ~2,6 MB/varredura
#   PRODUÇÃO (único número de campo)   ~14   MB/varredura
#   teste local renotificando 130 O.S.  ~26   MB/varredura
#
# Pelo número de campo, contra LIMITE_RAM_BOT_MB:
#   25s  -> ~2 GB/h   -> reinício controlado a cada ~2h  (12 por dia)
#   120s -> ~420 MB/h -> reinício controlado a cada ~10h (um por turno)
#
# E o que se ganha indo de 120s para 25s é pequeno: a notificação sai em ≤2 min
# em vez de ≤25s. O que DOÍA era o 1800s, com lote a cada 30 min e O.S. abertas
# e concluídas dentro da janela que nunca eram notificadas. 120s mata isso.
#
# Para voltar a 25s: mude só este número, mas antes olhe um turno inteiro de
# telemetria e calcule os MB/varredura reais (delta de bot_ram_priv_mb dividido
# pelas varreduras do período). Se der perto de 2,6, 25s cabe folgado.
#
# Se um dia a fragmentação incomodar, o lever NÃO é o intervalo: é reduzir a
# alocação por varredura (reaproveitar as estruturas em vez de recriá-las) ou
# trocar o alocador. Antes disso, confirme na telemetria que a curva não
# estabiliza sozinha -- arena reaproveitada tende a platô.
#
# --- 14/08/2026: 120 -> 45, a operação sentiu o atraso ---
#
# ESTE NÚMERO É QUANTIZADO, e é a coisa mais importante a saber antes de mexer
# nele. O laço testa o intervalo UMA VEZ POR VOLTA, e a volta leva ~24 a 29s
# (varia com o trabalho do ciclo). O efetivo é sempre arredondado PARA CIMA até
# o próximo tick:
#
#   ticks:  ~24,5   ~49   ~73,5   ~98   ~122,5
#   120  -> primeiro tick >= 120 = ~122s     <- pagava 122 achando que pagava 120
#   50   -> primeiro tick >=  50 =  ~73s     <- perdeu o tick de 49 por 1,5s
#   45   -> primeiro tick >=  45 =  ~49s
#
# O 50 foi tentado em produção neste mesmo dia e mediu 74/72/73/73s. Foi o que
# provou a quantização: 5 segundos a menos no número valem 24 segundos a menos
# de atraso, e 10 a mais não valem nada. ESCOLHA PELA FAIXA, NÃO PELO NÚMERO
# REDONDO -- 60 e 70 são o MESMO ~73s que 50; 110 e 120 são o mesmo ~122s.
#
# 45 e não 49: a volta oscila até ~29s, e um alvo colado no tick fica refém
# dessa oscilação. 45 dispara no segundo tick nas duas pontas da banda.
#
# O atraso ponta a ponta, medido: espera pela varredura + a busca no CAMPO (18 a
# 25s) + processar até notificar (~1,3s). Com 120 dava pior caso ~2min40, que
# foi o que a operação relatou; com 45, ~1min15.
#
# O portão de memória acima foi conferido antes da mudança, e com os dois pontos
# que ele pedia: 227 MB de RSS recém-subido, 236 MB após 8h40 e ~240 varreduras.
# São ~9 MB no turno inteiro, ou ~0,04 MB/varredura -- duas ordens de grandeza
# abaixo dos 2,6 do portão, e longe dos 14 do número de campo antigo (que daria
# ~3,3 GB nessas 240). É o vazamento do page.on+json, já corrigido, saindo da
# conta. Nas ~880 varreduras/turno que 45s traz isso dá ~35 MB, contra
# LIMITE_RAM_BOT_MB de 4500.
#
# CUIDADO AO REMEDIR: `ps --sort=-rss | head -1` pega o SITE (iniciar_site.py,
# ~340 MB), não o bot. Meça pelo PID: systemctl show -p MainPID --value campo-bot.
#
# POR QUE 45 E NÃO 25, com a RAM liberando: 25s cairia no PRIMEIRO tick (~24,5s)
# e são ~1.760 varreduras por turno, 7x as requisições no CAMPO. O CAMPO não é nosso
# e não temos medida de como ele reage a isso. 45s dá ~880/turno (3,6x) e já
# entrega mais da metade do atraso; se um dia 25 for necessário, meça a resposta
# do CAMPO antes, não a nossa RAM.
INTERVALO_VARREDURA_COMPLETA_SEG = 45

# Sessão HTTP da varredura de chamados, fora do Playwright.
#
# Medido em bancada com a mesma carga (96 requisições, 28.800 chamados):
#   contexto.request.put + dispose() -> 30 MB vira 463 MB, 514 mil objetos vivos
#   requests                         -> 28 MB vira 34 MB, objetos vivos IMÓVEIS
# e `requests` ainda é ~2,6x mais rápido por requisição, por não haver ida e
# volta ao driver do navegador.
#
# O token do CAMPO viaja em header, então esta sessão não depende do navegador
# para autenticar -- mas os cookies do contexto são copiados antes de cada
# varredura, para o caso de a API passar a exigir sessão também.
_SESSAO_CAMPO = requests.Session()


def _sincronizar_cookies_campo(contexto):
    """Espelha os cookies do navegador na sessão HTTP da varredura."""
    try:
        for c in contexto.cookies():
            _SESSAO_CAMPO.cookies.set(
                c.get("name"), c.get("value"),
                domain=c.get("domain") or None, path=c.get("path") or "/",
            )
    except Exception:
        logger.debug("Não consegui copiar os cookies do navegador para a sessão da varredura.")

# Quantas varreduras seguidas podem voltar vazias antes de considerarmos que
# o navegador virou zumbi (contexto morto respondendo nada) e reabri-lo. Em
# 03/08 o bot ficou ~5h varrendo "0 chamados" porque o erro fatal do browser
# era registrado como se fosse falha de rede -- veja ContextoNavegadorMorto.
MAX_CICLOS_VAZIOS_SEGUIDOS = 5

# Erros do Playwright que NÃO são intermitência de rede: significam que o
# contexto/navegador morreu. Repetir a requisição nunca resolve -- o único
# caminho é reabrir o navegador pelo laço externo de executar_monitoramento.
_MARCAS_CONTEXTO_MORTO = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "browser closed",
    "request context disposed",
    "connection closed while reading from the driver",
    "playwright driver",
)


class ContextoNavegadorMorto(Exception):
    """O contexto/navegador do Playwright morreu; exige reabrir o navegador."""


def _e_erro_de_contexto_morto(erro):
    texto = str(erro).lower()
    return any(marca in texto for marca in _MARCAS_CONTEXTO_MORTO)

CST_PERFIL_DIRETORIO = os.path.join(os.getcwd(), "perfil_campo_logistica")
ARQUIVO_NOTIFICADAS = os.path.join(PASTA_DADOS, "os_notificadas.json")
# Último agendamento visto por O.S. Existe para enxergar a REMARCAÇÃO: uma
# O.S. que já foi improdutiva e ganha data nova é reincidência igual, mas
# entra pela porta de trás -- ela já está em os_notificadas, então o caminho
# do entrante nunca mais roda para ela. Sem esta memória, o bot só via o
# primeiro round de cada O.S. e ficava cego para todos os seguintes.
ARQUIVO_AGENDAMENTOS_VISTOS = os.path.join(PASTA_DADOS, "agendamentos_vistos.json")
ARQUIVO_HISTORICO_ENTRANTES_CAPEX = os.path.join(PASTA_DADOS, "historico_entrantes_capex.json")
# Estado do dia EM ANDAMENTO (hoje) dos entrantes CAPEX. Separado do
# histórico acima porque o histórico só recebe um dia inteiro quando ele
# vira -- sem isso, se o processo reiniciar no meio do dia (o sistema cai
# ou é reiniciado várias vezes ao longo do dia), ENTRANTES_CAPEX_HOJE
# nascia zerado em memória e os entrantes já contados hoje eram perdidos.
ARQUIVO_ENTRANTES_CAPEX_HOJE = os.path.join(PASTA_DADOS, "entrantes_capex_hoje.json")
ARQUIVO_LOCK_INSTANCIA = os.path.join(PASTA_LOGS, "monitor_campo.lock")
TELEGRAM_MAX_CARACTERES = 3800

ARQUIVO_DIAGNOSTICO_REQUISICAO = os.path.join(PASTA_DADOS, "diagnostico_requisicao_chamados.json")
ARQUIVO_DIAGNOSTICO_ERRO_API = os.path.join(PASTA_LOGS, "diagnostico_erro_api.log")
ARQUIVO_DIAGNOSTICO_FILAS = os.path.join(PASTA_DADOS, "diagnostico_filas.json")
TAMANHO_MAX_DIAGNOSTICO_ERRO = 500 * 1024

# ============== PAINEL DE TV ==============
# Paleta espelhada de site/web/static/estilo.css (grafite neutro com índigo
# como único acento). Tk não conhece alfa, então o que no CSS é rgba(...) já
# entra aqui achatado sobre o fundo correspondente.
COR_FUNDO        = "#0B0C10"   # --fundo
COR_SUPERFICIE   = "#101116"   # topo: um degrau acima do fundo, sem borda
COR_CARD         = "#16171D"   # --carta
COR_BORDA        = "#26272D"   # --borda achatada sobre a carta
COR_TEXTO        = "#F5F5F7"   # --texto
COR_TEXTO_MUTED  = "#8B8F98"   # --texto-fraco
COR_ROXO         = "#5E6AD2"   # --roxo
COR_ROXO_CLARO   = "#8B93E0"   # --roxo-claro
COR_DESTAQUE     = "#00BFFF"   # --ciano (acento da coluna de CAPEX)
COR_VERDE        = "#00FF88"   # --verde
COR_VERMELHO     = "#FF4D4D"   # --vermelho (acento da coluna de garantias)
COR_LARANJA      = "#FFA500"   # --laranja

# O alerta de garantia é alarme de sala: continua vermelho e pulsando, só que
# em tons derivados do --vermelho do site em vez do carmim antigo.
COR_ALERTA        = "#D63C3C"
COR_ALERTA_ESCURO = "#8E2323"

MAX_ITENS_CAPEX = 7      # cards de CAPEX são mais compactos (3 linhas) -> cabem mais na tela
MAX_ITENS_GARANTIA = 5   # cards de garantia são mais altos (4 linhas, com técnico OFS) -> cabem menos
DURACAO_ALERTA_MS = 120000

FILA_EVENTOS_TV = queue.Queue()
TV_ATIVA = False
ARQUIVO_HISTORICO_PAINEL = os.path.join(PASTA_DADOS, "historico_painel.json")

# --- status de conexão das garantias (consulta ao Autenticador) ---
# O painel mostra se o cliente da garantia está ONLINE ou OFFLINE. A consulta
# é lenta (chega a 35s) e depende da VPN, então roda numa thread à parte e
# entrega o resultado por fila, igual ao clima.
FILA_STATUS_AUTENTICADOR = queue.Queue()
# 10 min, e não um intervalo curto: o CSV de resultado do Autenticador é UM arquivo
# só no servidor, compartilhado por todos os usuários daquela ferramenta web.
# Consultar de minuto em minuto não só faz o painel pegar resultado alheio
# como atropela a consulta de quem estiver usando a ferramenta do outro lado.
# Garantia nova consulta na hora (ver _PEDIDO_STATUS_AUTENTICADOR), então o que este
# intervalo controla é só o quanto o status envelhece na parede.
INTERVALO_STATUS_AUTENTICADOR_SEG = 600
_CONTRATOS_GARANTIA_PAINEL = []
_TRAVA_CONTRATOS_PAINEL = threading.Lock()
_PEDIDO_STATUS_AUTENTICADOR = threading.Event()
_THREADS_PAINEL_INICIADAS = False

# ============== PREVISÃO DO TEMPO ==============
CIDADE_CLIMA = "Caraguatatuba - SP"
LATITUDE_CLIMA = -23.6208
LONGITUDE_CLIMA = -45.4131
FILA_CLIMA = queue.Queue()
INTERVALO_ATUALIZACAO_CLIMA_SEG = 15 * 60

CODIGOS_CLIMA_WMO = {
    0: ("Céu limpo", "☀️"),
    1: ("Poucas nuvens", "🌤️"),
    2: ("Parcialmente nublado", "⛅"),
    3: ("Nublado", "☁️"),
    45: ("Neblina", "🌫️"),
    48: ("Neblina com geada", "🌫️"),
    51: ("Garoa fraca", "🌦️"),
    53: ("Garoa", "🌦️"),
    55: ("Garoa forte", "🌦️"),
    56: ("Garoa congelante", "🌦️"),
    57: ("Garoa congelante forte", "🌦️"),
    61: ("Chuva fraca", "🌧️"),
    63: ("Chuva", "🌧️"),
    65: ("Chuva forte", "🌧️"),
    66: ("Chuva congelante", "🌧️"),
    67: ("Chuva congelante forte", "🌧️"),
    71: ("Neve fraca", "🌨️"),
    73: ("Neve", "🌨️"),
    75: ("Neve forte", "🌨️"),
    77: ("Grãos de neve", "🌨️"),
    80: ("Pancadas de chuva fracas", "🌦️"),
    81: ("Pancadas de chuva", "🌦️"),
    82: ("Pancadas de chuva fortes", "⛈️"),
    85: ("Pancadas de neve fracas", "🌨️"),
    86: ("Pancadas de neve fortes", "🌨️"),
    95: ("Tempestade", "⛈️"),
    96: ("Tempestade com granizo", "⛈️"),
    99: ("Tempestade com granizo forte", "⛈️"),
}

_ultima_fixacao_dia = None


def buscar_previsao_tempo():
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={LATITUDE_CLIMA}&longitude={LONGITUDE_CLIMA}"
            "&current_weather=true&timezone=America%2FSao_Paulo"
        )
        resposta = requests.get(url, timeout=10)
        resposta.raise_for_status()
        dados = resposta.json()
        atual = dados.get('current_weather', {})
        temperatura = atual.get('temperature')
        vento = atual.get('windspeed')
        codigo = atual.get('weathercode')
        descricao, emoji = CODIGOS_CLIMA_WMO.get(codigo, ("N/D", "🌡️"))
        return {
            'temperatura': temperatura,
            'vento': vento,
            'descricao': descricao,
            'emoji': emoji,
        }
    except Exception as e:
        logger.warning(f"Falha ao buscar previsão do tempo de {CIDADE_CLIMA}: {e}")
        return None


def thread_atualizacao_clima():
    while True:
        previsao = buscar_previsao_tempo()
        if previsao:
            FILA_CLIMA.put(previsao)
        time.sleep(INTERVALO_ATUALIZACAO_CLIMA_SEG)


def vpn_esta_conectada():
    try:
        r = requests.head("https://campo.provedor.example/login/", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _janela_esta_cloaked(hwnd):
    try:
        DWMWA_CLOAKED = 14
        valor = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(valor), ctypes.sizeof(valor)
        )
        return bool(valor.value)
    except Exception:
        return False


def _processo_forticlient_rodando():
    if not _PSUTIL_DISPONIVEL:
        return None
    try:
        for proc in psutil.process_iter(['name']):
            nome = (proc.info.get('name') or '').lower()
            if nome in NOMES_PROCESSO_FORTICLIENT:
                return True
        return False
    except Exception as e:
        logger.warning(f"Falha ao checar processos do FortiClient via psutil: {e}")
        return None


def _encontrar_janela_forticlient_hwnd():
    if not _WIN32GUI_DISPONIVEL:
        logger.warning("Módulo win32gui não disponível: busca de janela do FortiClient desativada.")
        return None, False

    candidatos = []

    def _callback(hwnd, _extra):
        titulo = win32gui.GetWindowText(hwnd)
        if titulo and 'forticlient' in titulo.lower():
            candidatos.append((hwnd, titulo, _janela_esta_cloaked(hwnd)))
        return True

    try:
        win32gui.EnumWindows(_callback, None)
    except Exception as e:
        logger.warning(f"Falha ao enumerar janelas do Windows: {e}")
        return None, False

    if not candidatos:
        return None, False

    logger.info(f"Janela(s) do FortiClient encontrada(s): {[(h, t) for h, t, _ in candidatos]}")

    for hwnd, _titulo, cloaked in candidatos:
        if not cloaked:
            return hwnd, False

    primeiro_hwnd = candidatos[0][0]
    logger.warning(
        f"Única(s) janela(s) do FortiClient encontrada(s) está(ão) 'cloaked' pelo DWM "
        f"(hwnd={primeiro_hwnd}). A automação de clique tende a falhar nesse estado."
    )
    return primeiro_hwnd, True


_estado_crash_forticlient = {'consecutivos': 0, 'ultimo_alerta_ts': 0.0}
LIMITE_CRASHES_CONSECUTIVOS_PARA_ALERTAR = 3
INTERVALO_MIN_ENTRE_ALERTAS_CRASH_SEG = 900

# Passagens seguidas encontrando o FortiClient rodando SEM janela de topo antes
# de matá-lo e reabrir do zero. Toda a automação de reconexão é por GUI
# (pywinauto): sem janela ela não tem o que clicar e só devolve. Em 20/07 esse
# estado se repetiu 175 vezes em ~7h, com o bot cego o tempo todo, porque nada
# no laço mudava de estratégia -- só insistia.
LIMITE_SEM_JANELA_PARA_REINICIAR_FC = 3
_estado_sem_janela_forticlient = {'consecutivos': 0}

# De quantas em quantas tentativas de reconexão da VPN o alerta é repetido.
# A espera satura em 120s, então ~20 tentativas ≈ 40 min entre avisos.
TENTATIVAS_VPN_ENTRE_ALERTAS = 20


def _tentar_fechar_popup_erro_fatal_forticlient():
    try:
        from pywinauto import Application
        app_erro = Application(backend="uia").connect(title="Error", timeout=3)
        janela_erro = app_erro.window(title="Error")
    except Exception:
        return False

    logger.warning(
        "Popup fatal 'A JavaScript error occurred in the main process' do FortiClient "
        "encontrado. Isso é um erro intrínseco do FortiClient. É recomendado reinstalar "
        "ou atualizar o FortiClient na máquina local."
    )
    try:
        botao_ok = janela_erro.child_window(title="OK", control_type="Button")
        botao_ok.invoke()
        logger.info("Popup fatal fechado (OK clicado) para limpar a tela.")
    except Exception as e:
        logger.warning(f"Popup fatal encontrado, mas não consegui clicar em OK: {e}")

    return True


def _registrar_crash_forticlient_e_decidir_espera():
    state_crash = _estado_crash_forticlient
    state_crash['consecutivos'] += 1

    if state_crash['consecutivos'] >= LIMITE_CRASHES_CONSECUTIVOS_PARA_ALERTAR:
        agora = time.time()
        if (agora - state_crash['ultimo_alerta_ts']) >= INTERVALO_MIN_ENTRE_ALERTAS_CRASH_SEG:
            state_crash['ultimo_alerta_ts'] = agora
            try:
                enviar_alerta_telegram(
                    "🔴 ALERTA: o FortiClient está travando repetidamente com o erro "
                    "'A JavaScript error occurred in the main process' (erro do FortiClient). "
                    "Verifique o aplicativo na máquina local se o problema persistir."
                )
            except Exception:
                logger.warning("Falha ao enviar alerta de crash do FortiClient para o Telegram.")

    return 60


def _resetar_contador_crash_forticlient():
    _estado_crash_forticlient['consecutivos'] = 0
    _estado_sem_janela_forticlient['consecutivos'] = 0


def _matar_processo_forticlient():
    """Encerra o FortiClient à força para que a próxima passagem o abra limpo.

    Último recurso: só chamado quando o app está rodando sem nenhuma janela de
    topo por várias passagens seguidas, situação em que a automação por GUI não
    consegue fazer absolutamente nada e a VPN fica fora do ar indefinidamente.
    """
    # Só a GUI (forticlient.exe) -- é ela que o ramo "abrir do zero" reabre.
    # fortitray.exe e fortifw.exe são o tray e o firewall: derrubá-los mexeria
    # em componentes que não têm nada a ver com a janela que está faltando.
    encerrou = False
    try:
        resultado = subprocess.run(
            ["taskkill", "/F", "/IM", "FortiClient.exe"],
            capture_output=True, text=True, timeout=20,
        )
        if resultado.returncode == 0:
            encerrou = True
            logger.info("FortiClient (GUI) encerrado à força.")
        else:
            logger.warning(
                f"taskkill não encerrou o FortiClient (código {resultado.returncode}): "
                f"{(resultado.stdout or resultado.stderr or '').strip()[:200]}"
            )
    except Exception as e:
        logger.warning(f"Falha ao encerrar o FortiClient: {e}")

    if encerrou:
        time.sleep(5)   # dá tempo do Windows liberar o processo
    return encerrou


def _tentar_confirmar_certificado_forticlient():
    try:
        from pywinauto import Application
        app_cert = Application(backend="uia").connect(title="Server Certificate Warning", timeout=2)
        janela_cert = app_cert.window(title="Server Certificate Warning")
    except Exception:
        return False

    logger.warning(
        "Popup 'Server Certificate Warning' encontrado — confirmando o certificado "
        "automaticamente (clicando em 'Sim')."
    )
    try:
        botao_sim = janela_cert.child_window(title="Sim", control_type="Button")
        botao_sim.invoke()
        logger.info("Popup de certificado: botão 'Sim' clicado com sucesso.")
    except Exception as e:
        logger.warning(f"Popup de certificado encontrado, mas falhou ao clicar em 'Sim': {e}")

    return True


def _vpn_e_gerenciada_externamente():
    """True quando não há FortiClient/win32gui para automatizar -- ou seja,
    no Linux, onde a VPN é o vpn_sempre_ativa.py rodando como serviço
    systemd (campo-vpn.service) separado do bot, falando openconnect direto.

    Confirmado em 09/08/2026: a máquina Linux de produção NÃO tem o
    FortiClient original instalado, só o openconnect -- então não existe
    janela nenhuma para automatizar ali, e tentar seria sempre falhar.
    """
    return not _WIN32GUI_DISPONIVEL


def lidar_com_queda_de_vpn():
    """Ponto único que os dois lugares do laço principal chamam quando
    `vpn_esta_conectada()` vira False. Decide COMO reagir, ao contrário do
    antigo `reconectar_forticlient()` direto, que só sabia fazer uma coisa.

    - Windows (FortiClient instalado): automatiza a janela do FortiClient,
      exatamente como sempre foi -- comportamento 100% preservado.
    - Linux (só openconnect): não tem GUI para clicar, e não precisa: o
      campo-vpn.service já está tentando reconectar sozinho, em processo
      separado, com o próprio backoff dele (ver vpn_sempre_ativa.py). Aqui
      só logamos e devolvemos na hora -- quem chamou já dorme e testa
      `vpn_esta_conectada()` de novo no próprio laço, então o "esperar" já
      existe do lado de fora; não precisa duplicar aqui.
    """
    if _vpn_e_gerenciada_externamente():
        logger.info(
            "VPN fora do ar -- sem GUI para automatizar aqui (Linux). "
            "Confiando no campo-vpn.service (vpn_sempre_ativa) para reconectar "
            "sozinho; só aguardando."
        )
        return
    reconectar_forticlient()


def reconectar_forticlient():
    logger.warning("Queda de VPN detectada. Preparando reconexão do FortiClient...")

    caminho_fc = r"C:\Program Files\Fortinet\FortiClient\FortiClient.exe"
    app = None
    janela = None

    try:
        from pywinauto import Application
        hwnd, cloaked = _encontrar_janela_forticlient_hwnd()

        if hwnd and not cloaked:
            logger.info(f"FortiClient já estava em execução (hwnd={hwnd}). Restaurando a janela existente...")
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
            except Exception as e:
                logger.warning(f"Falha ao restaurar/focar a janela via win32gui (seguindo mesmo assim): {e}")
            time.sleep(1)

            app = Application(backend="uia").connect(handle=hwnd)
            janela = app.window(handle=hwnd)

        elif hwnd and cloaked:
            logger.warning(
                "Janela do FortiClient está 'cloaked' (não renderizada). "
                "Aguardando o app sair desse estado antes de tentar interagir."
            )
            time.sleep(5)
            return

        else:
            ja_rodando = _processo_forticlient_rodando()

            if ja_rodando:
                _estado_sem_janela_forticlient['consecutivos'] += 1
                sem_janela = _estado_sem_janela_forticlient['consecutivos']
                logger.warning(
                    "FortiClient já está em execução em segundo plano, mas nenhuma janela de "
                    f"topo foi encontrada (ocorrência {sem_janela} seguida)."
                )

                # Insistir só repete o mesmo resultado: sem janela, a automação
                # por GUI não tem o que clicar. Depois de algumas passagens,
                # mata o app para que o ramo de baixo o reabra do zero.
                if sem_janela >= LIMITE_SEM_JANELA_PARA_REINICIAR_FC:
                    logger.warning(
                        f"{sem_janela} passagens seguidas sem janela de topo. "
                        "Encerrando o FortiClient para reabri-lo do zero."
                    )
                    try:
                        enviar_alerta_telegram(
                            f"🟠 FortiClient está rodando sem janela há {sem_janela} tentativas "
                            "e a reconexão automática não consegue agir. Encerrando e reabrindo o app..."
                        )
                    except Exception:
                        pass
                    _estado_sem_janela_forticlient['consecutivos'] = 0
                    if _matar_processo_forticlient():
                        return   # próxima passagem cai no ramo "abrir do zero"

                crash_detectado = _tentar_fechar_popup_erro_fatal_forticlient()
                if crash_detectado:
                    time.sleep(_registrar_crash_forticlient_e_decidir_espera())
                else:
                    time.sleep(5)
                return

            logger.info("Nenhuma janela do FortiClient encontrada. Abrindo o programa do zero...")
            subprocess.Popen([caminho_fc])
            time.sleep(5)
            try:
                app = Application(backend="uia").connect(title_re=".*FortiClient.*", timeout=20)
                janela = app.window(title_re=".*FortiClient.*")
            except Exception:
                logger.warning(
                    "Programa foi iniciado, mas nenhuma janela 'FortiClient' apareceu em 20s."
                )
                crash_detectado = _tentar_fechar_popup_erro_fatal_forticlient()
                if crash_detectado:
                    time.sleep(_registrar_crash_forticlient_e_decidir_espera())
                else:
                    time.sleep(5)
                return

        _resetar_contador_crash_forticlient()

        try:
            janela.restore()
        except Exception:
            pass
        try:
            janela.maximize()
        except Exception:
            pass
        janela.set_focus()
        time.sleep(1)

        logger.info("Sniper de erro: verificando se o popup 'A JavaScript error occurred' apareceu...")
        try:
            app_erro = Application(backend="uia").connect(title="Error", timeout=2)
            janela_erro = app_erro.window(title="Error")
            logger.info("Sniper de erro: popup 'Error' do FortiClient ENCONTRADO na tela.")

            try:
                botao_ok = janela_erro.child_window(title="OK", control_type="Button")
                botao_ok.invoke()
                logger.info("Sniper de erro: botão 'OK' clicado com sucesso. Popup fechado automaticamente.")
                time.sleep(1)
            except Exception as e_click:
                logger.warning(
                    f"Sniper de erro: popup foi encontrado, mas falhou ao clicar em 'OK' ({e_click})."
                )
        except Exception as e_busca:
            logger.info(
                f"Sniper de erro: nenhum popup 'Error' detectado desta vez. Seguindo normalmente."
            )

        _tentar_confirmar_certificado_forticlient()

        try:
            janela.set_focus()
            time.sleep(0.5)
        except Exception:
            pass

        try:
            botao = janela.child_window(title="Conectar", control_type="Button")
            botao.invoke()
            logger.info("Botão 'Conectar' acionado via API do Windows.")
        except Exception:
            logger.info("Botão inacessível via API. Enviando comando 'ENTER' na janela...")
            janela.type_keys('{ENTER}')

        logger.info("Comando enviado! Aguardando 15s para a VPN autenticar e estabelecer rota...")

        tempo_total_espera = 15
        intervalo_checagem = 2
        tempo_decorrido = 0
        while tempo_decorrido < tempo_total_espera:
            time.sleep(intervalo_checagem)
            tempo_decorrido += intervalo_checagem
            if _tentar_confirmar_certificado_forticlient():
                time.sleep(3)
                break

    except Exception:
        logger.exception("Falha na automação da janela do FortiClient")


# ================= ESTATÍSTICAS =================
# Mesmo problema que ENTRANTES_CAPEX_HOJE tinha: ficava só em memória, e
# reinícios do processo no meio do dia zeravam os contadores de hoje
# (capex/garantias notificadas, erros de log, O.S analisadas) mesmo sem o
# dia ter virado de verdade. Agora persiste em ARQUIVO_ESTATISTICAS_STATUS
# a cada mutação, e recarrega do disco na inicialização do módulo.
ARQUIVO_ESTATISTICAS_STATUS = os.path.join(PASTA_DADOS, "estatisticas_status.json")

_stats_lock = threading.Lock()


def _carregar_estado_estatisticas_status():
    """Lê ARQUIVO_ESTATISTICAS_STATUS do disco, se existir, e devolve o
    dict no formato de ESTATISTICAS_STATUS. Se não existir ou não puder
    ser lido, devolve o estado inicial (hoje, tudo zerado)."""
    estado_inicial = {
        'data_referencia': datetime.now().date(),
        'capex_pendente_sul_rj': 0,
        'capex_pendente_litoral_sp': 0,
        'capex_notificadas_hoje': 0,
        'garantias_notificadas_hoje': 0,
        'improdutivas_notificadas_hoje': 0,
        'erros_log_hoje': 0,
        'os_analisadas_hoje': 0,
        'inicio_contagem_ts': time.time(),
    }
    if os.path.exists(ARQUIVO_ESTATISTICAS_STATUS):
        try:
            with open(ARQUIVO_ESTATISTICAS_STATUS, 'r', encoding='utf-8') as f:
                dados = json.load(f)
            estado = dict(estado_inicial)
            estado.update(dados)
            estado['data_referencia'] = datetime.strptime(dados['data_referencia'], "%Y-%m-%d").date()
            return estado
        except Exception:
            logger.exception(
                "Não foi possível carregar estatisticas_status.json, iniciando o dia zerado."
            )
    return estado_inicial


def _salvar_estado_estatisticas_status():
    """Persiste ESTATISTICAS_STATUS em disco. Deve ser chamado só de
    dentro de um bloco protegido por _stats_lock."""
    try:
        dados = dict(ESTATISTICAS_STATUS)
        dados['data_referencia'] = ESTATISTICAS_STATUS['data_referencia'].isoformat()
        salvar_json_atomico(ARQUIVO_ESTATISTICAS_STATUS, dados, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Não foi possível persistir estatisticas_status.json.")


ESTATISTICAS_STATUS = _carregar_estado_estatisticas_status()


def _resetar_stats_diarias_se_necessario():
    hoje = datetime.now().date()
    if ESTATISTICAS_STATUS['data_referencia'] != hoje:
        ESTATISTICAS_STATUS['data_referencia'] = hoje
        ESTATISTICAS_STATUS['capex_notificadas_hoje'] = 0
        ESTATISTICAS_STATUS['garantias_notificadas_hoje'] = 0
        ESTATISTICAS_STATUS['improdutivas_notificadas_hoje'] = 0
        ESTATISTICAS_STATUS['erros_log_hoje'] = 0
        ESTATISTICAS_STATUS['os_analisadas_hoje'] = 0
        ESTATISTICAS_STATUS['inicio_contagem_ts'] = time.time()
        _salvar_estado_estatisticas_status()


def registrar_capex_notificada():
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        ESTATISTICAS_STATUS['capex_notificadas_hoje'] += 1
        _salvar_estado_estatisticas_status()


def registrar_garantia_notificada():
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        ESTATISTICAS_STATUS['garantias_notificadas_hoje'] += 1
        _salvar_estado_estatisticas_status()


def registrar_improdutiva_notificada():
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        ESTATISTICAS_STATUS['improdutivas_notificadas_hoje'] = (
            ESTATISTICAS_STATUS.get('improdutivas_notificadas_hoje', 0) + 1
        )
        _salvar_estado_estatisticas_status()


def registrar_erro_log():
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        ESTATISTICAS_STATUS['erros_log_hoje'] += 1
        _salvar_estado_estatisticas_status()


def registrar_os_analisadas(quantidade):
    if not quantidade:
        return
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        ESTATISTICAS_STATUS['os_analisadas_hoje'] += quantidade
        _salvar_estado_estatisticas_status()


def atualizar_capex_pendente(qtd_sul_rj, qtd_litoral_sp):
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        ESTATISTICAS_STATUS['capex_pendente_sul_rj'] = qtd_sul_rj
        ESTATISTICAS_STATUS['capex_pendente_litoral_sp'] = qtd_litoral_sp
        _salvar_estado_estatisticas_status()


# ================= ESTADO DA MÁQUINA (bloco do /status) =================
# Pedido em 14/08/2026: o /status contava o que o bot fez, mas nada sobre o
# chão em que ele pisa. Numa máquina só, rodando bot + site + Chromium + VPN,
# "o bot está lento" e "a máquina está sem RAM" são perguntas diferentes e a
# segunda não tinha resposta sem SSH.

# UNIDADE FIXA POR SEÇÃO, e não a "melhor" para cada número. Máquina em GB,
# processo em MB. Misturar as duas -- 1.6 GB numa linha e 378 MB na seguinte --
# obriga quem lê a converter de cabeça justamente para comparar, que é a única
# coisa que se faz com esses números.
def _gb(n):
    return (n or 0) / (1024 ** 3)


def _mb(n):
    return (n or 0) / (1024 ** 2)


def _fmt_duracao(segundos):
    """Compacto de propósito: estas linhas dividem largura com o resto do
    bloco alinhado, e "13h 39min" custa 4 caracteres a mais que "13h39"."""
    if segundos is None:
        return "?"
    segundos = int(segundos)
    dias, resto = divmod(segundos, 86400)
    horas, resto = divmod(resto, 3600)
    minutos = resto // 60
    if dias:
        return f"{dias}d{horas}h"
    return f"{horas}h{minutos:02d}" if horas else f"{minutos}min"


def _rotulo_processo(info):
    """Nome útil de um processo. `python3` e `node` sozinhos não dizem nada: na
    produção há quatro python3 e o que os distingue é o script."""
    nome = str(info.get('name') or '?')
    if nome.lower().startswith(('python', 'node')):
        for parte in (info.get('cmdline') or [])[1:]:
            if parte.startswith('-'):
                continue
            base = os.path.basename(parte)
            if base:
                return base
    return nome


def montar_bloco_maquina():
    """Estado da máquina, em texto, para o /status.

    NUNCA levanta: cada medida vai no seu try. Um /status sem o bloco da
    máquina ainda responde o que a operação foi perguntar; um /status que
    estoura por causa de um contador de CPU não responde nada.
    """
    if not _PSUTIL_DISPONIVEL:
        return "\n\n🖥️ *Máquina*: indisponível (psutil não instalado)."

    # Tudo dentro de UM bloco ```: é o que dá fonte monoespaçada no Telegram e
    # no WhatsApp, e sem ela nenhum alinhamento por espaço sobrevive. Um bloco
    # só, e não dois, para não virar duas caixas cinzentas na conversa.
    #
    # Largura alvo: 36 colunas. Acima disso a bolha do WhatsApp quebra a linha
    # no celular e o alinhamento que se pagou para ter vai embora.
    linhas = []

    try:
        mem = psutil.virtual_memory()
        livre_ram = _gb(mem.available)
        # Campos de 5 e 6: o disco chega a "131.5 / 208.0" e um campo curto
        # demais empurra a barra, desalinhando justamente as duas linhas que
        # existem para ser comparadas.
        linhas.append(
            f"{'RAM':<7}{_gb(mem.total - mem.available):>5.1f} /"
            f"{_gb(mem.total):>6.1f} GB {mem.percent:>3.0f}%"
        )
    except Exception:
        livre_ram = None

    try:
        # A partição do próprio bot, e não '/': se um dia ele morar em disco
        # separado, é o dele que enche primeiro (log, PNG, JSON de 5 MB).
        disco = psutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        linhas.append(
            f"{'Disco':<7}{_gb(disco.used):>5.1f} /"
            f"{_gb(disco.total):>6.1f} GB {disco.percent:>3.0f}%"
        )
        livre_disco = _gb(disco.free)
    except Exception:
        livre_disco = None

    # O que sobra, na mesma linha: "usada x livre" é a pergunta, e as duas
    # metades separadas por três linhas não se comparam de relance.
    if livre_ram is not None or livre_disco is not None:
        partes = []
        if livre_ram is not None:
            partes.append(f"{livre_ram:>5.1f} GB RAM")
        if livre_disco is not None:
            partes.append(f"{livre_disco:.0f} GB disco")
        linhas.append(f"{'Livre':<7}" + " · ".join(partes))

    try:
        # interval curto e BLOQUEANTE: com interval=None a primeira chamada de
        # cada processo devolve 0,0%, porque psutil precisa de duas amostras.
        cpu = psutil.cpu_percent(interval=0.3)
        nucleos = psutil.cpu_count()
        if hasattr(os, "getloadavg"):
            # "carga 0.30/8" e não "carga 0.30": carga sozinha não se lê sem
            # saber contra quantos núcleos ela corre.
            resto = f"  carga {os.getloadavg()[0]:.2f}/{nucleos}"
        else:
            resto = f"  {nucleos} núcleos"   # Windows não tem load average
        linhas.append(f"{'CPU':<7}{cpu:>5.0f}%{resto}")
    except Exception:
        pass

    try:
        linhas.append(
            f"{'Uptime':<7}máq {_fmt_duracao(time.time() - psutil.boot_time())}"
            f" · bot {_fmt_duracao(time.time() - psutil.Process().create_time())}"
        )
    except Exception:
        pass

    try:
        vistos = []
        for proc in psutil.process_iter(['name', 'cmdline', 'memory_info']):
            try:
                info = proc.info
                mi = info.get('memory_info')
                rss = mi.rss if mi else 0
            except Exception:
                continue   # processo morreu entre listar e ler: normal
            if rss:
                vistos.append((rss, _rotulo_processo(info)))
        if vistos:
            vistos.sort(reverse=True)
            linhas.append(f"{'Proc':<7}{len(vistos):>5d} em execução")
            # Nome cortado em 21 com reticência: é o que mantém a coluna dos MB
            # reta. Sem o corte, um processo de nome comprido desloca só a linha
            # dele e a lista deixa de se ler de relance.
            linhas.append("")
            for rss, nome in vistos[:5]:
                curto = nome if len(nome) <= 21 else nome[:20] + "…"
                linhas.append(f"{curto:<22}{_mb(rss):>5.0f} MB")
    except Exception:
        pass

    if not linhas:
        return "\n\n🖥️ *Máquina*: não consegui medir."
    return "\n\n🖥️ *Máquina*\n```\n" + "\n".join(linhas) + "\n```"


def montar_mensagem_status():
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        capex_rj = ESTATISTICAS_STATUS['capex_pendente_sul_rj']
        capex_sp = ESTATISTICAS_STATUS['capex_pendente_litoral_sp']
        capex_notif = ESTATISTICAS_STATUS['capex_notificadas_hoje']
        garantias_notif = ESTATISTICAS_STATUS['garantias_notificadas_hoje']
        improdutivas_notif = ESTATISTICAS_STATUS.get('improdutivas_notificadas_hoje', 0)
        erros = ESTATISTICAS_STATUS['erros_log_hoje']
        os_analisadas = ESTATISTICAS_STATUS['os_analisadas_hoje']
        inicio_ts = ESTATISTICAS_STATUS['inicio_contagem_ts']

    minutos_decorridos = max((time.time() - inicio_ts) / 60.0, 1.0)
    os_por_minuto = os_analisadas / minutos_decorridos

    # Pausado, os números abaixo param no tempo. Sem este aviso o /status
    # devolveria contadores congelados com cara de contador ao vivo.
    aviso_pausa = ""
    if monitor_pausado():
        aviso_pausa = (
            "🌙 *MONITORAMENTO PAUSADO* — os números abaixo são os do momento "
            "em que foi pausado. Use /ligar para retomar.\n\n"
        )

    return (
        "⚙️ *Status atual do sistema*\n\n"
        f"{aviso_pausa}"
        f"CAPEX pendente SUL RJ: {capex_rj}\n"
        f"CAPEX pendente LITORAL NORTE SP: {capex_sp}\n"
        f"CAPEX notificadas hoje: {capex_notif}\n"
        f"Garantias notificadas hoje: {garantias_notif}\n"
        f"Improdutivas notificadas hoje: {improdutivas_notif}\n"
        f"Erros registrados no LOG hoje: {erros}\n"
        f"TOTAL de O.S analisadas por minuto: {os_por_minuto:.1f}"
        f"{montar_bloco_maquina()}"
    )


def enviar_status_telegram():
    mensagem = montar_mensagem_status()
    enviar_alerta_telegram(mensagem, parse_mode="Markdown")


# ================= TERMÔMETRO DE ENTRANTES CAPEX (por unidade, no dia) =================
# Mesmo padrão de reset diário automático usado em ESTATISTICAS_STATUS
# acima, só que aqui guardamos a contagem por unidade (não só um total),
# pra alimentar a imagem do termômetro (termometro_render.py).
#
# Além de contar os entrantes de HOJE, também persiste a contagem de cada
# dia (por unidade) em ARQUIVO_HISTORICO_ENTRANTES_CAPEX assim que o dia
# vira -- isso alimenta a "média histórica geral" (todas as unidades,
# todos os dias anteriores) usada como referência no termômetro, em vez
# de comparar as unidades só entre si dentro do mesmo dia.
#
# O estado do dia em andamento (ENTRANTES_CAPEX_HOJE) também é persistido
# em ARQUIVO_ENTRANTES_CAPEX_HOJE a cada entrante registrado, e recarregado
# do disco na inicialização do módulo -- assim, se o processo reiniciar no
# meio do dia, os entrantes já contados hoje não se perdem.
_entrantes_capex_lock = threading.Lock()


def _carregar_estado_entrantes_capex_hoje():
    """Lê ARQUIVO_ENTRANTES_CAPEX_HOJE do disco, se existir, e devolve o
    dict no formato de ENTRANTES_CAPEX_HOJE. Se não existir ou não puder
    ser lido, devolve o estado inicial (hoje, sem nenhum entrante)."""
    estado_inicial = {'data_referencia': datetime.now().date(), 'por_unidade': {}}
    if os.path.exists(ARQUIVO_ENTRANTES_CAPEX_HOJE):
        try:
            with open(ARQUIVO_ENTRANTES_CAPEX_HOJE, 'r', encoding='utf-8') as f:
                dados = json.load(f)
            return {
                'data_referencia': datetime.strptime(dados['data_referencia'], "%Y-%m-%d").date(),
                'por_unidade': dados.get('por_unidade', {}),
            }
        except Exception:
            logger.exception(
                "Não foi possível carregar entrantes_capex_hoje.json, iniciando o dia zerado."
            )
    return estado_inicial


def _salvar_estado_entrantes_capex_hoje():
    """Persiste ENTRANTES_CAPEX_HOJE em disco (chamado sempre que o estado
    muda: novo entrante registrado ou virada de dia). Deve ser chamado só
    de dentro de um bloco protegido por _entrantes_capex_lock."""
    try:
        dados = {
            'data_referencia': ENTRANTES_CAPEX_HOJE['data_referencia'].isoformat(),
            'por_unidade': ENTRANTES_CAPEX_HOJE['por_unidade'],
        }
        salvar_json_atomico(ARQUIVO_ENTRANTES_CAPEX_HOJE, dados, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Não foi possível persistir entrantes_capex_hoje.json.")


ENTRANTES_CAPEX_HOJE = _carregar_estado_entrantes_capex_hoje()


def carregar_historico_entrantes_capex():
    """Devolve o histórico salvo: {"AAAA-MM-DD": {unidade: quantidade}}."""
    if os.path.exists(ARQUIVO_HISTORICO_ENTRANTES_CAPEX):
        try:
            with open(ARQUIVO_HISTORICO_ENTRANTES_CAPEX, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            logger.exception(
                "Não foi possível carregar historico_entrantes_capex.json, iniciando novo histórico."
            )
    return {}


def salvar_historico_entrantes_capex(historico):
    # Mantém só os últimos 400 dias registrados, pra não crescer pra sempre.
    dias_ordenados = sorted(historico.keys())[-400:]
    historico_salvavel = {dia: historico[dia] for dia in dias_ordenados}
    try:
        salvar_json_atomico(ARQUIVO_HISTORICO_ENTRANTES_CAPEX, historico_salvavel, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Não foi possível persistir historico_entrantes_capex.json.")


def _persistir_dia_de_entrantes_no_historico(data_referencia, por_unidade):
    """Chamado só quando o dia vira: joga a contagem do dia que terminou
    dentro do arquivo de histórico, antes de zerar ENTRANTES_CAPEX_HOJE."""
    if not por_unidade:
        return  # dia sem nenhum entrante registrado -- nada a persistir
    try:
        historico = carregar_historico_entrantes_capex()
        historico[data_referencia.isoformat()] = por_unidade
        salvar_historico_entrantes_capex(historico)
    except Exception:
        logger.exception("Falha ao persistir o dia de entrantes CAPEX no histórico.")


def _resetar_entrantes_capex_se_necessario():
    hoje = datetime.now().date()
    if ENTRANTES_CAPEX_HOJE['data_referencia'] != hoje:
        _persistir_dia_de_entrantes_no_historico(
            ENTRANTES_CAPEX_HOJE['data_referencia'], ENTRANTES_CAPEX_HOJE['por_unidade']
        )
        ENTRANTES_CAPEX_HOJE['data_referencia'] = hoje
        ENTRANTES_CAPEX_HOJE['por_unidade'] = {}
        _salvar_estado_entrantes_capex_hoje()


def registrar_entrante_capex(unidade):
    """Chamado no mesmo instante em que um novo chamado de CAPEX é
    notificado (junto de registrar_capex_notificada()), pra contar 1
    entrante a mais pra essa unidade, hoje. Persiste em disco a cada
    chamada, pra sobreviver a reinícios do processo no meio do dia."""
    if not unidade:
        return
    with _entrantes_capex_lock:
        _resetar_entrantes_capex_se_necessario()
        por_unidade = ENTRANTES_CAPEX_HOJE['por_unidade']
        por_unidade[unidade] = por_unidade.get(unidade, 0) + 1
        _salvar_estado_entrantes_capex_hoje()


def obter_entrantes_capex_hoje():
    """Devolve uma cópia {unidade: quantidade} dos entrantes de CAPEX de
    hoje (reseta sozinho na virada do dia, persistindo o dia anterior no
    histórico antes de zerar)."""
    with _entrantes_capex_lock:
        _resetar_entrantes_capex_se_necessario()
        return dict(ENTRANTES_CAPEX_HOJE['por_unidade'])


def calcular_media_historica_geral_entrantes_capex():
    """Média histórica GERAL de entrantes de CAPEX: junta a contagem de
    TODAS as unidades em TODOS os dias já persistidos no histórico
    (excluindo hoje, que ainda está em andamento) e tira a média só dos
    valores > 0 -- mesmo critério já usado antes pra "média do dia", só
    que agora olhando o histórico inteiro em vez de só hoje.

    Devolve None se ainda não existe nenhum dia no histórico (bot rodando
    há pouco tempo) -- nesse caso o chamador decide o que fazer (ex: cair
    de volta pra média do próprio dia, como bootstrap)."""
    historico = carregar_historico_entrantes_capex()
    valores = [
        quantidade
        for por_unidade in historico.values()
        for quantidade in por_unidade.values()
        if quantidade and quantidade > 0
    ]
    if not valores:
        return None
    return sum(valores) / len(valores)


def gerar_e_enviar_termometro_capex():
    """Gera a imagem do termômetro de entrantes CAPEX (contagem de hoje,
    por unidade, comparada com a média histórica geral de dias
    anteriores) e envia pro Telegram e pro grupo do WhatsApp -- mesma
    lógica de envio já usada pro backlog (enviar_foto_telegram /
    enviar_imagem_whatsapp_grupo, importadas de backlog_envio)."""
    from backlog_envio import enviar_foto_telegram, enviar_imagem_whatsapp_grupo
    from termometro_render import gerar_imagem_termometro

    contagem = obter_entrantes_capex_hoje()

    media_geral = calcular_media_historica_geral_entrantes_capex()
    if media_geral is None:
        # Ainda não tem nenhum dia completo no histórico (ex: primeiro dia
        # rodando o termômetro) -- usa a média entre as unidades de hoje
        # como ponto de partida, só até existir histórico de verdade.
        valores_hoje = [v for v in contagem.values() if v > 0]
        media_geral = (sum(valores_hoje) / len(valores_hoje)) if valores_hoje else 0
        logger.info(
            "Termômetro CAPEX: ainda sem histórico de dias anteriores, "
            "usando a média de hoje como referência temporária."
        )

    try:
        caminho = gerar_imagem_termometro(contagem, media_geral, pasta_saida=PASTA_RELATORIOS)
    except Exception:
        logger.exception("Erro ao gerar a imagem do termômetro de entrantes CAPEX.")
        return False

    legenda = "🌡️ Termômetro de Entrantes CAPEX — hoje"
    ok_tg = enviar_foto_telegram(caminho, legenda)
    ok_wpp = enviar_imagem_whatsapp_grupo(caminho, legenda)
    if not (ok_tg or ok_wpp):
        logger.error("Falha ao enviar a imagem do termômetro de entrantes CAPEX.")
        return False
    return True


def thread_agendador_termometro_capex(intervalo_seg):
    """Dispara gerar_e_enviar_termometro_capex() de tempos em tempos, em
    thread própria (daemon), enquanto o processo estiver rodando.

    Espera 'intervalo_seg' ANTES do primeiro envio (em vez de disparar
    assim que o processo sobe) -- de propósito, porque o sistema reinicia
    várias vezes ao dia e um disparo imediato a cada boot geraria envios
    duplicados do termômetro no grupo. Assim, o pior caso é um reinício
    "atrasar" o próximo envio, nunca duplicar um envio que já saiu."""
    logger.info(
        f"Agendador do termômetro CAPEX iniciado -- próximo envio em "
        f"{intervalo_seg / 60:.0f} min, repetindo nesse intervalo."
    )
    while True:
        time.sleep(intervalo_seg)
        # Esta thread roda FORA do laço de monitoramento, então a pausa do
        # /desligar não a alcança sozinha. Sem esta guarda, o grupo receberia
        # termômetro de madrugada com o monitoramento supostamente desligado --
        # e com números velhos, congelados no instante da pausa.
        if monitor_pausado():
            logger.info("Termômetro CAPEX pulado: monitoramento pausado (/desligar).")
            continue
        try:
            gerar_e_enviar_termometro_capex()
        except Exception:
            logger.exception("Erro no agendador automático do termômetro CAPEX.")


class _ContadorErrosLogHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            try:
                registrar_erro_log()
            except Exception:
                pass


LOG_FILE = os.path.join(PASTA_LOGS, "monitor_campo.log")

# O log ROTACIONA. Até 08/08/2026 era um `FileHandler` puro e o arquivo tinha
# 25,3 MB / 253 mil linhas acumuladas desde 18/07, sem nenhum teto -- ia crescer
# até acabar o disco. Piorou quando a varredura passou a rodar a cada 120s em
# vez de 1800s: são 15x mais linhas por hora.
#
# 10 MB x 5 backups = 60 MB de teto. No ritmo atual isso guarda umas duas
# semanas, que é mais do que qualquer investigação precisou até hoje.
#
# Cuidado no Windows: a rotação renomeia o arquivo, e renomear falha se outro
# processo estiver com ele aberto. Dois já sabidos, e nenhum atrapalha: o filho
# `--render-backlog` desvia ANTES dos imports justamente para não abrir este log
# (ver o comentário no topo do arquivo), e o `telemetria.ps1` só faz leituras
# curtas com Get-Content. Se uma rotação falhar, o próprio logging avisa no
# stderr e a próxima tentativa passa.
_MAX_BYTES_LOG = 10 * 1024 * 1024
_BACKUPS_LOG = 5

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            LOG_FILE, maxBytes=_MAX_BYTES_LOG, backupCount=_BACKUPS_LOG,
            encoding='utf-8',
        ),
        logging.StreamHandler(sys.stdout),
        _ContadorErrosLogHandler(),
    ]
)
logger = logging.getLogger(__name__)

RELOAD_EM_ANDAMENTO = False
_CACHE_BASE_OFS = {"df": None, "mtime": None, "colunas": None, "indice": None}
_LOCK_FILE_HANDLE = None


def salvar_json_atomico(caminho, dados, **kwargs_json_dump):
    """Grava um JSON em disco de forma ATÔMICA: escreve tudo num arquivo
    temporário primeiro e só troca pelo arquivo final quando a escrita
    terminar por completo (os.replace).

    Por que isso importa: um `open(caminho, 'w')` direto apaga o conteúdo
    antigo na hora e vai escrevendo o novo aos poucos. Se o processo for
    encerrado no meio dessa escrita -- reinício do bot, o Windows fechando
    à força, antivírus, queda de energia -- o arquivo fica pela metade:
    início válido, fim cortado, e na próxima carga vira
    `json.decoder.JSONDecodeError`, perdendo TODO o conteúdo (o chamador
    cai no fallback de "iniciar vazio"). Foi exatamente isso que aconteceu
    com o reparos_avaliados.json.

    Com escrita atômica, ou a troca acontece por completo, ou o arquivo
    original (o de antes) continua intacto -- nunca fica um estado
    intermediário corrompido no disco."""
    caminho_temporario = f"{caminho}.tmp"
    with open(caminho_temporario, 'w', encoding='utf-8') as f:
        json.dump(dados, f, **kwargs_json_dump)
    os.replace(caminho_temporario, caminho)


def adquirir_lock_instancia_unica():
    global _LOCK_FILE_HANDLE
    try:
        _LOCK_FILE_HANDLE = open(ARQUIVO_LOCK_INSTANCIA, 'a+')
        if _PLATAFORMA_LOCK == "windows":
            _LOCK_FILE_HANDLE.seek(0)
            msvcrt.locking(_LOCK_FILE_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(_LOCK_FILE_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE_HANDLE.seek(0)
        _LOCK_FILE_HANDLE.truncate()
        _LOCK_FILE_HANDLE.write(str(os.getpid()))
        _LOCK_FILE_HANDLE.flush()
        return True
    except (OSError, IOError):
        if _LOCK_FILE_HANDLE:
            try:
                _LOCK_FILE_HANDLE.close()
            except Exception:
                pass
            _LOCK_FILE_HANDLE = None
        return False


def liberar_lock_instancia_unica():
    global _LOCK_FILE_HANDLE
    if _LOCK_FILE_HANDLE is None:
        return
    try:
        if _PLATAFORMA_LOCK == "windows":
            _LOCK_FILE_HANDLE.seek(0)
            msvcrt.locking(_LOCK_FILE_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(_LOCK_FILE_HANDLE.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    finally:
        try:
            _LOCK_FILE_HANDLE.close()
        except Exception:
            pass
        _LOCK_FILE_HANDLE = None


# ============== PAUSA DO MONITORAMENTO (/desligar e /ligar) ==============
# Desligar o monitor de madrugada exigia AnyDesk na máquina. Aqui "desligar" é
# DORMIR, não morrer: o processo continua vivo escutando o grupo, porque se ele
# morresse não sobraria ninguém para receber o /ligar depois. O que fecha é o
# navegador -- que é o que realmente consome memória.
#
# Quem lê isto: a pausa NÃO pode deixar o vigia (abaixo) matar o processo. O
# laço para de dar voltas quando dorme, então a espera da pausa segue carimbando
# batida de propósito. Sem isso o vigia mataria em 15 min, o relançamento subiria
# o bot de novo e o /desligar viraria um reinício com passos extras.
_pausa_monitor = threading.Event()


def monitor_pausado():
    return _pausa_monitor.is_set()


def pausar_monitor():
    """Liga a pausa. Devolve False se já estava pausado."""
    if _pausa_monitor.is_set():
        return False
    _pausa_monitor.set()
    return True


def retomar_monitor():
    """Desliga a pausa. Devolve False se já estava rodando."""
    if not _pausa_monitor.is_set():
        return False
    _pausa_monitor.clear()
    return True


# ============== JANELAS SOB DEMANDA (/exibirpaineltv e /exibirnavegador) ==============
# Até 07/08/2026 uma caixa Tk "Exibir navegador?" perguntava isso no arranque e
# ficava esperando clique -- o que inviabilizava a subida automática pela tarefa
# agendada: a máquina reiniciava e o bot parava naquela janela. Agora ele sobe
# sempre sem janela nenhuma, e as duas coisas viraram comando de grupo,
# independentes uma da outra (antes o mesmo clique ligava as duas).
#
# Tkinter só funciona na thread principal. Por isso o monitoramento passou a
# rodar SEMPRE em thread de fundo (ver main()), deixando a principal de plantão
# para criar o painel quando alguém pedir. Sem isso, /exibirpaineltv chegaria
# pela thread de escuta e não conseguiria abrir janela.
_painel_tv_pedido = threading.Event()
_navegador_visivel = threading.Event()
if os.environ.get('NAVEGADOR_VISIVEL_INICIAL') == '1':
    # Diagnostico/depuracao: forca o navegador visivel desde o boot, sem
    # precisar de comando no grupo (Telegram/WhatsApp) -- util quando esses
    # canais ainda nao estao disponiveis (primeira instalacao, sem QR
    # escaneado ainda) mas alguem precisa ver a tela, ex.: para digitar um
    # codigo de autenticador no primeiro login do CAMPO numa maquina nova.
    _navegador_visivel.set()
if os.environ.get('PAINEL_TV_INICIAL') == '1':
    # Producao (11/08/2026): o Painel de TV fica sempre ligado por padrao na
    # TV fisica, sem precisar de /exibirpaineltv a cada boot/reinicio. O
    # navegador do CAMPO continua oculto por padrao (default acima, sem env
    # var) -- os dois estados sao independentes, como sempre foram.
    _painel_tv_pedido.set()


def painel_tv_pedido():
    return _painel_tv_pedido.is_set()


def navegador_deve_aparecer():
    return _navegador_visivel.is_set()


# ============== VIGIA DO LAÇO DE MONITORAMENTO ==============
# Em 02/08 o laço principal travou dentro de uma chamada do Playwright sobre um
# driver morto e ficou 27h sem varrer nada -- sem exceção, sem log, com o
# processo "vivo" (as outras threads seguiram logando normalmente). A API
# síncrona do Playwright não tem timeout nesse caso, então nada lá dentro
# percebe. Este vigia roda FORA do laço: o laço carimba uma batida a cada
# volta e, se as batidas pararem, o processo é encerrado -- e a volta vem por
# duas vias independentes: o relançamento que ele engatilha antes de morrer
# (~15s) e a tarefa do Agendador do Windows (piso de 5 min), que cobre
# inclusive o caso de nem esta thread chegar a rodar.
_batida_monitor_ts = 0.0
_batida_monitor_lock = threading.Lock()

# Era 900 (15 min) porque o pior caso legítimo tinha de caber INTEIRO dentro de
# uma batida só: login de até 3 min, botão 'Carregar chamados' de até 2 min e
# busca paginada lenta, tudo somado sem carimbar nada no meio.
#
# Em 08/08/2026 as esperas longas passaram a carimbar por conta própria --
# `buscar_todas_paginas` bate a cada PÁGINA e `relogar` bate ao terminar o
# login. Com isso o pior caso legítimo entre duas batidas caiu para uma volta
# de laço (~30s) ou uma página da API (timeout de 30s), e o limite pôde apertar.
#
# 300s deixa 10x de margem sobre o pior caso legítimo e corta o prejuízo de cada
# travamento de 15 min para 5. Vale lembrar por que isso importa: a API síncrona
# do Playwright não tem timeout, então quando o renderer morre a chamada fica
# pendurada PARA SEMPRE e este vigia é a única saída. O detector de crash
# (page.on("crash")) resolve o caso comum em segundos; este limite é a rede para
# quando nem o evento chega.
TEMPO_MAX_SEM_BATIDA_SEG = 300

# Teto de memória do PRÓPRIO processo. Passou daqui, o vigia encerra e a tarefa
# do Agendador reergue -- reinício controlado de ~1 min no lugar de um
# congelamento imprevisível.
#
# Medido em 08/08/2026, com telemetria a cada 30s: o bot sobe usando ~600 MB e
# cresce ~50 MB por MINUTO, de forma praticamente linear. Em 40 min foi de 614
# MB para 2,8 GB, e a RAM livre da máquina caiu quase exatamente o mesmo tanto
# (3376 -> 1238 MB). Handles e threads ficaram estáveis o tempo todo, então é
# vazamento de memória, não de recurso.
#
# É o que explica os travamentos: quando a RAM acaba, o Windows pagina pesado,
# TUDO atrasa -- inclusive o sleep(30) deste vigia e o timeout de 20s do
# Playwright, que era o mistério central. Por isso o atraso de detecção vinha
# crescendo evento a evento (71 -> 126 -> 191 -> 374 -> 400s).
#
# ISTO É PALIATIVO: trata o sintoma, não a causa. Enquanto o vazamento existir,
# o teto vira reinício periódico -- a 55 MB/min, partindo de ~350 MB, 4500 MB dá
# cerca de 1h15 entre reinícios. Cada um custa ~1 min fora do ar, o que é bem
# melhor que o congelamento de 6h com detecção de 21 min que vinha acontecendo.
#
# 4500 numa máquina de 8 GB deixa folga para o Chromium (~500 MB) e o sistema.
# Se o vazamento for corrigido de verdade, este teto nunca dispara.
LIMITE_RAM_BOT_MB = 4500

# Piso de RAM LIVRE DA MÁQUINA. Abaixo disso o processo se encerra e a tarefa
# reergue -- levando o Chromium junto, que é metade do problema.
#
# Por que não bastava LIMITE_RAM_BOT_MB: ele olha só este processo, e medido em
# 08/08/2026 (4h de telemetria, uma varredura a cada 120s) existem DOIS
# vazamentos independentes crescendo em paralelo:
#
#   bot (Python)  ~540 MB/h   reciclar o navegador NÃO devolve
#   Chromium      ~700 MB/h   reciclar o navegador devolve (medido: 2,3 GB -> 0,3 GB)
#
# Somados dão ~1,2 GB/h. A máquina tem 7,9 GB e chegou a 210 MB livres em 4h,
# com o bot ainda em 2,5 GB -- ou seja, MUITO longe do teto de 4500, que nunca
# teria disparado. Quem estava acabando era a máquina, não o processo.
#
# 800 MB de piso: abaixo disso o Windows começa a paginar pesado, e paginação é
# o que transformava vazamento em travamento (o `sleep(30)` deste vigia e o
# timeout de 20s do Playwright atrasam junto com o resto).
#
# Este é o teto que realmente vai disparar no dia a dia; LIMITE_RAM_BOT_MB fica
# como rede para o caso de a máquina ter RAM sobrando e só este processo inchar.
PISO_RAM_LIVRE_MB = 800

# Abaixo deste intervalo entre dois reinícios por RAM, o reinício deixa de ser
# rotina e vira sintoma: significa que a taxa de vazamento piorou ou que algo
# novo está comendo a máquina. Aí sim o grupo é chamado.
#
# 45 min contra os ~2,5h esperados hoje (bot ~540 MB/h + Chromium ~700 MB/h,
# medidos em 08/08/2026): dá margem de sobra para variação normal e ainda pega
# uma regressão de 3x antes que ela vire travamento.
INTERVALO_MIN_ENTRE_RECICLAGENS_SEG = 45 * 60
ARQUIVO_ULTIMA_RECICLAGEM = os.path.join(PASTA_DADOS, "ultima_reciclagem_ram.txt")


def _segundos_desde_ultima_reciclagem():
    """Segundos desde o último reinício por RAM. None se não houver registro."""
    try:
        with open(ARQUIVO_ULTIMA_RECICLAGEM, encoding="utf-8") as f:
            return max(0.0, time.time() - float(f.read().strip()))
    except Exception:
        return None


def _registrar_reciclagem():
    """Carimba o instante do reinício por RAM, para o próximo saber o intervalo."""
    try:
        with open(ARQUIVO_ULTIMA_RECICLAGEM, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        logger.warning("Não consegui registrar o instante da reciclagem por RAM.")


def _ram_livre_maquina_mb():
    """RAM livre da MÁQUINA, em MB. None quando não dá para medir."""
    if not _PSUTIL_DISPONIVEL:
        return None
    try:
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        return None


def _ram_do_processo_mb():
    """Memória PRIVADA deste processo, em MB. None quando não dá para medir.

    Privada (commit), não `rss`. No Windows o `rss` é o working set, que é
    exatamente o número que o sistema ENCOLHE quando a memória aperta: medido em
    08/08/2026 às 04:02, o bot tinha working set de 1293 MB e memória privada de
    3345 MB -- o Windows já havia paginado 2 GB para o disco. Um teto baseado no
    working set nunca dispararia, porque ele cai justamente enquanto o vazamento
    piora. A privada é o que cresce de verdade.
    """
    if not _PSUTIL_DISPONIVEL:
        return None
    try:
        info = psutil.Process().memory_info()
        # 'private' existe no Windows; nos outros sistemas cai para o rss.
        bytes_ = getattr(info, "private", None) or info.rss
        return int(bytes_ / (1024 * 1024))
    except Exception:
        return None


def registrar_batida_monitor():
    """Carimba que o laço de monitoramento completou mais uma volta."""
    global _batida_monitor_ts
    with _batida_monitor_lock:
        _batida_monitor_ts = time.time()


def _idade_batida_monitor():
    with _batida_monitor_lock:
        if not _batida_monitor_ts:
            return None
        return time.time() - _batida_monitor_ts


# Segundos entre a morte deste processo e a subida do novo. Precisa dar tempo
# de o SO liberar o lock de instância única (logs/monitor_campo.lock), senão o
# bot novo sobe, não consegue o lock e desiste -- ficaríamos sem monitor.
ESPERA_RELANCAMENTO_SEG = 15


def _agendar_relancamento():
    """Deixa engatilhado um processo solto que sobe o bot de novo.

    O vigia mata o processo com os._exit, e sozinho isso é só metade: alguém
    precisa subir o bot de volta. Aqui o próprio bot engatilha essa volta antes
    de morrer, então funciona independente de como ele foi iniciado -- atalho,
    Agendador, linha de comando. O processo criado é DETACHED: não morre junto
    com este.

    Esta é a rede RÁPIDA, não a garantia. Ela cobre o caso comum -- o vigia
    percebeu, deu tempo de engatilhar -- e volta em ~15s. Não cobre, por
    definição, o caso em que o bot não chega a rodar código nenhum: processo
    morto no Gerenciador, falta de memória, máquina reiniciada, ou um
    travamento tão forte que nem a thread do vigia roda.

    A garantia de piso vem de fora do processo: a tarefa do Agendador do
    Windows tenta subir o bot a cada 5 min, e o padrão IgnoreNew faz ela
    ignorar o disparo quando já existe um rodando. As duas redes não precisam
    saber uma da outra -- se esta aqui já subiu o bot, o Agendador simplesmente
    pula a vez. Ver instalar_tarefa.ps1.

    Devolve em quantos segundos, aproximadamente, o bot volta. Quem chama usa
    isso para não prometer no alerta uma volta que não foi engatilhada.
    """
    if getattr(sys, 'frozen', False):
        alvo = f'"{sys.executable}"'
    else:
        alvo = f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    pasta = base_dir

    # ping no lugar de 'timeout': 'timeout' exige console e falha com
    # ERROR: Input redirection is not supported num processo destacado.
    espera = f"ping -n {ESPERA_RELANCAMENTO_SEG + 1} 127.0.0.1 >nul"
    comando = f'cmd /c {espera} & start "" /d "{pasta}" {alvo}'

    bandeiras = 0
    if os.name == "nt":
        bandeiras = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(comando, cwd=pasta, creationflags=bandeiras,
                     close_fds=True, shell=False)
    logger.critical(
        f"Relançamento engatilhado: sobe um bot em ~{ESPERA_RELANCAMENTO_SEG}s."
    )
    return ESPERA_RELANCAMENTO_SEG


def _encerrar_para_reiniciar(motivo_log: str, motivo_alerta: str,
                             silencioso: bool = False):
    """Engatilha a volta, avisa o grupo e mata o processo. Não retorna.

    Caminho único de encerramento do vigia, usado tanto pelo laço travado quanto
    pelos tetos de memória -- todos querem exatamente a mesma sequência, e
    duplicá-la já custou caro uma vez.

    `silencioso=True` faz o reinício acontecer sem mandar nada no Telegram. Não
    é para esconder problema: é para o reinício ROTINEIRO por RAM, que acontece
    a cada ~2,5h enquanto os vazamentos existirem. Dez mensagens por dia num
    grupo de trabalho, madrugada inclusive, não é informação -- é ruído, e ruído
    faz a operação parar de ler os alertas que importam. O log registra sempre;
    o grupo só é chamado quando o reinício foge do esperado.
    """
    logger.critical(motivo_log)

    # Engatilha a volta ANTES de avisar e morrer. None = não consegui engatilhar
    # nada. O alerta tem que dizer a verdade sobre isso: em 07/08/2026 ele
    # anunciou "Reiniciando sozinho" tendo delegado para um supervisor quebrado,
    # e o bot passou horas fora do ar sem ninguém desconfiar. Nada aqui pode
    # prometer uma volta que não foi engatilhada.
    segundos_ate_voltar = None
    try:
        segundos_ate_voltar = _agendar_relancamento()
    except Exception:
        logger.exception("Falha ao engatilhar o relançamento automático.")

    try:
        if silencioso:
            # Nada no grupo. O relançamento foi engatilhado (ou não) e isso está
            # no log -- e se NÃO foi, o silêncio cai por terra logo abaixo,
            # porque um reinício que não volta deixa de ser rotina.
            if segundos_ate_voltar is None:
                alerta_critico_telegram(
                    f"{motivo_alerta} NÃO consegui engatilhar minha volta. A "
                    "tarefa do Agendador tenta subir o bot em até 5 min -- "
                    "CONFIRA; se não voltou, suba na mão."
                )
        elif segundos_ate_voltar is not None:
            alerta_critico_telegram(
                f"{motivo_alerta} Reiniciando sozinho -- volta em "
                f"~{segundos_ate_voltar + 60}s."
            )
        else:
            # Não conseguimos engatilhar nada, mas a tarefa do Agendador ainda
            # tenta subir o bot na próxima janela de 5 min. Não prometemos: só
            # dizemos até quando esperar antes de agir.
            alerta_critico_telegram(
                f"{motivo_alerta} NÃO consegui engatilhar minha volta. A tarefa "
                "do Agendador tenta subir o bot em até 5 min -- CONFIRA; se não "
                "voltou, suba na mão."
            )
    except Exception:
        pass

    # os._exit porque sys.exit só encerraria esta thread; e um shutdown
    # "educado" pode ficar preso na mesma chamada do Playwright que travou.
    # O lock de instância única é liberado pelo SO ao morrer o processo.
    time.sleep(3)       # deixa o alerta sair antes de morrer
    os._exit(1)


def thread_vigia_monitor():
    """Encerra o processo quando o laço para de bater OU a memória estoura."""
    logger.info(
        f"Vigia do monitor iniciado (encerra o processo se o laço ficar "
        f"{TEMPO_MAX_SEM_BATIDA_SEG}s sem dar sinal, se a memória privada passar "
        f"de {LIMITE_RAM_BOT_MB} MB, ou se a RAM livre da máquina cair abaixo de "
        f"{PISO_RAM_LIVRE_MB} MB)."
    )
    # Duas leituras seguidas abaixo do piso para agir. Uma leitura solta pode
    # pegar o instante em que outra coisa da máquina (um backup, o próprio
    # Chromium abrindo) segurou memória por alguns segundos, e reiniciar o bot
    # por causa disso seria trocar um problema por outro.
    leituras_baixas = 0

    while True:
        time.sleep(30)

        # --- RAM livre da máquina ---
        # Vem PRIMEIRO porque é o teto que dispara de verdade: os dois
        # vazamentos (bot e Chromium) somam ~1,2 GB/h e a máquina acaba muito
        # antes de este processo sozinho chegar a LIMITE_RAM_BOT_MB.
        livre = _ram_livre_maquina_mb()
        if livre is not None and livre < PISO_RAM_LIVRE_MB:
            leituras_baixas += 1
            logger.warning(
                f"RAM livre da máquina em {livre} MB (piso {PISO_RAM_LIVRE_MB} MB) "
                f"-- leitura {leituras_baixas}/2."
            )
            if leituras_baixas >= 2:
                ram_bot = _ram_do_processo_mb()

                # Rotina ou sintoma? A conta é o intervalo desde o reinício
                # anterior. Espaçado = os vazamentos conhecidos fazendo o que
                # se espera deles, e o grupo não precisa saber. Apertado =
                # alguma coisa piorou, e aí o silêncio seria esconder.
                desde = _segundos_desde_ultima_reciclagem()
                rotina = desde is None or desde >= INTERVALO_MIN_ENTRE_RECICLAGENS_SEG
                _registrar_reciclagem()

                quando = ("primeiro registrado" if desde is None
                          else f"o anterior foi há {int(desde / 60)} min")
                _encerrar_para_reiniciar(
                    f"RAM livre da máquina em {livre} MB (piso {PISO_RAM_LIVRE_MB} MB), "
                    f"com o bot em {ram_bot} MB. Reinício por RAM ({quando}; "
                    f"{'rotina, sem alerta' if rotina else 'CEDO DEMAIS, avisando o grupo'}). "
                    "O Chromium morre junto e devolve a parte dele.",
                    f"🔴 Bot reiniciando por falta de RAM pela 2ª vez em "
                    f"{int((desde or 0) / 60)} min (livres: {livre} MB, bot: {ram_bot} MB). "
                    "O vazamento piorou -- vale olhar.",
                    silencioso=rotina,
                )
        else:
            leituras_baixas = 0

        # --- teto de memória ---
        # Vem ANTES da checagem de batida de propósito: com a memória estourada
        # o laço ainda está batendo, só que cada vez mais devagar por causa da
        # paginação. Pegando aqui, o reinício é controlado -- em vez de esperar
        # a máquina afundar até o laço travar de vez, 6h depois.
        ram = _ram_do_processo_mb()
        if ram is not None and ram >= LIMITE_RAM_BOT_MB:
            _encerrar_para_reiniciar(
                f"Memória privada em {ram} MB (limite {LIMITE_RAM_BOT_MB} MB). "
                "Encerrando o processo antes que a paginação derrube a máquina.",
                f"🟠 Bot em {ram / 1024:.1f} GB de memória, acima do teto de "
                f"{LIMITE_RAM_BOT_MB / 1024:.1f} GB.",
            )

        # --- laço travado ---
        idade = _idade_batida_monitor()
        if idade is None:
            continue        # o laço ainda não começou (login/carga inicial)
        if idade <= TEMPO_MAX_SEM_BATIDA_SEG:
            continue

        _encerrar_para_reiniciar(
            f"Laço de monitoramento travado: {int(idade)}s sem nenhuma volta "
            f"(limite {TEMPO_MAX_SEM_BATIDA_SEG}s). Encerrando o processo para "
            "subir tudo de novo, limpo.",
            f"🔴 Monitor travado há {int(idade / 60)} min sem varrer nada.",
        )


# Mesmo valor calculado no topo do arquivo, onde ele já precisou existir para o
# os.chdir. Reaproveitado em vez de recalculado para não haver duas versões da
# "raiz" podendo divergir numa refatoração futura.
base_dir = _RAIZ

chromium_path = os.path.join(base_dir, 'chromium', 'chrome-win64', 'chrome.exe')
if os.path.exists(chromium_path):
    os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'] = chromium_path
    logger.info(f"Usando Chromium em: {chromium_path}")
else:
    logger.warning(f"Chromium não encontrado em {chromium_path}. Usando busca automática do Playwright.")

SOM_ALERTA_GARANTIA = os.path.join(base_dir, "assets", "alerta_garantia.mp3")
_MCI_ALIAS_ALERTA = "alerta_garantia_som"

CAMINHO_LOGO_OPERACIONAL = os.path.join(base_dir, "assets", "logo_operacional.png")

# O logo tem duas versões. A original (logo_operacional.png) tem a palavra escrita
# em preto — serve para as imagens de fundo branco que o site gera, e some num
# painel escuro. A do site (logo.png, aqui copiada como logo_operacional_branco.png)
# é a versão branca, que é a certa para o Painel de TV. A lista é tentada em
# ordem, então uma instalação sem o arquivo novo ainda cai na original.
CAMINHOS_LOGO_PAINEL = [
    os.path.join(base_dir, "assets", "logo_operacional_branco.png"),
    os.path.join(base_dir, "assets", "logo_operacional.png"),
]


def tocar_som_alerta_garantia():
    """Toca o som de alerta na máquina local -- pensado para o Painel de TV
    (a TV física de produção tem caixa de som ligada nela; o alerta sonoro
    do site, via navegador, é uma coisa separada e continua funcionando
    independente disto)."""
    if not os.path.exists(SOM_ALERTA_GARANTIA):
        logger.warning(f"Arquivo de som '{os.path.basename(SOM_ALERTA_GARANTIA)}' não encontrado.")
        return

    if sys.platform.startswith('win'):
        def _tocar():
            try:
                import ctypes
                winmm = ctypes.windll.winmm
                winmm.mciSendStringW(f'close {_MCI_ALIAS_ALERTA}', None, 0, None)
                winmm.mciSendStringW(
                    f'open "{SOM_ALERTA_GARANTIA}" type mpegvideo alias {_MCI_ALIAS_ALERTA}',
                    None, 0, None
                )
                winmm.mciSendStringW(f'play {_MCI_ALIAS_ALERTA}', None, 0, None)
            except Exception as e:
                logger.error(f"Falha ao tocar som de alerta de garantia: {e}")
    else:
        # mpg123 troca o winmm/MCI do Windows -- toca o mp3 direto no sink de
        # audio padrão (ALSA/Pulse/Pipewire, o que estiver rodando na
        # máquina). "-q" só tira o textão de progresso do mpg123 do log.
        import shutil
        mpg123 = shutil.which("mpg123")

        def _tocar():
            if not mpg123:
                logger.warning(
                    "mpg123 não encontrado no PATH -- instale com "
                    "'sudo apt install mpg123' para o alerta sonoro funcionar."
                )
                return
            try:
                subprocess.run(
                    [mpg123, "-q", SOM_ALERTA_GARANTIA],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            except Exception as e:
                logger.error(f"Falha ao tocar som de alerta de garantia: {e}")

    threading.Thread(target=_tocar, daemon=True).start()


def _normalizar(texto):
    if texto is None:
        return ''
    texto = str(texto)
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').lower()


def unidade_bairro_permitido(unidade, bairro):
    unidade_norm = str(unidade or '').upper().strip()
    bairros_permitidos = UNIDADES_RESTRICAO_BAIRRO.get(unidade_norm)
    if not bairros_permitidos:
        return True

    bairro_norm = _normalizar(bairro) if bairro else ''
    if not bairro_norm:
        return False

    return any(_normalizar(b) in bairro_norm for b in bairros_permitidos)


def _extrair_telefone_de_dict_contato(d):
    chaves = {_normalizar(k): v for k, v in d.items()}
    numero = (chaves.get('numero') or chaves.get('telefone') or
              chaves.get('celular') or chaves.get('fone') or chaves.get('contato'))
    if numero is None:
        return None
    numero = str(numero)
    if '@' in numero:
        return None
    ddd = chaves.get('ddd')
    apenas_digitos = re.sub(r'\D', '', numero)
    if len(apenas_digitos) < 8:
        return None
    if ddd and not str(ddd) in numero:
        return f"({ddd}) {numero}"
    return numero


def _buscar_contatos_recursivo(obj, encontrados_contato):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if 'contato' in _normalizar(k):
                encontrados_contato.append(v)
            else:
                _buscar_contatos_recursivo(v, encontrados_contato)
    elif isinstance(obj, list):
        for item in obj:
            _buscar_contatos_recursivo(item, encontrados_contato)


def extrair_telefones_do_chamado(chamado):
    brutos = []
    _buscar_contatos_recursivo(chamado, brutos)
    telefones = []

    def coletar(valor):
        if isinstance(valor, dict):
            tel = _extrair_telefone_de_dict_contato(valor)
            if tel:
                telefones.append(tel)
            else:
                for v in valor.values():
                    coletar(v)
        elif isinstance(valor, list):
            for item in valor:
                coletar(item)
        elif isinstance(valor, str):
            partes = re.split(r'[,;/]', valor)
            for parte in partes:
                parte = parte.strip()
                if not parte or '@' in parte:
                    continue
                digitos = re.sub(r'\D', '', parte)
                if len(digitos) >= 8:
                    telefones.append(parte)

    for c in brutos:
        coletar(c)

    vistos = set()
    unicos = []
    for t in telefones:
        if t not in vistos:
            vistos.add(t)
            unicos.append(t)
    return unicos, brutos


def _encontrar_coluna(df, possibilidades):
    for p in possibilidades:
        for col in df.columns:
            if col.strip().lower() == p.strip().lower():
                return col
    return None


def _tratar_contrato_serie(serie):
    return serie.astype(str).str.split('.').str[0].str.strip()


def carregar_base_ofs():
    if not PANDAS_DISPONIVEL:
        logger.warning("pandas/openpyxl não instalados: verificação de garantia de Reparo desativada.")
        return None, None
    if not os.path.exists(BASE_OFS_ARQUIVO):
        logger.warning(f"Arquivo '{os.path.basename(BASE_OFS_ARQUIVO)}' não encontrado.")
        return None, None

    mtime_atual = os.path.getmtime(BASE_OFS_ARQUIVO)
    if _CACHE_BASE_OFS["df"] is not None and _CACHE_BASE_OFS["mtime"] == mtime_atual:
        return _CACHE_BASE_OFS["df"], _CACHE_BASE_OFS["colunas"]

    df = None
    for tentativa in range(3):
        try:
            df = pd.read_excel(BASE_OFS_ARQUIVO)
            break
        except PermissionError:
            logger.warning(f"Arquivo de base OFS travado. Tentativa {tentativa + 1}/3... Redirecionando nova leitura...")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Erro ao carregar Excel: {e}")
            return None, None

    if df is None:
        logger.error("Falha ao abrir a base OFS por restrições de permissão do arquivo no disco.")
        return None, None

    try:
        df.columns = df.columns.str.strip()

        col_contrato = _encontrar_coluna(df, ['Número do contrato', 'Contrato'])
        col_data = _encontrar_coluna(df, ['Data'])
        col_status = _encontrar_coluna(df, ['Status da Atividade'])
        col_tipo = _encontrar_coluna(df, ['Tipo de Atividade.1', 'Tipo de Atividade 2', 'Tipo de Atividade'])

        col_tecnico = _encontrar_coluna(
            df, ['Recurso', 'Técnico', 'Nome do Técnico', 'Nome', 'Resource', 'Técnico Executante']
        )
        if not col_tecnico:
            df['TÉCNICO'] = "Não Identificado"
            col_tecnico = 'TÉCNICO'
            logger.warning(
                "Base OFS: nenhuma coluna de técnico reconhecida. Usando 'Não Identificado' como fallback."
            )

        if not col_contrato or not col_data or not col_status or not col_tipo:
            logger.error("Base OFS: colunas esperadas não encontradas.")
            return None, None

        df[col_contrato] = _tratar_contrato_serie(df[col_contrato])
        df[col_data] = pd.to_datetime(df[col_data], errors='coerce', dayfirst=True)
        df = df[df[col_status].astype(str).str.contains('conclu', case=False, na=False)]

        colunas = {
            'contrato': col_contrato,
            'data': col_data,
            'status': col_status,
            'tipo': col_tipo,
            'tecnico': col_tecnico,
        }

        # Índice contrato -> [(data, tipo, técnico)], montado UMA vez por base.
        #
        # Antes, verificar_garantia_reparo fazia `df[df[contrato] == x]` para
        # CADA chamado: uma varredura booleana na base inteira por chamado, mais
        # um DataFrame temporário por chamado. Medido em 08/08/2026 com 4.401
        # linhas e 300 chamados: 776 ms pelo filtro contra 2 ms pelo índice —
        # 486x. O índice custa 10 ms para montar e ~1,5 MB de RAM, e devolveu o
        # mesmo veredito em 300 de 300 contratos.
        #
        # O ganho não é só CPU: os DataFrames temporários eram rotatividade de
        # memória a cada varredura, que é o que fragmenta o heap do processo.
        indice = {}
        for contrato, data, tipo, tecnico in zip(
            df[col_contrato], df[col_data], df[col_tipo], df[col_tecnico]
        ):
            indice.setdefault(str(contrato), []).append((data, tipo, tecnico))

        _CACHE_BASE_OFS["df"] = df
        _CACHE_BASE_OFS["mtime"] = mtime_atual
        _CACHE_BASE_OFS["colunas"] = colunas
        _CACHE_BASE_OFS["indice"] = indice
        logger.info(
            f"Base OFS carregada: {len(df)} atividades concluídas, "
            f"{len(indice)} contrato(s) no índice de garantia."
        )
        return df, colunas
    except Exception:
        logger.exception("Erro ao processar estrutura da Base OFS.")
        return None, None


def verificar_garantia_reparo(codigo_contrato, data_abertura):
    """O serviço anterior deste contrato ainda está no prazo de garantia?

    Usa o índice montado em carregar_base_ofs em vez de filtrar o DataFrame:
    esta função roda uma vez por chamado de reparo, em toda varredura, e o
    filtro por chamado era 486x mais caro (ver o comentário do índice).
    """
    df, colunas = carregar_base_ofs()
    if df is None or codigo_contrato in (None, 'N/D', ''):
        return False, None, None, None

    contrato_str = str(codigo_contrato).split('.')[0].strip()
    historico = (_CACHE_BASE_OFS.get("indice") or {}).get(contrato_str)
    if not historico:
        return False, None, None, None

    try:
        data_abertura_data = data_abertura.date() if hasattr(data_abertura, 'date') else data_abertura
    except Exception:
        return False, None, None, None

    for data_hist, tipo_bruto, tecnico_bruto in historico:
        if pd.isna(data_hist):
            continue
        dias = (data_abertura_data - data_hist.date()).days

        if dias < 0:
            continue

        tipo = str(tipo_bruto)
        tipo_norm = _normalizar(tipo)

        if pd.isna(tecnico_bruto):
            tecnico = "Não Identificado"
        else:
            tecnico = str(tecnico_bruto).strip() or "Não Identificado"

        if 'reparo' in tipo_norm and dias <= DIAS_GARANTIA_REPARO:
            return True, tipo, dias, tecnico
        if ('ativacao' in tipo_norm or 'mudanca' in tipo_norm) and dias <= DIAS_GARANTIA_ATIVACAO_MUDANCA:
            return True, tipo, dias, tecnico

    return False, None, None, None


def carregar_reparos_avaliados():
    if not os.path.exists(ARQUIVO_REPAROS_AVALIADOS):
        return {}
    try:
        with open(ARQUIVO_REPAROS_AVALIADOS, 'r', encoding='utf-8') as f:
            bruto = json.load(f)
    except Exception:
        logger.exception("Falha ao carregar reparos_avaliados.json — iniciando vazio.")
        return {}

    if isinstance(bruto, list):
        logger.info(
            f"reparos_avaliados.json em formato antigo (lista) — migrando "
            f"{len(bruto)} registro(s) como já tratados."
        )
        return {str(os_id): {'os_id': os_id, 'notificado': True} for os_id in bruto}

    if isinstance(bruto, dict):
        return bruto

    return {}


def salvar_reparos_avaliados(reparos_avaliados):
    reparos_filtrados = {}
    limite_data = datetime.now() - timedelta(days=45)
    
    for os_id, info in reparos_avaliados.items():
        try:
            data_abertura_str = info.get('data_abertura')
            if data_abertura_str:
                dt_abertura = datetime.fromisoformat(data_abertura_str)
                if dt_abertura >= limite_data:
                    reparos_filtrados[os_id] = info
            else:
                reparos_filtrados[os_id] = info
        except Exception:
            reparos_filtrados[os_id] = info

    try:
        salvar_json_atomico(ARQUIVO_REPAROS_AVALIADOS, reparos_filtrados, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Falha ao salvar reparos_avaliados.json.")


def notificar_garantia_telegram(unidade, contrato, nome_cliente, bairro, telefones_str, tecnico_ofs):
    corpo_mensagem = (
        f"GARANTIA: {html.escape(str(unidade))}\n"
        f"• Contrato: {html.escape(str(contrato))}\n"
        f"• Cliente: {html.escape(str(nome_cliente))}\n"
        f"• Bairro: {html.escape(str(bairro))}\n"
        f"• Telefone(s): {telefones_str}\n"
        f"• Técnico OFS: {html.escape(str(tecnico_ofs))}"
    )
    mensagem = f"<b>{corpo_mensagem}</b>"
    return enviar_alerta_telegram(mensagem, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Reincidência de improdutiva técnica
#
# Pergunta feita a cada entrante de CAPEX: este cliente já teve uma
# improdutiva nos últimos 30 dias? Se já teve, a visita que está entrando
# tende a ser a mesma história de novo -- e quem vai a campo merece saber
# disso antes de sair.
#
# Nem toda improdutiva conta. A régua não é a origem (TÉCNICA/COMERCIAL/
# CLIENTE) e sim se aquela visita perdida diz algo sobre a próxima:
# remarcação, cliente ausente, chuva e falta de material não dizem, e estão
# na MOTIVOS_SEM_ALERTA de improdutivas.py.
#
# A regra casa por contrato OU por nome (decisão de 13/08/2026; o endereço
# ficou de fora porque o número da rua só existe dentro de ordemServicos[] de
# um lado e grudado num texto único do outro). A classificação do motivo é a
# mesma tabela do antigo /improdutivas -- ver improdutivas.py.
# ---------------------------------------------------------------------------
def verificar_improdutiva_anterior(contrato, nome_cliente, data_abertura):
    """Devolve o registro da improdutiva anterior que merece aviso, ou None.

    Nunca levanta: uma base ausente, corrompida ou com coluna faltando não
    pode derrubar a notificação do entrante, que é o alerta principal. Falhar
    aqui custa um aviso a menos; falhar lá custa uma OS que ninguém viu.
    """
    if not PANDAS_DISPONIVEL:
        return None
    try:
        return improdutivas.consultar(
            BASE_IMPRODUTIVAS_ARQUIVO, contrato, nome_cliente, data_abertura
        )
    except Exception:
        logger.exception(
            "Falha ao consultar a base de improdutivas. O entrante segue "
            "sendo notificado normalmente, só sem este aviso."
        )
        return None


def notificar_improdutiva_telegram(unidade, contrato, nome_cliente, bairro,
                                   telefones_str, achado, agendamento=None):
    """Aviso de reincidência. Com `agendamento`, é uma REMARCAÇÃO.

    O título separa os dois casos de propósito: no grupo, "entrou uma O.S. de
    quem já deu improdutiva" e "remarcaram a O.S. que deu improdutiva" pedem
    reações diferentes, e quem lê decide pela primeira linha.
    """
    titulo = "REMARCADA APÓS IMPRODUTIVA" if agendamento else "IMPRODUTIVA ANTERIOR"
    linhas = [
        f"{titulo}: {html.escape(str(unidade))}",
        f"• Contrato: {html.escape(str(contrato))}",
        f"• Cliente: {html.escape(str(nome_cliente))}",
        f"• Bairro: {html.escape(str(bairro))}",
        f"• Telefone(s): {telefones_str}",
    ]
    if agendamento:
        linhas.append(f"• Novo agendamento: {html.escape(str(agendamento))}")

    quando = achado['data'].strftime('%d/%m')
    dias = achado['dias']
    quando_txt = "hoje" if dias == 0 else ("ontem" if dias == 1 else f"há {dias} dias")
    linhas.append(
        f"• Anterior: {html.escape(str(achado['motivo']))} em {quando} ({quando_txt})"
    )

    if achado['casou_por'] == 'contrato':
        linhas.append("• Casou por: contrato")
    else:
        # Nome é chave fraca: homônimo existe, e quem lê precisa saber que
        # este alerta pede uma conferida antes de virar decisão.
        linhas.append("• Casou por: NOME (confira — pode ser homônimo)")

    if achado.get('quantas', 1) > 1:
        linhas.append(f"• Improdutivas na janela: {achado['quantas']}")
    if achado.get('tecnico'):
        linhas.append(f"• Técnico anterior: {html.escape(str(achado['tecnico']))}")
    if achado.get('os'):
        linhas.append(f"• OS anterior: {html.escape(str(achado['os']))}")

    return enviar_alerta_telegram(f"<b>{chr(10).join(linhas)}</b>", parse_mode="HTML")


def _data_agendamento(valor):
    """Data do agendamento -> datetime, ou None.

    O CAMPO manda 'agendamentoData' como TEXTO 'AAAA-MM-DD' (conferido em
    13/08/2026 na amostra de produção) -- diferente de 'dataAbertura', que vem
    em epoch de milissegundos. Os dois formatos são aceitos aqui porque o
    campo não é lido por mais ninguém no sistema: se um dia a API trocar a
    representação, isto continua entendendo em vez de emudecer o aviso.
    """
    if not valor:
        return None
    texto = str(valor).strip()
    try:
        return datetime.strptime(texto[:10], '%Y-%m-%d')
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(float(texto) / 1000)
    except Exception:
        return None


def acompanhar_remarcacao(chamado, os_id, unidade, agendamentos_vistos):
    """Avisa quando remarcam uma O.S. que já foi improdutiva.

    Só notificar o entrante deixava um buraco: a O.S. entra, alguém vê o
    aviso, o técnico vai e volta improdutivo, remarcam -- e a remarcação, que
    é a reincidência de verdade, não gerava aviso nenhum, porque a O.S. já
    estava em os_notificadas e o ramo do entrante nunca mais roda para ela.

    A PRIMEIRA vez que uma O.S. aparece aqui nunca alerta, só registra. Isso
    vale tanto para a O.S. que acabou de entrar (o aviso de entrante já saiu
    logo acima, e dois seguidos seriam ruído) quanto para as que já estavam
    em campo quando esta função passou a existir -- sem essa regra, a
    primeira varredura depois de publicar despejaria uma remarcação falsa
    para cada O.S. aberta do litoral e do RJ de uma vez só.

    Devolve True se o registro mudou. NÃO grava em disco: quem chama junta as
    mudanças e grava uma vez por varredura. Gravar aqui custaria o arquivo
    inteiro reescrito a cada O.S. -- e é exatamente na primeira varredura,
    quando TODAS são novidade, que isso seria pior.
    """
    chave = str(os_id)
    agendamento = chamado.get('agendamentoData')
    agora = str(agendamento or '')

    anterior = agendamentos_vistos.get(chave)
    if anterior == agora:
        return False

    agendamentos_vistos[chave] = agora

    # Primeira vez que vemos esta O.S., ou remarcação para "sem data" (o
    # agendamento foi apagado): registra e cala.
    if anterior is None or not agora:
        return True

    contrato = chamado.get('codigoContrato', 'N/D')
    nome_cliente = chamado.get('nomeCliente', 'N/D')
    if isinstance(nome_cliente, str):
        nome_cliente = nome_cliente.strip() or 'N/D'

    # A janela de 30 dias conta a partir da data NOVA, não da abertura da
    # O.S.: o que interessa é se houve improdutiva perto da visita que vão
    # fazer agora. Uma O.S. aberta há dois meses e remarcada para amanhã
    # continua valendo a conferida.
    quando = _data_agendamento(agendamento) or datetime.now()

    achado = verificar_improdutiva_anterior(contrato, nome_cliente, quando)
    if not achado:
        return True

    cidade = (chamado.get('enderecoCidade') or '').strip() or unidade
    bairro = chamado.get('enderecoBairro', 'N/D')
    telefones, _ = extrair_telefones_do_chamado(chamado)
    telefones_str = ", ".join(telefones) if telefones else "N/D"

    data_nova = _data_agendamento(agendamento)
    agendamento_txt = data_nova.strftime('%d/%m') if data_nova else str(agendamento)

    if notificar_improdutiva_telegram(cidade, contrato, nome_cliente, bairro,
                                      telefones_str, achado,
                                      agendamento=agendamento_txt) is not None:
        registrar_improdutiva_notificada()
        logger.warning(
            f"REMARCADA APÓS IMPRODUTIVA: OS {os_id} ({nome_cliente}) "
            f"— novo agendamento {agendamento_txt}, casou por "
            f"{achado['casou_por']} com '{achado['motivo']}' de "
            f"{achado['dias']} dia(s) atrás."
        )
    return True


# ---------------------------------------------------------------------------
# Vigência dos reparos pendentes
#
# A varredura avalia cada reparo UMA vez: quem já está em reparos_avaliados é
# pulado no laço. Ou seja, ninguém volta a olhar para ele -- e o chamado sendo
# fechado no CAMPO não deixa rastro nenhum aqui. Como a reavaliação por Base
# OFS varre TODOS os pendentes, sem esta trava uma planilha nova dispara
# garantia de O.S. fechada dias atrás, indistinguível de uma legítima.
#
# ============ /improdutivas: a lista consolidada (13/08/2026) ============
# Os avisos de reincidência são EVENTOS: contam que algo aconteceu (entrou uma
# O.S., remarcaram uma O.S.) e somem no meio da conversa do grupo. Ninguém
# consegue rolar três dias de mensagens para montar roteiro.
#
# Esta lista responde outra pergunta: o que ESTÁ DE PÉ agora. Por isso ela não
# se monta a partir dos avisos já enviados -- lá dentro há O.S. que já foram
# resolvidas -- e sim varrendo os CAPEX abertos no CAMPO neste momento e
# perguntando de cada um, com a MESMA regra do aviso, se há improdutiva
# recente. Uma O.S. que fechou some da lista sozinha, sem ninguém dar baixa.
def montar_lista_improdutivas_abertas(lista_chamados):
    """{'litoral': [...], 'rj': [...], 'total': n, 'analisados': n}.

    Nunca levanta: é resposta a comando no grupo, e falhar aqui tem de virar
    um recado, não um traceback que só aparece no log.
    """
    por_regiao = {'litoral': [], 'rj': []}
    de_qual_regiao = {}
    for sigla in LITORAL_SP:
        de_qual_regiao[sigla] = 'litoral'
    for sigla in RJ:
        de_qual_regiao[sigla] = 'rj'

    analisados = 0
    for chamado in (lista_chamados or ()):
        if not isinstance(chamado, dict):
            continue

        fila = chamado.get('fila')
        if isinstance(fila, dict):
            codigo = fila.get('codigo')
        elif isinstance(fila, str):
            codigo = fila
        else:
            codigo = chamado.get('codigo')
        if codigo not in CODIGOS_ALVO:
            continue

        unidade = str(chamado.get('enderecoUnidade', '')).upper().strip()
        regiao = de_qual_regiao.get(unidade)
        if not regiao:
            continue

        analisados += 1
        contrato = chamado.get('codigoContrato', 'N/D')
        nome_cliente = chamado.get('nomeCliente') or 'N/D'
        if isinstance(nome_cliente, str):
            nome_cliente = nome_cliente.strip() or 'N/D'

        # A janela de 30 dias conta a partir do agendamento quando existe --
        # mesma escolha do aviso de remarcação: o que importa é se houve
        # improdutiva perto da visita que vão fazer, não perto da abertura.
        agendamento = chamado.get('agendamentoData')
        quando = _data_agendamento(agendamento)
        if quando is None:
            data_ms = chamado.get('dataAbertura')
            if data_ms:
                try:
                    quando = datetime.fromtimestamp(float(data_ms) / 1000)
                except Exception:
                    quando = None
        if quando is None:
            quando = datetime.now()

        try:
            achado = verificar_improdutiva_anterior(contrato, nome_cliente, quando)
        except Exception:
            logger.exception("Falha ao consultar improdutiva da OS %s.", chamado.get('id'))
            continue
        if not achado:
            continue

        data_agenda = _data_agendamento(agendamento)
        por_regiao[regiao].append({
            'os_id': chamado.get('id'),
            'unidade': unidade,
            'contrato': str(contrato),
            'cliente': str(nome_cliente),
            'bairro': str(chamado.get('enderecoBairro') or 'N/D'),
            'agendamento': data_agenda.strftime('%d/%m') if data_agenda else None,
            'ordem_agenda': data_agenda or datetime.max,
            'motivo': str(achado.get('motivo') or 'N/D'),
            'dias': achado.get('dias'),
            'casou_por': achado.get('casou_por'),
            'quantas': achado.get('quantas', 1),
        })

    for itens in por_regiao.values():
        # Quem tem visita marcada mais cedo primeiro; sem data vai para o fim,
        # que é a ordem em que a operação precisa agir.
        itens.sort(key=lambda i: (i['ordem_agenda'], i['unidade'], i['contrato']))

    return {
        'litoral': por_regiao['litoral'],
        'rj': por_regiao['rj'],
        'total': len(por_regiao['litoral']) + len(por_regiao['rj']),
        'analisados': analisados,
        'gerado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    }


def _bloco_improdutivas(titulo, itens, negrito):
    if not itens:
        return [f"{negrito}{titulo}{negrito}", "_nenhuma no momento_", ""]

    linhas = [f"{negrito}{titulo} ({len(itens)}){negrito}", ""]
    for item in itens:
        cabeca = f"• {item['contrato']} — {item['unidade']}"
        if item['agendamento']:
            cabeca += f" — agenda {item['agendamento']}"
        linhas.append(cabeca)
        linhas.append(f"   {item['cliente']} · {item['bairro']}")

        dias = item['dias']
        if dias == 0:
            quando_txt = "hoje"
        elif dias == 1:
            quando_txt = "ontem"
        else:
            quando_txt = f"há {dias} dias"
        detalhe = f"   improdutiva: {item['motivo']} ({quando_txt})"
        if item['quantas'] > 1:
            detalhe += f" · {item['quantas']} na janela"
        if item['casou_por'] != 'contrato':
            # Mesma ressalva do aviso: nome é chave fraca e quem lê tem de
            # saber disso ANTES de mandar técnico.
            detalhe += " · casou por NOME (confira)"
        linhas.append(detalhe)
        linhas.append("")
    return linhas


def montar_mensagem_improdutivas(dados, whatsapp=False):
    """O texto do /improdutivas. `whatsapp` troca o negrito de HTML para *."""
    negrito = "*" if whatsapp else ""

    if dados['total'] == 0:
        corpo = (
            f"Nenhum CAPEX em aberto com improdutiva recente "
            f"({dados['analisados']} analisado(s))."
        )
        return (f"{negrito}IMPRODUTIVAS REINCIDENTES EM ABERTO{negrito}\n"
                f"_{dados['gerado_em']}_\n\n{corpo}")

    linhas = [
        f"{negrito}IMPRODUTIVAS REINCIDENTES EM ABERTO{negrito}",
        f"_{dados['total']} de {dados['analisados']} CAPEX abertos · {dados['gerado_em']}_",
        "",
    ]
    linhas += _bloco_improdutivas("SUL RJ", dados['rj'], negrito)
    linhas += _bloco_improdutivas("LITORAL NORTE SP", dados['litoral'], negrito)
    return "\n".join(linhas).strip()


def responder_improdutivas(whatsapp=False):
    """Monta e envia a lista consolidada no grupo onde os comandos vivem."""
    enviar = enviar_alerta_whatsapp_grupo if whatsapp else enviar_alerta_telegram
    try:
        dados = montar_lista_improdutivas_abertas(obter_lista_chamados_atual())
    except Exception:
        logger.exception("Falha ao montar a lista consolidada de improdutivas.")
        enviar("⚠️ Não consegui montar a lista de improdutivas. Veja o log.")
        return

    texto = montar_mensagem_improdutivas(dados, whatsapp=whatsapp)

    # A mensagem cresce com a operação; quebrar por bloco evita esbarrar no
    # limite do Telegram (4096) sem cortar um item ao meio.
    limite = 3500
    if len(texto) <= limite:
        enviar(texto)
        return
    atual = []
    tamanho = 0
    for linha in texto.split("\n"):
        if tamanho + len(linha) + 1 > limite and atual:
            enviar("\n".join(atual))
            time.sleep(1.0)
            atual, tamanho = [], 0
        atual.append(linha)
        tamanho += len(linha) + 1
    if atual:
        enviar("\n".join(atual))


# A trava é o conjunto de O.S. de reparo vistas ABERTAS na última varredura
# completa. Fica só em memória de propósito: é barato, sempre fresco (a
# varredura roda a cada volta) e não acrescenta escrita a um arquivo que já
# passa de 5 MB.
_REPAROS_ABERTOS = {"os": set(), "carimbo": None}
_TRAVA_REPAROS_ABERTOS = threading.Lock()

# Pendente que não aparece como aberto há mais de N dias é lixo: some do
# arquivo. Sem isto a lista só cresce -- medida em dois dias seguidos, passou
# de 2.520 para 4.660 registros -- e cada garantia reescreve tudo isso em
# disco, porque a gravação é do arquivo inteiro.
DIAS_PODA_REPARO_PENDENTE = 7


def publicar_reparos_abertos(os_ids):
    """Chamado ao fim de uma varredura COMPLETA, com as O.S. de reparo vistas."""
    with _TRAVA_REPAROS_ABERTOS:
        _REPAROS_ABERTOS["os"] = set(os_ids)
        _REPAROS_ABERTOS["carimbo"] = datetime.now()


def reparos_abertos_conhecidos():
    """(conjunto, carimbo). carimbo=None => nenhuma varredura completa ainda."""
    with _TRAVA_REPAROS_ABERTOS:
        return set(_REPAROS_ABERTOS["os"]), _REPAROS_ABERTOS["carimbo"]


# ============ LISTA DE GARANTIAS PARA OS GRUPOS REGIONAIS (13/08/2026) ============
# A lista de hora em hora nasce do cruzamento de duas coisas que já existiam:
# as garantias notificadas (reparos_avaliados.json) e as O.S. de reparo que a
# última varredura viu abertas no CAMPO (logo acima). Ver garantias_lista.py.
def preencher_tecnico_ofs_faltante(reparos_avaliados):
    """Grava o técnico OFS nas garantias notificadas que não o têm.

    O técnico é fato FIXO do serviço original: quem executou o reparo/ativação
    que gerou a garantia já executou, e isso não muda mais. Então ele é
    procurado UMA vez e gravado -- nunca recalculado na hora de montar a lista.

    Recalcular seria pior do que inútil. `verificar_garantia_reparo` devolve o
    PRIMEIRO serviço do contrato que cai na janela, e a Base OFS é substituída
    pelo site a cada envio. Com outra base, outro serviço do mesmo contrato
    pode casar primeiro -- e a linha sairia com o `dias_aging` gravado de um
    serviço e o técnico de outro. Duas verdades na mesma linha, sem nada
    denunciando.

    Daí a conferência abaixo: o técnico só é aceito quando o serviço
    reencontrado é comprovadamente o MESMO que gerou a garantia (mesmo tipo e
    mesmo aging). Não batendo, fica sem técnico -- `N/D` é honesto, técnico
    errado não.

    Só serve às garantias notificadas antes de 13/08/2026, quando o campo
    ainda não era gravado. Devolve quantos registros mudaram.
    """
    preenchidos = 0
    for chave, info in (reparos_avaliados or {}).items():
        if not isinstance(info, dict) or not info.get('notificado'):
            continue
        if info.get('tecnico_ofs'):
            continue

        contrato = info.get('codigo_contrato')
        if not contrato or contrato == 'N/D':
            continue

        quando = None
        if info.get('data_abertura'):
            try:
                quando = datetime.fromisoformat(str(info['data_abertura']))
            except Exception:
                quando = None
        if quando is None:
            # Sem a data de abertura não dá para reencontrar o serviço com
            # segurança -- e chutar datetime.now() casaria qualquer coisa.
            continue

        try:
            eh_garantia, tipo, aging, tecnico = verificar_garantia_reparo(contrato, quando)
        except Exception:
            logger.debug("Falha ao reencontrar o técnico OFS do contrato %s.",
                         contrato, exc_info=True)
            continue

        if not (eh_garantia and tecnico):
            continue
        if info.get('dias_aging') is not None and aging != info.get('dias_aging'):
            continue
        if info.get('tipo_anterior') and str(tipo) != str(info.get('tipo_anterior')):
            continue

        info['tecnico_ofs'] = tecnico
        preenchidos += 1

    if preenchidos:
        logger.info(
            "Técnico OFS preenchido em %d garantia(s) antiga(s) e gravado em disco.",
            preenchidos
        )
    return preenchidos


def estado_para_lista_garantias():
    """(reparos_avaliados, O.S. abertas, carimbo) para montar a lista.

    Lê o JSON do disco em vez de compartilhar o dict do laço de varredura.
    Pode: toda garantia é gravada NA HORA em que é notificada (ver o comentário
    do salvar_reparos_avaliados no laço), então o arquivo já contém tudo o que
    a lista precisa. E assim nenhuma outra thread encosta no dict que a
    varredura está usando.
    """
    abertas, carimbo = reparos_abertos_conhecidos()
    reparos = carregar_reparos_avaliados()

    # Preenche o que falta e GRAVA. Roda aqui, e não no arranque, porque
    # depende da Base OFS já estar carregada; e é barato repetir, já que a
    # primeira passagem resolve tudo o que era resolvível e as seguintes só
    # percorrem um dicionário sem consultar nada.
    if preencher_tecnico_ofs_faltante(reparos):
        salvar_reparos_avaliados(reparos)

    return reparos, abertas, carimbo


def gerar_e_enviar_garantias_agora(regioes=None):
    """Monta e manda a lista para os grupos regionais. Usado pelo agendador e
    pelo comando /garantias."""
    reparos, abertas, carimbo = estado_para_lista_garantias()
    return garantias_envio.gerar_e_enviar(
        reparos, abertas,
        carimbo_varredura=carimbo,
        regioes=regioes,
    )


def podar_reparos_antigos(reparos_avaliados):
    """Remove pendentes parados há muito tempo. Devolve quantos saíram.

    Só mexe em quem NÃO foi notificado: o registro de uma garantia já enviada
    tem que viver para sempre, senão a O.S. volta a ser avaliada do zero e a
    operação recebe a mesma mensagem duas vezes.

    Podar um chamado que por acaso ainda esteja aberto não faz mal: a varredura
    seguinte o encontra de novo, reavalia e -- aí sim, com ele vivo -- notifica.
    """
    limite = datetime.now() - timedelta(days=DIAS_PODA_REPARO_PENDENTE)
    a_remover = []
    for chave, info in reparos_avaliados.items():
        if info.get('notificado', True):
            continue
        # visto_em é a última data em que a varredura o encontrou aberto;
        # quem nunca foi carimbado cai na data de abertura.
        referencia = info.get('visto_em') or info.get('data_abertura')
        if not referencia:
            continue
        try:
            quando = datetime.fromisoformat(referencia)
        except Exception:
            continue
        if quando < limite:
            a_remover.append(chave)

    for chave in a_remover:
        reparos_avaliados.pop(chave, None)
    if a_remover:
        logger.info(
            f"Poda de reparos pendentes: {len(a_remover)} registro(s) sem sinal de "
            f"vida há mais de {DIAS_PODA_REPARO_PENDENTE} dias foram removidos "
            f"({len(reparos_avaliados)} restantes)."
        )
    return len(a_remover)


def reavaliar_reparos_pendentes(reparos_avaliados):
    """Reavalia pendentes contra a Base OFS nova. Devolve False se não deu para
    rodar agora -- nesse caso quem chamou NÃO pode dar a Base como processada."""
    os_abertas, carimbo = reparos_abertos_conhecidos()
    if carimbo is None:
        logger.warning(
            "Reavaliação da Base OFS adiada: nenhuma varredura completa desde que "
            "o bot subiu, então não há como saber quais reparos ainda estão "
            "abertos. Roda assim que a primeira varredura terminar."
        )
        return False

    pendentes = [
        info for info in reparos_avaliados.values()
        if not info.get('notificado', True) and info.get('codigo_contrato') and info.get('data_abertura')
    ]
    if not pendentes:
        logger.info("Reavaliação da Base OFS: nenhum reparo pendente no momento.")
        if podar_reparos_antigos(reparos_avaliados):
            salvar_reparos_avaliados(reparos_avaliados)
        return True

    total_pendentes = len(pendentes)
    pendentes = [i for i in pendentes if str(i.get('os_id')) in os_abertas]
    ignorados = total_pendentes - len(pendentes)
    if ignorados:
        logger.info(
            f"Reavaliação da Base OFS: {ignorados} pendente(s) fora da lista de "
            f"chamados abertos (fechados no campo) — não serão notificados."
        )
    if not pendentes:
        # Caso comum daqui para a frente: a Base muda, mas os pendentes que
        # casariam já foram fechados no campo. Podar AQUI é o que mantém o
        # arquivo do tamanho da realidade em vez de só crescer.
        logger.info("Reavaliação da Base OFS: nenhum pendente ainda aberto.")
        if podar_reparos_antigos(reparos_avaliados):
            salvar_reparos_avaliados(reparos_avaliados)
        return True

    logger.info(
        f"Reavaliando {len(pendentes)} reparo(s) pendente(s) contra a Base OFS atualizada..."
    )

    for info in pendentes:
        # Mesmo motivo do laço de chamados: isto roda DENTRO do laço principal e
        # já chegou a ter 18.676 pendentes. Cada um que vira garantia manda
        # mensagem no Telegram, então o total pode passar de meia hora.
        registrar_batida_monitor()
        os_id = info.get('os_id')
        try:
            try:
                data_abertura_dt = datetime.fromisoformat(info['data_abertura'])
            except Exception:
                continue

            eh_garantia, tipo_anterior, dias_aging, tecnico_ofs = verificar_garantia_reparo(
                info.get('codigo_contrato'), data_abertura_dt
            )
            if not eh_garantia:
                continue

            tocar_som_alerta_garantia()

            unidade = info.get('unidade', 'N/D')
            contrato = info.get('codigo_contrato', 'N/D')
            nome_cliente = info.get('nome_cliente', 'N/D')
            bairro = info.get('bairro', 'N/D')
            telefones_str = info.get('telefones', 'N/D')
            if not tecnico_ofs:
                tecnico_ofs = 'N/D'

            message_id = notificar_garantia_telegram(
                unidade, contrato, nome_cliente, bairro, telefones_str, tecnico_ofs
            )
            if message_id is not None:
                fixar_mensagem_telegram(message_id)
                info['notificado'] = True
                info['tipo_anterior'] = tipo_anterior
                info['dias_aging'] = dias_aging
                # Guardado desde 13/08/2026 para a lista de garantias que vai
                # aos grupos de hora em hora. Antes ele era usado na mensagem e
                # jogado fora -- e reencontrá-lo depois exige a Base OFS do dia
                # em que a garantia nasceu, que já não é a que está em disco.
                info['tecnico_ofs'] = tecnico_ofs
                salvar_reparos_avaliados(reparos_avaliados)
                logger.info(
                    f"Notificada (GARANTIA - reavaliação): OS {os_id} - Reparo, serviço anterior "
                    f"'{tipo_anterior}' concluído há {dias_aging} dias ({nome_cliente}) "
                    f"- Técnico OFS: {tecnico_ofs}"
                )

                if TV_ATIVA:
                    FILA_EVENTOS_TV.put({
                        'tipo': 'garantia',
                        'unidade': unidade,
                        'contrato': contrato,
                        'cliente': nome_cliente,
                        'bairro': bairro,
                        'telefones': telefones_str,
                        'tecnico_ofs': tecnico_ofs,
                        'tipo_anterior': tipo_anterior,
                        'dias_aging': dias_aging,
                        'timestamp': datetime.now().strftime('%H:%M:%S'),
                    })
            else:
                logger.error(
                    f"Falha ao notificar garantia (reavaliação) OS {os_id} – tentará de novo no próximo ciclo."
                )
            time.sleep(0.2)
        except Exception:
            logger.exception(
                f"Falha ao reavaliar reparo OS {os_id} contra a Base OFS atualizada."
            )

    if podar_reparos_antigos(reparos_avaliados):
        salvar_reparos_avaliados(reparos_avaliados)
    return True


def carregar_os_notificadas():
    if os.path.exists(ARQUIVO_NOTIFICADAS):
        try:
            with open(ARQUIVO_NOTIFICADAS, 'r') as f:
                return set(json.load(f))
        except Exception:
            logger.exception("Não foi possível carregar os_notificadas.json, iniciando novo set.")
    return set()


def salvar_os_notificadas(os_ids):
    ids_salvaveis = list(os_ids)[-5000:]
    try:
        salvar_json_atomico(ARQUIVO_NOTIFICADAS, ids_salvaveis)
    except Exception:
        logger.exception("Não foi possível persistir os_notificadas no arquivo local.")


def carregar_agendamentos_vistos():
    """{os_id: último agendamento visto}. Chave e valor viram texto: o JSON
    não guarda inteiro como chave, e comparar tipos diferentes daria remarcação
    falsa a cada reinício."""
    if os.path.exists(ARQUIVO_AGENDAMENTOS_VISTOS):
        try:
            with open(ARQUIVO_AGENDAMENTOS_VISTOS, 'r') as f:
                dados = json.load(f)
            if isinstance(dados, dict):
                return {str(k): str(v) for k, v in dados.items()}
        except Exception:
            logger.exception(
                "Não foi possível carregar agendamentos_vistos.json, iniciando vazio.")
    return {}


def salvar_agendamentos_vistos(vistos):
    # Mesmo teto de os_notificadas, e pela mesma razão: o arquivo cresce a cada
    # O.S. nova e nada o poda. Corta pelas mais antigas (dict preserva ordem de
    # inserção), então o que sobrevive é o que ainda está em campo.
    if len(vistos) > 5000:
        for chave in list(vistos)[:len(vistos) - 5000]:
            vistos.pop(chave, None)
    try:
        salvar_json_atomico(ARQUIVO_AGENDAMENTOS_VISTOS, vistos)
    except Exception:
        logger.exception("Não foi possível persistir agendamentos_vistos no arquivo local.")


# =========================================
# FUNÇÕES DO auto.provedor.example – EXTRAÇÃO VIA TEXTO
# =========================================
PROVEDOR_AUTO_BASE_URL = "https://auto.provedor.example"
PROVEDOR_AUTO_SESSAO_TTL_SEG = 20 * 60

# Circuit breaker: se detectarmos falha de conexão (timeout/rede), paramos de tentar
# por um tempo em vez de insistir e prolongar um possível bloqueio de IP/WAF no Provedor.
PROVEDOR_AUTO_BLOQUEIO_PAUSA_SEG = 15 * 60
_provedor_auto_bloqueado_ate = 0
_provedor_auto_bloqueio_lock = threading.Lock()

_provedor_auto_sessao = None
_provedor_auto_sessao_ts = 0
_provedor_auto_lock = threading.Lock()


def _provedor_auto_registrar_bloqueio():
    """Marca o Provedor como temporariamente indisponível, evitando novas tentativas por um tempo."""
    global _provedor_auto_bloqueado_ate
    with _provedor_auto_bloqueio_lock:
        _provedor_auto_bloqueado_ate = time.time() + PROVEDOR_AUTO_BLOQUEIO_PAUSA_SEG
    logger.warning(
        f"Provedor: possível bloqueio de rede/IP detectado. Pausando novas tentativas por "
        f"{PROVEDOR_AUTO_BLOQUEIO_PAUSA_SEG // 60} minutos."
    )


def _provedor_auto_em_pausa():
    with _provedor_auto_bloqueio_lock:
        restante = _provedor_auto_bloqueado_ate - time.time()
    return restante if restante > 0 else 0


def _provedor_auto_login():
    if not PROVEDOR_AUTO_EMAIL or not PROVEDOR_AUTO_SENHA:
        raise RuntimeError("Credenciais do Provedor não configuradas.")
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": "Mozilla/5.0 (compatible; BotAIR/1.0)"})
    resp_login = sessao.get(f"{PROVEDOR_AUTO_BASE_URL}/users/sign_in", timeout=15)
    resp_login.raise_for_status()
    m = re.search(r'name="authenticity_token"\s+value="([^"]+)"', resp_login.text)
    if not m:
        raise RuntimeError("authenticity_token não encontrado.")
    token = m.group(1)
    payload = {
        "utf8": "✓", "authenticity_token": token,
        "user[email]": PROVEDOR_AUTO_EMAIL, "user[password]": PROVEDOR_AUTO_SENHA,
        "user[remember_me]": "1", "commit": "Entrar",
    }
    resp_post = sessao.post(f"{PROVEDOR_AUTO_BASE_URL}/users/sign_in", data=payload, timeout=15, allow_redirects=True)
    resp_post.raise_for_status()
    if "/users/sign_in" in resp_post.url:
        raise RuntimeError("Login no Provedor falhou.")
    logger.info("Login no auto.provedor.example realizado com sucesso.")
    return sessao


def _invalidar_sessao_provedor_auto():
    global _provedor_auto_sessao, _provedor_auto_sessao_ts
    with _provedor_auto_lock:
        _provedor_auto_sessao = None
        _provedor_auto_sessao_ts = 0


def _obter_sessao_provedor_auto():
    global _provedor_auto_sessao, _provedor_auto_sessao_ts
    with _provedor_auto_lock:
        if _provedor_auto_sessao is not None and (time.time() - _provedor_auto_sessao_ts) < PROVEDOR_AUTO_SESSAO_TTL_SEG:
            return _provedor_auto_sessao
        sessao = _provedor_auto_login()
        _provedor_auto_sessao = sessao
        _provedor_auto_sessao_ts = time.time()
        return sessao


def _extrair_int6_id(html_contrato):
    m = re.search(r'Int6\s*ID:\s*</b>\s*(\d+)', html_contrato)
    return m.group(1) if m else None


def consultar_dbm_onu_provedor(contrato, _tentativa=1):
    """Extrai status de sinal (LOS, Rx ONU, Rx OLT) do texto da página + leitura histórica."""
    restante_pausa = _provedor_auto_em_pausa()
    if restante_pausa:
        return None, f"Provedor temporariamente indisponível (possível bloqueio de rede), tente novamente em {int(restante_pausa // 60) + 1} min"

    try:
        sessao = _obter_sessao_provedor_auto()
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
        _provedor_auto_registrar_bloqueio()
        return None, f"possível bloqueio de rede/IP no Provedor ({e})"
    except Exception as e:
        return None, f"falha ao autenticar no Provedor ({e})"

    try:
        resp_contrato = sessao.get(f"{PROVEDOR_AUTO_BASE_URL}/contracted_services/{contrato}", timeout=15, allow_redirects=True)
        if "/users/sign_in" in resp_contrato.url:
            if _tentativa <= 1:
                _invalidar_sessao_provedor_auto()
                return consultar_dbm_onu_provedor(contrato, _tentativa=2)
            return None, "sessão expirada"
        if resp_contrato.status_code == 404:
            return None, "contrato não encontrado"
        resp_contrato.raise_for_status()

        html = resp_contrato.content.decode('utf-8', errors='ignore')

        rx_onu_match = re.search(r'Atenua[çc][ãa]o\s+Rx\s+ONU\s+([\d.-]+|LOS|N[ãa]o\s+Dispon[íi]vel)', html, re.IGNORECASE)
        rx_olt_match = re.search(r'Atenua[çc][ãa]o\s+Rx\s+OLT\s+([\d.-]+|LOS|N[ãa]o\s+Dispon[íi]vel)', html, re.IGNORECASE)

        los = False
        rx_power_onu = None
        rx_power_olt = None

        if rx_onu_match:
            valor_onu = _normalizar(rx_onu_match.group(1).strip())
            if "los" in valor_onu:
                los = True
                rx_power_onu = None
            elif "nao disponivel" in valor_onu or "n/d" in valor_onu:
                rx_power_onu = None
            else:
                try:
                    rx_power_onu = float(valor_onu.replace(',', '.'))
                    los = False
                except ValueError:
                    pass

        if rx_olt_match:
            valor_olt = _normalizar(rx_olt_match.group(1).strip())
            if "los" in valor_olt:
                rx_power_olt = None
            elif "nao disponivel" in valor_olt or "n/d" in valor_olt:
                rx_power_olt = None
            else:
                try:
                    rx_power_olt = float(valor_olt.replace(',', '.'))
                except ValueError:
                    pass

        int6_id = _extrair_int6_id(html)
        dbm_onu = None
        dbm_olt = None
        if int6_id:
            try:
                resp_power = sessao.get(f"{PROVEDOR_AUTO_BASE_URL}/gpon_clients/{int6_id}/last_power_readings", timeout=15)
                resp_power.raise_for_status()
                leituras = resp_power.json()
                if leituras:
                    ultima = leituras[0]
                    dbm_onu = ultima.get('read_power')
                    dbm_olt = ultima.get('olt_read_power')
            except Exception:
                pass

        return {
            'los': los,
            'rx_power_onu': rx_power_onu,
            'rx_power_olt': rx_power_olt,
            'dbm_onu': dbm_onu,
            'dbm_olt': dbm_olt,
        }, None

    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
        _provedor_auto_registrar_bloqueio()
        logger.warning(f"Provedor: erro de conexão ao consultar contrato {contrato}: {e}")
        return None, f"possível bloqueio de rede/IP no Provedor ({e})"
    except Exception as e:
        logger.exception("Erro inesperado no Provedor")
        return None, f"erro inesperado ({e})"


# =========================================
# FUNÇÕES DO AUTENTICADOR
# =========================================

ESPERA_MAX_CSV_AUTENTICADOR_SEG = 5
INTERVALO_LEITURA_CSV_AUTENTICADOR_SEG = 0.8
TENTATIVAS_CONSULTA_AUTENTICADOR = 3


def _ler_tabela_autenticador(sessao):
    """Uma leitura do CSV do Autenticador, já normalizada. Devolve (df, erro)."""
    res = sessao.get(AUTENTICADOR_URL_LER_CSV, verify=False, timeout=(5, 35))
    html_resp = res.text

    if '<table>' not in html_resp:
        return None, "Resposta do servidor não contém tabela (verifique se a VPN está conectada)."

    try:
        tabelas = pd.read_html(io.StringIO(html_resp))
    except ImportError as e:
        return None, f"Biblioteca de parse HTML ausente (ex: lxml): {e}"
    except ValueError:
        # tabela presente mas ainda sem linha nenhuma: o processamento não
        # terminou de escrever. Não é erro, é "espere mais um pouco".
        return None, None

    if not tabelas:
        return None, None

    df = tabelas[0]
    df.columns = [c.lower().strip() for c in df.columns]

    col_map = {
        'contrato': 'CONTRATO', 'username': 'USERNAME', 'acctstarttime': 'INÍCIO',
        'acctstoptime': 'FIM', 'circuitid': 'CIRCUITO', 'callingstationid': 'MAC',
        'trafego': 'TRÁFEGO', 'servidor': 'SERVIDOR'
    }
    df.rename(columns=col_map, inplace=True)
    for col in ['CONTRATO', 'USERNAME', 'INÍCIO', 'FIM', 'CIRCUITO', 'MAC', 'TRÁFEGO', 'SERVIDOR']:
        if col not in df.columns:
            df[col] = ''

    df['CONTRATO'] = df['CONTRATO'].apply(
        lambda x: str(int(x)) if pd.notna(x) and str(x).replace('.0', '').isdigit() else str(x)
    )
    return df, None


def _esperar_csv_autenticador(sessao, lista_contratos):
    """Uma tentativa: espera o CSV virar o resultado DESTA consulta.

    Devolve (df, erro, refazer). `refazer=True` significa que o arquivo está
    com resultado de outra pessoa -- reler não adianta, é preciso refazer o
    pedido.
    """
    pedidos = {str(c).strip() for c in lista_contratos}
    limite = time.time() + ESPERA_MAX_CSV_AUTENTICADOR_SEG
    ultima_assinatura = None
    ultimo_df = None
    erro_ultimo = None

    while True:
        df, erro = _ler_tabela_autenticador(sessao)
        if erro:
            erro_ultimo = erro
        elif df is not None:
            encontrados = {str(c).strip() for c in df['CONTRATO'].tolist()}
            intrusos = encontrados - pedidos

            if intrusos:
                # Contrato que ninguém aqui pediu só pode ter vindo de outra
                # consulta: o arquivo do servidor é global. Nosso pedido foi
                # atropelado, e insistir na leitura não traz ele de volta.
                return None, None, True

            if pedidos.issubset(encontrados):
                return df, None, False       # tudo que foi pedido já está lá

            # Tabela VAZIA não é resposta: é o instante em que o servidor está
            # reescrevendo o arquivo. Guardá-la como resultado aceitável fazia
            # a retentativa ser engolida e todo contrato virar NÃO LOCALIZADO.
            if encontrados:
                ultimo_df = df
                assinatura = (len(df), tuple(sorted(encontrados)))
                if assinatura == ultima_assinatura:
                    return df, None, False   # estabilizou faltando alguém
                ultima_assinatura = assinatura

        if time.time() >= limite:
            break
        time.sleep(INTERVALO_LEITURA_CSV_AUTENTICADOR_SEG)

    return ultimo_df, erro_ultimo, ultimo_df is None


def _consultar_autenticador_com_retentativa(sessao, lista_contratos):
    """Faz o ciclo pedir->processar->ler, repetindo quando o resultado é de
    outra pessoa.

    O CSV de resultado do Autenticador é UM arquivo só no servidor, compartilhado
    por todos os usuários daquela ferramenta. Quando alguém dispara uma
    consulta grande, ela sobrescreve a nossa: a leitura devolve centenas de
    contratos que não pedimos e, logo depois, um arquivo vazio enquanto está
    sendo reescrito. Sem tratar isso, todo contrato "some" e vira NÃO
    LOCALIZADO -- indistinguível de cliente sem sessão. É a causa do status
    oscilar entre ONLINE e NÃO LOCALIZADO sem nada mudar na rede.

    Reler não resolve, porque o pedido em si foi atropelado. O que resolve é
    refazer o ciclo inteiro.
    """
    payload = {"contratos": "\n".join(lista_contratos)}
    erro_ultimo = None

    for tentativa in range(1, TENTATIVAS_CONSULTA_AUTENTICADOR + 1):
        sessao.post(AUTENTICADOR_URL_SAVE, data=payload, verify=False, timeout=(5, 35))
        sessao.get(AUTENTICADOR_URL_PROCESSA, verify=False, timeout=(5, 35))

        df, erro, refazer = _esperar_csv_autenticador(sessao, lista_contratos)
        if erro:
            erro_ultimo = erro
        if not refazer and df is not None:
            return df, None
        if tentativa < TENTATIVAS_CONSULTA_AUTENTICADOR:
            logger.info(
                f"Autenticador: resultado da consulta veio de outra origem "
                f"(tentativa {tentativa}/{TENTATIVAS_CONSULTA_AUTENTICADOR}); refazendo o pedido."
            )
            time.sleep(1.5)

    logger.warning(
        f"Autenticador: {TENTATIVAS_CONSULTA_AUTENTICADOR} tentativas e o resultado continuou "
        "sendo sobrescrito por outra consulta."
    )
    return None, erro_ultimo or "O Autenticador não devolveu o resultado desta consulta."


def consultar_autenticador_status(lista_contratos):
    """
    Consulta o status das sessões (online/offline) dos contratos informados
    diretamente no Autenticador.
    """
    if not PANDAS_DISPONIVEL:
        return None, "Dependência 'pandas' não está instalada nesta máquina."

    try:
        sessao = requests.Session()
        df, erro = _consultar_autenticador_com_retentativa(sessao, lista_contratos)
        if erro or df is None:
            return pd.DataFrame(), erro or "O Autenticador não devolveu resultado."

        status_contratos = {}
        for contrato in lista_contratos:
            contrato_str = str(contrato).strip()
            df_contrato = df[df['CONTRATO'] == contrato_str]
            if df_contrato.empty:
                status_contratos[contrato_str] = 'NÃO LOCALIZADO'
            else:
                tem_ativo = any(pd.isna(val) or str(val).strip() == '' for val in df_contrato['FIM'])
                status_contratos[contrato_str] = 'ONLINE' if tem_ativo else 'OFFLINE'

        linhas_resumo = []
        for contrato in lista_contratos:
            c_str = str(contrato).strip()
            status = status_contratos.get(c_str, 'ERRO')
            df_c = df[df['CONTRATO'] == c_str]
            if not df_c.empty:
                ativo = df_c[pd.isna(df_c['FIM']) | (df_c['FIM'].astype(str).str.strip() == '')]
                info = ativo.iloc[0] if not ativo.empty else df_c.iloc[0]
                linhas_resumo.append({
                    'CONTRATO': c_str, 'STATUS': status,
                    'USERNAME': info.get('USERNAME', ''),
                    'INÍCIO': info.get('INÍCIO', '') if status == 'ONLINE' else '',
                    'FIM': info.get('FIM', '') if status != 'ONLINE' else '',
                    'CIRCUITO': info.get('CIRCUITO', ''),
                    'MAC': info.get('MAC', ''),
                    'TRÁFEGO': info.get('TRÁFEGO', '') if status == 'ONLINE' else '',
                    'SERVIDOR': info.get('SERVIDOR', '')
                })
            else:
                linhas_resumo.append({
                    'CONTRATO': c_str, 'STATUS': status, 'USERNAME': '', 'INÍCIO': '', 'FIM': '',
                    'CIRCUITO': '', 'MAC': '', 'TRÁFEGO': '', 'SERVIDOR': ''
                })

        return pd.DataFrame(linhas_resumo), None

    except Exception as e:
        logger.exception("Erro na consulta ao Autenticador.")
        return pd.DataFrame(), f"ERRO NA CONSULTA: {e}"


def _escapar_markdown_legacy(valor):
    """Escapa caracteres especiais do Markdown legacy do Telegram (_ * ` [)."""
    texto = '' if valor is None else str(valor).strip()
    if not texto or texto.lower() == 'nan':
        return '-'
    for ch in ('_', '*', '`', '['):
        texto = texto.replace(ch, '\\' + ch)
    return texto


def _emoji_status_autenticador(status):
    status = (status or '').upper()
    if status == 'ONLINE':
        return "🟢"
    if status == 'OFFLINE':
        return "🔴"
    return "⚪"


def formatar_mensagem_autenticador(df, dbm_por_contrato=None, para_telegram=True):
    """Monta a mensagem com o resultado da consulta ao Autenticador + status do sinal (Provedor)."""
    dbm_por_contrato = dbm_por_contrato or {}
    blocos = []
    
    def _tratar_valor(valor):
        """Aplica escape somente se for para o Telegram."""
        if para_telegram:
            return _escapar_markdown_legacy(valor)
        return '' if valor is None else str(valor).strip() or '-'

    for _, row in df.iterrows():
        contrato = str(row.get('CONTRATO') or '').strip()
        status_bruto = str(row.get('STATUS', '')).strip() or 'ERRO'
        emoji = _emoji_status_autenticador(status_bruto)

        is_online = (status_bruto.upper() == 'ONLINE')
        
        # Determinação dos valores de ONU e OLT conforme o estado do Autenticador
        if is_online:
            onu_val = "N/D"
            olt_val = "--"
            dados, erro = dbm_por_contrato.get(contrato, (None, None))
            if dados:
                power_onu = dados.get('rx_power_onu') if dados.get('rx_power_onu') is not None else dados.get('dbm_onu')
                power_olt = dados.get('rx_power_olt') if dados.get('rx_power_olt') is not None else dados.get('dbm_olt')
                
                if power_onu is not None:
                    onu_val = f"{power_onu:.1f} dBm"
                if power_olt is not None:
                    olt_val = f"{power_olt:.1f} dBm"
        else:
            onu_val = "LOS"
            olt_val = "--"

        bloco = (
            f"*CONTRATO:* {_tratar_valor(row.get('CONTRATO'))}\n"
            f"*STATUS:* {emoji} {status_bruto}\n"
            f"*USERNAME:* {_tratar_valor(row.get('USERNAME'))}\n"
            f"*INÍCIO:* {_tratar_valor(row.get('INÍCIO'))}\n"
            f"*FIM:* {_tratar_valor(row.get('FIM'))}\n"
            f"*CIRCUITO:* {_tratar_valor(row.get('CIRCUITO'))}\n"
            f"*MAC:* {_tratar_valor(row.get('MAC'))}\n"
            f"*TRÁFEGO:* {_tratar_valor(row.get('TRÁFEGO'))}\n"
            f"*ONU:* {_tratar_valor(onu_val)}\n"
            f"*OLT:* {_tratar_valor(olt_val)}"
        )

        if is_online:
            _, erro = dbm_por_contrato.get(contrato, (None, None))
            if erro:
                bloco += f"\n⚠️ Dados de sinal: {_tratar_valor(erro)}"

        blocos.append(bloco)
    return "\n\n".join(blocos)


def _extrair_contratos_do_texto(texto):
    """Extrai números de contrato a partir do texto enviado pelo usuário (um ou vários por linha)."""
    contratos = []
    for linha in texto.replace(',', '\n').replace(';', '\n').split('\n'):
        somente_digitos = re.sub(r'\D', '', linha)
        if somente_digitos:
            contratos.append(somente_digitos)
    return list(dict.fromkeys(contratos))


def processar_consulta_autenticador_telegram(texto_recebido):
    """Recebe o texto digitado após o comando /autenticador, consulta e responde."""
    contratos = _extrair_contratos_do_texto(texto_recebido)

    if not contratos:
        enviar_alerta_telegram(
            "⚠️ Não identifiquei nenhum número de contrato válido. "
            "Envie /autenticador novamente e digite apenas o número do contrato."
        )
        return

    logger.info(f"Consultando Autenticador via Telegram para o(s) contrato(s): {contratos}")
    df, erro = consultar_autenticador_status(contratos)

    if erro:
        enviar_alerta_telegram(f"⚠️ Erro ao consultar o Autenticador:\n{erro}")
        return
    if df is None or df.empty:
        enviar_alerta_telegram("⚠️ Nenhuma informação retornada pelo Autenticador para o(s) contrato(s) informado(s).")
        return

    dbm_por_contrato = {}
    for indice, contrato in enumerate(contratos):
        try:
            dados_dbm, erro_dbm = consultar_dbm_onu_provedor(contrato)
        except Exception:
            logger.exception(f"Falha inesperada ao consultar dBm do contrato {contrato} no Provedor.")
            dados_dbm, erro_dbm = None, "erro inesperado ao consultar o Provedor"
        dbm_por_contrato[contrato] = (dados_dbm, erro_dbm)

        if indice < len(contratos) - 1:
            time.sleep(random.uniform(1.5, 3.0))

    mensagem = formatar_mensagem_autenticador(df, dbm_por_contrato, para_telegram=True)
    enviar_alerta_telegram(mensagem, parse_mode="Markdown")


def processar_consulta_autenticador_whatsapp(texto_recebido):
    """Recebe o texto digitado após o /autenticador no grupo do WhatsApp, consulta
    e responde SÓ no WhatsApp (não usa enviar_alerta_telegram, que também
    replicaria a resposta para o grupo do Telegram)."""
    contratos = _extrair_contratos_do_texto(texto_recebido)

    if not contratos:
        enviar_alerta_whatsapp_grupo(
            "⚠️ Não identifiquei nenhum número de contrato válido. "
            "Envie /autenticador novamente e digite apenas o número do contrato."
        )
        return

    logger.info(f"Consultando Autenticador via WhatsApp para o(s) contrato(s): {contratos}")
    df, erro = consultar_autenticador_status(contratos)

    if erro:
        enviar_alerta_whatsapp_grupo(f"⚠️ Erro ao consultar o Autenticador:\n{erro}")
        return
    if df is None or df.empty:
        enviar_alerta_whatsapp_grupo("⚠️ Nenhuma informação retornada pelo Autenticador para o(s) contrato(s) informado(s).")
        return

    dbm_por_contrato = {}
    for indice, contrato in enumerate(contratos):
        try:
            dados_dbm, erro_dbm = consultar_dbm_onu_provedor(contrato)
        except Exception:
            logger.exception(f"Falha inesperada ao consultar dBm do contrato {contrato} no Provedor.")
            dados_dbm, erro_dbm = None, "erro inesperado ao consultar o Provedor"
        dbm_por_contrato[contrato] = (dados_dbm, erro_dbm)

        if indice < len(contratos) - 1:
            time.sleep(random.uniform(1.5, 3.0))

    mensagem = formatar_mensagem_autenticador(df, dbm_por_contrato, para_telegram=False)
    enviar_alerta_whatsapp_grupo(mensagem)


def _converter_mensagem_para_whatsapp(mensagem, parse_mode):
    """As mensagens de alerta são montadas pensando no Telegram (HTML ou
    Markdown legacy). O WhatsApp usa uma formatação bem mais simples
    (*negrito* com um asterisco só, sem tags e sem escape de caracteres),
    então aqui a gente adapta antes de encaminhar pro grupo."""
    if not mensagem:
        return mensagem

    if parse_mode == "HTML":
        match = re.match(r"^<b>(.*)</b>$", mensagem, flags=re.DOTALL)
        if match:
            linhas = match.group(1).split("\n")
            texto = "\n".join(f"*{linha}*" if linha.strip() else linha for linha in linhas)
        else:
            texto = mensagem.replace("<b>", "*").replace("</b>", "*")

        texto = texto.replace("<i>", "_").replace("</i>", "_")
        texto = re.sub(r"<[^>]+>", "", texto)
        return html.unescape(texto)

    if parse_mode == "Markdown":
        return re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", mensagem)

    return mensagem


# ============== FILA DE REENVIO DOS ALERTAS DO GRUPO ==============
# A O.S. é marcada como notificada com base só no message_id do Telegram; se o
# envio ao grupo do WhatsApp falha (serviço Node fora, ou "WhatsApp ainda não
# conectado"), nada reprocessa aquele envio -- a mensagem simplesmente some.
# Foram ~50 alertas perdidos entre 23 e 24/07 desse jeito. Agora o que falha
# fica aqui e é reenviado assim que o serviço voltar.
TAMANHO_MAX_FILA_WHATSAPP = 200
IDADE_MAX_ALERTA_WHATSAPP_SEG = 30 * 60   # alerta velho demais não ajuda ninguém
INTERVALO_REENVIO_WHATSAPP_SEG = 30

_fila_whatsapp = deque(maxlen=TAMANHO_MAX_FILA_WHATSAPP)
_fila_whatsapp_lock = threading.Lock()


def _postar_alerta_whatsapp(mensagem):
    """Envio cru, sem fila. Devolve True só se o grupo realmente recebeu."""
    try:
        resposta = _SESSAO_WHATSAPP.post(
            WHATSAPP_ALERTA_URL,
            json={"mensagem": mensagem},
            timeout=10,
        )
        if resposta.status_code != 200:
            logger.warning(
                f"Serviço de alerta do WhatsApp retornou {resposta.status_code}: {resposta.text}"
            )
            return False
        logger.debug("Alerta enviado ao grupo do WhatsApp com sucesso.")
        return True
    except Exception as e:
        logger.warning(
            f"Falha ao enviar alerta ao grupo do WhatsApp (serviço Node pode estar offline): {e}"
        )
        return False


def enviar_alerta_whatsapp_grupo(mensagem, reenfileirar_se_falhar=False):
    """reenfileirar_se_falhar só é ligado no caminho de broadcast
    (enviar_alerta_telegram), por onde passam os alertas de CAPEX/garantia --
    informação que não se repete. As respostas diretas de comando ("recebi o
    arquivo", "nenhum contrato válido") ficam de fora de propósito: chegar meia
    hora atrasada é pior do que não chegar."""
    if not WHATSAPP_ALERTA_ATIVO:
        return False

    if _postar_alerta_whatsapp(mensagem):
        return True

    if reenfileirar_se_falhar:
        with _fila_whatsapp_lock:
            lotada = len(_fila_whatsapp) == TAMANHO_MAX_FILA_WHATSAPP
            _fila_whatsapp.append((time.time(), mensagem))
            pendentes = len(_fila_whatsapp)
        if lotada:
            logger.warning(
                f"Fila de alertas do WhatsApp cheia ({TAMANHO_MAX_FILA_WHATSAPP}): "
                "o alerta mais antigo foi descartado."
            )
        logger.info(f"Alerta guardado para reenvio ao grupo ({pendentes} na fila).")
    return False


def thread_reenvio_alertas_whatsapp():
    """Reenvia ao grupo os alertas que falharam, quando o serviço voltar."""
    logger.info("Thread de reenvio de alertas do WhatsApp iniciada.")
    while True:
        time.sleep(INTERVALO_REENVIO_WHATSAPP_SEG)

        with _fila_whatsapp_lock:
            pendentes = list(_fila_whatsapp)
            _fila_whatsapp.clear()
        if not pendentes:
            continue

        agora = time.time()
        vencidos = [p for p in pendentes if agora - p[0] > IDADE_MAX_ALERTA_WHATSAPP_SEG]
        pendentes = [p for p in pendentes if agora - p[0] <= IDADE_MAX_ALERTA_WHATSAPP_SEG]
        if vencidos:
            logger.warning(
                f"{len(vencidos)} alerta(s) do WhatsApp descartado(s) por passarem de "
                f"{IDADE_MAX_ALERTA_WHATSAPP_SEG // 60} min sem conseguir sair."
            )

        enviados = 0
        nao_enviados = []
        for quando, mensagem in pendentes:
            # Assim que um falha, para de tentar os outros nesta rodada: o
            # serviço continua fora e insistir só enche o log.
            if nao_enviados or not _postar_alerta_whatsapp(mensagem):
                nao_enviados.append((quando, mensagem))
                continue
            enviados += 1
            time.sleep(1)   # não despejar tudo de uma vez no serviço

        if nao_enviados:
            with _fila_whatsapp_lock:
                for item in reversed(nao_enviados):
                    _fila_whatsapp.appendleft(item)

        if enviados:
            logger.info(f"{enviados} alerta(s) atrasado(s) reenviado(s) ao grupo do WhatsApp.")


def _localizar_node_executavel(pasta_servico):
    nome_local = "node.exe" if sys.platform.startswith("win") else "node"
    caminho_local = os.path.join(pasta_servico, nome_local)
    if os.path.exists(caminho_local):
        return caminho_local
    return "node"


# Segunda barreira contra material de sessão do WhatsApp cair no log.
#
# A primeira está no index.js, que redige no próprio `console`. Esta existe
# porque nem tudo passa pelo console: `process.stdout.write` direto, saída de
# um módulo nativo, ou um `npm install` que traga uma dependência nova
# escrevendo de outro jeito. Como aqui é o ponto onde a linha vira ESCRITA EM
# DISCO, é o último lugar onde ainda dá para parar.
#
# O que estava em jogo: em 08/08/2026 o monitor_campo.log tinha 38.605 linhas com
# `privKey: <Buffer ...>`, `currentRatchet` e `identityKey` em texto puro,
# acumuladas desde 23/07 -- chave viva da sessão que ainda está em uso.
_PADROES_SEGREDO_WHATSAPP = re.compile(
    r"(privKey|pubKey|ephemeralKeyPair|currentRatchet|identityKey|signedPreKey"
    r"|preKey|noiseKey|advSecretKey|chainKey|rootKey|macKey|encKey|<Buffer\s)",
    re.IGNORECASE,
)
_LIMITE_LINHA_WHATSAPP = 500

# O index.js prefixa tudo com "[PID 1234] ". Precisa sair antes de medir
# indentação, senão nenhuma linha nunca começa com espaço e o teste vira
# letra morta. Ele é opcional no padrão de propósito: o que NÃO passa pelo
# console (process.stdout.write de um módulo nativo, por exemplo) chega sem
# prefixo nenhum -- e é justamente esse o caso que este filtro precisa pegar,
# porque é o único que a redação do index.js não alcança.
_PREFIXO_PID_WHATSAPP = re.compile(r"^\[PID \d+\] ?")


def _ler_log_processo_whatsapp(processo):
    suprimidas = 0
    try:
        for linha in processo.stdout:
            linha = linha.rstrip("\n")
            if not linha:
                continue

            if "✅ Conectado ao WhatsApp" in linha:
                STATUS_SERVICO_WHATSAPP["conectado"] = True
            elif "Conexão com o WhatsApp encerrada" in linha or "Sessão deslogada" in linha:
                STATUS_SERVICO_WHATSAPP["conectado"] = False

            # Duas razões para descartar, e a segunda importa tanto quanto:
            #
            # 1. material de chave -- o motivo de segurança.
            # 2. linha INDENTADA -- toda mensagem que o index.js emite começa na
            #    coluna 0; indentação só aparece quando alguém imprimiu um
            #    objeto e o util.inspect quebrou em várias linhas. Medido no log
            #    de produção de 08/08/2026, em 216.539 linhas do WhatsApp:
            #      195.679 indentadas (despejo) -- 87.351 delas com chave
            #       20.860 na coluna 0 (mensagem real) -- 9 casando com o padrão,
            #              todas o texto inofensivo "prekey bundle"
            #    Ou seja: derrubar as indentadas tira 90% do volume e não perde
            #    uma única mensagem operacional. Sem isso, mesmo redigido, o
            #    despejo enche o log de `},` e `_chains: {` e engole a rotação.
            conteudo = _PREFIXO_PID_WHATSAPP.sub("", linha)
            if _PADROES_SEGREDO_WHATSAPP.search(linha) or conteudo.startswith((" ", "\t")):
                # Não loga NEM redigido: o despejo da libsignal vem em dezenas
                # de linhas por sessão e cada uma sozinha já é ruído. Conta e
                # avisa de vez em quando, para o sumiço não ser silencioso.
                suprimidas += 1
                if suprimidas in (1, 100) or suprimidas % 1000 == 0:
                    logger.warning(
                        f"[WhatsApp-Alerta] {suprimidas} linha(s) de despejo de objeto "
                        "suprimidas do log (podem conter material de sessão)."
                    )
                continue

            if len(linha) > _LIMITE_LINHA_WHATSAPP:
                cortado = len(linha) - _LIMITE_LINHA_WHATSAPP
                linha = linha[:_LIMITE_LINHA_WHATSAPP] + f"…(+{cortado} chars)"

            logger.info(f"[WhatsApp-Alerta] {linha}")
    except Exception as e:
        logger.warning(f"Leitura do log do serviço de alerta WhatsApp interrompida: {e}")
    finally:
        STATUS_SERVICO_WHATSAPP["conectado"] = False


def _ler_log_processo_vpn(processo):
    try:
        for linha in processo.stdout:
            linha = linha.rstrip("\n")
            if linha:
                logger.info(f"[VPN] {linha}")
    except Exception as e:
        logger.warning(f"Leitura da saída do script da VPN interrompida: {e}")


def iniciar_vpn_sempre_ativa():
    if not VPN_AUTOSTART:
        logger.info("Autostart da VPN desativado (VPN_AUTOSTART=0).")
        return

    if _vpn_e_gerenciada_externamente():
        # Linux: o campo-vpn.service já sobe o vpn_sempre_ativa.py como root,
        # em processo separado, antes do bot iniciar (ver instalar.sh/systemd).
        # Se o bot também tentasse subir aqui, ia rodar como o usuário sem
        # privilégio 'operacional' -- o próprio vpn_sempre_ativa.py recusa de
        # propósito (precisa de root) e só gera log de erro à toa.
        logger.info(
            "VPN gerenciada externamente (campo-vpn.service) -- pulando "
            "autostart pelo bot."
        )
        return

    # Quando o bot está rodando como .exe empacotado (PyInstaller), sys.executable
    # é o PRÓPRIO bot_campo_monitoramento.exe -- rodar "[sys.executable, vpn_sempre_ativa.py]"
    # nesse caso só abriria outra instância do bot, não o script da VPN (que precisa de
    # um interpretador Python de verdade). Por isso, quando frozen, procuramos um
    # vpn_sempre_ativa.exe compilado à parte, ao lado do bot.
    if getattr(sys, 'frozen', False):
        diretorio_base = os.path.dirname(sys.executable)
        caminho_vpn_exe = os.environ.get(
            'VPN_EXE_CAMINHO', os.path.join(diretorio_base, "vpn_sempre_ativa.exe")
        )
        if not os.path.exists(caminho_vpn_exe):
            logger.warning(
                f"vpn_sempre_ativa.exe não encontrado em '{caminho_vpn_exe}'. "
                "Pulando o autostart da VPN (o bot segue normalmente)."
            )
            return
        comando = [caminho_vpn_exe]
        pasta_trabalho = diretorio_base
    else:
        if not os.path.exists(VPN_SCRIPT_CAMINHO):
            logger.warning(
                f"Script da VPN não encontrado em '{VPN_SCRIPT_CAMINHO}'. "
                "Pulando o autostart da VPN (o bot segue normalmente)."
            )
            return
        comando = [sys.executable, VPN_SCRIPT_CAMINHO]
        pasta_trabalho = os.path.dirname(VPN_SCRIPT_CAMINHO)

    try:
        env_filho = dict(os.environ, PYTHONUTF8="1")
        processo = subprocess.Popen(
            comando,
            cwd=pasta_trabalho,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env_filho,
        )
    except Exception as e:
        logger.warning(f"Falha ao iniciar a VPN: {e}")
        return

    threading.Thread(target=_ler_log_processo_vpn, args=(processo,), daemon=True).start()
    logger.info(
        f"Script da VPN iniciado (PID {processo.pid}). Ele mesmo pede elevação (UAC) se "
        "necessário e reconecta sozinho -- acompanhe em vpn_sempre_ativa.log."
    )


# Quanto esperar a porta liberar quando o Node anterior ainda está morrendo.
# O encerramento leva um par de segundos; 20s cobre com folga sem atrasar a
# subida do bot de forma perceptível.
SEGUNDOS_ESPERA_PORTA_WHATSAPP = 20

# A porta sai da própria URL do serviço, para continuar valendo quando alguém
# sobrescrever WHATSAPP_ALERTA_URL pelo ambiente.
try:
    from urllib.parse import urlsplit as _urlsplit
    WHATSAPP_ALERTA_PORTA = _urlsplit(WHATSAPP_ALERTA_URL).port or 3939
except Exception:
    WHATSAPP_ALERTA_PORTA = 3939


def _porta_whatsapp_ocupada():
    """Alguém está escutando na porta do serviço?"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", WHATSAPP_ALERTA_PORTA)) == 0


def _servico_whatsapp_saudavel():
    """A porta responde /status como um serviço de verdade?"""
    try:
        resposta = _SESSAO_WHATSAPP.get(
            f"http://127.0.0.1:{WHATSAPP_ALERTA_PORTA}/status", timeout=3
        )
        return resposta.status_code == 200
    except Exception:
        return False


def _esperar_porta_whatsapp_livre():
    """True quando dá para subir o Node; False se já há um serviço sadio lá.

    Distingue os dois motivos de a porta estar ocupada: um serviço vivo (que
    deve ser reaproveitado) e um processo em vias de morrer (que só precisa de
    alguns segundos).
    """
    limite = time.time() + SEGUNDOS_ESPERA_PORTA_WHATSAPP
    avisou = False
    while _porta_whatsapp_ocupada():
        if _servico_whatsapp_saudavel():
            return False
        if time.time() >= limite:
            logger.warning(
                f"A porta {WHATSAPP_ALERTA_PORTA} continua ocupada depois de "
                f"{SEGUNDOS_ESPERA_PORTA_WHATSAPP}s e não responde /status. "
                "Subindo o Node assim mesmo -- ele vai reclamar de EADDRINUSE se "
                "o processo anterior ainda estiver lá."
            )
            return True
        if not avisou:
            logger.info(
                f"Porta {WHATSAPP_ALERTA_PORTA} ainda ocupada pelo serviço anterior. "
                "Aguardando ele encerrar..."
            )
            avisou = True
        time.sleep(1)
    return True


def iniciar_servico_alerta_whatsapp():
    if not WHATSAPP_ALERTA_ATIVO:
        logger.info("Envio de alerta ao grupo do WhatsApp desativado (WHATSAPP_ALERTA_ATIVO=0).")
        return

    if not WHATSAPP_ALERTA_AUTOSTART:
        logger.info("Autostart do serviço de alerta WhatsApp desativado (WHATSAPP_ALERTA_AUTOSTART=0).")
        return

    if not os.path.isdir(WHATSAPP_ALERTA_SERVICO_DIR):
        aviso = (
            f"Pasta do serviço de alerta WhatsApp não encontrada em "
            f"'{WHATSAPP_ALERTA_SERVICO_DIR}'. Verifique se o index.js, config.json e "
            f"package.json estão na raiz do projeto, ou ajuste WHATSAPP_ALERTA_SERVICO_DIR."
        )
        logger.warning(aviso)
        return

    cmd_node = _localizar_node_executavel(WHATSAPP_ALERTA_SERVICO_DIR)

    node_modules = os.path.join(WHATSAPP_ALERTA_SERVICO_DIR, "node_modules")
    if not os.path.isdir(node_modules):
        aviso = (
            "Dependências do serviço de alerta WhatsApp não instaladas ainda. "
            "Rode 'npm install' na raiz do projeto (mesma pasta do index.js)."
        )
        logger.warning(aviso)
        return

    # Espera a porta 3939 ficar livre antes de subir o Node.
    #
    # Em 08/08/2026 o serviço passou a ser filho do bot, e com isso morre junto
    # com ele num reinício. Só que o bot novo sobe em ~15s e o Node velho ainda
    # está morrendo, segurando a porta: o novo levava `EADDRINUSE`, desistia, o
    # velho terminava de morrer e ficava NINGUÉM atendendo -- com o bot gravando
    # um WARNING a cada 5s para sempre. Foi exatamente o que aconteceu às 15:59.
    #
    # Se quem estiver na porta for um serviço SADIO (respondeu /status), não há
    # o que fazer: reaproveita e não sobe um segundo.
    if not _esperar_porta_whatsapp_livre():
        logger.info(
            "A porta 3939 já está sendo atendida por um serviço de WhatsApp vivo. "
            "Reaproveitando em vez de subir outro."
        )
        STATUS_SERVICO_WHATSAPP["iniciado"] = True
        return

    try:
        processo = subprocess.Popen(
            [cmd_node, "index.js"],
            cwd=WHATSAPP_ALERTA_SERVICO_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except Exception as e:
        aviso = f"Falha ao iniciar o serviço de alerta WhatsApp: {e}"
        logger.warning(aviso)
        return

    STATUS_SERVICO_WHATSAPP["iniciado"] = True
    STATUS_SERVICO_WHATSAPP["processo"] = processo
    threading.Thread(target=_ler_log_processo_whatsapp, args=(processo,), daemon=True).start()
    logger.info(f"Serviço de alerta WhatsApp iniciado (PID {processo.pid}).")


def enviar_alerta_telegram(mensagem, parse_mode=None):
    mensagem_whatsapp = _converter_mensagem_para_whatsapp(mensagem, parse_mode)
    # Caminho dos alertas de CAPEX/garantia: se o grupo não receber agora, a
    # mensagem entra na fila de reenvio -- a O.S. já vai ficar marcada como
    # notificada pelo Telegram e ninguém reprocessaria esse envio.
    enviar_alerta_whatsapp_grupo(mensagem_whatsapp, reenfileirar_se_falhar=True)

    if len(mensagem) > TELEGRAM_MAX_CARACTERES:
        sufixo = "\n\n[...mensagem truncada por exceder o limite do Telegram...]"
        mensagem = mensagem[:TELEGRAM_MAX_CARACTERES - len(sufixo)] + sufixo
        logger.warning("Mensagem para o Telegram truncada por exceder o limite de caracteres.")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensagem}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resposta = requests.post(url, json=payload, timeout=10)
        if resposta.status_code != 200:
            logger.error(f"Telegram retornou {resposta.status_code}: {resposta.text}")
            return None
        logger.debug("Mensagem enviada ao Telegram com sucesso.")
        try:
            return resposta.json()["result"]["message_id"]
        except Exception:
            logger.warning("Não foi possível extrair message_id da resposta do Telegram.")
            return None
    except Exception as e:
        logger.error(f"Falha ao enviar mensagem Telegram: {e}")
        return None


def fixar_mensagem_telegram(message_id, notificar=True):
    global _ultima_fixacao_dia
    if not message_id:
        logger.warning("fixar_mensagem_telegram: message_id ausente, não é possível fixar.")
        return False

    hoje = datetime.now().date()
    if _ultima_fixacao_dia == hoje:
        notificar = False
    else:
        _ultima_fixacao_dia = hoje

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/pinChatMessage"
    payload = {
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "disable_notification": not notificar,
    }
    try:
        resposta = requests.post(url, json=payload, timeout=10)
        if resposta.status_code != 200:
            logger.error(f"Falha ao fixar mensagem no Telegram: {resposta.status_code}: {resposta.text}")
            return False
        logger.info(f"Mensagem {message_id} fixada com sucesso no grupo.")
        return True
    except Exception as e:
        logger.error(f"Erro ao tentar fixar mensagem Telegram: {e}")
        return False


def alerta_critico_telegram(mensagem):
    logger.critical(mensagem)
    enviar_alerta_telegram(f"⚠️ ERRO CRÍTICO NO MONITOR:\n{mensagem}")


# =====================================================================
# INTEGRAÇÃO COM O PAINEL OPERACIONAL (site)
# ---------------------------------------------------------------------
# Duas pontes com o site que roda na mesma máquina:
#
#   1) comando /painel  -> responde no grupo com o endereço do site, e
#      sobe o site sozinho se ele não estiver no ar. Resolve o endereço
#      público mudar a cada reinício: quem precisar pede /painel.
#
#   2) servidor HTTP local -> o botão "Atualizar" do site chama aqui para
#      pedir um backlog novo. Só este processo consegue gerar, porque a
#      lista de chamados fica em memória (LISTA_CHAMADOS_ATUAL).
# =====================================================================

PAINEL_PORTA_PONTE = 3940          # porta local que o site chama
PAINEL_ESPERA_SUBIR_SEG = 45       # quanto esperar o site responder ao subir
PAINEL_ESPERA_TUNEL_SEG = 25       # tempo extra só para o endereço público sair


def _painel_caminho(nome):
    if getattr(sys, 'frozen', False):
        raiz = os.path.dirname(sys.executable)
    else:
        raiz = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(raiz, "dados", nome)


def _painel_ler_json(nome):
    caminho = _painel_caminho(nome)
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except (OSError, ValueError):
        return None


def _painel_no_ar(endereco, timeout=3):
    """O site responde nesse endereço agora?"""
    if not endereco:
        return False
    try:
        resposta = requests.get(f"{endereco.rstrip('/')}/saude", timeout=timeout)
        return resposta.status_code == 200
    except Exception:
        return False


def painel_iniciar_site():
    """Sobe o site numa janela própria. Devolve (ok, mensagem)."""
    config = _painel_ler_json("painel_config.json")
    if not config:
        return False, ("não sei onde o site está instalado — rode o instalador "
                       "dele uma vez para eu aprender o caminho")

    pasta = config.get("pasta")
    python = config.get("python") or "python"
    if not pasta or not os.path.isdir(pasta):
        return False, f"a pasta do site não existe mais: {pasta}"

    script = os.path.join(pasta, "iniciar_site.py")
    if not os.path.exists(script):
        return False, f"não achei o iniciar_site.py em {pasta}"

    # Janela própria (o operador vê o site rodando e fecha para desligar) e
    # fora do job do bot -- sem o breakaway, encerrar o bot derruba o site junto.
    if os.name == "nt":
        tentativas = [
            subprocess.CREATE_NEW_CONSOLE | 0x01000000,   # CREATE_BREAKAWAY_FROM_JOB
            subprocess.CREATE_NEW_CONSOLE,
        ]
    else:
        tentativas = [0]

    ultimo_erro = None
    for criacao in tentativas:
        try:
            subprocess.Popen([python, script], cwd=pasta, creationflags=criacao,
                             close_fds=True)
            logger.info("Site do painel iniciado a pedido do comando /painel.")
            return True, ""
        except OSError as erro:
            ultimo_erro = erro
            continue
        except Exception as erro:
            logger.exception("Falha ao iniciar o site do painel.")
            return False, f"não consegui iniciar o site: {erro}"

    logger.error(f"Falha ao iniciar o site do painel: {ultimo_erro}")
    return False, f"não consegui iniciar o site: {ultimo_erro}"


def montar_mensagem_painel():
    """Resposta do /painel: sobe o site se preciso e devolve o endereço."""
    endereco = _painel_ler_json("painel_endereco.json") or {}
    config = _painel_ler_json("painel_config.json") or {}
    local = endereco.get("local") or f"http://localhost:{config.get('porta', 8800)}"

    if not _painel_no_ar(local):
        ok, motivo = painel_iniciar_site()
        if not ok:
            return f"⚠️ O painel está fora do ar e {motivo}."

        enviar_alerta_telegram("⏳ O painel estava desligado. Subindo, aguarde...")

        # 1) espera o site responder
        subiu = False
        limite = time.time() + PAINEL_ESPERA_SUBIR_SEG
        while time.time() < limite:
            time.sleep(3)
            endereco = _painel_ler_json("painel_endereco.json") or endereco
            local = endereco.get("local") or local
            if _painel_no_ar(local):
                subiu = True
                break

        if not subiu:
            return ("⚠️ Mandei subir o painel, mas ele não respondeu a tempo. "
                    "Tente /painel de novo em um minuto.")

        # 2) o túnel sai alguns segundos depois do site; espera só um pouco por
        #    ele. Sem cloudflared instalado nunca vem, e aí seguimos sem.
        limite_tunel = time.time() + PAINEL_ESPERA_TUNEL_SEG
        while time.time() < limite_tunel and not endereco.get("publico"):
            time.sleep(3)
            endereco = _painel_ler_json("painel_endereco.json") or endereco

    linhas = ["📊 *PAINEL OPERACIONAL*", ""]
    if endereco.get("publico"):
        linhas.append(f"🌐 {endereco['publico']}")
    else:
        linhas.append("🌐 Sem endereço externo agora (o túnel não subiu).")
    if endereco.get("rede_local"):
        linhas.append(f"🏠 Na rede da empresa: {endereco['rede_local']}")
    # O PIN único saiu de cena: agora cada pessoa entra com o próprio e-mail,
    # pela conta Google ou por senha que ela mesma define. Não há mais segredo
    # compartilhado para anunciar aqui -- e anunciar um que não abre mais nada
    # seria pior do que não dizer nada.
    linhas += [
        "",
        "🔐 Entre com o seu e-mail. No primeiro acesso, o site envia um código "
        "para você criar a sua senha.",
        "",
        "_Sem acesso? Peça a um administrador para cadastrar o seu e-mail._",
    ]
    return "\n".join(linhas)


def montar_mensagem_comandos(whatsapp=False):
    """Lista de comandos disponíveis, mostrada quando alguém pede /comandos.

    O WhatsApp aceita 'backlog' e 'termometro' sem a barra, então a lista é
    montada com o prefixo certo para cada canal.
    """
    barra = "" if whatsapp else "/"
    tipos = ", ".join(TIPOS_BACKLOG_VALIDOS)

    linhas = [
        "🤖 *COMANDOS DISPONÍVEIS*",
        "",
        f"📊 *{barra}backlog* — gera o backlog de todos os tipos",
        f"     _{barra}backlog {tipos}_ para um tipo só",
        f"🌡️ *{barra}termometro* — termômetro de entrantes CAPEX",
        "🛠️ *​/improdutivas* — reincidentes de improdutiva em aberto, por região",
        f"📄 *{barra}garantias* — manda a lista de garantias aos grupos regionais",
        "📡 *​/autenticador* — consulta status de um contrato",
        "🖥️ *​/painel* — endereço do site do painel",
        "🔄 *​/reiniciar* — reinicia a máquina inteira (VPN, bot, site, painel)",
    ]
    if not whatsapp:
        linhas.append("⚙️ *​/status* — resumo do sistema e contadores do dia")

    if monitor_pausado():
        linhas += [
            "",
            f"☀️ *{barra}ligar* — retomar o monitoramento",
            "     _o monitoramento está PAUSADO agora_",
        ]
    else:
        linhas += [
            "",
            f"🌙 *{barra}desligar* — pausa o monitoramento (fecha o navegador,",
            "     para os alertas). Retoma com "
            f"*{barra}ligar*, sem precisar de AnyDesk.",
        ]

    # Só oferece o lado que faz sentido no estado atual -- listar "exibir" com o
    # painel já aberto só gera comando digitado à toa.
    linhas.append("")
    if painel_tv_pedido():
        linhas.append(f"📺 *{barra}ocultarpaineltv* — fecha o painel na TV")
    else:
        linhas.append(f"📺 *{barra}exibirpaineltv* — abre o painel na TV")

    if navegador_deve_aparecer():
        linhas.append(f"🖥️ *{barra}ocultarnavegador* — volta o navegador a ficar oculto")
    else:
        linhas.append(f"🖥️ *{barra}exibirnavegador* — mostra o navegador (para conferir algo)")

    linhas += [
        "",
        "_Backlog e termômetro são gerados só quando solicitados._",
    ]
    return "\n".join(linhas)


def _painel_tratar_pedido(caminho, corpo_bruto=b""):
    """Executa o que o site pediu. Devolve (codigo_http, resposta)."""
    if caminho == "/saude":
        return 200, {"ok": True, "bot": "no ar"}

    if caminho == "/gerar-backlog":
        # Pausado, a lista em memória está congelada no instante do /desligar.
        # Gerar daí mandaria imagens desatualizadas ao grupo justamente quando
        # ele deveria estar em silêncio.
        if monitor_pausado():
            return 409, {"ok": False,
                         "erro": "o monitoramento está pausado (/desligar no grupo); "
                                 "use /ligar e tente de novo em ~1 minuto"}

        lista = obter_lista_chamados_atual()
        if not lista:
            return 409, {"ok": False,
                         "erro": "o bot ainda não carregou a lista de chamados; "
                                 "tente de novo em alguns minutos"}
        threading.Thread(
            target=gerar_e_enviar_backlog_todos_tipos,
            args=(lista,),
            daemon=True,
        ).start()
        logger.info("Backlog solicitado pelo site (botão Atualizar).")
        return 202, {"ok": True, "mensagem": "geração do backlog iniciada"}

    # Rota generica: o site ja monta o texto pronto (ex.: lista de nao
    # confirmados da Confirmacao de Agenda) e so pede para o bot repassar
    # ao grupo - evita duplicar regra de negocio aqui no bot.
    if caminho == "/enviar-mensagem":
        try:
            texto = json.loads(corpo_bruto or b"{}").get("texto", "").strip()
        except (ValueError, UnicodeDecodeError):
            return 400, {"ok": False, "erro": "corpo da requisição não é um JSON válido"}
        if not texto:
            return 400, {"ok": False, "erro": "campo 'texto' vazio"}

        threading.Thread(
            target=enviar_alerta_telegram, args=(texto,), daemon=True,
        ).start()
        logger.info("Mensagem do site repassada ao grupo (%d caracteres).", len(texto))
        return 202, {"ok": True, "mensagem": "enviado ao grupo"}

    return 404, {"ok": False, "erro": "rota desconhecida"}


def iniciar_ponte_painel():
    """Servidor HTTP local que atende os pedidos do site."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _responder(self, codigo, corpo):
            dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def do_POST(self):
            tamanho = int(self.headers.get("Content-Length") or 0)
            corpo_bruto = self.rfile.read(tamanho) if tamanho else b""
            codigo, corpo = _painel_tratar_pedido(self.path, corpo_bruto)
            self._responder(codigo, corpo)

        def do_GET(self):
            codigo, corpo = _painel_tratar_pedido(self.path)
            self._responder(codigo, corpo)

        def log_message(self, *args):
            pass   # não poluir o log do bot com cada requisição

    try:
        # só localhost: o site roda na mesma máquina, ninguém de fora precisa
        servidor = ThreadingHTTPServer(("127.0.0.1", PAINEL_PORTA_PONTE), Handler)
        logger.info(f"Ponte do painel ouvindo em 127.0.0.1:{PAINEL_PORTA_PONTE}")
        servidor.serve_forever()
    except OSError as erro:
        logger.warning(f"Não consegui abrir a ponte do painel: {erro}")
    except Exception:
        logger.exception("Erro inesperado na ponte do painel.")


def _obter_texto_comando(mensagem_telegram):
    texto = (mensagem_telegram.get("text") or "").strip()
    if not texto:
        return ""
    primeira_palavra = texto.split()[0]
    return primeira_palavra.split('@')[0].lower()


# ============ /reiniciar (reboot da máquina inteira) ============
# Atraso antes do reboot de verdade: dá tempo do alerta sair pelo Telegram/
# WhatsApp (HTTP) antes da máquina cair. Sem isso a mensagem "reiniciando..."
# corre o risco de nunca sair.
REINICIAR_ATRASO_SEG = 3
_reiniciar_em_andamento = threading.Event()


def _reiniciar_sistema_thread():
    """Roda em thread separada para não travar o laço de escuta de comandos
    enquanto espera o atraso. Reboot da MÁQUINA INTEIRA (não só os serviços) --
    sudo com regra restrita (só 'systemctl reboot', nada mais) instalada pelo
    instalar.sh, passo 2i. Só age em Linux: o bot também roda em dev/Windows,
    onde este comando não deve fazer nada."""
    try:
        time.sleep(REINICIAR_ATRASO_SEG)
        if not sys.platform.startswith('linux'):
            logger.warning(
                "/reiniciar pedido, mas a plataforma não é Linux -- ignorando "
                "(o comando só reinicia a máquina de produção)."
            )
            return
        logger.warning("Executando reinício completo do sistema (comando /reiniciar).")
        subprocess.run(["sudo", "/usr/bin/systemctl", "reboot"], timeout=15, check=True)
    except Exception:
        logger.exception("Falha ao executar o reinício do sistema via /reiniciar.")
    finally:
        # Se o reboot realmente disparou, a máquina cai e isto nem chega a
        # rodar -- só importa no caminho de falha, para permitir tentar de novo.
        _reiniciar_em_andamento.clear()


def escutar_comandos_telegram():
    logger.info("Thread de escuta de comandos do Telegram iniciada (aguardando '/status')...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = None

    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset

            resposta = requests.get(url, params=params, timeout=35)
            if resposta.status_code != 200:
                logger.warning(
                    f"getUpdates (comandos Telegram) retornou {resposta.status_code}: {resposta.text}"
                )
                time.sleep(5)
                continue

            dados = resposta.json()
            for update in dados.get("result", []):
                offset = update["update_id"] + 1

                mensagem_telegram = update.get("message") or update.get("channel_post")
                if not mensagem_telegram:
                    continue

                chat = mensagem_telegram.get("chat", {})
                if str(chat.get("id")) != str(CHAT_ID):
                    continue

                chat_id_str = str(chat.get("id"))
                texto_bruto = (mensagem_telegram.get("text") or "").strip()

                comando = _obter_texto_comando(mensagem_telegram)

                if comando in ("/comandos", "/ajuda", "/help"):
                    logger.info("Comando /comandos recebido no grupo.")
                    try:
                        enviar_alerta_telegram(montar_mensagem_comandos())
                    except Exception:
                        logger.exception("Falha ao enviar a lista de comandos.")
                    continue

                if comando == "/reiniciar":
                    if _reiniciar_em_andamento.is_set():
                        enviar_alerta_telegram("🔄 Já estou reiniciando o sistema — aguarde.")
                    else:
                        _reiniciar_em_andamento.set()
                        logger.warning("Comando /reiniciar recebido no grupo (Telegram). Reiniciando o sistema.")
                        enviar_alerta_telegram(
                            "🔄 *Reiniciando o sistema todo.* VPN, bot, site e painel de TV "
                            "voltam sozinhos em 1-2 minutos."
                        )
                        threading.Thread(target=_reiniciar_sistema_thread, daemon=True).start()
                    continue

                if comando == "/status":
                    logger.info("Comando /status recebido no grupo. Enviando resumo do sistema...")
                    try:
                        enviar_status_telegram()
                    except Exception:
                        logger.exception("Falha ao montar/enviar o resumo do comando /status.")
                    continue

                if comando in ("/exibirpaineltv", "/ocultarpaineltv"):
                    exibir = comando == "/exibirpaineltv"
                    if exibir == painel_tv_pedido():
                        enviar_alerta_telegram(
                            "📺 O painel de TV já está " + ("aberto." if exibir else "fechado.")
                        )
                    elif exibir:
                        _painel_tv_pedido.set()
                        logger.warning("Comando /exibirpaineltv recebido no grupo (Telegram).")
                        enviar_alerta_telegram(
                            "📺 Abrindo o painel de TV na máquina. O navegador continua oculto."
                        )
                    else:
                        _painel_tv_pedido.clear()
                        logger.warning("Comando /ocultarpaineltv recebido no grupo (Telegram).")
                        enviar_alerta_telegram("📺 Fechando o painel de TV.")
                    continue

                if comando in ("/exibirnavegador", "/ocultarnavegador"):
                    exibir = comando == "/exibirnavegador"
                    if exibir == navegador_deve_aparecer():
                        enviar_alerta_telegram(
                            "🖥️ O navegador já está " + ("à vista." if exibir else "oculto.")
                        )
                    else:
                        _navegador_visivel.set() if exibir else _navegador_visivel.clear()
                        logger.warning(f"Comando {comando} recebido no grupo (Telegram).")
                        enviar_alerta_telegram(
                            ("🖥️ Reabrindo o navegador *à vista* na máquina."
                             if exibir else
                             "🖥️ Voltando o navegador para o modo oculto.")
                            + "\nO Chromium precisa reiniciar para isso — a varredura "
                              "volta ao normal em cerca de 1 minuto."
                        )
                    continue

                if comando == "/desligar":
                    if pausar_monitor():
                        logger.warning("Comando /desligar recebido no grupo (Telegram). Pausando o monitoramento.")
                        enviar_alerta_telegram(
                            "🌙 *Monitoramento pausado.*\n"
                            "O navegador vai fechar e nenhum alerta será enviado.\n"
                            "Use /ligar para retomar."
                        )
                    else:
                        enviar_alerta_telegram("🌙 O monitoramento já está pausado. Use /ligar para retomar.")
                    continue

                if comando == "/ligar":
                    if retomar_monitor():
                        logger.warning("Comando /ligar recebido no grupo (Telegram). Retomando o monitoramento.")
                        enviar_alerta_telegram(
                            "☀️ *Retomando o monitoramento.*\n"
                            "Abrindo o navegador e refazendo o login — a varredura "
                            "volta ao normal em cerca de 1 minuto."
                        )
                    else:
                        enviar_alerta_telegram("☀️ O monitoramento já está rodando.")
                    continue

                # ============ NOVO: endereço do painel (sobe o site se preciso) ============
                if comando == "/painel":
                    logger.info("Comando /painel recebido no grupo.")
                    threading.Thread(
                        target=lambda: enviar_alerta_telegram(montar_mensagem_painel()),
                        daemon=True,
                    ).start()
                    continue

                if comando == "/autenticador":
                    logger.info("Comando /autenticador recebido no grupo. Aguardando contrato...")
                    AGUARDANDO_CONTRATO_AUTENTICADOR[chat_id_str] = time.time()
                    enviar_alerta_telegram("📡 Digite o contrato para consultar no Autenticador:")
                    continue

                # ============ MODIFICADO: suporte a subcomandos /backlog ============
                if comando.startswith("/backlog"):
                    partes = texto_bruto.split(maxsplit=1)
                    subtipo = partes[1].strip().lower() if len(partes) > 1 else None

                    if subtipo is None:
                        # Nenhum tipo informado ("/backlog" sozinho) -> envia TODOS os tipos
                        enviar_alerta_telegram("⏳ Gerando backlog completo (todos os tipos), aguarde...")
                        threading.Thread(
                            target=gerar_e_enviar_backlog_todos_tipos,
                            args=(obter_lista_chamados_atual(),),
                            daemon=True,
                        ).start()
                        continue

                    if subtipo not in TIPOS_BACKLOG_VALIDOS:
                        enviar_alerta_telegram(
                            f"⚠️ Tipo inválido. Use: /backlog {', '.join(TIPOS_BACKLOG_VALIDOS)}\n"
                            f"Exemplo: /backlog reparo"
                        )
                        continue

                    enviar_alerta_telegram(f"⏳ Gerando backlog de {subtipo}, aguarde...")
                    threading.Thread(
                        target=gerar_e_enviar_backlog_tipo,
                        args=(obter_lista_chamados_atual(), subtipo),
                        daemon=True,
                    ).start()
                    continue

                # ============ NOVO: termômetro de entrantes CAPEX ============
                if comando == "/termometro":
                    enviar_alerta_telegram("⏳ Gerando termômetro de entrantes CAPEX, aguarde...")
                    threading.Thread(
                        target=gerar_e_enviar_termometro_capex,
                        daemon=True,
                    ).start()
                    continue

                # ============ NOVO (13/08/2026): reincidentes em aberto ============
                if comando == "/improdutivas":
                    logger.info("Comando /improdutivas recebido. Montando a lista consolidada...")
                    enviar_alerta_telegram("⏳ Levantando as improdutivas reincidentes em aberto...")
                    threading.Thread(
                        target=responder_improdutivas,
                        kwargs={'whatsapp': False},
                        daemon=True,
                    ).start()
                    continue

                # ============ NOVO (13/08/2026): a lista de garantias, fora de hora ============
                if comando == "/garantias":
                    enviar_alerta_telegram(
                        "⏳ Gerando a lista de garantias e mandando para os grupos regionais..."
                    )
                    threading.Thread(
                        target=gerar_e_enviar_garantias_agora,
                        daemon=True,
                    ).start()
                    continue

                if texto_bruto and not texto_bruto.startswith("/"):
                    ts_prompt = AGUARDANDO_CONTRATO_AUTENTICADOR.get(chat_id_str)
                    if ts_prompt is not None:
                        AGUARDANDO_CONTRATO_AUTENTICADOR.pop(chat_id_str, None)
                        if (time.time() - ts_prompt) <= TIMEOUT_AGUARDANDO_CONTRATO_AUTENTICADOR_SEG:
                            try:
                                processar_consulta_autenticador_telegram(texto_bruto)
                            except Exception:
                                logger.exception("Falha ao processar consulta do /autenticador via Telegram.")
                                enviar_alerta_telegram("⚠️ Erro inesperado ao consultar o Autenticador. Tente novamente com /autenticador.")
                        else:
                            enviar_alerta_telegram("⏱️ Tempo para digitar o contrato expirou. Envie /autenticador novamente.")
                        continue

        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.RequestException as e:
            logger.warning(f"Falha de rede ao consultar comandos do Telegram: {e}")
            time.sleep(5)
        except Exception:
            logger.exception("Erro inesperado na thread de escuta de comandos do Telegram.")
            time.sleep(5)


def _obter_comando_whatsapp(texto):
    texto = (texto or "").strip()
    if not texto:
        return ""
    primeira_palavra = texto.split()[0]
    return primeira_palavra.split('@')[0].lower()



def escutar_comandos_whatsapp():
    logger.info("Thread de escuta de comandos do WhatsApp iniciada (aguardando '/autenticador' no grupo)...")

    while True:
        try:
            resposta = _SESSAO_WHATSAPP.get(WHATSAPP_MENSAGENS_URL, timeout=30)
            if resposta.status_code != 200:
                logger.warning(
                    f"/mensagens (comandos WhatsApp) retornou {resposta.status_code}: {resposta.text}"
                )
                time.sleep(WHATSAPP_MENSAGENS_INTERVALO_SEG)
                continue

            dados = resposta.json()
            for msg in dados.get("mensagens", []):
                remetente = msg.get("participante")
                texto_bruto = (msg.get("texto") or "").strip()
                arquivo = msg.get("arquivo")
                if not remetente or (not texto_bruto and not arquivo):
                    continue

                # Anexo no grupo não é mais assunto do bot. O único comando que
                # esperava arquivo era o /improdutivas antigo, de lote; o
                # /improdutivas de hoje lê o CAMPO e não quer anexo nenhum. As
                # bases entram pelo site. Segue ignorando explicitamente para
                # não cair no parser de comando de texto (mensagem com anexo
                # chega com texto="" do lado do Node).
                if arquivo:
                    continue

                comando = _obter_comando_whatsapp(texto_bruto)

                if comando in ("/comandos", "comandos", "/ajuda", "ajuda", "/help"):
                    logger.info("Comando /comandos recebido no grupo do WhatsApp.")
                    try:
                        enviar_alerta_whatsapp_grupo(montar_mensagem_comandos(whatsapp=True))
                    except Exception:
                        logger.exception("Falha ao enviar a lista de comandos no WhatsApp.")
                    continue

                if comando == "/reiniciar":
                    if _reiniciar_em_andamento.is_set():
                        enviar_alerta_whatsapp_grupo("🔄 Já estou reiniciando o sistema — aguarde.")
                    else:
                        _reiniciar_em_andamento.set()
                        logger.warning("Comando '/reiniciar' recebido no grupo do WhatsApp. Reiniciando o sistema.")
                        enviar_alerta_whatsapp_grupo(
                            "🔄 *Reiniciando o sistema todo.* VPN, bot, site e painel de TV "
                            "voltam sozinhos em 1-2 minutos."
                        )
                        threading.Thread(target=_reiniciar_sistema_thread, daemon=True).start()
                    continue

                if comando in ("/exibirpaineltv", "exibirpaineltv",
                               "/ocultarpaineltv", "ocultarpaineltv"):
                    exibir = comando.lstrip("/") == "exibirpaineltv"
                    if exibir == painel_tv_pedido():
                        enviar_alerta_whatsapp_grupo(
                            "📺 O painel de TV já está " + ("aberto." if exibir else "fechado.")
                        )
                    elif exibir:
                        _painel_tv_pedido.set()
                        logger.warning("Comando 'exibirpaineltv' recebido no grupo do WhatsApp.")
                        enviar_alerta_whatsapp_grupo(
                            "📺 Abrindo o painel de TV na máquina. O navegador continua oculto."
                        )
                    else:
                        _painel_tv_pedido.clear()
                        logger.warning("Comando 'ocultarpaineltv' recebido no grupo do WhatsApp.")
                        enviar_alerta_whatsapp_grupo("📺 Fechando o painel de TV.")
                    continue

                if comando in ("/exibirnavegador", "exibirnavegador",
                               "/ocultarnavegador", "ocultarnavegador"):
                    exibir = comando.lstrip("/") == "exibirnavegador"
                    if exibir == navegador_deve_aparecer():
                        enviar_alerta_whatsapp_grupo(
                            "🖥️ O navegador já está " + ("à vista." if exibir else "oculto.")
                        )
                    else:
                        _navegador_visivel.set() if exibir else _navegador_visivel.clear()
                        logger.warning(f"Comando '{comando}' recebido no grupo do WhatsApp.")
                        enviar_alerta_whatsapp_grupo(
                            ("🖥️ Reabrindo o navegador *à vista* na máquina."
                             if exibir else
                             "🖥️ Voltando o navegador para o modo oculto.")
                            + "\nO Chromium precisa reiniciar para isso — a varredura "
                              "volta ao normal em cerca de 1 minuto."
                        )
                    continue

                if comando in ("/desligar", "desligar"):
                    if pausar_monitor():
                        logger.warning("Comando 'desligar' recebido no grupo do WhatsApp. Pausando o monitoramento.")
                        enviar_alerta_whatsapp_grupo(
                            "🌙 *Monitoramento pausado.*\n"
                            "O navegador vai fechar e nenhum alerta será enviado.\n"
                            "Digite *ligar* para retomar."
                        )
                    else:
                        enviar_alerta_whatsapp_grupo("🌙 O monitoramento já está pausado. Digite *ligar* para retomar.")
                    continue

                if comando in ("/ligar", "ligar"):
                    if retomar_monitor():
                        logger.warning("Comando 'ligar' recebido no grupo do WhatsApp. Retomando o monitoramento.")
                        enviar_alerta_whatsapp_grupo(
                            "☀️ *Retomando o monitoramento.*\n"
                            "Abrindo o navegador e refazendo o login — a varredura "
                            "volta ao normal em cerca de 1 minuto."
                        )
                    else:
                        enviar_alerta_whatsapp_grupo("☀️ O monitoramento já está rodando.")
                    continue

                if comando == "/autenticador":
                    logger.info("Comando /autenticador recebido no grupo do WhatsApp. Aguardando contrato...")
                    AGUARDANDO_CONTRATO_AUTENTICADOR_WHATSAPP[remetente] = time.time()
                    enviar_alerta_whatsapp_grupo("📡 Digite o contrato para consultar no Autenticador:")
                    continue

                # ============ NOVO: endereço do painel (sobe o site se preciso) ============
                if comando == "/painel":
                    logger.info("Comando /painel recebido no grupo do WhatsApp.")
                    threading.Thread(
                        target=lambda: enviar_alerta_whatsapp_grupo(montar_mensagem_painel()),
                        daemon=True,
                    ).start()
                    continue

                # ============ MODIFICADO: suporte a subcomandos "backlog" ============
                if comando == "backlog":
                    partes = texto_bruto.split(maxsplit=1)
                    subtipo = partes[1].strip().lower() if len(partes) > 1 else None

                    if subtipo is None:
                        # Nenhum tipo informado ("backlog" sozinho) -> envia TODOS os tipos
                        enviar_alerta_whatsapp_grupo("⏳ Gerando backlog completo (todos os tipos), aguarde...")
                        threading.Thread(
                            target=gerar_e_enviar_backlog_todos_tipos,
                            args=(obter_lista_chamados_atual(),),
                            daemon=True,
                        ).start()
                        continue

                    if subtipo not in TIPOS_BACKLOG_VALIDOS:
                        enviar_alerta_whatsapp_grupo(
                            f"⚠️ Tipo inválido. Use: backlog {', '.join(TIPOS_BACKLOG_VALIDOS)}\n"
                            f"Exemplo: backlog reparo"
                        )
                        continue

                    enviar_alerta_whatsapp_grupo(f"⏳ Gerando backlog de {subtipo}, aguarde...")
                    threading.Thread(
                        target=gerar_e_enviar_backlog_tipo,
                        args=(obter_lista_chamados_atual(), subtipo),
                        daemon=True,
                    ).start()
                    continue

                # ============ NOVO: termômetro de entrantes CAPEX ============
                if comando == "termometro":
                    enviar_alerta_whatsapp_grupo("⏳ Gerando termômetro de entrantes CAPEX, aguarde...")
                    threading.Thread(
                        target=gerar_e_enviar_termometro_capex,
                        daemon=True,
                    ).start()
                    continue

                # ============ NOVO (13/08/2026): reincidentes em aberto ============
                # Aceita com e sem barra: no WhatsApp os outros comandos são
                # escritos sem ela, mas "/improdutivas" é como este comando
                # sempre foi chamado e é o que a operação vai digitar.
                if comando in ("improdutivas", "/improdutivas"):
                    logger.info("Comando /improdutivas recebido no grupo do WhatsApp.")
                    enviar_alerta_whatsapp_grupo("⏳ Levantando as improdutivas reincidentes em aberto...")
                    threading.Thread(
                        target=responder_improdutivas,
                        kwargs={'whatsapp': True},
                        daemon=True,
                    ).start()
                    continue

                # ============ NOVO (13/08/2026): a lista de garantias, fora de hora ============
                if comando in ("garantias", "/garantias"):
                    enviar_alerta_whatsapp_grupo(
                        "⏳ Gerando a lista de garantias e mandando para os grupos regionais..."
                    )
                    threading.Thread(
                        target=gerar_e_enviar_garantias_agora,
                        daemon=True,
                    ).start()
                    continue

                if not texto_bruto.startswith("/"):
                    ts_prompt = AGUARDANDO_CONTRATO_AUTENTICADOR_WHATSAPP.get(remetente)
                    if ts_prompt is not None:
                        AGUARDANDO_CONTRATO_AUTENTICADOR_WHATSAPP.pop(remetente, None)
                        if (time.time() - ts_prompt) <= TIMEOUT_AGUARDANDO_CONTRATO_AUTENTICADOR_SEG:
                            try:
                                processar_consulta_autenticador_whatsapp(texto_bruto)
                            except Exception:
                                logger.exception("Falha ao processar consulta do /autenticador via WhatsApp.")
                                enviar_alerta_whatsapp_grupo("⚠️ Erro inesperado ao consultar o Autenticador. Tente novamente com /autenticador.")
                        else:
                            enviar_alerta_whatsapp_grupo("⏱️ Tempo para digitar o contrato expirou. Envie /autenticador novamente.")
                        continue

        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.RequestException as e:
            logger.warning(f"Falha de rede ao consultar comandos do WhatsApp (serviço Node pode estar offline): {e}")
            time.sleep(WHATSAPP_MENSAGENS_INTERVALO_SEG)
        except Exception:
            logger.exception("Erro inesperado na thread de escuta de comandos do WhatsApp.")
            time.sleep(WHATSAPP_MENSAGENS_INTERVALO_SEG)


def reload_seguro(pagina):
    global RELOAD_EM_ANDAMENTO
    RELOAD_EM_ANDAMENTO = True
    try:
        pagina.reload(timeout=45000)
        pagina.wait_for_load_state("networkidle", timeout=45000)
    except Exception as e:
        logger.error(f"Falha ao tentar dar reload na página: {e}")
    finally:
        time.sleep(1)
        RELOAD_EM_ANDAMENTO = False


def token_nao_informado_presente(pagina):
    try:
        locator = pagina.locator(SELECTOR_TOKEN_NAO_INFORMADO)
        return locator.count() > 0
    except Exception as e:
        logger.debug(f"Erro ao verificar mensagem 'token não informado': {e}")
        return False


MENSAGENS_DESLOGADO_IMEDIATO = [
    ("Token não informado", SELECTOR_TOKEN_NAO_INFORMADO),
    ("Ops! Nosso servidor está offline", SELECTOR_SERVIDOR_OFFLINE),
    ("Seção encerrada", SELECTOR_SECAO_ENCERRADA),
]


def detectar_mensagem_deslogado_imediata(pagina):
    for nome, selector in MENSAGENS_DESLOGADO_IMEDIATO:
        try:
            if pagina.locator(selector).count() > 0:
                return nome
        except Exception as e:
            logger.debug(f"Erro ao verificar mensagem '{nome}': {e}")
    return None


def nenhum_registro_presente_na_tela(pagina):
    try:
        return pagina.locator(SELECTOR_NENHUM_REGISTRO).count() > 0
    except Exception as e:
        logger.debug(f"Erro ao verificar 'Nenhum registro localizado.': {e}")
        return False


def bot_f_presente_na_tela(pagina):
    try:
        return pagina.locator(SELECTOR_BOT_F).count() > 0
    except Exception as e:
        logger.debug(f"Erro ao verificar chamado sentinela BOT-F: {e}")
        return False


def refazer_login(pagina):
    logger.warning("Mensagem 'Token não informado' detectada. Refazendo login...")
    enviar_alerta_telegram("🟡 Sessão expirada no CAMPO ('token não informado'). Refazendo login automaticamente...")

    # Esta função é a espera legítima mais longa do bot: somando os timeouts dá
    # até ~400s (60 do goto + 180 do dashboard + 30 do segundo goto + 120 do
    # 'Carregar chamados' + 10). Ela roda DENTRO do laço, então sem carimbar no
    # meio ela sozinha estoura TEMPO_MAX_SEM_BATIDA_SEG e o vigia mata um bot
    # que está apenas esperando o CAMPO. Cada batida abaixo vem logo DEPOIS de uma
    # espera terminar, ou seja, só carimba quando houve progresso de verdade --
    # se o Playwright pendurar de vez, nenhuma delas roda e o vigia age.
    try:
        pagina.goto("https://campo.provedor.example/login/", timeout=60000)
        pagina.wait_for_load_state("networkidle")
        pagina.wait_for_timeout(2000)
        registrar_batida_monitor()

        if "dashboard" not in pagina.url:
            botao_login = pagina.locator('button:has-text("Entrar com login Provedor")')
            if botao_login.count() > 0:
                botao_login.first.click()
                try:
                    pagina.wait_for_url("**/dashboard**", timeout=180000)
                    logger.info("Login refeito com sucesso, dashboard carregado.")
                except PlaywrightTimeoutError:
                    logger.warning("Timeout ao esperar dashboard após refazer login.")
                registrar_batida_monitor()
            else:
                logger.error("Botão de login não encontrado ao tentar refazer login.")

        logger.info("Retornando para a página de chamados após refazer login...")
        pagina.goto("https://campo.provedor.example/logistica/#/chamado", timeout=30000)
        pagina.wait_for_load_state("networkidle")
        registrar_batida_monitor()

        botao_carregar = pagina.locator(SELECTOR_BOTAO_CARREGAR_CHAMADOS)
        botao_carregar.wait_for(state="visible", timeout=120000)
        registrar_batida_monitor()
        if botao_carregar.is_enabled():
            botao_carregar.first.click()
            pagina.wait_for_timeout(10000)
            logger.info("Chamados recarregados após refazer login.")

        enviar_alerta_telegram("🟢 Login refeito com sucesso no CAMPO. Monitoramento retomado normalmente.")
        return True
    except Exception as e:
        logger.error(f"Falha ao refazer login após 'token não informado': {e}")
        enviar_alerta_telegram(f"⚠️ Falha ao refazer login automaticamente: {e}")
        return False


# A caixa "Exibir navegador?" (perguntar_exibir_navegador) morava aqui e foi
# removida em 07/08/2026: ela travava a subida do bot esperando um clique, o que
# impedia o início automático pela tarefa agendada. Quem decide isso agora são
# os comandos /exibirnavegador e /exibirpaineltv, em tempo de execução.


# =========================================================================
# PAINEL DE TV
#
# A tela da sala de operação, em Tkinter, dentro deste mesmo processo — não há
# navegador nem segunda aplicação para manter viva. Roda em 1366x768, que a TV
# amplia para 1080p, e a identidade visual acompanha a do site (grafite neutro
# com índigo de acento, ver site/web/static/estilo.css).
#
# O Canvas do Tk não tem canal alfa, não arredonda canto e não suaviza forma
# nenhuma. Três consequências que explicam quase todo o desenho daqui:
#
#   * forma é imagem: o fundo dos cards é gerado no PIL em 3x e reduzido, que é
#     de onde vem o canto arredondado limpo;
#   * traço fino não existe: numa tela que ainda vai ser ampliada, régua de 1px
#     sai dura e tremida, então superfície se separa por contraste de cor;
#   * transparência se imita: o fade interpola as cores a partir do fundo da
#     tela, em vez de mexer em opacidade.
# =========================================================================

QUADROS_ENTRADA = 9          # passos do fade de entrada de um card
_RAIO_CARD = 12
_BARRA_X0, _BARRA_X1 = 8, 11  # trilho de acento à esquerda do card
_MARGEM_TEXTO_ESQ = 26
_MARGEM_TEXTO_DIR = 18
ESPACO_ENTRE_CARDS = 9
ALTURA_CABECALHO_SECAO = 32
ALTURA_TOPO = 68

ESTADOS_STATUS = {
    "ONLINE":         (COR_VERDE, "ONLINE"),
    "OFFLINE":        (COR_VERMELHO, "OFFLINE"),
    "NÃO LOCALIZADO": (COR_LARANJA, "NÃO LOCALIZADO"),
    "VERIFICANDO":    (COR_TEXTO_MUTED, "VERIFICANDO"),
    "SEM DADOS":      (COR_TEXTO_MUTED, "SEM DADOS"),
}


# ------------------------------- cor -------------------------------------
def _hex_para_rgb(cor):
    cor = cor.lstrip("#")
    return tuple(int(cor[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_para_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def misturar_cores(cor_origem, cor_destino, t):
    """Interpola duas cores #RRGGBB. t=0 devolve a origem, t=1 o destino."""
    t = max(0.0, min(1.0, t))
    a = _hex_para_rgb(cor_origem)
    b = _hex_para_rgb(cor_destino)
    return _rgb_para_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


# ------------------------------ fonte ------------------------------------
_FAMILIAS_DISPONIVEIS = None


def _familia_disponivel(*candidatas):
    """Primeira família de fonte que existe de verdade nesta máquina.

    A versão anterior pedia "Segoe UI" em tudo. No Linux do servidor essa
    fonte não existe e o Tk caía num substituto silencioso. Resolvendo aqui,
    o painel fica igual no Windows e no Ubuntu — e se um dia a Inter (a fonte
    do site) for instalada, ele passa a usá-la sozinho.
    """
    global _FAMILIAS_DISPONIVEIS
    if _FAMILIAS_DISPONIVEIS is None:
        _FAMILIAS_DISPONIVEIS = {f.lower() for f in tkfont.families()}
    for nome in candidatas:
        if nome.lower() in _FAMILIAS_DISPONIVEIS:
            return nome
    return candidatas[-1]


class FontesPainel:
    """Tamanhos em PIXEL (size negativo) de propósito: em ponto, o mesmo
    número muda de tamanho conforme o DPI da tela."""

    def __init__(self):
        ui = _familia_disponivel("Inter", "DejaVu Sans", "Liberation Sans", "Segoe UI")
        mono = _familia_disponivel("JetBrains Mono", "DejaVu Sans Mono", "Consolas")

        self.hora = tkfont.Font(family=mono, size=-13)
        self.unidade = tkfont.Font(family=ui, size=-13, weight="bold")
        self.contrato = tkfont.Font(family=ui, size=-14, weight="bold")
        self.cliente = tkfont.Font(family=ui, size=-16, weight="bold")
        self.meta = tkfont.Font(family=ui, size=-13)
        self.rodape_card = tkfont.Font(family=ui, size=-13)
        self.selo = tkfont.Font(family=ui, size=-12, weight="bold")
        self.secao = tkfont.Font(family=ui, size=-14, weight="bold")
        self.secao_contagem = tkfont.Font(family=ui, size=-13)
        self.titulo_topo = tkfont.Font(family=ui, size=-14, weight="bold")
        self.clima = tkfont.Font(family=ui, size=-14)
        self.relogio = tkfont.Font(family=mono, size=-32, weight="bold")
        self.data = tkfont.Font(family=ui, size=-13)
        self.vazio = tkfont.Font(family=ui, size=-14)
        self.alerta_etiqueta = tkfont.Font(family=ui, size=-24, weight="bold")
        self.alerta_titulo = tkfont.Font(family=ui, size=-62, weight="bold")
        self.alerta_rotulo = tkfont.Font(family=ui, size=-16, weight="bold")
        self.alerta_valor = tkfont.Font(family=ui, size=-34, weight="bold")


FONTES_PAINEL = None   # instanciado quando já existe um root Tk


def encaixar_texto(texto, fonte, largura_max):
    """Corta com reticências em vez de quebrar linha: num painel de TV a
    altura do card é fixa, então texto que vaza tem que sumir, não empurrar."""
    texto = "" if texto is None else str(texto)
    if largura_max <= 0 or fonte.measure(texto) <= largura_max:
        return texto
    largura_util = largura_max - fonte.measure("…")
    if largura_util <= 0:
        return "…"
    corte = texto
    while corte and fonte.measure(corte) > largura_util:
        corte = corte[:-1]
    return corte.rstrip(" ·,|") + "…"


def espacar_titulo(texto, separador=" "):
    """Imita o letter-spacing dos rótulos maiúsculos do site (o Tk não tem)."""
    return separador.join(texto)


# ------------------------- formas desenhadas -----------------------------
_cache_formas = {}


def _reduzir_para_tk(img, largura, altura):
    return ImageTk.PhotoImage(img.resize((largura, altura), Image.LANCZOS))


def fundo_do_cartao(largura, altura, acento, passo=QUADROS_ENTRADA - 1):
    """Fundo do card no passo `passo` do fade (o último é o estado final).

    O fade sai da cor do fundo da tela e vai até as cores finais, o que faz o
    card materializar sem precisar de canal alfa — que o Tk não tem.
    """
    if not _PIL_DISPONIVEL:
        return None

    largura = max(40, int(largura))
    altura = max(20, int(altura))
    chave = ("card", largura, altura, acento, passo)
    if chave in _cache_formas:
        return _cache_formas[chave]

    t = passo / float(QUADROS_ENTRADA - 1)
    esc = 3  # desenha em 3x e reduz: é daí que vem o antialiasing
    img = Image.new("RGB", (largura * esc, altura * esc), COR_FUNDO)
    desenho = ImageDraw.Draw(img)

    desenho.rounded_rectangle(
        [0, 0, largura * esc - 1, altura * esc - 1],
        autenticador=_RAIO_CARD * esc,
        fill=misturar_cores(COR_FUNDO, COR_CARD, t),
        outline=misturar_cores(COR_FUNDO, COR_BORDA, t),
        width=esc,
    )

    margem = max(12, int(altura * 0.17))
    desenho.rounded_rectangle(
        [_BARRA_X0 * esc, margem * esc, _BARRA_X1 * esc, (altura - margem) * esc],
        autenticador=int(1.5 * esc),
        fill=misturar_cores(COR_FUNDO, acento, t),
    )

    imagem = _reduzir_para_tk(img, largura, altura)
    _cache_formas[chave] = imagem
    return imagem


def pilula_marcador(largura, altura, cor, fundo=COR_FUNDO):
    """Marcador arredondado dos cabeçalhos de seção."""
    if not _PIL_DISPONIVEL:
        return None
    chave = ("pilula", largura, altura, cor, fundo)
    if chave in _cache_formas:
        return _cache_formas[chave]

    esc = 4
    img = Image.new("RGB", (largura * esc, altura * esc), fundo)
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, largura * esc - 1, altura * esc - 1],
        autenticador=int(largura * esc / 2), fill=cor
    )
    imagem = _reduzir_para_tk(img, largura, altura)
    _cache_formas[chave] = imagem
    return imagem


def limpar_cache_formas():
    _cache_formas.clear()


# ---------------------- status de conexão (Autenticador) -----------------------
def registrar_contratos_painel(contratos):
    """O painel publica aqui os contratos que estão em tela; a thread do
    Autenticador lê daqui. Lista curta (no máximo MAX_ITENS_GARANTIA)."""
    with _TRAVA_CONTRATOS_PAINEL:
        _CONTRATOS_GARANTIA_PAINEL[:] = list(contratos)


def _contratos_painel_atuais():
    with _TRAVA_CONTRATOS_PAINEL:
        return list(_CONTRATOS_GARANTIA_PAINEL)


def thread_status_autenticador_painel():
    """Mantém o ONLINE/OFFLINE dos cards de garantia em dia.

    Roda solta porque a consulta ao Autenticador é lenta (até 35s de timeout) e
    depende da VPN — dentro do laço do Tk isso congelaria o relógio e a
    animação. O resultado volta pela FILA_STATUS_AUTENTICADOR.
    """
    falhas_seguidas = 0
    while True:
        try:
            contratos = _contratos_painel_atuais()
            if contratos:
                df, erro = consultar_autenticador_status(contratos)

                if erro or df is None or df.empty:
                    falhas_seguidas += 1
                    logger.warning(
                        f"Status do painel: Autenticador não respondeu ({erro or 'sem linhas'}). "
                        f"Falha {falhas_seguidas}."
                    )
                    # uma falha isolada não apaga o que está na tela; a
                    # segunda seguida, sim — status velho engana quem olha
                    if falhas_seguidas >= 2:
                        FILA_STATUS_AUTENTICADOR.put({
                            "status": {c: "SEM DADOS" for c in contratos},
                            "rodape": f"Autenticador · sem resposta {datetime.now():%H:%M}",
                        })
                else:
                    falhas_seguidas = 0
                    status = {}
                    for _, linha in df.iterrows():
                        contrato = str(linha.get("CONTRATO") or "").strip()
                        if contrato:
                            status[contrato] = str(linha.get("STATUS") or "").strip().upper()
                    FILA_STATUS_AUTENTICADOR.put({
                        "status": status,
                        "rodape": f"Autenticador · {datetime.now():%H:%M}",
                    })
        except Exception:
            logger.exception("Falha na thread de status do painel de TV.")

        # O intervalo longo é a cadência de quem está SAUDÁVEL: o status na
        # parede pode envelhecer 10 min sem prejuízo. Depois de uma falha,
        # esperar tudo isso deixaria o painel em "VERIFICANDO" por 10 minutos
        # por causa de uma disputa que costuma durar segundos -- então a
        # próxima tentativa vem bem antes, afastando-se a cada falha seguida
        # para não virar insistência contra um servidor ocupado.
        if falhas_seguidas:
            espera = min(INTERVALO_STATUS_AUTENTICADOR_SEG, 90 * falhas_seguidas)
        else:
            espera = INTERVALO_STATUS_AUTENTICADOR_SEG

        # acorda antes da hora quando entra garantia nova (ver _processar_fila)
        _PEDIDO_STATUS_AUTENTICADOR.wait(timeout=espera)
        _PEDIDO_STATUS_AUTENTICADOR.clear()


# ------------------------------- card ------------------------------------
class Cartao(tk.Canvas):
    """Um item da lista.

    É um Canvas e não um Frame com Labels porque assim dá para arredondar o
    canto, posicionar ao pixel e animar a entrada mexendo em cor e posição
    dos itens, sem criar nem destruir widget nenhum durante a animação.
    """

    # Três coisas acontecem juntas para o card PARECER que chegou, e não que
    # simplesmente apareceu:
    #   1. a altura abre em ease-out e empurra a lista para baixo;
    #   2. o conteúdo entra deslizando de cima e ASSENTA com uma ultrapassagem
    #      curta (ease-out-back) — é isso que dá peso à chegada;
    #   3. as cores sobem do fundo até o valor final.
    # A ultrapassagem fica só no conteúdo, nunca na altura: se a altura
    # passasse do ponto, a lista inteira daria um solavanco.
    DURACAO_ENTRADA = 0.6
    DESLOCAMENTO_INICIAL = 0.5      # fração da altura em que o conteúdo começa

    def __init__(self, master, altura, acento, evento, com_status=False):
        # A largura pedida é só um mínimo simbólico: quem decide de verdade é
        # o grid da coluna (o canvas estica com sticky="ew"). Se o canvas
        # pedisse a largura "certa", esse pedido viraria largura MÍNIMA da
        # coluna e as duas colunas juntas estourariam a tela.
        #
        # Não pode ser 1: um widget de 1px acrescentado a um container que já
        # foi realizado o Tk simplesmente não mapeia — fica invisível para
        # sempre. 80 é pequeno o bastante para não empurrar a coluna e grande
        # o bastante para o Tk levar a sério.
        super().__init__(
            master, width=80, height=altura,
            bg=COR_FUNDO, highlightthickness=0, bd=0, takefocus=0
        )
        self.largura = 0
        self.altura = altura
        self.acento = acento
        self.evento = evento
        self.com_status = com_status

        self._itens_texto = []      # [(id, cor_final)]
        self._id_fundo = None
        self._ref_fundo = None
        self._job_animacao = None
        self._job_selo = None
        self._passo_atual = QUADROS_ENTRADA - 1
        self._status = "VERIFICANDO"
        self._id_status_ponto = None
        self._id_status_texto = None
        self._id_selo_novo = None
        self._y_linha1 = 20
        self._pendente_novo = False
        self._duracao_selo = 30000
        self._deslocamento = 0.0

        self.bind("<Configure>", self._ao_redimensionar)

    # ---------------- largura ----------------
    def _ao_redimensionar(self, evento):
        # a animação de entrada mexe na altura e dispara <Configure> a cada
        # quadro; só a mudança de LARGURA obriga a redesenhar
        if evento.width == self.largura:
            return
        self._redesenhar_com_largura(evento.width)

    def garantir_desenho(self):
        """Desenha já, sem depender de o <Configure> chegar pela fila de
        eventos. Quem chama roda um update_idletasks antes, então o
        winfo_width() aqui já é a largura final que o grid deu."""
        self._redesenhar_com_largura(self.winfo_width())

    def _redesenhar_com_largura(self, largura):
        if largura == self.largura or largura < 100:
            return
        self.largura = largura
        self._desenhar()
        if self._pendente_novo and self._id_selo_novo is None and not self._job_animacao:
            self._desenhar_selo_novo()
        if self._passo_atual < QUADROS_ENTRADA - 1:
            self._aplicar_passo(self._passo_atual)

    # ---------------- desenho ----------------
    def _texto(self, x, y, texto, fonte, cor, ancora="w"):
        item = self.create_text(x, y, text=texto, font=fonte, fill=cor, anchor=ancora)
        self._itens_texto.append((item, cor))
        return item

    def _largura_texto(self, item):
        caixa = self.bbox(item)
        return 0 if not caixa else caixa[2] - caixa[0]

    def _desenhar(self):
        self.delete("all")
        self._itens_texto = []
        self._id_status_texto = None
        self._id_status_ponto = None
        self._id_selo_novo = None
        self._deslocamento = 0.0   # os itens voltam para a posição de base
        if self.largura < 60:
            return                 # ainda não recebeu largura do grid

        self._ref_fundo = fundo_do_cartao(self.largura, self.altura, self.acento)
        if self._ref_fundo is not None:
            self._id_fundo = self.create_image(0, 0, image=self._ref_fundo, anchor="nw")
        else:  # sem Pillow: retângulo chapado, feio mas funciona
            self._id_fundo = self.create_rectangle(
                0, 0, self.largura - 1, self.altura - 1,
                fill=COR_CARD, outline=COR_BORDA
            )

        ev = self.evento
        limite_dir = self.largura - _MARGEM_TEXTO_DIR

        # O bloco de texto é centralizado na vertical: assim o card pode
        # esticar para ocupar a tela toda sem deixar o conteúdo encostado
        # na borda de cima.
        linhas = 4 if self.com_status else 3
        espaco = max(20, min(30, (self.altura - 36) // (linhas - 1)))
        y = (self.altura - espaco * (linhas - 1)) // 2
        self._y_linha1 = y

        # linha 1: hora · unidade · contrato
        x = _MARGEM_TEXTO_ESQ
        item = self._texto(x, y, ev.get("timestamp", "--:--:--"),
                           FONTES_PAINEL.hora, COR_TEXTO_MUTED)
        x += self._largura_texto(item) + 12

        item = self._texto(x, y, str(ev.get("unidade", "N/D")).upper(),
                           FONTES_PAINEL.unidade, self.acento)
        x += self._largura_texto(item) + 12

        self._texto(x, y, f"Contrato {ev.get('contrato', 'N/D')}",
                    FONTES_PAINEL.contrato, COR_TEXTO)

        if self.com_status:
            self._desenhar_status()

        # linha 2: cliente
        y += espaco
        self._texto(
            _MARGEM_TEXTO_ESQ, y,
            encaixar_texto(ev.get("cliente", "N/D"), FONTES_PAINEL.cliente,
                           limite_dir - _MARGEM_TEXTO_ESQ - 58),
            FONTES_PAINEL.cliente, COR_TEXTO
        )

        # linha 3: bairro · telefones
        y += espaco
        partes = []
        if ev.get("bairro"):
            partes.append(str(ev["bairro"]))
        if ev.get("telefones"):
            partes.append(str(ev["telefones"]).replace(",", "  ·  "))
        self._texto(
            _MARGEM_TEXTO_ESQ, y,
            encaixar_texto("  ·  ".join(partes), FONTES_PAINEL.meta,
                           limite_dir - _MARGEM_TEXTO_ESQ),
            FONTES_PAINEL.meta, COR_TEXTO_MUTED
        )

        # linha 4 (só garantia): técnico OFS e serviço anterior
        if self.com_status:
            y += espaco
            detalhes = []
            if ev.get("tecnico_ofs"):
                detalhes.append(f"Téc. {ev['tecnico_ofs']}")
            if ev.get("tipo_anterior"):
                detalhes.append(f"{ev['tipo_anterior']} ({ev.get('dias_aging', '?')}d)")
            if detalhes:
                self._texto(
                    _MARGEM_TEXTO_ESQ, y,
                    encaixar_texto("  ·  ".join(detalhes), FONTES_PAINEL.rodape_card,
                                   limite_dir - _MARGEM_TEXTO_ESQ),
                    FONTES_PAINEL.rodape_card, COR_ROXO_CLARO
                )

    def _desenhar_status(self):
        # a bolinha é o glifo ● da própria fonte: sai suavizada pelo
        # renderizador de texto e entra sozinha no fade, sem virar imagem
        cor, rotulo = ESTADOS_STATUS.get(self._status, ESTADOS_STATUS["SEM DADOS"])
        y = self._y_linha1
        limite_dir = self.largura - _MARGEM_TEXTO_DIR

        self._id_status_texto = self.create_text(
            limite_dir, y, text=rotulo, font=FONTES_PAINEL.selo, fill=cor, anchor="e"
        )
        self._itens_texto.append((self._id_status_texto, cor))

        caixa = self.bbox(self._id_status_texto)
        self._id_status_ponto = self.create_text(
            (caixa[0] if caixa else limite_dir) - 8, y,
            text="●", font=FONTES_PAINEL.selo, fill=cor, anchor="e"
        )
        self._itens_texto.append((self._id_status_ponto, cor))

    def definir_status(self, status):
        """Troca o ONLINE/OFFLINE sem redesenhar o card inteiro."""
        if not self.com_status:
            return
        novo = (status or "SEM DADOS").upper()
        if novo not in ESTADOS_STATUS:
            novo = "SEM DADOS"
        if novo == self._status:
            return
        self._status = novo
        if self.largura < 60:
            return

        for item in (self._id_status_texto, self._id_status_ponto):
            if item is not None:
                self.delete(item)
        self._itens_texto = [
            (i, c) for (i, c) in self._itens_texto
            if i not in (self._id_status_texto, self._id_status_ponto)
        ]
        self._desenhar_status()
        if self._deslocamento:
            self.move(self._id_status_texto, 0, self._deslocamento)
            self.move(self._id_status_ponto, 0, self._deslocamento)

        # se o card ainda está no meio do fade, o status novo não pode
        # aparecer em cor cheia antes do resto
        if self._passo_atual < QUADROS_ENTRADA - 1:
            self._aplicar_passo(self._passo_atual)

    # ---------------- selo NOVO ----------------
    def marcar_como_novo(self, duracao_ms=30000, adiar=False):
        """adiar=True deixa o selo para o fim da animação: ele aparece no
        instante em que o card assenta, o que reforça a chegada."""
        self._pendente_novo = True
        self._duracao_selo = duracao_ms
        if not adiar and self.largura >= 60 and self._id_selo_novo is None:
            self._desenhar_selo_novo()

    def _desenhar_selo_novo(self):
        self._id_selo_novo = self.create_text(
            self.largura - _MARGEM_TEXTO_DIR, self._y_linha1 + 24,
            text="NOVO", font=FONTES_PAINEL.selo, fill=COR_ROXO, anchor="e"
        )
        self._itens_texto.append((self._id_selo_novo, COR_ROXO))
        if self._job_selo:
            try:
                self.after_cancel(self._job_selo)
            except Exception:
                pass
        self._job_selo = self.after(self._duracao_selo, self._apagar_selo_novo)

    def _apagar_selo_novo(self, passo=0):
        """Some devagar: apagado de uma vez, o selo pisca fora da tela e
        chama atenção justamente quando já não interessa mais."""
        self._job_selo = None
        if self._id_selo_novo is None:
            return
        total = 12
        if passo >= total:
            self.delete(self._id_selo_novo)
            self._itens_texto = [
                (i, c) for (i, c) in self._itens_texto if i != self._id_selo_novo
            ]
            self._id_selo_novo = None
            self._pendente_novo = False
            return
        try:
            self.itemconfigure(
                self._id_selo_novo,
                fill=misturar_cores(COR_ROXO, COR_CARD, passo / float(total))
            )
        except tk.TclError:
            return
        self._job_selo = self.after(45, lambda: self._apagar_selo_novo(passo + 1))

    # ---------------- animação ----------------
    @staticmethod
    def _suavizar_saida(t):
        return 1 - (1 - t) ** 3

    @staticmethod
    def _assentar(t):
        """ease-out-back: passa ~10% do destino e volta."""
        c1 = 1.70158
        c3 = c1 + 1
        return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2

    def _aplicar_passo(self, passo):
        """Pinta o card no estágio `passo` do fade (0 = invisível)."""
        self._passo_atual = passo
        t = passo / float(QUADROS_ENTRADA - 1)

        imagem = fundo_do_cartao(self.largura, self.altura, self.acento, passo)
        if imagem is not None:
            self._ref_fundo = imagem
            try:
                self.itemconfigure(self._id_fundo, image=imagem)
            except tk.TclError:
                return

        for item, cor_final in self._itens_texto:
            try:
                self.itemconfigure(item, fill=misturar_cores(COR_FUNDO, cor_final, t))
            except tk.TclError:
                pass

    def _aplicar_deslocamento(self, alvo):
        """Move TUDO que está no canvas para o deslocamento pedido."""
        delta = alvo - self._deslocamento
        if abs(delta) < 0.5:
            return
        try:
            self.move("all", 0, delta)
        except tk.TclError:
            return
        self._deslocamento = alvo

    def animar_entrada(self, ao_terminar=None):
        self._aplicar_passo(0)
        self._aplicar_deslocamento(-self.altura * self.DESLOCAMENTO_INICIAL)
        self.configure(height=max(6, int(self.altura * 0.22)))
        self._animar_quadro(time.monotonic(), ao_terminar)

    def _animar_quadro(self, inicio, ao_terminar):
        t = min(1.0, (time.monotonic() - inicio) / self.DURACAO_ENTRADA)
        abertura = self._suavizar_saida(t)
        pouso = self._assentar(t)

        try:
            self.configure(height=max(6, int(self.altura * (0.22 + 0.78 * abertura))))
        except tk.TclError:
            return

        self._aplicar_deslocamento(
            -self.altura * self.DESLOCAMENTO_INICIAL * (1 - pouso)
        )

        passo = int(round(min(1.0, t / 0.75) * (QUADROS_ENTRADA - 1)))
        if passo != self._passo_atual:
            self._aplicar_passo(passo)

        if t >= 1.0:
            self.configure(height=self.altura)
            self._aplicar_deslocamento(0.0)
            self._aplicar_passo(QUADROS_ENTRADA - 1)
            self._job_animacao = None
            if self._pendente_novo and self._id_selo_novo is None:
                self._desenhar_selo_novo()   # o selo aparece quando o card pousa
            if ao_terminar:
                ao_terminar()
            return

        self._job_animacao = self.after(
            16, lambda: self._animar_quadro(inicio, ao_terminar)
        )

    def redimensionar(self, altura):
        """Só a altura: a largura chega sozinha pelo <Configure> do grid."""
        if altura == self.altura:
            return
        self.altura = altura
        self.configure(height=altura)
        self._desenhar()
        if self._pendente_novo:
            self._desenhar_selo_novo()

    def destroy(self):
        for job in (self._job_animacao, self._job_selo):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        super().destroy()


# --------------------- tamanho real da tela (X11) -------------------------
# Estado da conexão própria com o X. Aberta uma vez e reaproveitada: o vigia
# da geometria pergunta a cada 5s, e abrir/fechar conexão nesse ritmo seria
# desperdício. Fica tudo em None no Windows, onde nada disto existe.
_x11 = None
_x11_display = None
_x11_indisponivel = False


def _abrir_x11():
    """Conexão própria com o X, só para ler a geometria da raiz."""
    global _x11, _x11_display, _x11_indisponivel
    if _x11_indisponivel or _x11_display is not None:
        return _x11_display
    if not sys.platform.startswith("linux"):
        _x11_indisponivel = True
        return None
    try:
        lib = ctypes.CDLL("libX11.so.6")
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        display = lib.XOpenDisplay(None)
        if not display:
            raise RuntimeError("XOpenDisplay devolveu nulo")
        _x11, _x11_display = lib, display
        return _x11_display
    except Exception as e:
        logger.warning(f"Painel de TV: sem acesso ao X para medir a tela ({e}). "
                       "Vai usar a medida do Tk.")
        _x11_indisponivel = True
        return None


def tamanho_real_da_tela(janela):
    """Tamanho da tela AGORA — não o que o Tk anotou quando conectou.

    `winfo_screenwidth()` devolve o valor que o Xlib gravou na abertura da
    conexão. Quando o xrandr muda a resolução DEPOIS disso, esse número não
    acompanha: o Xlib só o corrige se o cliente pedir (XRRUpdateConfiguration)
    ao receber o evento do RANDR, e o Tk não escuta RANDR.

    Foi assim que o painel ficou 2732x768 numa tela de 1366x768 — o dobro da
    largura, com a coluna de GARANTIAS e o relógio desenhados fora da tela.
    E o vigia de geometria não viu problema nenhum porque estava comparando o
    valor velho com ele mesmo: os dois lados da conta vinham da mesma medida
    congelada. Vigia que mede com a régua errada jura que está tudo certo.

    A janela RAIZ do X é a fonte de verdade: ela é redimensionada de fato
    quando o modo muda. Fora do X11, ou se a leitura falhar, cai no Tk.
    """
    display = _abrir_x11()
    if display is not None:
        try:
            raiz = _x11.XDefaultRootWindow(ctypes.c_void_p(display))
            devolve_raiz = ctypes.c_ulong()
            x = ctypes.c_int(); y = ctypes.c_int()
            largura = ctypes.c_uint(); altura = ctypes.c_uint()
            borda = ctypes.c_uint(); profundidade = ctypes.c_uint()
            ok = _x11.XGetGeometry(
                ctypes.c_void_p(display), ctypes.c_ulong(raiz),
                ctypes.byref(devolve_raiz), ctypes.byref(x), ctypes.byref(y),
                ctypes.byref(largura), ctypes.byref(altura),
                ctypes.byref(borda), ctypes.byref(profundidade),
            )
            if ok and largura.value > 0 and altura.value > 0:
                return largura.value, altura.value
        except Exception as e:
            logger.warning(f"Painel de TV: falha ao medir a tela pelo X ({e}).")
    return janela.winfo_screenwidth(), janela.winfo_screenheight()


# ------------------------------ coluna -----------------------------------
class ColunaPainel(tk.Frame):
    """Cabeçalho de seção + a lista de cards embaixo."""

    def __init__(self, master, titulo, acento, maximo, altura_minima,
                 altura_maxima, com_status=False):
        super().__init__(master, bg=COR_FUNDO)
        self.acento = acento
        self.maximo = maximo
        self.altura_minima = altura_minima
        self.altura_maxima = altura_maxima
        self.com_status = com_status
        self.altura_card = altura_minima
        self.cartoes = []

        cabecalho = tk.Frame(self, bg=COR_FUNDO, height=ALTURA_CABECALHO_SECAO)
        cabecalho.pack(fill="x", pady=(0, 8))
        cabecalho.pack_propagate(False)

        self._ref_marca = pilula_marcador(4, 16, acento)
        marca = tk.Label(cabecalho, bg=COR_FUNDO)
        if self._ref_marca is not None:
            marca.configure(image=self._ref_marca)
        marca.pack(side="left", pady=(2, 0))

        tk.Label(
            cabecalho, text=espacar_titulo(titulo), bg=COR_FUNDO, fg=COR_TEXTO,
            font=FONTES_PAINEL.secao, anchor="w"
        ).pack(side="left", padx=(11, 0))

        self.rotulo_contagem = tk.Label(
            cabecalho, text="0", bg=COR_FUNDO, fg=COR_TEXTO_MUTED,
            font=FONTES_PAINEL.secao_contagem, anchor="w"
        )
        self.rotulo_contagem.pack(side="left", padx=(12, 0))

        self.rotulo_extra = tk.Label(
            cabecalho, text="", bg=COR_FUNDO, fg=COR_TEXTO_MUTED,
            font=FONTES_PAINEL.secao_contagem, anchor="e"
        )
        self.rotulo_extra.pack(side="right")

        self.lista = tk.Frame(self, bg=COR_FUNDO)
        self.lista.pack(fill="both", expand=True)
        self.lista.grid_columnconfigure(0, weight=1)

        self.vazio = tk.Label(
            self.lista, text="Sem registros por enquanto",
            bg=COR_FUNDO, fg=COR_TEXTO_MUTED, font=FONTES_PAINEL.vazio
        )
        self.vazio.grid(row=0, column=0, pady=34, sticky="ew")

    def altura_util(self):
        altura = self.lista.winfo_height()
        if altura <= 20:
            altura = tamanho_real_da_tela(self.winfo_toplevel())[1] - 150
        return max(200, altura)

    def calcular_altura_card(self):
        """Divide a altura da coluna pelo NÚMERO MÁXIMO de itens, e não pelos
        que estão em tela: assim o card não muda de tamanho a cada evento que
        chega — a lista cresce sempre com a mesma medida e, cheia, ocupa a
        tela inteira sem sobrar faixa vazia."""
        disponivel = self.altura_util() - (self.maximo - 1) * ESPACO_ENTRE_CARDS
        altura = disponivel // self.maximo
        return max(self.altura_minima, min(self.altura_maxima, altura))

    def adicionar(self, evento, animar=True):
        if self.vazio.winfo_ismapped():
            self.vazio.grid_forget()

        self.altura_card = self.calcular_altura_card()
        cartao = Cartao(self.lista, self.altura_card, self.acento, evento,
                        com_status=self.com_status)
        self.cartoes.insert(0, cartao)
        self._reposicionar()

        while len(self.cartoes) > self.maximo:
            self.cartoes.pop().destroy()

        # deixa o grid dar a largura ao card ANTES de qualquer coisa: senão o
        # primeiro quadro da animação sai vazio e o conteúdo "pipoca" no fim
        self.lista.update_idletasks()
        cartao.garantir_desenho()

        if animar:
            cartao.marcar_como_novo(adiar=True)
            cartao.animar_entrada()

        self.rotulo_contagem.configure(text=str(len(self.cartoes)))
        return cartao

    def _reposicionar(self):
        for indice, cartao in enumerate(self.cartoes):
            cartao.grid(row=indice, column=0, sticky="ew",
                        pady=(0, ESPACO_ENTRE_CARDS))

    def definir_extra(self, texto):
        self.rotulo_extra.configure(text=texto)

    def redimensionar(self):
        self.altura_card = self.calcular_altura_card()
        for cartao in self.cartoes:
            cartao.redimensionar(self.altura_card)

    def cartao_por_contrato(self):
        mapa = {}
        for cartao in self.cartoes:
            contrato = str(cartao.evento.get("contrato", "")).strip()
            if contrato:
                mapa.setdefault(contrato, []).append(cartao)
        return mapa

    def contratos(self):
        return [
            str(c.evento.get("contrato", "")).strip()
            for c in self.cartoes
            if str(c.evento.get("contrato", "")).strip()
        ]


# ------------------------------ painel -----------------------------------
class PainelTV(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Central de Monitoramento - CAMPO Logística")
        self.configure(bg=COR_FUNDO)

        global FONTES_PAINEL
        FONTES_PAINEL = FontesPainel()

        self._em_tela_cheia = False
        self._tela_conhecida = tamanho_real_da_tela(self)
        self._logo_ref = None
        self._job_ajuste = None
        self._job_preaquecer = None
        self._alerta_ativo = False
        self._job_alerta = None
        self._job_respiracao = None
        self._job_barra = None
        self._fase_respiracao = 0.0
        self._contrato_em_alerta = ""
        self._etiqueta_status_alerta = None
        self._itens_overlay = []

        self.dados_capex_salvos = []
        self.dados_garantias_salvos = []

        self.update_idletasks()
        self.after(150, self._aplicar_tela_cheia)

        self.bind("<Escape>", lambda e: self._alternar_tela_cheia(False))
        self.bind("<F11>", lambda e: self._alternar_tela_cheia(not self._em_tela_cheia))
        self.bind("<Configure>", self._agendar_ajuste)

        self._montar_layout()
        self._montar_overlay_alerta()
        self._carregar_historico_painel()
        self._atualizar_relogio()
        self._processar_fila()
        self._processar_fila_clima()
        self._processar_fila_status()
        self._vigiar_geometria()

        iniciar_threads_do_painel()

    # ---------------- layout ----------------
    def _montar_layout(self):
        # O topo se separa do corpo só pela cor da superfície. Não há régua de
        # 1px: no Tk ela sairia dura e desalinhada, e o site também não usa
        # borda aqui — usa contraste de superfície.
        topo = tk.Frame(self, bg=COR_SUPERFICIE, height=ALTURA_TOPO)
        topo.pack(fill="x", side="top")
        topo.pack_propagate(False)

        esquerda = tk.Frame(topo, bg=COR_SUPERFICIE)
        esquerda.pack(side="left", padx=(26, 0))

        self.logo_label = tk.Label(esquerda, bg=COR_SUPERFICIE)
        logo = self._carregar_imagem_logo(altura_desejada=34)
        if logo is not None:
            self._logo_ref = logo
            self.logo_label.configure(image=logo)
        self.logo_label.pack(side="left", pady=(2, 0))

        tk.Label(
            esquerda, text=espacar_titulo("CENTRAL DE ALERTA"),
            bg=COR_SUPERFICIE, fg=COR_TEXTO_MUTED, font=FONTES_PAINEL.titulo_topo
        ).pack(side="left", padx=(26, 0))

        direita = tk.Frame(topo, bg=COR_SUPERFICIE)
        direita.pack(side="right", padx=(0, 28))

        bloco_relogio = tk.Frame(direita, bg=COR_SUPERFICIE)
        bloco_relogio.pack(side="right", pady=(6, 6))

        self.relogio_label = tk.Label(
            bloco_relogio, text="", bg=COR_SUPERFICIE, fg=COR_TEXTO,
            font=FONTES_PAINEL.relogio, anchor="e"
        )
        self.relogio_label.pack(side="bottom", anchor="e")

        self.data_label = tk.Label(
            bloco_relogio, text="", bg=COR_SUPERFICIE, fg=COR_TEXTO_MUTED,
            font=FONTES_PAINEL.data, anchor="e"
        )
        self.data_label.pack(side="bottom", anchor="e")

        self.clima_label = tk.Label(
            direita, text=f"{CIDADE_CLIMA} · carregando…",
            bg=COR_SUPERFICIE, fg=COR_TEXTO_MUTED, font=FONTES_PAINEL.clima, anchor="e"
        )
        self.clima_label.pack(side="right", padx=(0, 30))

        corpo = tk.Frame(self, bg=COR_FUNDO)
        corpo.pack(fill="both", expand=True, padx=26, pady=(18, 20))
        corpo.grid_columnconfigure(0, weight=1, uniform="col")
        corpo.grid_columnconfigure(1, weight=1, uniform="col")
        corpo.grid_rowconfigure(0, weight=1)

        self.coluna_capex = ColunaPainel(
            corpo, "ENTRANTES DE CAPEX", COR_DESTAQUE, MAX_ITENS_CAPEX,
            altura_minima=74, altura_maxima=104
        )
        self.coluna_capex.grid(row=0, column=0, sticky="nsew", padx=(0, 13))

        self.coluna_garantia = ColunaPainel(
            corpo, "GARANTIAS", COR_VERMELHO, MAX_ITENS_GARANTIA,
            altura_minima=96, altura_maxima=136, com_status=True
        )
        self.coluna_garantia.grid(row=0, column=1, sticky="nsew", padx=(13, 0))
        self.coluna_garantia.definir_extra("Autenticador · aguardando")

    def _carregar_imagem_logo(self, altura_desejada=34):
        for caminho in CAMINHOS_LOGO_PAINEL:
            if not os.path.exists(caminho):
                continue
            try:
                if _PIL_DISPONIVEL:
                    imagem = Image.open(caminho).convert("RGBA")
                    proporcao = altura_desejada / float(imagem.height)
                    imagem = imagem.resize(
                        (max(1, int(imagem.width * proporcao)), altura_desejada),
                        Image.LANCZOS
                    )
                    # o PNG é transparente: achatar sobre a cor do topo, senão
                    # o Tk compõe sobre preto e deixa uma auréola em volta
                    fundo = Image.new("RGBA", imagem.size, COR_SUPERFICIE)
                    return ImageTk.PhotoImage(
                        Image.alpha_composite(fundo, imagem).convert("RGB")
                    )
                imagem = tk.PhotoImage(file=caminho)
                fator = max(1, round(imagem.height() / altura_desejada))
                return imagem.subsample(fator, fator)
            except Exception as e:
                logger.warning(f"Falha ao carregar o logo '{os.path.basename(caminho)}': {e}")
                continue
        logger.warning("Nenhum logo encontrado para o Painel de TV.")
        return None

    def _aplicar_tela_cheia(self):
        self.update_idletasks()
        tela = tamanho_real_da_tela(self)
        self.geometry(f"{tela[0]}x{tela[1]}+0+0")
        try:
            self.attributes("-fullscreen", True)
        except Exception:
            pass
        try:
            self.state("zoomed")
        except Exception:
            pass
        self._tela_conhecida = tela
        self._em_tela_cheia = True

    def _vigiar_geometria(self):
        """Reage a tela que muda de tamanho DEBAIXO da janela.

        No arranque o X sobe com a tela do notebook, e só depois o script do
        HDMI passa a TV a primária e desliga a do notebook. Quem abriu em tela
        cheia antes dessa troca fica com a janela do tamanho antigo — e como
        nenhum evento avisa que a RESOLUÇÃO mudou (a janela em si não foi
        redimensionada), o painel ficava com metade do conteúdo fora da tela
        até alguém reiniciar o bot. Vale para qualquer mexida no HDMI, não só
        no boot: tirar e recolocar o cabo cai no mesmo caso.

        A medida vem de tamanho_real_da_tela(), não de winfo_screenwidth():
        veja lá por que a segunda mente depois de um xrandr.
        """
        tela = tamanho_real_da_tela(self)
        if tela != self._tela_conhecida:
            anterior = self._tela_conhecida
            self._tela_conhecida = tela
            if self._em_tela_cheia:
                logger.info(
                    f"Painel de TV: a tela mudou de {anterior[0]}x{anterior[1]} para "
                    f"{tela[0]}x{tela[1]}. Reaplicando tela cheia."
                )
                self._aplicar_tela_cheia()
        elif self._em_tela_cheia and self.winfo_width() != tela[0]:
            # tela cheia pedida mas a janela nao acompanhou (WM atravessado)
            self._aplicar_tela_cheia()
        self.after(5000, self._vigiar_geometria)

    def _alternar_tela_cheia(self, ligar):
        if ligar:
            self._aplicar_tela_cheia()
            return
        try:
            self.attributes("-fullscreen", False)
        except Exception:
            pass
        self._em_tela_cheia = False
        self.geometry("1600x900")

    def _agendar_ajuste(self, evento=None):
        # binding de <Configure> na janela também recebe o Configure de TODO
        # widget filho (a bindtag do toplevel está na cadeia de cada um). Sem
        # este filtro, cada quadro da animação de um card reagendaria um
        # recálculo geral do painel.
        if evento is not None and evento.widget is not self:
            return
        if self._job_ajuste:
            self.after_cancel(self._job_ajuste)
        self._job_ajuste = self.after(180, self._reajustar_cartoes)

    def _reajustar_cartoes(self):
        self._job_ajuste = None
        limpar_cache_formas()
        self.coluna_capex.redimensionar()
        self.coluna_garantia.redimensionar()
        self._agendar_preaquecimento()

    def _agendar_preaquecimento(self):
        if self._job_preaquecer:
            self.after_cancel(self._job_preaquecer)
        self._job_preaquecer = self.after(1500, self._preaquecer_fundos)

    def _preaquecer_fundos(self, pendentes=None):
        """Gera antes da hora as imagens dos passos do fade.

        Sem isto, o primeiro entrante do dia paga a conta: são 9 imagens por
        tamanho/cor desenhadas em 3x e reduzidas, e nesta máquina isso trava
        a tela bem no meio da animação. Uma imagem por tique mantém o painel
        respondendo enquanto o cache enche.
        """
        self._job_preaquecer = None
        if not _PIL_DISPONIVEL:
            return

        if pendentes is None:
            pendentes = []
            for coluna in (self.coluna_capex, self.coluna_garantia):
                largura = coluna.lista.winfo_width()
                if largura <= 100:
                    return
                for passo in range(QUADROS_ENTRADA):
                    pendentes.append((largura, coluna.altura_card,
                                      coluna.acento, passo))

        if not pendentes:
            return
        largura, altura, acento, passo = pendentes[0]
        fundo_do_cartao(largura, altura, acento, passo)
        self._job_preaquecer = self.after(
            30, lambda: self._preaquecer_fundos(pendentes[1:])
        )

    # ---------------- histórico em disco ----------------
    def _salvar_historico_painel(self):
        try:
            dados = {
                "capex": self.dados_capex_salvos,
                "garantia": self.dados_garantias_salvos
            }
            salvar_json_atomico(ARQUIVO_HISTORICO_PAINEL, dados,
                                ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Falha ao salvar histórico do painel de TV: {e}")

    def _carregar_historico_painel(self):
        if not os.path.exists(ARQUIVO_HISTORICO_PAINEL):
            return
        try:
            with open(ARQUIVO_HISTORICO_PAINEL, "r", encoding="utf-8") as f:
                dados = json.load(f)

            self.dados_capex_salvos = dados.get("capex", [])[:MAX_ITENS_CAPEX]
            self.dados_garantias_salvos = dados.get("garantia", [])[:MAX_ITENS_GARANTIA]

            # do mais antigo para o mais novo: cada adicionar() insere no topo
            for evento in reversed(self.dados_capex_salvos):
                self.coluna_capex.adicionar(evento, animar=False)
            for evento in reversed(self.dados_garantias_salvos):
                self.coluna_garantia.adicionar(evento, animar=False)

            self._publicar_contratos_garantia()
        except Exception as e:
            logger.warning(f"Falha ao carregar histórico do painel de TV: {e}")

    def _publicar_contratos_garantia(self):
        registrar_contratos_painel(self.coluna_garantia.contratos())

    # ---------------- relógio / clima ----------------
    def _atualizar_relogio(self):
        agora = datetime.now()
        self.relogio_label.configure(text=agora.strftime("%H:%M:%S"))
        self.data_label.configure(text=agora.strftime("%d/%m/%Y"))
        self.after(1000, self._atualizar_relogio)

    def _processar_fila_clima(self):
        previsao_mais_recente = None
        try:
            while True:
                previsao_mais_recente = FILA_CLIMA.get_nowait()
        except queue.Empty:
            pass

        if previsao_mais_recente is not None:
            self._atualizar_label_clima(previsao_mais_recente)

        self.after(1000, self._processar_fila_clima)

    def _atualizar_label_clima(self, previsao):
        temperatura = previsao.get('temperatura')
        descricao = previsao.get('descricao', 'N/D')
        emoji = previsao.get('emoji', '')

        if temperatura is not None:
            texto = f"{emoji} {CIDADE_CLIMA}  ·  {temperatura:.0f}°C  ·  {descricao}"
        else:
            texto = f"{emoji} {CIDADE_CLIMA}  ·  {descricao}"

        self.clima_label.configure(text=texto.strip())

    # ---------------- eventos ----------------
    def _processar_fila(self):
        # /ocultarpaineltv chega por outra thread, e Tk não aceita ser destruído
        # de fora da principal. Então o comando só limpa o Event e o fechamento
        # acontece aqui, dentro do próprio laço do Tk, que é onde é seguro.
        if not painel_tv_pedido():
            logger.info("Painel de TV fechado a pedido do grupo (/ocultarpaineltv).")
            self.destroy()
            return

        try:
            while True:
                evento = FILA_EVENTOS_TV.get_nowait()
                if evento.get('tipo') == 'garantia':
                    self.coluna_garantia.adicionar(evento)
                    self.dados_garantias_salvos.insert(0, evento)
                    del self.dados_garantias_salvos[MAX_ITENS_GARANTIA:]
                    self._publicar_contratos_garantia()
                    _PEDIDO_STATUS_AUTENTICADOR.set()   # consulta o Autenticador já
                    self._exibir_alerta_garantia(evento)
                else:
                    self.coluna_capex.adicionar(evento)
                    self.dados_capex_salvos.insert(0, evento)
                    del self.dados_capex_salvos[MAX_ITENS_CAPEX:]
                self._salvar_historico_painel()
        except queue.Empty:
            pass
        self.after(300, self._processar_fila)

    def _processar_fila_status(self):
        ultimo = None
        try:
            while True:
                ultimo = FILA_STATUS_AUTENTICADOR.get_nowait()
        except queue.Empty:
            pass

        if ultimo is not None:
            mapa = self.coluna_garantia.cartao_por_contrato()
            for contrato, status in ultimo.get("status", {}).items():
                for cartao in mapa.get(str(contrato), []):
                    cartao.definir_status(status)
            self.coluna_garantia.definir_extra(ultimo.get("rodape", ""))

            # o alerta pode estar na tela antes de o Autenticador responder
            if self._alerta_ativo and self._contrato_em_alerta:
                novo = ultimo.get("status", {}).get(self._contrato_em_alerta)
                if novo and self._etiqueta_status_alerta is not None:
                    cor, rotulo = ESTADOS_STATUS.get(
                        str(novo).upper(), ESTADOS_STATUS["SEM DADOS"]
                    )
                    try:
                        self._etiqueta_status_alerta.configure(text=f"● {rotulo}", fg=cor)
                    except tk.TclError:
                        pass

        self.after(1000, self._processar_fila_status)

    # ---------------- alerta de garantia ----------------
    #
    # Alarme de sala: tem que ser visto do outro lado do ambiente e, ao mesmo
    # tempo, ser lido por quem chega perto. Quem pulsa é só a MOLDURA, e por
    # interpolação — respirar chama a atenção igual a piscar, sem castigar
    # quem trabalha na sala nem apagar o texto meio segundo a cada ciclo. O
    # miolo fica escuro, com os dados em blocos grandes e uma barra mostrando
    # quanto falta para o alerta sair sozinho.
    def _montar_overlay_alerta(self):
        self.overlay = tk.Frame(self, bg=COR_ALERTA)
        self.overlay_miolo = tk.Frame(self.overlay, bg=COR_FUNDO)
        self.overlay_miolo.pack(fill="both", expand=True, padx=18, pady=18)

        # a barra de tempo é empacotada ANTES do conteúdo: o conteúdo usa
        # expand=True e, se viesse primeiro, engoliria a faixa inteira
        self.overlay_trilho = tk.Frame(self.overlay_miolo, bg=COR_CARD, height=7)
        self.overlay_trilho.pack(fill="x", side="bottom")
        self.overlay_trilho.pack_propagate(False)
        self.overlay_barra = tk.Frame(self.overlay_trilho, bg=COR_ALERTA)
        self.overlay_barra.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        self.overlay_conteudo = tk.Frame(self.overlay_miolo, bg=COR_FUNDO)
        self.overlay_conteudo.pack(fill="both", expand=True, padx=64)

    def _rotulo_overlay(self, master, texto, fonte, cor, **kwargs):
        etiqueta = tk.Label(master, text=texto, bg=COR_FUNDO, fg=cor, font=fonte,
                            anchor="w", justify="left", **kwargs)
        self._itens_overlay.append((etiqueta, cor))
        return etiqueta

    def _largura_conteudo_alerta(self):
        """Largura real de onde o texto do alerta cabe.

        Medida do próprio overlay, e não da janela: no momento em que o alerta
        é montado a janela pode ainda não ter chegado ao tamanho final, e uma
        medida pequena demais faz o nome do cliente sair truncado. Por isso o
        overlay é posicionado antes de o conteúdo ser montado.
        """
        largura = self.overlay_conteudo.winfo_width()
        if largura <= 200:   # ainda não dimensionado: cai na tela toda
            largura = tamanho_real_da_tela(self)[0] - 2 * 18 - 2 * 64
        return max(400, largura)

    def _largura_coluna_alerta(self):
        return max(200, self._largura_conteudo_alerta() // 4)

    def _bloco_overlay(self, master, rotulo, valor, cor_valor=COR_TEXTO,
                       linha=0, coluna=0, colunas=1, espaco_acima=0):
        """Devolve a etiqueta do VALOR — é ela que pode mudar depois (o
        status de conexão chega do Autenticador com o alerta já na tela)."""
        caixa = tk.Frame(master, bg=COR_FUNDO)
        caixa.grid(row=linha, column=coluna, columnspan=colunas, sticky="w",
                   pady=(espaco_acima, 0))
        self._rotulo_overlay(caixa, espacar_titulo(rotulo),
                             FONTES_PAINEL.alerta_rotulo, COR_TEXTO_MUTED).pack(anchor="w")
        etiqueta_valor = self._rotulo_overlay(
            caixa,
            encaixar_texto(valor, FONTES_PAINEL.alerta_valor,
                           self._largura_coluna_alerta() * colunas - 26),
            FONTES_PAINEL.alerta_valor, cor_valor
        )
        etiqueta_valor.pack(anchor="w", pady=(5, 0))
        return etiqueta_valor

    def _status_do_contrato(self, contrato):
        for cartao in self.coluna_garantia.cartoes:
            if str(cartao.evento.get("contrato", "")).strip() == contrato:
                return cartao._status
        return "VERIFICANDO"

    def _exibir_alerta_garantia(self, evento):
        # O overlay entra em tela ANTES do conteúdo. Parece detalhe de ordem,
        # mas é o que dá a _largura_conteudo_alerta() uma largura real para
        # medir: com o texto montado primeiro, a medida sai de uma janela
        # ainda sem tamanho final e trunca o nome do cliente.
        if not self._alerta_ativo:
            self._alerta_ativo = True
            self.overlay.place(x=0, y=0, relwidth=1, relheight=1)
            self.overlay.lift()
            self._fase_respiracao = 0.0
            self._respirar_moldura()
        self.update_idletasks()

        for widget in self.overlay_conteudo.winfo_children():
            widget.destroy()
        self._itens_overlay = []
        self._contrato_em_alerta = str(evento.get("contrato", "")).strip()

        # expand=True sem fill vertical => o bloco fica centralizado na altura
        bloco = tk.Frame(self.overlay_conteudo, bg=COR_FUNDO)
        bloco.pack(expand=True, fill="x")

        topo = tk.Frame(bloco, bg=COR_FUNDO)
        topo.pack(anchor="w")
        self._rotulo_overlay(
            topo, espacar_titulo("ALERTA DE GARANTIA"),
            FONTES_PAINEL.alerta_etiqueta, COR_ALERTA
        ).pack(side="left")
        if evento.get("timestamp"):
            self._rotulo_overlay(
                topo, evento["timestamp"], FONTES_PAINEL.alerta_etiqueta, COR_TEXTO_MUTED
            ).pack(side="left", padx=(22, 0))

        self._rotulo_overlay(
            bloco,
            encaixar_texto(evento.get("cliente", "N/D"), FONTES_PAINEL.alerta_titulo,
                           self._largura_conteudo_alerta()),
            FONTES_PAINEL.alerta_titulo, COR_TEXTO
        ).pack(anchor="w", pady=(16, 0))

        # uma grade só para as fileiras: assim as colunas ficam alinhadas
        # entre si em vez de cada fileira medir do seu jeito
        grade = tk.Frame(bloco, bg=COR_FUNDO)
        grade.pack(anchor="w", fill="x", pady=(38, 0))
        for indice in range(4):
            grade.grid_columnconfigure(indice, weight=1, uniform="alerta")

        self._bloco_overlay(grade, "CONTRATO", evento.get("contrato", "N/D"), coluna=0)
        self._bloco_overlay(grade, "UNIDADE",
                            str(evento.get("unidade", "N/D")).upper(),
                            COR_DESTAQUE, coluna=1)
        self._bloco_overlay(grade, "BAIRRO", evento.get("bairro", "N/D"), coluna=2)

        cor_status, rotulo_status = ESTADOS_STATUS.get(
            self._status_do_contrato(self._contrato_em_alerta),
            ESTADOS_STATUS["VERIFICANDO"]
        )
        self._etiqueta_status_alerta = self._bloco_overlay(
            grade, "CONEXÃO", f"● {rotulo_status}", cor_status, coluna=3
        )

        # nomes de técnico e tipo de serviço são longos: ganham duas colunas
        # cada um, senão o valor entra cortado — e aqui nada pode faltar
        self._bloco_overlay(
            grade, "TELEFONE(S)",
            str(evento.get("telefones", "N/D")).replace(",", "   ·   "),
            linha=1, coluna=0, colunas=2, espaco_acima=34
        )
        if evento.get("tecnico_ofs"):
            self._bloco_overlay(grade, "TÉCNICO OFS", evento["tecnico_ofs"],
                                COR_ROXO_CLARO, linha=1, coluna=2, colunas=2,
                                espaco_acima=34)
        if evento.get("tipo_anterior"):
            self._bloco_overlay(
                grade, "SERVIÇO ANTERIOR",
                f"{evento['tipo_anterior']}  ·  concluído há "
                f"{evento.get('dias_aging', '?')} dia(s)",
                COR_ROXO_CLARO, linha=2, coluna=0, colunas=3, espaco_acima=34
            )

        agora = time.monotonic()
        self._animar_entrada_alerta(agora)
        self._animar_barra_tempo(agora)

        if self._job_alerta:
            self.after_cancel(self._job_alerta)
        self._job_alerta = self.after(DURACAO_ALERTA_MS, self._ocultar_alerta)

    def _animar_entrada_alerta(self, inicio):
        duracao = 0.45
        t = min(1.0, (time.monotonic() - inicio) / duracao)
        suave = 1 - (1 - t) ** 3
        for widget, cor_final in self._itens_overlay:
            try:
                widget.configure(fg=misturar_cores(COR_FUNDO, cor_final, suave))
            except tk.TclError:
                return
        if t < 1.0:
            self.after(16, lambda: self._animar_entrada_alerta(inicio))

    def _respirar_moldura(self):
        """Pulso contínuo da moldura. Interpolado, não ligado/desligado: de
        longe chama igual e de perto não castiga quem trabalha na sala."""
        if not self._alerta_ativo:
            return
        self._fase_respiracao = (self._fase_respiracao + 0.055) % 1.0
        onda = (1 - math.cos(self._fase_respiracao * 2 * math.pi)) / 2
        try:
            self.overlay.configure(bg=misturar_cores(COR_ALERTA_ESCURO, COR_ALERTA, onda))
        except tk.TclError:
            return
        self._job_respiracao = self.after(40, self._respirar_moldura)

    def _animar_barra_tempo(self, inicio):
        if not self._alerta_ativo:
            return
        restante = 1.0 - min(
            1.0, (time.monotonic() - inicio) / (DURACAO_ALERTA_MS / 1000.0)
        )
        try:
            self.overlay_barra.place_configure(relwidth=max(0.0, restante))
        except tk.TclError:
            return
        if restante > 0:
            self._job_barra = self.after(200, lambda: self._animar_barra_tempo(inicio))

    def _ocultar_alerta(self):
        self._alerta_ativo = False
        for job in (self._job_respiracao, self._job_barra):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self._job_respiracao = None
        self._job_barra = None
        self.overlay.place_forget()
        self._job_alerta = None


def iniciar_threads_do_painel():
    """Clima e status do Autenticador sobem UMA vez por processo.

    O painel é aberto e fechado várias vezes pelo grupo (/exibirpaineltv e
    /ocultarpaineltv). Sem esta guarda, ligá-las no __init__ deixaria uma
    thread órfã a cada abertura.
    """
    global _THREADS_PAINEL_INICIADAS
    if _THREADS_PAINEL_INICIADAS:
        return
    _THREADS_PAINEL_INICIADAS = True
    threading.Thread(target=thread_atualizacao_clima, daemon=True,
                     name="clima-painel").start()
    threading.Thread(target=thread_status_autenticador_painel, daemon=True,
                     name="autenticador-painel").start()


def executar_monitoramento(exibir=None):
    os_notificadas = carregar_os_notificadas()
    agendamentos_vistos = carregar_agendamentos_vistos()
    reparos_avaliados = carregar_reparos_avaliados()
    qtd_reparos_notificados = sum(1 for info in reparos_avaliados.values() if info.get('notificado'))
    qtd_reparos_pendentes = len(reparos_avaliados) - qtd_reparos_notificados
    logger.info(f"{len(os_notificadas)} OS já notificadas anteriormente.")
    logger.info(f"{len(agendamentos_vistos)} OS com agendamento já conhecido.")
    logger.info(
        f"{qtd_reparos_notificados} Reparos já confirmados e notificados como garantia anteriormente "
        f"({qtd_reparos_pendentes} pendente(s) de reavaliação contra a Base OFS)."
    )

    carregar_base_ofs()

    estado_reavaliacao_base_ofs = {'mtime': None}

    # `exibir` só serve como valor inicial (testes/linha de comando). A partir
    # daqui quem manda é o Event, que o /exibirnavegador e o /ocultarnavegador
    # mexem em tempo de execução -- por isso o headless é relido a cada volta
    # externa, e não fixado uma vez só como era até 07/08/2026.
    if exibir:
        _navegador_visivel.set()
    logger.info(
        f"Navegador à vista: {navegador_deve_aparecer()} "
        "(use /exibirnavegador no grupo para mudar)"
    )

    while True:
        # Espera de VPN, abertura do navegador e login são lentos MAS estão
        # vivos: sem bater aqui, o vigia mataria o processo no meio de uma
        # queda de VPN e o supervisor o reabriria em loop, a cada 6 minutos.
        registrar_batida_monitor()

        # Dormindo por /desligar: fica aqui, ANTES da VPN e do navegador, para
        # não reconectar FortiClient nem abrir Chromium à toa. O /ligar solta.
        if monitor_pausado():
            inicio_pausa = time.time()
            logger.info("Monitoramento pausado (/desligar). Aguardando /ligar...")
            proximo_aviso = time.time() + 600
            while monitor_pausado():
                registrar_batida_monitor()   # ver comentário em _pausa_monitor
                # Sem isto o log fica mudo e o site passa a exibir "bot parado"
                # (fontes/backlog.py usa a idade do log). Pausado != caído.
                if time.time() >= proximo_aviso:
                    logger.info(
                        f"Monitoramento segue pausado há {int((time.time() - inicio_pausa) / 60)} min. "
                        "Aguardando /ligar."
                    )
                    proximo_aviso = time.time() + 600
                time.sleep(5)
            logger.info(
                f"Pausa encerrada (/ligar) após {int((time.time() - inicio_pausa) / 60)} min. "
                "Reabrindo o navegador..."
            )

        # Relido AQUI, a cada reabertura: headless não é alternável num navegador
        # já aberto, então /exibirnavegador só tem efeito quando o Chromium
        # renasce -- e é o próprio laço interno que provoca isso, ao detectar a
        # diferença e dar break.
        headless = not navegador_deve_aparecer()

        tentativas_vpn = 0
        inicio_queda_vpn = None
        while not vpn_esta_conectada():
            registrar_batida_monitor()
            tentativas_vpn += 1
            if inicio_queda_vpn is None:
                inicio_queda_vpn = time.time()
            logger.warning(f"Sem conexão com a rede (Tentativa {tentativas_vpn}). Acionando FortiClient...")

            # Antes o alerta saía só na tentativa 1: em 20/07 o bot passou ~7h
            # tentando reconectar em silêncio, e ninguém soube até de manhã.
            # Agora repete periodicamente, dizendo há quanto tempo está fora.
            if tentativas_vpn == 1:
                enviar_alerta_telegram("🔴 ALERTA: Sem conexão de rede! O bot iniciou o loop de reconexão do FortiClient...")
            elif tentativas_vpn % TENTATIVAS_VPN_ENTRE_ALERTAS == 0:
                minutos_fora = int((time.time() - inicio_queda_vpn) / 60)
                enviar_alerta_telegram(
                    f"🔴 VPN continua fora há {minutos_fora} min ({tentativas_vpn} tentativas "
                    "de reconexão). O monitoramento segue parado — pode ser preciso olhar a máquina."
                )

            lidar_com_queda_de_vpn()

            intervalo_espera_vpn = min(20 + (tentativas_vpn * 5), 120)
            logger.info(f"Aguardando {intervalo_espera_vpn} segundos para a rota estabilizar antes de tentar de novo...")
            time.sleep(intervalo_espera_vpn)

        if tentativas_vpn > 0:
            enviar_alerta_telegram("🟢 Rede restabelecida com sucesso! Iniciando o navegador e abrindo o CAMPO...")
            logger.info("Rede confirmada. Prosseguindo com a abertura do navegador...")

        with sync_playwright() as p:
            logger.info("Iniciando navegador com perfil persistente...")
            try:
                caminho_executavel = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH')

                # A máquina de produção tem 8 GB e roda bot + Chromium + Node +
                # site. Estas flags existem para o navegador ocupar o mínimo:
                # ele só precisa manter a sessão do CAMPO viva e fazer a SPA
                # emitir as requisições de onde o token é lido -- ninguém olha
                # essa tela.
                #
                # imagesEnabled=false é o corte grande, e vai por
                # --blink-settings DE PROPÓSITO, não por `page.route()`:
                # handlers de rota criam um objeto Python por requisição, exatamente
                # como o `page.on("response")` que causava o vazamento -- bloquear
                # imagem por rota reintroduziria o problema que estamos matando.
                #
                # NÃO VOLTE COM --js-flags=--max-old-space-size NEM COM
                # --renderer-process-limit. Eu pus os dois em 08/08/2026 às 15:27
                # "para o navegador ocupar menos", e eles derrubaram o bot três
                # vezes no mesmo dia. A SPA do CAMPO segura ~1850 chamados no DOM e
                # em memória JS; com o heap do V8 limitado a 256 MB ela estoura,
                # o renderer morre por OOM, e com um renderer só não sobra
                # ninguém. A telemetria pegou o instante exato:
                #
                #   17:55:53 | 7 processos chrome | 483 MB
                #   17:56:24 | 6 processos chrome | 107 MB   <- renderer morreu
                #
                # e o laço parou de logar em 17:56:24. O estrago não é o renderer
                # cair, é o que vem depois: a chamada síncrona do Playwright
                # seguinte fica esperando resposta de um renderer morto, e a API
                # síncrona NÃO TEM TIMEOUT -- 900s até o vigia matar o processo.
                # Economizar 300 MB de navegador não paga 15 min de cegueira.
                parametros_launch = {
                    "user_data_dir": CST_PERFIL_DIRETORIO,
                    "headless": headless,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-gpu",
                        # não baixa imagem nenhuma da SPA
                        "--blink-settings=imagesEnabled=false",
                        "--disable-dev-shm-usage",
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--disable-sync",
                        "--disable-default-apps",
                        "--no-first-run",
                        "--metrics-recording-only",
                        "--mute-audio",
                        "--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints,InterestFeedContentSuggestions",
                    ]
                }

                if caminho_executavel:
                    parametros_launch["executable_path"] = caminho_executavel

                contexto = p.chromium.launch_persistent_context(**parametros_launch)
            except Exception as e:
                alerta_critico_telegram(f"Falha ao iniciar navegador: {e}")
                return

            pagina = contexto.new_page()

            # Renderer morto = clique órfão. Sem isto, quando o processo do
            # renderer cai, a próxima chamada síncrona do Playwright fica
            # pendurada esperando uma resposta que não vem -- e a API síncrona
            # não tem timeout, então o laço só volta quando o vigia mata o
            # processo, 900s depois. Foi assim que 08/08/2026 perdeu 15 min
            # duas vezes (17:39 e 18:10).
            #
            # `crash` e `close` disparam NO MÁXIMO UMA VEZ por página, então
            # registrá-los não tem nada a ver com o `page.on("response")` que
            # causava o vazamento: aquele criava um objeto Python por resposta
            # HTTP, milhares por minuto. Aqui são dois handlers que ligam um
            # booleano e nunca mais rodam.
            #
            # Ler `navegador_morreu['sim']` é acesso a dict em Python puro: não
            # fala com o driver, não pode pendurar. É de propósito -- qualquer
            # sonda que CONVERSE com a página (page.title(), por exemplo) é
            # justamente o que trava, e essa tentativa já custou 919s em 08/08.
            navegador_morreu = {'sim': False, 'motivo': ''}

            def _marcar_morte(motivo):
                if not navegador_morreu['sim']:
                    navegador_morreu['sim'] = True
                    navegador_morreu['motivo'] = motivo
                    logger.error(f"Navegador sinalizou morte: {motivo}.")

            pagina.on("crash", lambda _p: _marcar_morte("renderer travou/estourou memória (crash)"))
            pagina.on("close", lambda _p: _marcar_morte("página fechada por fora"))

            estado_sessao = {
                'ultima_atividade_valida_ts': time.time(),
                'url_chamados_base': None,
                'token_atual': None,
                'corpo_filtro_atual': None,
                # o listener de captura está registrado agora?
                'captura_armada': False,
            }

            def extrair_lista_chamados(dados):
                if isinstance(dados, list):
                    return dados
                elif isinstance(dados, dict):
                    return dados.get('content', dados.get('data', []))
                return []

            def checar_atualizacao_base_ofs_e_reavaliar():
                try:
                    if not os.path.exists(BASE_OFS_ARQUIVO):
                        return
                    mtime_atual = os.path.getmtime(BASE_OFS_ARQUIVO)
                    if mtime_atual == estado_reavaliacao_base_ofs['mtime']:
                        return

                    primeira_leitura = estado_reavaliacao_base_ofs['mtime'] is None
                    carregar_base_ofs()

                    if primeira_leitura:
                        # Só registra o mtime e sai. Antes de 08/08/2026 o
                        # reavaliar_reparos_pendentes ficava FORA deste if, então
                        # o teste só trocava a mensagem de log e a reavaliação
                        # rodava em TODA subida do bot -- 18.676 reparos pendentes,
                        # e crescendo. Como ela roda dentro do laço, de forma
                        # síncrona, era candidata direta a estourar o vigia de
                        # 900s. A base não mudou desde a última execução; não há
                        # nada para reavaliar aqui.
                        estado_reavaliacao_base_ofs['mtime'] = mtime_atual
                        logger.info(
                            "Base OFS: leitura inicial registrada para controle de "
                            "reavaliação (nada a reavaliar até o arquivo mudar)."
                        )
                        return

                    logger.info(
                        "Base OFS: novo arquivo detectado no disco (mtime mudou) — "
                        "recarregando e reavaliando reparos pendentes..."
                    )
                    # O mtime só é dado como processado se a reavaliação REALMENTE
                    # rodou. Ela recusa rodar enquanto não houver uma varredura
                    # completa para dizer quais chamados seguem abertos; marcar
                    # antes faria essa Base nova ser esquecida para sempre.
                    if reavaliar_reparos_pendentes(reparos_avaliados):
                        estado_reavaliacao_base_ofs['mtime'] = mtime_atual
                except Exception:
                    logger.exception("Falha ao checar/reavaliar a Base OFS atualizada.")

            def processar_lista_chamados(lista_chamados, capex_confiavel=True,
                                         reparos_confiavel=True):
                """capex_confiavel=False quando a lista chegou truncada. Nesse
                caso as O.S. novas ainda são notificadas, mas a contagem de
                CAPEX pendente NÃO é regravada -- senão o site mostra 0 (ou um
                número menor) até a próxima varredura boa.

                reparos_confiavel=False quando QUALQUER uma das duas buscas veio
                cortada. Aí o conjunto de reparos abertos não é publicado. As
                duas flags andam separadas de propósito -- ver o docstring de
                buscar_chamados_via_api.

                O parâmetro `origem` saiu em 08/08/2026 junto com o listener
                que lia chamados: agora só a varredura chama isto, então toda
                lista parcial é anomalia e merece WARNING."""
                checar_atualizacao_base_ofs_e_reavaliar()

                if not isinstance(lista_chamados, list):
                    logger.debug("lista_chamados recebida não é uma lista estruturada. Ignorando processamento.")
                    return

                if lista_chamados:
                    estado_sessao['ultima_atividade_valida_ts'] = time.time()

                registrar_os_analisadas(len(lista_chamados))
                contagem_capex_pendente_rj = 0
                contagem_capex_pendente_sp = 0

                # ES05 comum marca aqui e a gravação sai UMA VEZ no fim do laço;
                # garantia NOTIFICADA grava na hora (ver o porquê lá embaixo).
                #
                # Em produção o dicionário tem ~18.888 entradas, e serializá-lo
                # com indent=2 dá 5,2 MB. Gravar por chamado novo custava isso
                # vezes o número de ES05 novos da varredura, DENTRO do laço.
                # Medido em 08/08/2026, 20 varreduras com 30 ES05 novos cada:
                #
                #   1 gravação por chamado    125,1s   (3,1 GB escritos em disco)
                #   1 gravação por varredura    4,2s
                #
                # Memória não muda (a bancada mediu +0 MB nos dois), então isto
                # não é sobre vazamento: é que a varredura passou a rodar em toda
                # volta, e 6s de json.dump travando o laço a cada 25s é o tipo de
                # coisa que vira "o bot está lento" sem nenhum erro no log.
                reparos_sujos = False
                # Mesma razão do reparos_sujos acima, e o caso aqui é ainda mais
                # agudo: na PRIMEIRA varredura depois de publicar, toda O.S.
                # aberta é novidade -- ou seja, o pior caso do "1 gravação por
                # chamado" acontece logo de saída, justamente uma vez.
                agendamentos_mudaram = False
                os_reparo_abertas = set()

                for chamado in lista_chamados:
                    # Batida por chamado. Notificar não é instantâneo: cada O.S.
                    # nova custa um POST ao Telegram mais `time.sleep(0.2)` de
                    # espaçamento, ~1,1s na prática. Um lote de 300 O.S. novas --
                    # o que acontece depois de uma queda longa, ou quando o
                    # filtro muda -- passa de 5 min DENTRO deste laço, e sem
                    # carimbar aqui o vigia mataria o bot justamente enquanto ele
                    # avisa a operação. Iterar é progresso real; se travar de
                    # verdade, trava dentro de uma chamada com timeout próprio e
                    # a batida para junto.
                    registrar_batida_monitor()
                    if not isinstance(chamado, dict):
                        logger.debug(f"Elemento ignorado na lista de chamados (não é um dicionário): {chamado}")
                        continue

                    fila = chamado.get('fila')
                    if isinstance(fila, dict):
                        codigo = fila.get('codigo')
                    elif isinstance(fila, str):
                        codigo = fila
                    else:
                        codigo = chamado.get('codigo')

                    if codigo and 'BOT-F' in str(codigo).upper():
                        estado_sessao['ultima_atividade_valida_ts'] = time.time()

                    if codigo in CODIGOS_ALVO:
                        unidade = str(chamado.get('enderecoUnidade', '')).upper().strip()
                        if unidade not in (LITORAL_SP + RJ):
                            continue

                        bairro_capex = chamado.get('enderecoBairro', '')
                        if not unidade_bairro_permitido(unidade, bairro_capex):
                            continue

                        if unidade in RJ:
                            contagem_capex_pendente_rj += 1
                        elif unidade in LITORAL_SP:
                            contagem_capex_pendente_sp += 1

                        os_id = chamado.get('id')
                        if os_id and os_id not in os_notificadas:
                            try:
                                contrato = chamado.get('codigoContrato', 'N/D')
                                nome_cliente = chamado.get('nomeCliente', 'N/D')
                                if isinstance(nome_cliente, str):
                                    nome_cliente = nome_cliente.strip() or 'N/D'
                                bairro = chamado.get('enderecoBairro', 'N/D')

                                cidade = (chamado.get('enderecoCidade') or '').strip()
                                if not cidade:
                                    cidade = unidade

                                telefones, _ = extrair_telefones_do_chamado(chamado)
                                telefones_str = ", ".join(telefones) if telefones else "N/D"

                                mensagem = (
                                    f"CAPEX: {cidade}\n"
                                    f"• Contrato: {contrato}\n"
                                    f"• Cliente: {nome_cliente}\n"
                                    f"• Bairro: {bairro}\n"
                                    f"• Telefone(s): {telefones_str}"
                                )
                                message_id = enviar_alerta_telegram(mensagem)
                                if message_id is not None:
                                    os_notificadas.add(os_id)
                                    salvar_os_notificadas(os_notificadas)
                                    registrar_capex_notificada()
                                    registrar_entrante_capex(unidade)
                                    logger.info(f"Notificada: OS {os_id} - {codigo} ({nome_cliente})")

                                    # Reincidência de improdutiva técnica: vai
                                    # DEPOIS do entrante ter sido notificado e
                                    # carimbado. Assim, se esta consulta falhar
                                    # ou a base estiver velha, o alerta
                                    # principal já saiu -- e a OS não volta a
                                    # ser notificada no próximo ciclo só porque
                                    # o extra deu errado.
                                    data_abertura_ms = chamado.get('dataAbertura')
                                    quando_entrou = datetime.now()
                                    if data_abertura_ms:
                                        try:
                                            quando_entrou = datetime.fromtimestamp(
                                                data_abertura_ms / 1000
                                            )
                                        except Exception:
                                            pass

                                    achado = verificar_improdutiva_anterior(
                                        contrato, nome_cliente, quando_entrou
                                    )
                                    if achado:
                                        notificar_improdutiva_telegram(
                                            cidade, contrato, nome_cliente, bairro,
                                            telefones_str, achado
                                        )
                                        registrar_improdutiva_notificada()
                                        logger.warning(
                                            f"IMPRODUTIVA ANTERIOR: OS {os_id} ({nome_cliente}) "
                                            f"— casou por {achado['casou_por']} com "
                                            f"'{achado['motivo']}' de {achado['dias']} dia(s) atrás."
                                        )

                                    if TV_ATIVA:
                                        FILA_EVENTOS_TV.put({
                                            'tipo': 'capex',
                                            'unidade': unidade,
                                            'contrato': contrato,
                                            'cliente': nome_cliente,
                                            'bairro': bairro,
                                            'telefones': telefones_str,
                                            'timestamp': datetime.now().strftime('%H:%M:%S'),
                                        })
                                else:
                                    logger.error(
                                        f"Falha ao notificar OS {os_id} – será reprocessada no próximo ciclo."
                                    )
                                time.sleep(0.2)
                            except Exception:
                                logger.exception(
                                    f"Falha ao processar/notificar chamado CAPEX OS {os_id}. "
                                    "Será reavaliado no próximo ciclo."
                                )

                        # Fora do "if os_id not in os_notificadas" de propósito:
                        # a remarcação acontece justamente DEPOIS de a O.S. já
                        # ter sido notificada, que é o caso em que aquele ramo
                        # nunca mais roda.
                        if os_id:
                            try:
                                if acompanhar_remarcacao(
                                    chamado, os_id, unidade, agendamentos_vistos
                                ):
                                    agendamentos_mudaram = True
                            except Exception:
                                logger.exception(
                                    f"Falha ao acompanhar remarcação da OS {os_id}."
                                )

                    elif codigo == 'ES05':
                        os_id = chamado.get('id')
                        chave_reparo = str(os_id) if os_id else None

                        if chave_reparo:
                            # Prova de vida, colhida ANTES do filtro abaixo: um
                            # reparo já avaliado é pulado dali para a frente, e
                            # sem carimbar aqui ninguém no sistema saberia que
                            # ele continua aberto. É o que separa uma garantia
                            # legítima de uma O.S. fechada semana passada na
                            # hora em que a Base OFS é atualizada.
                            os_reparo_abertas.add(chave_reparo)
                            registro = reparos_avaliados.get(chave_reparo)
                            if registro is not None:
                                hoje_iso = datetime.now().date().isoformat()
                                # data (e não hora): assim o carimbo muda no
                                # máximo uma vez por dia por registro, em vez
                                # de sujar 5 MB de JSON a cada volta
                                if registro.get('visto_em') != hoje_iso:
                                    registro['visto_em'] = hoje_iso
                                    reparos_sujos = True

                        if chave_reparo and chave_reparo not in reparos_avaliados:
                            try:
                                data_abertura_ms = chamado.get('dataAbertura')
                                data_abertura_dt = datetime.now()
                                if data_abertura_ms:
                                    try:
                                        data_abertura_dt = datetime.fromtimestamp(data_abertura_ms / 1000)
                                    except Exception:
                                        pass

                                codigo_contrato = chamado.get('codigoContrato')
                                eh_garantia, tipo_anterior, dias_aging, tecnico_ofs = verificar_garantia_reparo(
                                    codigo_contrato, data_abertura_dt
                                )

                                beauty_unidade = chamado.get('enderecoUnidade', 'N/D')
                                contrato = chamado.get('codigoContrato', 'N/D')
                                nome_cliente = chamado.get('nomeCliente', 'N/D')
                                if isinstance(nome_cliente, str):
                                    nome_cliente = nome_cliente.strip() or 'N/D'
                                bairro = chamado.get('enderecoBairro', 'N/D')
                                telefones, _ = extrair_telefones_do_chamado(chamado)
                                telefones_str = ", ".join(telefones) if telefones else "N/D"

                                dados_reparo = {
                                    'os_id': os_id,
                                    'codigo_contrato': codigo_contrato,
                                    'data_abertura': data_abertura_dt.isoformat(),
                                    'unidade': beauty_unidade,
                                    'nome_cliente': nome_cliente,
                                    'bairro': bairro,
                                    'telefones': telefones_str,
                                    'notificado': False,
                                }

                                if eh_garantia:
                                    tocar_som_alerta_garantia()

                                    if not tecnico_ofs:
                                        tecnico_ofs = 'N/D'

                                    message_id = notificar_garantia_telegram(
                                        beauty_unidade, contrato, nome_cliente, bairro, telefones_str, tecnico_ofs
                                    )
                                    if message_id is not None:
                                        fixar_mensagem_telegram(message_id)
                                        dados_reparo['notificado'] = True
                                        dados_reparo['tipo_anterior'] = tipo_anterior
                                        dados_reparo['dias_aging'] = dias_aging
                                        # Ver a nota em reavaliar_reparos_pendentes:
                                        # o técnico é gravado para a lista de
                                        # garantias dos grupos.
                                        dados_reparo['tecnico_ofs'] = tecnico_ofs
                                        reparos_avaliados[chave_reparo] = dados_reparo
                                        # Grava NA HORA, sem esperar o lote. Esta é
                                        # a única escrita que não pode ser adiada:
                                        # a mensagem de garantia já foi enviada e
                                        # fixada no grupo, então perder este
                                        # registro num reinício não custa
                                        # reprocessamento, custa uma notificação
                                        # DUPLICADA para a operação. Garantia é
                                        # rara (algumas por dia), então pagar os
                                        # 5,2 MB aqui não pesa -- o que pesava era
                                        # pagar por ES05 comum, que são centenas.
                                        salvar_reparos_avaliados(reparos_avaliados)
                                        reparos_sujos = False
                                        registrar_garantia_notificada()
                                        logger.info(
                                            f"Notificada (GARANTIA): OS {os_id} - Reparo, serviço anterior "
                                            f"'{tipo_anterior}' concluído há {dias_aging} dias ({nome_cliente}) "
                                            f"- Técnico OFS: {tecnico_ofs}"
                                        )

                                        if TV_ATIVA:
                                            FILA_EVENTOS_TV.put({
                                                'tipo': 'garantia',
                                                'unidade': beauty_unidade,
                                                'contrato': contrato,
                                                'cliente': nome_cliente,
                                                'bairro': bairro,
                                                'telefones': telefones_str,
                                                'tecnico_ofs': tecnico_ofs,
                                                'tipo_anterior': tipo_anterior,
                                                'dias_aging': dias_aging,
                                                'timestamp': datetime.now().strftime('%H:%M:%S'),
                                            })
                                    else:
                                        logger.error(
                                            f"Falha ao notificar garantia OS {os_id} – será reprocessada no próximo ciclo."
                                        )
                                    time.sleep(0.2)
                                else:
                                    reparos_avaliados[chave_reparo] = dados_reparo
                                    reparos_sujos = True
                                    logger.debug(
                                        f"Reparo OS {os_id} avaliado: não é garantia (contrato {codigo_contrato})."
                                    )
                            except Exception:
                                logger.exception(
                                    f"Falha ao processar/notificar chamado de GARANTIA OS {os_id}. "
                                    "Será reavaliado no próximo ciclo."
                                )

                # Fora do laço: uma gravação cobre todos os ES05 novos da
                # varredura. Se o processo morrer antes daqui, os reparos voltam
                # como pendentes e são reavaliados na próxima volta -- que é o
                # comportamento correto e já é o que o `except` acima promete.
                if reparos_sujos:
                    salvar_reparos_avaliados(reparos_avaliados)

                if agendamentos_mudaram:
                    salvar_agendamentos_vistos(agendamentos_vistos)

                # Só uma varredura COMPLETA vale como prova de vida. Publicar
                # uma lista subcontada faria a reavaliação concluir que
                # chamados abertos estão fechados, e calar garantia de verdade
                # -- erro pior que o que esta trava veio consertar.
                #
                # `reparos_confiavel` e não `capex_confiavel`: o conjunto aqui é
                # a UNIÃO das duas buscas, então basta uma delas vir cortada
                # para ele sair menor que a realidade. Até 14/08/2026 esta linha
                # olhava só a busca 1, e às 10:27 daquele dia publicou 673
                # chamados no lugar de 1.684 sem nenhum aviso.
                if reparos_confiavel:
                    publicar_reparos_abertos(os_reparo_abertas)

                if capex_confiavel:
                    atualizar_capex_pendente(contagem_capex_pendente_rj, contagem_capex_pendente_sp)
                else:
                    logger.warning(
                        "Varredura incompleta neste ciclo: mantendo a última contagem de CAPEX "
                        f"pendente conhecida em vez de gravar RJ={contagem_capex_pendente_rj} / "
                        f"SP={contagem_capex_pendente_sp} (subcontados) por cima dela."
                    )

            def montar_url_busca_direta(url_original, size_desejado, pagina=0):
                partes = urlsplit(url_original)
                query = dict(parse_qsl(partes.query))
                query['size'] = str(size_desejado)
                query['page'] = str(pagina)
                nova_query = urlencode(query)
                return urlunsplit((partes.scheme, partes.netloc, partes.path, nova_query, partes.fragment))

            def buscar_todas_paginas(url_base, payload, headers, tamanho_pagina=TAMANHO_PAGINA_BUSCA_DIRETA, max_paginas=20):
                """Busca a lista de chamados percorrendo as páginas da API.

                A paginação NÃO é opcional: a API trava se o 'size' passar de 300,
                e os volumes reais são de 600 a 1800 chamados. Chegou-se a tentar
                trocar isto por uma requisição única em 08/08/2026 -- não dá,
                traria 290 de 1814 e o resto sumiria calado.

                Vai por `requests` e NÃO pelo canal do Playwright: era daí que
                vinha metade do vazamento de memória (ver _SESSAO_CAMPO). O token
                já está em `headers`, então o navegador não participa desta
                chamada -- ele continua servindo só para manter a sessão viva e
                entregar o token.
                """
                todos = []
                paginas_buscadas = 0
                # Só vira True quando a paginação termina inteira. Enquanto
                # estiver False, a contagem de CAPEX derivada desta busca está
                # subcontada e NÃO pode ser gravada por cima da boa.
                completa = False
                for pagina in range(max_paginas):
                    # Batida por PÁGINA, não por varredura. São até 20 páginas com
                    # timeout de 30s cada, então uma rede ruim faz esta função
                    # sozinha passar de 10 min -- trabalhando, não travada. Sem
                    # carimbar aqui, o vigia teria de manter um limite frouxo o
                    # bastante para caber o pior caso, e é justamente esse limite
                    # frouxo que custou 15 min de cegueira por episódio.
                    registrar_batida_monitor()
                    url_busca = montar_url_busca_direta(url_base, tamanho_pagina, pagina)
                    try:
                        resp = _SESSAO_CAMPO.put(url_busca, data=json.dumps(payload),
                                               headers=headers, timeout=(10, 20))
                    except Exception as e:
                        # Aqui só chega falha de REDE: esta chamada não passa mais
                        # pelo navegador, então ela não diz nada sobre o contexto
                        # estar vivo. Quem detecta navegador zumbi são as operações
                        # de página do laço (refresh, token_nao_informado_presente,
                        # detectar_mensagem_deslogado_imediata), que levantam
                        # ContextoNavegadorMorto por conta própria.
                        logger.warning(
                            f"Falha de rede na página {pagina} da busca de chamados "
                            f"(mantendo as {len(todos)} já coletadas): {e}"
                        )
                        break

                    if resp.status_code in (401, 403):
                        return todos, True, False

                    if resp.status_code != 200:
                        logger.warning(f"Busca paginada (page={pagina}) retornou status {resp.status_code}")
                        salvar_diagnostico_erro_api(resp, motivo=f"Busca paginada page={pagina} status {resp.status_code}")
                        break

                    try:
                        pagina_itens = extrair_lista_chamados(resp.json())
                    except Exception:
                        logger.exception(f"Falha ao interpretar resposta da página {pagina} da busca de chamados.")
                        break

                    todos.extend(pagina_itens)
                    paginas_buscadas += 1

                    if len(pagina_itens) < tamanho_pagina:
                        # Página curta = acabou a lista: esta é a única saída
                        # que garante que coletamos tudo.
                        completa = True
                        break

                    if pagina == max_paginas - 1:
                        logger.warning(
                            f"Busca de chamados atingiu o limite de {max_paginas} páginas "
                            f"({tamanho_pagina} itens cada) sem terminar."
                        )

                logger.info(
                    f"Busca paginada concluída: {paginas_buscadas} página(s), "
                    f"{len(todos)} chamado(s) no total (tamanho por página: {tamanho_pagina})"
                    f"{'.' if completa else ' -- INCOMPLETA, contagem não confiável.'}"
                )

                return todos, False, completa

            def buscar_chamados_via_api():
                """Devolve (tentou, deslogado, chamados, capex_confiavel,
                reparos_confiavel).

                SÃO DUAS GARANTIAS DIFERENTES, e por isso duas flags. Até
                14/08/2026 existia só a primeira, e a completude da busca 2 era
                atribuída a `_completa2` e descartada -- o furo abaixo.

                capex_confiavel: a busca 1 (CAPEX+ES05 por unidade) percorreu
                    todas as páginas. É ela que alimenta a contagem de CAPEX
                    pendente do site; truncada, a contagem sai menor que a real
                    e não pode sobrescrever a boa.

                reparos_confiavel: as DUAS buscas vieram inteiras. É o que
                    autoriza publicar o conjunto de reparos abertos, porque ele
                    é a união das duas -- e um conjunto subcontado faz a
                    reavaliação concluir que reparo aberto foi fechado, calando
                    garantia de verdade.

                Amarrar as duas seria errado nos dois sentidos: uma falha de
                rede na busca 2 congelaria a contagem de CAPEX sem motivo, e uma
                busca 1 truncada não pode publicar reparos ainda que a 2 esteja
                boa. Em 14/08/2026, às 10:27, a busca 2 voltou com 290 de ~1.419
                (falha de rede na página 1) e a varredura foi tratada como
                completa: 673 chamados no lugar de 1.684, sem um aviso sequer.
                """
                url_base = estado_sessao['url_chamados_base']
                token = estado_sessao['token_atual']

                if not url_base or not token:
                    return False, False, [], False, False

                if 'all_buckets' in url_base:
                    url_base = url_base.replace('all_buckets', '')
                    logger.info("URL corrigida: removido 'all_buckets' do endpoint.")

                headers = {
                    "content-type": "application/json;charset=UTF-8",
                    "accept": "application/json, text/plain, */*",
                    "token": token,
                }

                # A varredura sai por fora do navegador; se a API algum dia
                # passar a exigir cookie de sessão além do token, é aqui que
                # ele chega.
                _sincronizar_cookies_campo(contexto)

                unidades_alvo = LITORAL_SP + RJ
                # ============ CORRIGIDO: faltavam UP02 (Upgrade) e ES15
                # (Mudança de Cômodo) nesta lista -- por isso o backlog
                # desses dois tipos sempre dava zero: a API nem devolvia
                # esses chamados, então não tinha nada pra calcular. ============
                filas_alvo = list(set(CODIGOS_ALVO + ["ES05", "ES06", "REPPME", "UP02", "ES15"]))
                payload1 = {
                    "enderecoUnidade": unidades_alvo,
                    "dataConclusao": "IS NULL",
                    "identificador": [],
                    "prioridade": [],
                    "classificacao_nome": [],
                    "contrato_status": [],
                    "usuarioAtribuido": [],
                    "fila_codigo": filas_alvo,
                    "contrato": None
                }
                try:
                    chamados1, deslogado1, completa1 = buscar_todas_paginas(url_base, payload1, headers)
                except ContextoNavegadorMorto:
                    raise   # o laço externo precisa reabrir o navegador
                except Exception as e:
                    logger.warning(f"Falha na busca CAPEX+ES05 (unidades): {e}")
                    return True, False, [], False, False

                if deslogado1:
                    logger.warning("Busca 1 retornou 401/403 — sessão deslogada.")
                    return True, True, [], False, False

                # Busca 2: reparo nas siglas fora da nossa área.
                #
                # Era uma varredura NACIONAL (`enderecoUnidade: []`) até
                # 14/08/2026. Medido antes de trocar: 1.419 dos 1.672 chamados
                # de cada volta (85%), 5 páginas, e 6.607 dos 7.361 registros de
                # reparos_avaliados.json -- para 0 garantias em 24 notificadas
                # no histórico, e 0 entre os 3.122 reparos que estavam abertos
                # fora das nossas siglas no momento da medição.
                #
                # O mecanismo que ela cobria continua coberto, e é o mesmo:
                # garantia de serviço nosso executado fora da área. Só que agora
                # o alvo é declarado em SIGLAS_GARANTIA_EXTRA em vez de varrido.
                #
                # Lista vazia => a busca não sai. Mandar `enderecoUnidade: []`
                # para "não quero nenhuma" devolveria o país inteiro de novo,
                # que é exatamente o oposto.
                # completa2 nasce True porque "não havia o que buscar" é um
                # resultado íntegro: com a lista vazia não existe reparo fora da
                # área para perder. É diferente de "busquei e veio cortado".
                chamados2, deslogado2, completa2 = [], False, True
                if SIGLAS_GARANTIA_EXTRA:
                    payload2 = {
                        "enderecoUnidade": list(SIGLAS_GARANTIA_EXTRA),
                        "dataConclusao": "IS NULL",
                        "fila_codigo": ["ES05"],
                        "contrato": None
                    }
                    try:
                        chamados2, deslogado2, completa2 = buscar_todas_paginas(url_base, payload2, headers)
                    except ContextoNavegadorMorto:
                        raise   # o laço externo precisa reabrir o navegador
                    except Exception as e:
                        logger.warning(f"Falha na busca ES05 das siglas extras: {e}")
                        chamados2, deslogado2, completa2 = [], False, False

                if deslogado2:
                    logger.warning("Busca 2 retornou 401/403 — sessão deslogada.")
                    return True, True, [], False, False

                ids_vistos = set()
                todos = []
                for c in chamados1 + chamados2:
                    if not isinstance(c, dict):
                        continue
                    cid = c.get('id')
                    if cid and cid not in ids_vistos:
                        ids_vistos.add(cid)
                        todos.append(c)

                if not completa2:
                    logger.warning(
                        "Busca das siglas extras veio TRUNCADA: o conjunto de "
                        "reparos abertos não será publicado neste ciclo."
                    )

                return True, False, todos, completa1, (completa1 and completa2)

            # REMOVIDO em 08/08/2026: `salvar_diagnostico_requisicao_sucesso`.
            # Só era chamada pelo ramo do listener que lia o corpo da resposta
            # -- justamente o que causava o vazamento e saiu daqui. O que ela
            # gravava (url, headers, post_data da requisição de chamados) hoje
            # vive em `estado_sessao` e aparece no log da varredura.

            # REMOVIDA em 08/08/2026: `buscar_chamados_para_notificar` (varredura
            # "leve"). Eu a escrevi para notificar rápido sem pagar a varredura
            # completa, e ela estava errada nas DUAS pontas:
            #
            #   volume  -- ES05 global devolve 1338 chamados, não os ~529 que
            #              estimei. Somando o CAPEX, a "leve" era 1580 contra
            #              1690 da completa: 93% do custo, rodando a cada 120s
            #              em vez de 1800s. ~15x mais trabalho por hora.
            #   premissa -- medido depois: o json.loads das 8 páginas custa
            #              +0,2 MB por varredura. O parse é de graça. Trazer
            #              menos JSON nunca ia economizar a memória que era o
            #              motivo de existir dela.
            #
            # Hoje a completa roda em toda volta (ver
            # INTERVALO_VARREDURA_COMPLETA_SEG) e cobre notificação e backlog
            # com uma busca só, que é como as versões estáveis sempre fizeram.

            def salvar_diagnostico_erro_api(response, motivo):
                try:
                    if os.path.exists(ARQUIVO_DIAGNOSTICO_ERRO_API) and os.path.getsize(ARQUIVO_DIAGNOSTICO_ERRO_API) > TAMANHO_MAX_DIAGNOSTICO_ERRO:
                        backup = ARQUIVO_DIAGNOSTICO_ERRO_API + ".old"
                        os.replace(ARQUIVO_DIAGNOSTICO_ERRO_API, backup)
                        logger.info("Arquivo de diagnóstico de erro rotacionado (tamanho limite atingido).")

                    # Hoje só a varredura (requests) chama isto, mas segue
                    # aceitando os dois formatos -- no do Playwright `text()` é
                    # método e o status vem em `status`; no do requests `text` é
                    # propriedade e o status vem em `status_code`.
                    try:
                        corpo = getattr(response, "text")
                        corpo_bruto = corpo() if callable(corpo) else corpo
                    except Exception:
                        corpo_bruto = "<não foi possível ler o corpo da resposta>"
                    try:
                        url_resp = response.url
                    except Exception:
                        url_resp = "<sem URL>"
                    status = getattr(response, "status", None)
                    if status is None:
                        status = getattr(response, "status_code", "?")
                    linha = (
                        f"\n=== {datetime.now().isoformat()} — {motivo} ===\n"
                        f"URL: {url_resp}\n"
                        f"Status: {status}\n"
                        f"Headers resposta: {dict(response.headers)}\n"
                        f"Corpo bruto: {str(corpo_bruto)[:2000]}\n"
                    )
                    with open(ARQUIVO_DIAGNOSTICO_ERRO_API, "a", encoding="utf-8") as f:
                        f.write(linha)
                    logger.info(f"Diagnóstico de erro da API salvo em '{ARQUIVO_DIAGNOSTICO_ERRO_API}'.")
                except Exception:
                    logger.exception("Falha ao salvar diagnóstico de erro da API de chamados.")

            def processar_resposta_chamados(response):
                """Captura token/URL/filtro da requisição que a SPA faz sozinha.

                NUNCA chamar `response.json()` (nem `.text()`, nem `.body()`)
                aqui. Medido em bancada em 08/08/2026: um handler de `response`
                faz o Playwright reter um objeto `Response` por resposta, pelo
                tempo de vida da CONEXÃO -- fechar a página não libera. Enquanto
                o handler só lê cabeçalho, isso custa ~20 KB por resposta e é
                irrelevante; no instante em que ele lê o CORPO, cada um desses
                objetos retidos passa a fixar a estrutura parseada inteira. Com
                a mesma carga, 12 rodadas foram de 33 MB (só cabeçalho) para
                788 MB (com `.json()`), e de 40 mil para 1 milhão de objetos vivos.

                Quem lê chamados agora é a varredura, por `requests`, onde não
                há canal do Playwright no caminho e a memória fica plana.
                """
                if RELOAD_EM_ANDAMENTO:
                    return
                if response.request.method == "PUT" and "chamado" in response.url:
                    try:
                        req = response.request
                        url_capturada = req.url
                        if 'all_buckets' in url_capturada:
                            url_capturada = url_capturada.replace('all_buckets', '')
                        estado_sessao['url_chamados_base'] = url_capturada
                        estado_sessao['token_atual'] = req.headers.get('token')
                        estado_sessao['corpo_filtro_atual'] = req.post_data
                    except Exception:
                        logger.debug("Não foi possível capturar token/URL/corpo desta requisição.")

            # REMOVIDO em 08/08/2026: `salvar_diagnostico_filas`.
            # Era um farejador declaradamente TEMPORÁRIO, para descobrir os
            # códigos de fila de Upgrade e Mudança de Cômodo -- já descobertos
            # (UP02 e ES15, hoje fixos em `filas_alvo`) e gravados em
            # dados/diagnostico_filas.json. Ficou registrado como segundo
            # listener de `response`, ou seja: rodava em TODA resposta da tela,
            # para sempre, só para ler um flag já resolvido -- e cada resposta
            # que passa por um handler fica retida na memória do processo.

            def armar_captura_token():
                """Liga o listener só enquanto falta token ou URL."""
                if not estado_sessao['captura_armada']:
                    pagina.on("response", processar_resposta_chamados)
                    estado_sessao['captura_armada'] = True

            def desarmar_captura_token():
                """Desliga assim que capturou.

                Mesmo lendo só cabeçalho, cada resposta que passa por um handler
                deixa um objeto retido até o navegador fechar. Com o handler no
                ar por 8h isso vira dezenas de milhares. Como token e URL não
                mudam entre logins, manter o listener ligado o tempo todo é
                pagar esse preço à toa -- ele é rearmado no refazer_login.
                """
                if estado_sessao['captura_armada']:
                    try:
                        pagina.remove_listener("response", processar_resposta_chamados)
                    except Exception:
                        logger.debug("Não consegui remover o listener de captura de token.")
                    estado_sessao['captura_armada'] = False

            # NÃO acrescente aqui uma sonda do tipo `pagina.title()` por volta.
            # Tentei em 08/08/2026 e o laço travou 919s na primeira vez que o
            # navegador engasgou: chamada síncrona do Playwright não tem timeout,
            # e nesse estado ela bloqueia para sempre. É o mesmo defeito descrito
            # no comentário de TEMPO_MAX_INATIVIDADE_SEG sobre 02/08.
            #
            # A detecção de navegador zumbi vem, sem chamada nova, do bloco do
            # botão de refresh mais abaixo: ele já fala com a página a cada
            # volta, e agora deixa passar o erro de contexto morto em vez de
            # engolir tudo.

            def relogar():
                """Refaz o login e volta a capturar token/URL.

                O token antigo morre no relogin, então é descartado aqui --
                e é esse descarte que faz o listener de captura ser rearmado
                sozinho. Assim nenhum caminho de relogin precisa lembrar de
                rearmar na mão.
                """
                estado_sessao['token_atual'] = None
                estado_sessao['url_chamados_base'] = None
                refazer_login(pagina)
                # O login pode levar até 3 min legitimamente (espera do botão
                # 'Carregar chamados' com timeout de 120s). Carimbar aqui é o que
                # permite ao vigia usar um limite apertado sem derrubar bot são.
                registrar_batida_monitor()
                armar_captura_token()

            armar_captura_token()

            try:
                logger.info("Acessando página de login...")
                pagina.goto("https://campo.provedor.example/login/", timeout=60000)
                pagina.wait_for_load_state("networkidle")
                pagina.wait_for_timeout(2000)

                if "dashboard" in pagina.url:
                    logger.info("Sessão já ativa, redirecionado ao dashboard.")
                else:
                    botao_login = pagina.locator('button:has-text("Entrar com login Provedor")')
                    if botao_login.count() > 0:
                        logger.info("Clicando em 'Entrar com login Provedor'...")
                        botao_login.first.click()
                        logger.info("Aguardando login (se pedir código do autenticador, digite-o agora)...")
                        try:
                            pagina.wait_for_url("**/dashboard**", timeout=180000)
                            logger.info("Login concluído, dashboard carregado.")
                        except PlaywrightTimeoutError:
                            logger.warning("Timeout ao esperar dashboard (3 min). Continuing assim mesmo...")
                        # Mesmo motivo de refazer_login: 60s de goto + 180s aqui
                        # já encostam em TEMPO_MAX_SEM_BATIDA_SEG antes mesmo de
                        # chegar na batida logo abaixo.
                        registrar_batida_monitor()
                    else:
                        logger.error("Botão de login não encontrado.")
                        raise Exception("Botão 'Entrar com login Provedor' ausente.")

                logger.info("Navegando para a página de chamados...")
                pagina.goto("https://campo.provedor.example/logistica/#/chamado", timeout=30000)
                pagina.wait_for_load_state("networkidle")

                registrar_batida_monitor()   # login pode ter levado até 3 min

                logger.info("Aguardando botão 'Carregar chamados'...")
                botao_carregar = pagina.locator(SELECTOR_BOTAO_CARREGAR_CHAMADOS)
                botao_carregar.wait_for(state="visible", timeout=120000)
                if botao_carregar.is_enabled():
                    logger.info("Clicando em 'Carregar chamados'...")
                    botao_carregar.first.click()
                    pagina.wait_for_timeout(10000)
                    logger.info("Primeira carga de chamados concluída.")
                else:
                    logger.error("Botão 'Carregar chamados' desabilitado.")
                    raise Exception("Não foi possível carregar a lista de chamados.")

                logger.info("Monitoramento iniciado. Atualizando a cada ~25 segundos...")
                # A CONTAGEM, não os nomes: lista vazia é o estado que precisa
                # gritar, porque aí a busca extra simplesmente não sai. Sigla
                # errada (o caso `PBS`) aparece pelo outro lado, no
                # "Busca paginada concluída" da busca 2 vindo com 0 chamados.
                logger.info(
                    "Reparo de garantia fora da nossa área: %s",
                    f"{len(SIGLAS_GARANTIA_EXTRA)} sigla(s) vigiada(s)."
                    if SIGLAS_GARANTIA_EXTRA else "busca extra desligada."
                )

                ciclos_vazios_seguidos = 0
                # 0.0 faz a PRIMEIRA volta já rodar a varredura completa -- senão
                # o backlog nasceria vazio e ficaria assim até o primeiro intervalo.
                ultima_varredura_completa_ts = 0.0

                while True:
                    registrar_batida_monitor()

                    # ANTES de qualquer chamada ao Playwright, de propósito: se o
                    # renderer morreu, tocar na página aqui é o que pendura o laço.
                    # Só lê um booleano ligado pelos handlers de crash/close.
                    if navegador_morreu['sim']:
                        logger.error(
                            f"Navegador morto ({navegador_morreu['motivo']}). "
                            "Reabrindo em vez de conversar com uma página morta."
                        )
                        enviar_alerta_telegram(
                            "🟠 O navegador do CAMPO caiu. Reabrindo automaticamente..."
                        )
                        break

                    # A captura de token/URL fica ligada só enquanto faz falta.
                    # Checar aqui, no topo, cobre sozinho todos os caminhos de
                    # relogin -- inclusive os que ainda venham a ser escritos.
                    if estado_sessao['token_atual'] and estado_sessao['url_chamados_base']:
                        desarmar_captura_token()

                    # Sai do laço para o finally fechar o navegador; o while
                    # externo então dorme na espera da pausa, sem Chromium no ar.
                    if monitor_pausado():
                        logger.info("Pausa pedida pelo grupo: encerrando o navegador.")
                        break

                    # Mesmo caminho para trocar headless <-> à vista: o Chromium
                    # precisa renascer com a outra opção, não dá para alternar
                    # com ele aberto.
                    visivel_agora = not headless
                    if navegador_deve_aparecer() != visivel_agora:
                        logger.info(
                            "Visibilidade do navegador mudou: "
                            f"{'à vista' if visivel_agora else 'oculto'} -> "
                            f"{'à vista' if not visivel_agora else 'oculto'}. Reabrindo..."
                        )
                        break

                    if not vpn_esta_conectada():
                        if _vpn_e_gerenciada_externamente():
                            # Linux: nada aqui vai "consertar" a VPN -- só o
                            # campo-vpn.service faz isso, em processo separado.
                            # A mensagem não promete o que não sabemos ainda;
                            # quem confirma de verdade é o laço externo
                            # (vpn_esta_conectada() lá em cima, antes de abrir
                            # o navegador de novo).
                            enviar_alerta_telegram("🔴 ALERTA: Queda de VPN detectada! Fechando a sessão do CAMPO até a rede voltar...")
                            logger.warning("VPN caiu! Fechando o navegador -- o campo-vpn.service cuida da reconexão.")
                        else:
                            enviar_alerta_telegram("🔴 ALERTA: Queda de VPN detectada! Iniciando protocolo de reconexão do FortiClient...")
                            logger.warning("VPN caiu! Reconectando FortiClient...")

                        lidar_com_queda_de_vpn()

                        if not _vpn_e_gerenciada_externamente():
                            enviar_alerta_telegram("🟢 VPN restabelecida. Reiniciando o navegador para limpar a sessão do CAMPO...")
                        logger.info("Forçando o encerramento do navegador para relogar no CAMPO...")
                        break

                    mensagem_deslogado = detectar_mensagem_deslogado_imediata(pagina)
                    if mensagem_deslogado:
                        logger.warning(f"Mensagem de sessão encerrada detectada na tela: '{mensagem_deslogado}'.")
                        relogar()
                        estado_sessao['ultima_atividade_valida_ts'] = time.time()
                        time.sleep(INTERVALO_BUSCA)
                        continue

                    tempo_inativo = time.time() - estado_sessao['ultima_atividade_valida_ts']
                    if tempo_inativo > TEMPO_MAX_INATIVIDADE_SEG:
                        if bot_f_presente_na_tela(pagina):
                            estado_sessao['ultima_atividade_valida_ts'] = time.time()
                        elif nenhum_registro_presente_na_tela(pagina):
                            logger.warning(
                                f"Inatividade de {int(tempo_inativo)}s sem nenhum chamado válido. Refazendo login..."
                            )
                            enviar_alerta_telegram(
                                "🟡 Possível sessão deslogada no CAMPO. "
                                "Refazendo login automaticamente..."
                            )
                            relogar()
                        estado_sessao['ultima_atividade_valida_ts'] = time.time()
                        time.sleep(INTERVALO_BUSCA)
                        continue

                    if token_nao_informado_presente(pagina):
                        relogar()
                        estado_sessao['ultima_atividade_valida_ts'] = time.time()
                        time.sleep(INTERVALO_BUSCA)
                        continue

                    # Este bloco é também o detector de navegador zumbi.
                    #
                    # Ele já conversa com a página a cada volta, então não custa
                    # nada a mais -- e desde que a varredura saiu do Playwright
                    # (ver _SESSAO_CAMPO) ele é o único ponto do laço que ainda
                    # toca no navegador. Por isso o erro de contexto morto SOBE
                    # daqui em vez de ser engolido: sem isso, um navegador morto
                    # se disfarça de "interface indisponível" e o bot gira em
                    # falso por horas, como aconteceu em 03/08.
                    try:
                        botao_refresh = pagina.locator(SELECTOR_BOTAO_REFRESH)
                        if botao_refresh.count() > 0 and botao_refresh.first.is_visible():
                            logger.debug("Clicando no botão 'Atualizar lista'...")
                            botao_refresh.first.click()
                            pagina.wait_for_timeout(1500)
                    except Exception as e:
                        if _e_erro_de_contexto_morto(e):
                            raise ContextoNavegadorMorto(str(e)) from e
                        logger.debug("Não foi possível clicar no botão de refresh (interface pode estar indisponível).")

                    # Com INTERVALO_VARREDURA_COMPLETA_SEG em 25 isto dá True em
                    # TODA volta, logo depois do clique no refresh -- que é
                    # exatamente o desenho das versões que rodavam bem. O `if`
                    # continua existindo porque o intervalo é o dial de emergência:
                    # subir o número volta a espaçar sem mexer em mais nada.
                    #
                    # `varreu_agora` existe porque `tentou=False` JÁ TINHA um
                    # significado: "tentei e não consegui token/URL", e o ramo que
                    # trata isso RECARREGA a página. Reaproveitar esse False para
                    # dizer "pulei de propósito" fez o bot dar reload no CAMPO a cada
                    # 25s -- entrou em produção às 04:32 de 08/08/2026 e foi pego
                    # pelo aviso "Token/URL da API ainda não disponíveis" repetindo
                    # no log. São dois estados diferentes e precisam de duas
                    # variáveis.
                    varreu_agora = bool(
                        INTERVALO_VARREDURA_COMPLETA_SEG
                        and time.time() - ultima_varredura_completa_ts
                        >= INTERVALO_VARREDURA_COMPLETA_SEG
                    )
                    if varreu_agora:
                        # Carimba ANTES de buscar: se a busca demorar ou falhar, o
                        # intervalo continua contando a partir da tentativa, e não
                        # vira uma sequência de varreduras coladas.
                        ultima_varredura_completa_ts = time.time()
                        tentou, deslogado_api, lista_chamados, capex_confiavel, reparos_confiavel = buscar_chamados_via_api()
                    else:
                        tentou, deslogado_api, lista_chamados, capex_confiavel, reparos_confiavel = False, False, [], False, False
                        # Batida VISÍVEL no log, uma linha por volta.
                        #
                        # Sem ela, as voltas puladas não escrevem nada e o log fica
                        # mudo por até 30 min -- indistinguível de travamento, tanto
                        # para quem olha o console quanto para o `log_idade_s` da
                        # telemetria, que existe justamente para dizer "o laço
                        # parou". Em 08/08/2026 o operador viu 3,5 min de silêncio,
                        # concluiu que tinha travado e reiniciou na mão; a
                        # telemetria mostrou depois que o bot estava trabalhando o
                        # tempo todo, com a CPU subindo normalmente.
                        #
                        # Silêncio no log tem de continuar significando morte.
                        faltam = int(INTERVALO_VARREDURA_COMPLETA_SEG
                                     - (time.time() - ultima_varredura_completa_ts))
                        logger.info(
                            "Ciclo ok (varredura espaçada). Próxima varredura "
                            f"completa em ~{max(0, faltam) // 60} min."
                        )
                    if deslogado_api:
                        logger.warning(
                            "Busca direta retornou status de sessão deslogada (401/403). Refazendo login..."
                        )
                        enviar_alerta_telegram(
                            "🟡 Sessão deslogada detectada pela API (status 401/403 na busca direta). "
                            "Refazendo login automaticamente..."
                        )
                        relogar()
                        estado_sessao['ultima_atividade_valida_ts'] = time.time()
                        time.sleep(INTERVALO_BUSCA)
                        continue

                    if tentou:
                        logger.info(
                            f"🔄 Varredura concluída: {len(lista_chamados)} chamados analisados."
                        )

                        # Varredura vazia repetida = navegador respondendo nada
                        # (contexto zumbi). Reabre em vez de girar em falso, como
                        # aconteceu por ~5h em 03/08.
                        if lista_chamados:
                            ciclos_vazios_seguidos = 0
                        else:
                            ciclos_vazios_seguidos += 1
                            if ciclos_vazios_seguidos >= MAX_CICLOS_VAZIOS_SEGUIDOS:
                                logger.error(
                                    f"{ciclos_vazios_seguidos} varreduras seguidas sem nenhum chamado. "
                                    "Tratando como navegador zumbi e reabrindo o navegador."
                                )
                                enviar_alerta_telegram(
                                    f"🟠 {ciclos_vazios_seguidos} varreduras seguidas vieram vazias. "
                                    "Reabrindo o navegador automaticamente..."
                                )
                                break

                        # Só publica a lista para o backlog quando ela veio
                        # inteira: um backlog gerado a partir de uma varredura
                        # truncada sai com números menores que a realidade.
                        # Lista boa e um pouco velha > lista nova e cortada.
                        #
                        # Mantenha `varreu_agora` no teste mesmo hoje, com uma
                        # varredura só: ele é o que impede uma busca PARCIAL de
                        # ser publicada como se fosse o universo inteiro. Em
                        # 08/08/2026 existiu aqui uma "varredura leve" que
                        # devolvia capex_confiavel=True sem trazer ES06, REPPME,
                        # UP02 nem ES15 -- sem esta guarda ela teria zerado
                        # Upgrade e Mudança de Cômodo no backlog, em silêncio.
                        if capex_confiavel and varreu_agora:
                            # Projeção, não a lista crua: é o que sobrevive até a
                            # próxima varredura, e guardar o chamado inteiro
                            # custava 8x mais memória (ver projetar_para_cache).
                            LISTA_CHAMADOS_ATUAL["dados"] = projetar_para_cache(lista_chamados)
                            salvar_amostra_chamados(lista_chamados)
                            salvar_diagnostico_plano(lista_chamados)

                        # Recebe a lista CRUA de propósito: a extração de
                        # telefone desce na estrutura aninhada, que a projeção
                        # não tem. Depois daqui ela pode ser liberada.
                        processar_lista_chamados(
                            lista_chamados,
                            capex_confiavel=capex_confiavel,
                            reparos_confiavel=reparos_confiavel,
                        )
                    elif varreu_agora:
                        # Só recarrega quando a varredura REALMENTE foi tentada e
                        # falhou. Nas voltas em que ela foi pulada de propósito não
                        # há nada de errado para recuperar -- e o reload aqui custa
                        # caro: derruba o token que o listener capturou e obriga a
                        # clicar em 'Carregar chamados' de novo.
                        logger.warning("Token/URL da API ainda não disponíveis. Tentando usar listener como fallback...")
                        reload_seguro(pagina)
                        if "login" in pagina.url:
                            logger.warning("Página de login detectada após reload. Refazendo login...")
                            relogar()
                        else:
                            try:
                                botao_carregar = pagina.locator(SELECTOR_BOTAO_CARREGAR_CHAMADOS)
                                if botao_carregar.is_visible(timeout=5000) and botao_carregar.is_enabled():
                                    logger.info("Pós-reload: clicando em 'Carregar chamados'...")
                                    botao_carregar.first.click()
                                    pagina.wait_for_timeout(3000)
                            except Exception:
                                pass

                    time.sleep(INTERVALO_BUSCA)

            except KeyboardInterrupt:
                logger.warning("Encerramento manual solicitado (Ctrl+C).")
                raise
            except ContextoNavegadorMorto as e:
                # Caminho esperado quando o navegador morre: cai aqui, o finally
                # fecha o que sobrou e o while externo reabre tudo do zero.
                logger.error(f"Navegador/contexto morreu ({e}). Reabrindo o navegador...")
            except Exception as e:
                logger.error(f"Monitoramento interrompido por erro: {e}")
            finally:
                try:
                    contexto.close()
                    logger.info("Navegador fechado (limpeza de sessão).")
                except Exception as e:
                    logger.warning(f"Navegador já estava fechado ou falhou ao fechar: {e}")

        logger.info("Aguardando 5 segundos antes de reiniciar o processo do navegador...")
        time.sleep(5)


def main():
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            logger.warning("Processo encerrado manualmente pelo usuário (KeyboardInterrupt).")
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Exceção não tratada:", exc_info=(exc_type, exc_value, exc_traceback))
        alerta_critico_telegram(f"Exceção não tratada: {exc_value}")

    sys.excepthook = handle_exception

    if not adquirir_lock_instancia_unica():
        logger.warning(
            "Outra instância do monitor já está em execução (lock ativo). "
            "Encerrando esta tentativa para evitar conflito no perfil do "
            "navegador."
        )
        # Sair aqui é rotina, não erro: é assim que a colisão entre as duas
        # redes de recuperação se resolve. Se o relançamento interno já subiu
        # um bot, a tentativa do Agendador cai neste ponto e desiste -- e
        # vice-versa. Ninguém precisa ler o código de saída para isso
        # funcionar: o Agendador usa MultipleInstances=IgnoreNew e o
        # relançamento interno é um processo solto, sem supervisor atrás.
        return

    try:
        if not os.path.exists(CST_PERFIL_DIRETORIO):
            os.makedirs(CST_PERFIL_DIRETORIO)
            logger.info("Pasta de perfil criada. Na primeira execução, faça login e MFA manualmente.")

        logger.info("=== Iniciando processo do monitor ===")

        # Fora do laço de propósito: é o único que continua funcionando quando
        # o laço de monitoramento trava.
        threading.Thread(target=thread_vigia_monitor, daemon=True).start()

        thread_status = threading.Thread(target=escutar_comandos_telegram, daemon=True)
        thread_status.start()

        # ============ NOVO: ponte com o site do painel. Atende o botão
        # "Atualizar" da tela de backlog, que pede um backlog novo. ============
        threading.Thread(target=iniciar_ponte_painel, daemon=True).start()

        # O backlog NAO tem mais agendador automatico: passou a ser gerado
        # somente quando alguem pede -- pelo comando "backlog" no grupo
        # (Telegram/WhatsApp) ou pelo botao "Gerar backlog novo" do site.

        # ============ NOVO: agendador automático do termômetro de
        # entrantes CAPEX, repetindo a cada 1h30. ============
        thread_termometro = threading.Thread(
            target=thread_agendador_termometro_capex,
            args=(int(1.5 * 60 * 60),),  # 1h30
            daemon=True
        )
        thread_termometro.start()

        # ============ NOVO (13/08/2026): lista de garantias nos grupos
        # regionais, na hora cheia das 7h às 19h. Sobe aqui, antes do
        # iniciar_servico_alerta_whatsapp abaixo, sem problema: a thread não
        # envia nada até bater a primeira hora cheia, e a essa altura o
        # serviço do WhatsApp já está de pé. ============
        threading.Thread(
            target=garantias_envio.thread_agendador_garantias,
            args=(estado_para_lista_garantias,),
            daemon=True,
        ).start()

        iniciar_vpn_sempre_ativa()

        iniciar_servico_alerta_whatsapp()

        if WHATSAPP_ALERTA_ATIVO:
            thread_status_whatsapp = threading.Thread(target=escutar_comandos_whatsapp, daemon=True)
            thread_status_whatsapp.start()

            threading.Thread(target=thread_reenvio_alertas_whatsapp, daemon=True).start()

        global TV_ATIVA
        try:
            # O monitoramento SEMPRE vai para thread de fundo, mesmo sem painel.
            # A thread principal fica de plantão no laço abaixo só para poder
            # criar a janela do Tk quando o /exibirpaineltv pedir -- Tk não
            # funciona fora dela. Até 07/08/2026 isso dependia da resposta a uma
            # caixa de diálogo no arranque, que travava a subida automática.
            thread_monitor = threading.Thread(
                target=executar_monitoramento, args=(False,), daemon=True
            )
            thread_monitor.start()
            logger.info(
                "Monitoramento em segundo plano. Sem janelas: use /exibirpaineltv "
                "para o painel na TV e /exibirnavegador para ver o Chromium."
            )

            while True:
                if not painel_tv_pedido():
                    time.sleep(1)
                    continue

                # TV_ATIVA liga o abastecimento da FILA_EVENTOS_TV lá no laço de
                # monitoramento. Fora daqui a fila não recebe nada, para não
                # crescer sem ninguém consumindo.
                TV_ATIVA = True
                logger.info("Abrindo o painel de TV a pedido do grupo.")
                try:
                    PainelTV().mainloop()
                except Exception:
                    logger.exception("Painel de TV caiu; o monitoramento segue.")
                finally:
                    TV_ATIVA = False
                    _painel_tv_pedido.clear()
                    with FILA_EVENTOS_TV.mutex:      # descarta o que sobrou
                        FILA_EVENTOS_TV.queue.clear()
                    logger.info("Painel de TV encerrado. Monitoramento segue normal.")
        except KeyboardInterrupt:
            logger.warning("=== Processo finalizado por Ctrl+C ===")
        finally:
            logger.info("=== Processo do monitor finalizado ===")
    finally:
        liberar_lock_instancia_unica()


if __name__ == "__main__":
    main()