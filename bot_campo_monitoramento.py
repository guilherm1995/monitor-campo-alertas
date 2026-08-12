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
import time
import random
import logging
from logging.handlers import RotatingFileHandler
import re
import unicodedata
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
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
from improdutivas import analisar_improdutivas, formatar_mensagens_whatsapp

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
    from PIL import Image, ImageTk
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

# ================= IMPRODUTIVAS (relatório OFS via CSV no grupo) =================
# Mesmo esquema do AGUARDANDO_CONTRATO_AUTENTICADOR_WHATSAPP acima: chaveado pelo
# participante, pra várias pessoas poderem usar /improdutivas ao mesmo tempo
# sem se atrapalhar. O timeout é maior que o do /autenticador (10 min em vez de 5)
# porque encontrar e anexar o CSV do OFS costuma levar mais tempo do que
# digitar um número de contrato.
AGUARDANDO_ARQUIVO_IMPRODUTIVAS_WHATSAPP = {}
TIMEOUT_AGUARDANDO_ARQUIVO_IMPRODUTIVAS_SEG = 10 * 60

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
CAMPOS_CACHE_BACKLOG = ("id", "fila", "enderecoUnidade", "codigoContrato",
                        "dataAbertura", "dataConclusao", "agendamentoData")


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
INTERVALO_VARREDURA_COMPLETA_SEG = 120

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
COR_FUNDO = "#081B3A"
COR_SIDEBAR = "#06152E"
COR_CARD = "#102B57"
COR_CARD_ESCURO = "#0A1931"
COR_LINHA = "#16376B"
COR_DESTAQUE = "#00BFFF"
COR_AZUL_BOTAO = "#007BFF"
COR_VERDE = "#00FF88"
COR_ALERTA = "#C10037"
COR_ALERTA_ESCURO = "#900028"
COR_TEXTO = "#FFFFFF"
COR_TEXTO_MUTED = "#AAAAAA"

MAX_ITENS_CAPEX = 7      # cards de CAPEX são mais compactos (2 linhas) -> cabem mais na tela
MAX_ITENS_GARANTIA = 5   # cards de garantia são mais altos (3 linhas, com técnico OFS) -> cabem menos
DURACAO_ALERTA_MS = 120000
INTERVALO_PISKAR_MS = 600

FILA_EVENTOS_TV = queue.Queue()
TV_ATIVA = False
ARQUIVO_HISTORICO_PAINEL = os.path.join(PASTA_DADOS, "historico_painel.json")

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


def montar_mensagem_status():
    with _stats_lock:
        _resetar_stats_diarias_se_necessario()
        capex_rj = ESTATISTICAS_STATUS['capex_pendente_sul_rj']
        capex_sp = ESTATISTICAS_STATUS['capex_pendente_litoral_sp']
        capex_notif = ESTATISTICAS_STATUS['capex_notificadas_hoje']
        garantias_notif = ESTATISTICAS_STATUS['garantias_notificadas_hoje']
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
        f"Erros registrados no LOG hoje: {erros}\n"
        f"TOTAL de O.S analisadas por minuto: {os_por_minuto:.1f}"
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


def tocar_som_alerta_garantia():
    if not sys.platform.startswith('win'):
        logger.warning("Reprodução de som de alerta só implementada para Windows.")
        return
    if not os.path.exists(SOM_ALERTA_GARANTIA):
        logger.warning(f"Arquivo de som '{os.path.basename(SOM_ALERTA_GARANTIA)}' não encontrado.")
        return

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


def reavaliar_reparos_pendentes(reparos_avaliados):
    pendentes = [
        info for info in reparos_avaliados.values()
        if not info.get('notificado', True) and info.get('codigo_contrato') and info.get('data_abertura')
    ]
    if not pendentes:
        logger.info("Reavaliação da Base OFS: nenhum reparo pendente no momento.")
        return

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

def consultar_autenticador_status(lista_contratos):
    """
    Consulta o status das sessões (online/offline) dos contratos informados
    diretamente no Autenticador.
    """
    if not PANDAS_DISPONIVEL:
        return None, "Dependência 'pandas' não está instalada nesta máquina."

    try:
        sessao = requests.Session()
        payload = {"contratos": "\n".join(lista_contratos)}

        sessao.post(AUTENTICADOR_URL_SAVE, data=payload, verify=False, timeout=(5, 35))
        sessao.get(AUTENTICADOR_URL_PROCESSA, verify=False, timeout=(5, 35))
        res = sessao.get(AUTENTICADOR_URL_LER_CSV, verify=False, timeout=(5, 35))
        html_resp = res.text

        if '<table>' not in html_resp:
            return pd.DataFrame(), "Resposta do servidor não contém tabela (verifique se a VPN está conectada)."

        try:
            tabelas = pd.read_html(io.StringIO(html_resp))
        except ImportError as e:
            return pd.DataFrame(), f"Biblioteca de parse HTML ausente (ex: lxml): {e}"

        if not tabelas:
            return pd.DataFrame(), "Nenhuma tabela encontrada na resposta do Autenticador."

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

        df['CONTRATO'] = df['CONTRATO'].apply(lambda x: str(int(x)) if pd.notna(x) and str(x).replace('.0', '').isdigit() else str(x))

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


# --- Vigia do serviço Node (ponte HTTP do WhatsApp) ---
# De quanto em quanto o vigia bate em /status. 20s dá detecção em ~1 min sem
# ficar martelando a ponte.
INTERVALO_VIGIA_WHATSAPP_SEG = 20
# Carência depois de (re)subir o Node, antes de a checagem valer. O Baileys leva
# alguns segundos para abrir a porta; 45s cobre com folga. O trava de 09/08 NÃO
# abriu a porta nem em 1h50, então esta carência não mascara um travamento real.
CARENCIA_ARRANQUE_WHATSAPP_SEG = 45
# Quantas checagens seguidas sem /status para dar a ponte por morta e relançar.
# 3 x 20s = ~1 min de fora do ar confirmado, longe do transitório de uma
# reconexão do Baileys (que nem derruba a ponte).
FALHAS_ATE_REINICIAR_WHATSAPP = 3
# Depois de tantos relançamentos sem sucesso, avisa UMA vez no Telegram. Silêncio
# aqui repetiria o buraco de 09/08 por outro caminho.
MAX_RELANCAMENTOS_WHATSAPP_ANTES_ALERTA = 3


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


def _encerrar_processo_whatsapp():
    """Mata o Node do WhatsApp que este bot subiu, antes de relançar outro.

    Necessário porque o travamento de 09/08 deixou o processo VIVO sem abrir a
    porta -- `_esperar_porta_whatsapp_livre` sozinho não o remove (ele não segura
    a porta) e um simples relançamento vazaria um Node zumbi por tentativa.
    """
    proc = STATUS_SERVICO_WHATSAPP.get("processo")
    if proc is None:
        return
    try:
        if proc.poll() is None:          # ainda vivo
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
    except Exception as e:
        logger.warning(f"Falha ao encerrar o Node do WhatsApp antes de relançar: {e}")
    finally:
        STATUS_SERVICO_WHATSAPP["conectado"] = False
        STATUS_SERVICO_WHATSAPP["processo"] = None


def thread_vigia_servico_whatsapp():
    """Relança o serviço Node do WhatsApp se a ponte HTTP parar de responder.

    Cuida SÓ da ponte HTTP (porta respondendo /status). A reconexão da sessão do
    WhatsApp o próprio Baileys já faz sozinho -- um 428 rotineiro derruba
    `conectado` mas a ponte segue no ar (`servidor.listen` no index.js é
    independente da conexão), então isso NÃO é motivo de relançar. Usar /status
    em vez do estado "conectado" é justamente o que evita reinício falso a cada
    reconexão normal.

    O buraco que isto tapa é outro: em 09/08/2026 um reinício por RAM subiu um
    Node que travou no arranque e NUNCA abriu a porta -- processo vivo, ponte
    muda, e o bot passou ~1h50 gravando "serviço Node pode estar offline" a cada
    3s sem ninguém agir, porque o processo estar VIVO não era o mesmo que a ponte
    estar no ar. O Telegram não foi afetado (canal à parte).
    """
    if not (WHATSAPP_ALERTA_ATIVO and WHATSAPP_ALERTA_AUTOSTART):
        return

    logger.info(
        "Vigia do serviço WhatsApp iniciado (relança o Node se a ponte HTTP "
        f"ficar {FALHAS_ATE_REINICIAR_WHATSAPP} checagens sem responder /status)."
    )
    # O Node acabou de subir junto com o bot; dá o tempo de arranque antes de a
    # primeira checagem valer.
    time.sleep(CARENCIA_ARRANQUE_WHATSAPP_SEG)

    falhas = 0
    relancamentos = 0
    alertou_persistente = False

    while True:
        time.sleep(INTERVALO_VIGIA_WHATSAPP_SEG)

        if _servico_whatsapp_saudavel():
            if falhas or relancamentos:
                logger.info("Ponte do WhatsApp respondendo /status de novo.")
            falhas = 0
            relancamentos = 0
            alertou_persistente = False
            continue

        falhas += 1
        logger.warning(
            f"Ponte do WhatsApp não respondeu /status -- checagem "
            f"{falhas}/{FALHAS_ATE_REINICIAR_WHATSAPP}."
        )
        if falhas < FALHAS_ATE_REINICIAR_WHATSAPP:
            continue

        # Fora do ar confirmado. Mata o Node travado e sobe outro.
        falhas = 0
        relancamentos += 1
        logger.warning(
            f"Ponte do WhatsApp fora do ar. Relançando o serviço Node "
            f"(tentativa {relancamentos})."
        )
        _encerrar_processo_whatsapp()
        try:
            iniciar_servico_alerta_whatsapp()
        except Exception:
            logger.exception("Falha ao relançar o serviço de alerta WhatsApp.")

        # Mesmo tempo de arranque antes de voltar a cobrar.
        time.sleep(CARENCIA_ARRANQUE_WHATSAPP_SEG)

        # Insistiu várias vezes e não voltou: avisa UMA vez pelo Telegram, que é
        # o canal que ainda funciona quando o WhatsApp está fora.
        if (relancamentos >= MAX_RELANCAMENTOS_WHATSAPP_ANTES_ALERTA
                and not alertou_persistente and not _servico_whatsapp_saudavel()):
            alertou_persistente = True
            try:
                alerta_critico_telegram(
                    f"🟠 Serviço de alerta do WhatsApp não sobe: {relancamentos} "
                    "relançamentos sem a ponte responder. Os alertas do grupo "
                    "estão saindo só pelo Telegram até isso voltar."
                )
            except Exception:
                pass


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
#   1) comando /painel  -> responde no grupo com o endereço e o PIN, e
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
    """Resposta do /painel: sobe o site se preciso e devolve endereço + PIN."""
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
    linhas += [
        "",
        f"🔑 PIN: {endereco.get('pin', '(não informado)')}",
        "",
        "_O endereço muda quando o site reinicia — peça /painel de novo se falhar._",
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
        "📡 *​/autenticador* — consulta status de um contrato",
        "🖥️ *​/painel* — endereço e PIN do site do painel",
    ]
    if whatsapp:
        linhas.append("📎 *​/improdutivas* — análise do relatório OFS (envie o CSV depois)")
    else:
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


def processar_comando_improdutivas_whatsapp(arquivo):
    """Recebe o dict {"nome": ..., "caminho": ...} de um arquivo baixado pelo
    serviço Node (ver index.js), valida que é um CSV, roda a análise de
    improdutivas e manda o resultado de volta pro grupo, uma mensagem por
    região (Litoral Norte SP / Sul RJ)."""
    caminho = (arquivo or {}).get("caminho")
    nome = (arquivo or {}).get("nome") or "arquivo"

    if not caminho or not os.path.exists(caminho):
        enviar_alerta_whatsapp_grupo(
            "⚠️ Não consegui localizar o arquivo recebido. Tente novamente com /improdutivas."
        )
        return

    if not nome.lower().endswith(".csv"):
        enviar_alerta_whatsapp_grupo(
            f"⚠️ Esperava um arquivo .csv do relatório OFS, recebi \"{nome}\". "
            f"Envie /improdutivas de novo e anexe o CSV."
        )
        return

    try:
        resultado = analisar_improdutivas(caminho)
    except ValueError as e:
        # Erros "esperados" (coluna faltando, arquivo vazio etc.) -- mensagem
        # já vem pronta pra mostrar ao usuário, sem stack trace.
        enviar_alerta_whatsapp_grupo(f"⚠️ {e}")
        return
    except Exception:
        logger.exception("Falha inesperada ao analisar arquivo de improdutivas.")
        enviar_alerta_whatsapp_grupo(
            "⚠️ Erro inesperado ao analisar o arquivo. Tente novamente com /improdutivas."
        )
        return

    try:
        for mensagem in formatar_mensagens_whatsapp(resultado):
            enviar_alerta_whatsapp_grupo(mensagem)
            time.sleep(1.5)  # evita rajada de mensagens muito próximas
    except Exception:
        logger.exception("Falha ao enviar o resultado de improdutivas pro grupo do WhatsApp.")


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

                # Arquivo anexado (ex: CSV do OFS) -- só nos interessa se tem
                # alguém esperando esse arquivo por causa do /improdutivas.
                # Tratado antes do resto pra não cair no parser de comando de
                # texto (mensagens com anexo têm texto="" do lado do Node).
                if arquivo:
                    ts_prompt = AGUARDANDO_ARQUIVO_IMPRODUTIVAS_WHATSAPP.pop(remetente, None)
                    if ts_prompt is not None:
                        if (time.time() - ts_prompt) <= TIMEOUT_AGUARDANDO_ARQUIVO_IMPRODUTIVAS_SEG:
                            enviar_alerta_whatsapp_grupo("⏳ Recebi o arquivo, analisando as improdutivas...")
                            threading.Thread(
                                target=processar_comando_improdutivas_whatsapp,
                                args=(arquivo,),
                                daemon=True,
                            ).start()
                        else:
                            enviar_alerta_whatsapp_grupo(
                                "⏱️ Tempo para enviar o arquivo expirou. Envie /improdutivas novamente."
                            )
                    continue

                comando = _obter_comando_whatsapp(texto_bruto)

                if comando in ("/comandos", "comandos", "/ajuda", "ajuda", "/help"):
                    logger.info("Comando /comandos recebido no grupo do WhatsApp.")
                    try:
                        enviar_alerta_whatsapp_grupo(montar_mensagem_comandos(whatsapp=True))
                    except Exception:
                        logger.exception("Falha ao enviar a lista de comandos no WhatsApp.")
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

                # ============ NOVO: /improdutivas (análise do relatório OFS) ============
                if comando == "/improdutivas":
                    logger.info("Comando /improdutivas recebido no grupo do WhatsApp. Aguardando arquivo CSV...")
                    AGUARDANDO_ARQUIVO_IMPRODUTIVAS_WHATSAPP[remetente] = time.time()
                    enviar_alerta_whatsapp_grupo(
                        "📎 Envie o arquivo OFS (CSV) do dia anterior anexado aqui no grupo."
                    )
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


class PainelTV(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Central de Monitoramento - CAMPO Logística")
        self.configure(bg=COR_FUNDO)

        self._em_tela_cheia = False
        self.update_idletasks()
        self.after(150, self._aplicar_tela_cheia)

        self.bind('<Escape>', lambda e: self._alternar_tela_cheia(False))
        self.bind('<F11>', lambda e: self._alternar_tela_cheia(not self._em_tela_cheia))

        self.itens_capex = []
        self.itens_garantia = []
        self.dados_capex_salvos = []
        self.dados_garantias_salvos = []
        self._alerta_ativo = False
        self._piscar_estado = False
        self._job_alerta = None
        self._logo_imagem_ref = None

        self._montar_layout_base()
        self._carregar_historico_painel()
        self._montar_overlay_alerta()
        self._atualizar_relogio()
        self._processar_fila()
        self._processar_fila_clima()

        threading.Thread(target=thread_atualizacao_clima, daemon=True).start()

    def _aplicar_tela_cheia(self):
        self.update_idletasks()
        largura = self.winfo_screenwidth()
        altura = self.winfo_screenheight()
        self.geometry(f"{largura}x{altura}+0+0")

        try:
            self.attributes('-fullscreen', True)
        except Exception:
            pass

        try:
            self.state('zoomed')
        except Exception:
            pass

        self._em_tela_cheia = True

    def _alternar_tela_cheia(self, ligar):
        if ligar:
            self._aplicar_tela_cheia()
        else:
            try:
                self.attributes('-fullscreen', False)
            except Exception:
                pass
            self._em_tela_cheia = False
            self.geometry("1600x900")

    def _salvar_historico_painel(self):
        try:
            dados = {
                "capex": self.dados_capex_salvos,
                "garantia": self.dados_garantias_salvos
            }
            salvar_json_atomico(ARQUIVO_HISTORICO_PAINEL, dados, ensure_ascii=False, indent=2)
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

            if self.dados_capex_salvos:
                if self.placeholder_capex.winfo_ismapped():
                    self.placeholder_capex.grid_forget()
                for ev in reversed(self.dados_capex_salvos):
                    novo_item = self._criar_item_capex(ev)
                    self.itens_capex.insert(0, novo_item)
                for index, it in enumerate(self.itens_capex):
                    it.grid(row=index, column=0, sticky="ew", pady=3)

            if self.dados_garantias_salvos:
                if self.placeholder_garantia.winfo_ismapped():
                    self.placeholder_garantia.grid_forget()
                for ev in reversed(self.dados_garantias_salvos):
                    novo_item = self._criar_item_garantia(ev)
                    self.itens_garantia.insert(0, novo_item)
                for index, it in enumerate(self.itens_garantia):
                    it.grid(row=index, column=0, sticky="ew", pady=3)
        except Exception as e:
            logger.warning(f"Falha ao carregar histórico do painel de TV: {e}")

    def _montar_layout_base(self):
        header = tk.Frame(self, bg=COR_SIDEBAR, height=110)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        header.grid_columnconfigure(0, weight=0)
        header.grid_columnconfigure(1, weight=1)
        header.grid_columnconfigure(2, weight=0)
        header.grid_columnconfigure(3, weight=0)
        header.grid_rowconfigure(0, weight=1)

        self.logo_label = tk.Label(header, bg=COR_SIDEBAR)
        imagem_logo = self._carregar_imagem_logo(altura_desejada=60)
        if imagem_logo is not None:
            self._logo_imagem_ref = imagem_logo
            self.logo_label.configure(image=imagem_logo)
        self.logo_label.grid(row=0, column=0, sticky="w", padx=(35, 10), pady=8)

        self.clima_label = tk.Label(
            header, text=f"🌡️ {CIDADE_CLIMA}: carregando...",
            bg=COR_SIDEBAR, fg=COR_TEXTO_MUTED, font=("Segoe UI", 16, "bold"),
            anchor="e", justify="right"
        )
        self.clima_label.grid(row=0, column=2, sticky="e", padx=25, pady=12)

        self.relogio_label = tk.Label(
            header, text="", bg=COR_SIDEBAR, fg=COR_TEXTO,
            font=("Consolas", 26, "bold"), anchor="e"
        )
        self.relogio_label.grid(row=0, column=3, sticky="e", padx=45, pady=12)

        self._job_ajuste_titulo = None
        self.bind('<Configure>', self._agendar_ajuste_titulo)

        self._configurar_estilo_abas()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=0, pady=(6, 0))

        self.aba_alerta = tk.Frame(self.notebook, bg=COR_FUNDO)
        self.notebook.add(self.aba_alerta, text="  Central de Alerta  ")

        tk.Label(
            self.aba_alerta, text="CENTRAL DE ALERTA",
            bg=COR_FUNDO, fg=COR_TEXTO_MUTED, font=("Segoe UI", 16, "bold")
        ).pack(fill="x", pady=(10, 6))

        self.corpo = tk.Frame(self.aba_alerta, bg=COR_FUNDO)
        self.corpo.pack(fill="both", expand=True, padx=35, pady=(0, 12))
        self.corpo.grid_columnconfigure(0, weight=1, uniform="colunas")
        self.corpo.grid_columnconfigure(1, weight=1, uniform="colunas")
        self.corpo.grid_rowconfigure(1, weight=1)

        frame_capex = tk.Frame(self.corpo, bg=COR_FUNDO)
        frame_capex.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 15))

        tk.Label(
            frame_capex, text="🟦 ENTRANTES DE CAPEX", bg=COR_FUNDO, fg=COR_DESTAQUE,
            font=("Segoe UI", 17, "bold"), anchor="w"
        ).pack(fill="x", pady=(0, 6))

        self.lista_capex_container = tk.Frame(frame_capex, bg=COR_FUNDO)
        self.lista_capex_container.pack(fill="both", expand=True)
        self.lista_capex_container.grid_columnconfigure(0, weight=1)

        self.placeholder_capex = tk.Label(
            self.lista_capex_container, text="Aguardando entrantes...",
            bg=COR_FUNDO, fg=COR_TEXTO_MUTED, font=("Segoe UI", 15, "italic")
        )
        self.placeholder_capex.grid(row=0, column=0, pady=30, sticky="ew")

        frame_garantia = tk.Frame(self.corpo, bg=COR_FUNDO)
        frame_garantia.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(15, 0))

        tk.Label(
            frame_garantia, text="🟥 GARANTIAS", bg=COR_FUNDO, fg=COR_ALERTA,
            font=("Segoe UI", 17, "bold"), anchor="w"
        ).pack(fill="x", pady=(0, 6))

        self.lista_garantia_container = tk.Frame(frame_garantia, bg=COR_FUNDO)
        self.lista_garantia_container.pack(fill="both", expand=True)
        self.lista_garantia_container.grid_columnconfigure(0, weight=1)

        self.placeholder_garantia = tk.Label(
            self.lista_garantia_container, text="Aguardando garantias...",
            bg=COR_FUNDO, fg=COR_TEXTO_MUTED, font=("Segoe UI", 15, "italic")
        )
        self.placeholder_garantia.grid(row=0, column=0, pady=30, sticky="ew")

    def _configurar_estilo_abas(self):
        estilo = ttk.Style(self)
        try:
            estilo.theme_use("clam")
        except Exception:
            pass
        estilo.configure("TNotebook", background=COR_FUNDO, borderwidth=0)
        estilo.configure(
            "TNotebook.Tab",
            background=COR_SIDEBAR, foreground=COR_TEXTO_MUTED,
            padding=(18, 10), font=("Segoe UI", 11, "bold"), borderwidth=0
        )
        estilo.map(
            "TNotebook.Tab",
            background=[("selected", COR_CARD)],
            foreground=[("selected", COR_TEXTO)]
        )

    def _carregar_imagem_logo(self, altura_desejada=70):
        if not os.path.exists(CAMINHO_LOGO_OPERACIONAL):
            logger.warning(f"Logo '{os.path.basename(CAMINHO_LOGO_OPERACIONAL)}' não encontrada.")
            return None
        try:
            if _PIL_DISPONIVEL:
                imagem = Image.open(CAMINHO_LOGO_OPERACIONAL)
                proporcao = altura_desejada / float(imagem.height)
                largura_nova = max(1, int(imagem.width * proporcao))
                imagem = imagem.resize((largura_nova, altura_desejada), Image.LANCZOS)
                return ImageTk.PhotoImage(imagem)
            else:
                logger.warning("Pillow não instalado: redimensionando logo com subsample.")
                imagem = tk.PhotoImage(file=CAMINHO_LOGO_OPERACIONAL)
                altura_original = imagem.height()
                if altura_original > altura_desejada:
                    fator = max(1, round(altura_original / altura_desejada))
                    imagem = imagem.subsample(fator, fator)
                return imagem
        except Exception as e:
            logger.warning(f"Falha ao carregar a logo: {e}")
            return None

    def _montar_overlay_alerta(self):
        self.overlay = tk.Frame(self, bg=COR_ALERTA)

        self.overlay_titulo = tk.Label(
            self.overlay, text="⚠  ALERTA DE GARANTIA  ⚠",
            bg=COR_ALERTA, fg="#FFFFFF", font=("Segoe UI", 64, "bold")
        )
        self.overlay_titulo.pack(pady=(70, 40))

        self.overlay_corpo = tk.Frame(self.overlay, bg=COR_ALERTA)
        self.overlay_corpo.pack(expand=True, fill="both", padx=120)

    def _agendar_ajuste_titulo(self, event=None):
        if self._job_ajuste_titulo:
            self.after_cancel(self._job_ajuste_titulo)
        self._job_ajuste_titulo = self.after(150, self._reajustar_wraplength_itens)

    def _atualizar_relogio(self):
        agora = datetime.now().strftime("%d/%m/%Y   %H:%M:%S")
        self.relogio_label.configure(text=agora)
        self.after(1000, self._atualizar_relogio)

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
                    self._exibir_alerta_garantia(evento)
                    self._adicionar_item_garantia(evento)
                else:
                    self._adicionar_item_capex(evento)
        except queue.Empty:
            pass
        self.after(300, self._processar_fila)

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
        emoji = previsao.get('emoji', '🌡️')

        if temperatura is not None:
            texto = f"{emoji} {CIDADE_CLIMA}: {temperatura:.0f}°C — {descricao}"
        else:
            texto = f"{emoji} {CIDADE_CLIMA}: {descricao}"

        self.clima_label.configure(text=texto)

    def _obter_wraplength_lista(self, container, margem=50):
        self.update_idletasks()
        largura = container.winfo_width()
        if largura <= 10:
            largura = self.winfo_screenwidth() // 2
        return max(280, largura - margem)

    def _criar_item_compacto(self, container, cor_borda, linha1, linha2, linha3=None):
        item = tk.Frame(container, bg=COR_CARD, highlightbackground=cor_borda, highlightthickness=1)

        label1 = tk.Label(
            item, text=linha1, bg=COR_CARD, fg=COR_TEXTO,
            font=("Segoe UI", 13, "bold"), anchor="w", justify="left"
        )
        label1.pack(fill="x", padx=10, pady=(4, 0))

        label2 = tk.Label(
            item, text=linha2, bg=COR_CARD, fg=COR_TEXTO_MUTED,
            font=("Segoe UI", 11), anchor="w", justify="left",
            wraplength=self._obter_wraplength_lista(container)
        )
        label2.pack(fill="x", padx=10, pady=(1, 0 if linha3 else 4))

        labels_wrap = [label2]
        if linha3:
            label3 = tk.Label(
                item, text=linha3, bg=COR_CARD, fg=COR_VERDE,
                font=("Segoe UI", 11, "bold"), anchor="w", justify="left",
                wraplength=self._obter_wraplength_lista(container)
            )
            label3.pack(fill="x", padx=10, pady=(0, 4))
            labels_wrap.append(label3)

        item._labels_wrap = labels_wrap
        return item

    def _criar_item_capex(self, evento):
        linha1 = (
            f"{evento.get('timestamp', '')}   📍 {evento.get('unidade', 'N/D')}   "
            f"Contrato: {evento.get('contrato', 'N/D')}"
        )
        linha2 = (
            f"Cliente: {evento.get('cliente', 'N/D')}  |  "
            f"Bairro: {evento.get('bairro', 'N/D')}  |  "
            f"📞 {evento.get('telefones', 'N/D')}"
        )
        return self._criar_item_compacto(self.lista_capex_container, COR_DESTAQUE, linha1=linha1, linha2=linha2)

    def _adicionar_item_capex(self, evento):
        if self.placeholder_capex.winfo_ismapped():
            self.placeholder_capex.grid_forget()

        novo_item = self._criar_item_capex(evento)
        self.itens_capex.insert(0, novo_item)
        self.dados_capex_salvos.insert(0, evento)

        for index, it in enumerate(self.itens_capex):
            it.grid(row=index, column=0, sticky="ew", pady=3)

        while len(self.itens_capex) > MAX_ITENS_CAPEX:
            antigo = self.itens_capex.pop()
            antigo.destroy()

        while len(self.dados_capex_salvos) > MAX_ITENS_CAPEX:
            self.dados_capex_salvos.pop()

        self._salvar_historico_painel()

    def _criar_item_garantia(self, evento):
        linha1 = (
            f"{evento.get('timestamp', '')}   📍 {evento.get('unidade', 'N/D')}   "
            f"Contrato: {evento.get('contrato', 'N/D')}"
        )

        detalhe_extra = ""
        if evento.get('tipo_anterior'):
            detalhe_extra = f"  |  Serv. anterior: {evento['tipo_anterior']} ({evento.get('dias_aging', '?')}d)"

        linha2 = (
            f"Cliente: {evento.get('cliente', 'N/D')}  |  "
            f"Bairro: {evento.get('bairro', 'N/D')}  |  "
            f"📞 {evento.get('telefones', 'N/D')}{detalhe_extra}"
        )

        linha3 = None
        if evento.get('tecnico_ofs'):
            linha3 = f"Técnico OFS: {evento['tecnico_ofs']}"

        return self._criar_item_compacto(self.lista_garantia_container, COR_ALERTA, linha1, linha2, linha3)

    def _adicionar_item_garantia(self, evento):
        if self.placeholder_garantia.winfo_ismapped():
            self.placeholder_garantia.grid_forget()

        novo_item = self._criar_item_garantia(evento)
        self.itens_garantia.insert(0, novo_item)
        self.dados_garantias_salvos.insert(0, evento)

        for index, it in enumerate(self.itens_garantia):
            it.grid(row=index, column=0, sticky="ew", pady=3)

        while len(self.itens_garantia) > MAX_ITENS_GARANTIA:
            antigo = self.itens_garantia.pop()
            antigo.destroy()

        while len(self.dados_garantias_salvos) > MAX_ITENS_GARANTIA:
            self.dados_garantias_salvos.pop()

        self._salvar_historico_painel()

    def _reajustar_wraplength_itens(self):
        novo_wrap_capex = self._obter_wraplength_lista(self.lista_capex_container)
        novo_wrap_garantia = self._obter_wraplength_lista(self.lista_garantia_container)

        for it in self.itens_capex:
            for label in getattr(it, '_labels_wrap', []):
                try:
                    label.configure(wraplength=novo_wrap_capex)
                except Exception:
                    pass

        for it in self.itens_garantia:
            for label in getattr(it, '_labels_wrap', []):
                try:
                    label.configure(wraplength=novo_wrap_garantia)
                except Exception:
                    pass

    def _exibir_alerta_garantia(self, evento):
        for widget in self.overlay_corpo.winfo_children():
            widget.destroy()

        linhas = [
            f"Unidade: {evento.get('unidade', 'N/D')}",
            f"Contrato: {evento.get('contrato', 'N/D')}",
            f"Cliente: {evento.get('cliente', 'N/D')}",
            f"Bairro: {evento.get('bairro', 'N/D')}",
            f"Telefone(s): {evento.get('telefones', 'N/D')}",
        ]
        if evento.get('tecnico_ofs'):
            linhas.append(f"Técnico OFS: {evento['tecnico_ofs']}")
        if evento.get('tipo_anterior'):
            linhas.append(
                f"Serviço anterior: {evento['tipo_anterior']}  (concluído há {evento.get('dias_aging', '?')} dias)"
            )

        for txt in linhas:
            tk.Label(
                self.overlay_corpo, text=txt, bg=COR_ALERTA, fg="#FFFFFF",
                font=("Segoe UI", 34, "bold"), anchor="w", justify="left",
                wraplength=max(400, self.winfo_screenwidth() - 280)
            ).pack(fill="x", pady=8)

        if not self._alerta_ativo:
            self._alerta_ativo = True
            self.overlay.place(x=0, y=0, relwidth=1, relheight=1)
            self.overlay.lift()
            self._piscar_alerta()

        if self._job_alerta:
            self.after_cancel(self._job_alerta)
        self._job_alerta = self.after(DURACAO_ALERTA_MS, self._ocultar_alerta)

    def _piscar_alerta(self):
        if not self._alerta_ativo:
            return
        self._piscar_estado = not self._piscar_estado
        cor = COR_ALERTA if self._piscar_estado else COR_ALERTA_ESCURO

        self.overlay.configure(bg=cor)
        self.overlay_titulo.configure(bg=cor)
        self.overlay_corpo.configure(bg=cor)
        for widget in self.overlay_corpo.winfo_children():
            try:
                widget.configure(bg=cor)
            except Exception:
                pass

        self.after(INTERVALO_PISKAR_MS, self._piscar_alerta)

    def _ocultar_alerta(self):
        self._alerta_ativo = False
        self.overlay.place_forget()
        self._job_alerta = None


def executar_monitoramento(exibir=None):
    os_notificadas = carregar_os_notificadas()
    reparos_avaliados = carregar_reparos_avaliados()
    qtd_reparos_notificados = sum(1 for info in reparos_avaliados.values() if info.get('notificado'))
    qtd_reparos_pendentes = len(reparos_avaliados) - qtd_reparos_notificados
    logger.info(f"{len(os_notificadas)} OS já notificadas anteriormente.")
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

            reconectar_forticlient()

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
                    estado_reavaliacao_base_ofs['mtime'] = mtime_atual
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
                        logger.info(
                            "Base OFS: leitura inicial registrada para controle de "
                            "reavaliação (nada a reavaliar até o arquivo mudar)."
                        )
                        return

                    logger.info(
                        "Base OFS: novo arquivo detectado no disco (mtime mudou) — "
                        "recarregando e reavaliando reparos pendentes..."
                    )
                    reavaliar_reparos_pendentes(reparos_avaliados)
                except Exception:
                    logger.exception("Falha ao checar/reavaliar a Base OFS atualizada.")

            def processar_lista_chamados(lista_chamados, capex_confiavel=True):
                """capex_confiavel=False quando a lista chegou truncada. Nesse
                caso as O.S. novas ainda são notificadas, mas a contagem de
                CAPEX pendente NÃO é regravada -- senão o site mostra 0 (ou um
                número menor) até a próxima varredura boa.

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

                    elif codigo == 'ES05':
                        os_id = chamado.get('id')
                        chave_reparo = str(os_id) if os_id else None
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
                """Devolve (tentou, deslogado, chamados, capex_confiavel).

                capex_confiavel só é True quando a busca 1 (CAPEX+ES05 por
                unidade) percorreu todas as páginas. É ela que alimenta a
                contagem de CAPEX pendente do site; se vier truncada, a
                contagem sai menor que a real e não pode sobrescrever a boa.
                """
                url_base = estado_sessao['url_chamados_base']
                token = estado_sessao['token_atual']

                if not url_base or not token:
                    return False, False, [], False

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
                    return True, False, [], False

                if deslogado1:
                    logger.warning("Busca 1 retornou 401/403 — sessão deslogada.")
                    return True, True, [], False

                payload2 = {
                    "enderecoUnidade": [],
                    "dataConclusao": "IS NULL",
                    "fila_codigo": ["ES05"],
                    "contrato": None
                }
                try:
                    chamados2, deslogado2, _completa2 = buscar_todas_paginas(url_base, payload2, headers)
                except ContextoNavegadorMorto:
                    raise   # o laço externo precisa reabrir o navegador
                except Exception as e:
                    logger.warning(f"Falha na busca ES05 global: {e}")
                    chamados2, deslogado2 = [], False

                if deslogado2:
                    logger.warning("Busca 2 retornou 401/403 — sessão deslogada.")
                    return True, True, [], False

                ids_vistos = set()
                todos = []
                for c in chamados1 + chamados2:
                    if not isinstance(c, dict):
                        continue
                    cid = c.get('id')
                    if cid and cid not in ids_vistos:
                        ids_vistos.add(cid)
                        todos.append(c)

                return True, False, todos, completa1

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
                        enviar_alerta_telegram("🔴 ALERTA: Queda de VPN detectada! Iniciando protocolo de reconexão do FortiClient...")
                        logger.warning("VPN caiu! Reconectando FortiClient...")

                        reconectar_forticlient()

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
                        tentou, deslogado_api, lista_chamados, capex_confiavel = buscar_chamados_via_api()
                    else:
                        tentou, deslogado_api, lista_chamados, capex_confiavel = False, False, [], False
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
                        processar_lista_chamados(lista_chamados, capex_confiavel=capex_confiavel)
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

        iniciar_vpn_sempre_ativa()

        iniciar_servico_alerta_whatsapp()

        if WHATSAPP_ALERTA_ATIVO:
            thread_status_whatsapp = threading.Thread(target=escutar_comandos_whatsapp, daemon=True)
            thread_status_whatsapp.start()

            threading.Thread(target=thread_reenvio_alertas_whatsapp, daemon=True).start()

            # Vigia da ponte HTTP do WhatsApp: relança o Node se ele subir
            # travado (o buraco de 09/08/2026). Fora do laço de monitoramento
            # de propósito, para agir mesmo se o laço travar.
            threading.Thread(target=thread_vigia_servico_whatsapp, daemon=True).start()

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