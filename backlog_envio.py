# ================= BACKLOG CAPEX: envio (Telegram + WhatsApp) e agendador =================
import os
import time
import base64
import logging
import threading
import io
from datetime import datetime

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings()

from backlog_capex import calcular_backlog_capex, CATEGORIAS as CATEGORIAS_CAPEX, REGIOES
from backlog_reparo import calcular_backlog_reparo, CATEGORIAS as CATEGORIAS_REPARO, coletar_contratos_reparo_abertos
from backlog_ofs import carregar_contratos_ofs_do_dia
from backlog_render import gerar_imagens_backlog_generico, gerar_imagens_backlog
from backlog_conveniencia import carregar_conveniencias

logger = logging.getLogger(__name__)

# ---- Reaproveita as mesmas configs já usadas pro resto do bot (Telegram/WhatsApp) ----
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def configurar_telegram(token, chat_id):
    global TELEGRAM_TOKEN, CHAT_ID
    TELEGRAM_TOKEN = token
    CHAT_ID = chat_id

WHATSAPP_ALERTA_URL = os.environ.get('WHATSAPP_ALERTA_URL', 'http://127.0.0.1:3939/alerta')
WHATSAPP_ALERTA_IMAGEM_URL = os.environ.get(
    'WHATSAPP_ALERTA_IMAGEM_URL',
    WHATSAPP_ALERTA_URL.replace('/alerta', '/alerta-imagem'),
)
WHATSAPP_ALERTA_ATIVO = os.environ.get('WHATSAPP_ALERTA_ATIVO', '1') != '0'

BACKLOG_INTERVALO_SEG = float(os.environ.get('BACKLOG_INTERVALO_SEG', str(2.5 * 60 * 60)))  # 2h30
BACKLOG_PASTA_SAIDA = os.environ.get('BACKLOG_PASTA_SAIDA', os.path.join(os.getcwd(), 'relatorios'))
os.makedirs(BACKLOG_PASTA_SAIDA, exist_ok=True)

LEGENDAS_CATEGORIA = {
    "Ativação": "📊 Backlog CAPEX — Ativação",
    "Mudança de endereço": "📊 Backlog CAPEX — Mudança de Endereço",
    "Reparo": "📊 Backlog REPARO",
    "Upgrade": "📊 Backlog UPGRADE",
    "Mudança de cômodo": "📊 Backlog MUDANÇA DE CÔMODO",
}

_lock_envio = threading.Lock()

# ---- Funções de consulta ao Autenticador (cópia do bot_campo_monitoramento) ----
AUTENTICADOR_URL_SAVE = "https://provedor.example/status.php?action=save"
AUTENTICADOR_URL_PROCESSA = "https://provedor.example/processa.php?bg=1"
AUTENTICADOR_URL_LER_CSV = "https://provedor.example/ler_csv.php"

def consultar_autenticador_status(lista_contratos):
    if not lista_contratos:
        return pd.DataFrame(), "Nenhum contrato informado."

    try:
        sessao = requests.Session()
        payload = {"contratos": "\n".join(lista_contratos)}

        sessao.post(AUTENTICADOR_URL_SAVE, data=payload, verify=False, timeout=(5, 35))
        sessao.get(AUTENTICADOR_URL_PROCESSA, verify=False, timeout=(5, 35))
        res = sessao.get(AUTENTICADOR_URL_LER_CSV, verify=False, timeout=(5, 35))
        html_resp = res.text

        if '<table>' not in html_resp:
            return pd.DataFrame(), "Resposta do servidor não contém tabela (verifique a VPN)."

        tabelas = pd.read_html(io.StringIO(html_resp))
        if not tabelas:
            return pd.DataFrame(), "Nenhuma tabela encontrada."

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

        status_contratos = {}
        for contrato in lista_contratos:
            c_str = str(contrato).strip()
            df_c = df[df['CONTRATO'] == c_str]
            if df_c.empty:
                status_contratos[c_str] = 'NÃO LOCALIZADO'
            else:
                tem_ativo = any(pd.isna(val) or str(val).strip() == '' for val in df_c['FIM'])
                status_contratos[c_str] = 'ONLINE' if tem_ativo else 'OFFLINE'

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

# ---- Funções de envio ----
def enviar_foto_telegram(caminho_imagem, legenda=None):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram não configurado.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(caminho_imagem, "rb") as arquivo_imagem:
            resposta = requests.post(
                url,
                data={"chat_id": CHAT_ID, "caption": legenda or ""},
                files={"photo": arquivo_imagem},
                timeout=30,
            )
        if resposta.status_code != 200:
            logger.error(f"Telegram: {resposta.status_code} - {resposta.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"Falha ao enviar imagem: {e}")
        return False

def enviar_imagem_whatsapp_grupo(caminho_imagem, legenda=None):
    if not WHATSAPP_ALERTA_ATIVO:
        return False
    try:
        with open(caminho_imagem, "rb") as arquivo_imagem:
            imagem_base64 = base64.b64encode(arquivo_imagem.read()).decode("ascii")
        resposta = requests.post(
            WHATSAPP_ALERTA_IMAGEM_URL,
            json={"imagemBase64": imagem_base64, "legenda": legenda or ""},
            timeout=30,
        )
        if resposta.status_code != 200:
            logger.warning(f"WhatsApp imagem: {resposta.status_code}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Falha ao enviar imagem WhatsApp: {e}")
        return False

# ---- Função principal de geração e envio ----
def gerar_e_enviar_backlog_tipo(lista_chamados, tipo, conveniencias=None):
    with _lock_envio:
        if not lista_chamados:
            logger.warning(f"gerar_e_enviar_backlog_tipo({tipo}): lista vazia.")
            return False

        try:
            conveniencias = conveniencias or carregar_conveniencias()
        except Exception as e:
            logger.warning(f"Falha ao carregar conveniências: {e}")
            conveniencias = []

        # Contratos que o OFS GERAL tem agendados para hoje. Se o arquivo não
        # estiver lá ou não puder ser lido, segue com o conjunto vazio: o
        # "Enviado D0" volta a valer só pelo agendamento do CAMPO (como era antes),
        # em vez de derrubar a geração inteira do backlog.
        try:
            contratos_ofs_d0, _info_ofs = carregar_contratos_ofs_do_dia()
        except Exception as e:
            logger.warning(f"Falha ao cruzar com o OFS GERAL: {e}")
            contratos_ofs_d0 = set()

        if tipo == 'capex':
            idade, agendamento = calcular_backlog_capex(
                lista_chamados, conveniencias, contratos_ofs_d0=contratos_ofs_d0
            )
            categorias = CATEGORIAS_CAPEX
            regioes = REGIOES
            prefixo = "backlog_capex"
            titulo = "CAPEX"
        elif tipo == 'reparo':
            contratos = coletar_contratos_reparo_abertos(lista_chamados)
            if not contratos:
                logger.warning("Nenhum chamado de Reparo.")
                return False
            df_status, erro = consultar_autenticador_status(contratos)
            if erro:
                logger.error(f"Autenticador erro: {erro}")
                return False
            status_por_contrato = {}
            for _, row in df_status.iterrows():
                contrato = str(row.get('CONTRATO', '')).strip()
                status = str(row.get('STATUS', '')).strip().upper()
                if contrato:
                    status_por_contrato[contrato] = status
            idade, agendamento = calcular_backlog_reparo(
                lista_chamados,
                status_autenticador_por_contrato=status_por_contrato,
                conveniencias=conveniencias,
                contratos_ofs_d0=contratos_ofs_d0,
            )
            # ============ CORRIGIDO: antes mandava CATEGORIAS_REPARO inteiro
            # (Reparo + Upgrade + Mudança de cômodo juntos) mesmo pedindo só
            # 'reparo'. Agora filtra só a categoria pedida. ============
            categorias = {"Reparo": CATEGORIAS_REPARO["Reparo"]}
            regioes = REGIOES
            prefixo = "backlog_reparo"
            titulo = "REPARO"
        elif tipo == 'upgrade':
            if "Upgrade" not in CATEGORIAS_REPARO:
                logger.warning("Upgrade não habilitado.")
                return False
            contratos = coletar_contratos_reparo_abertos(lista_chamados)
            if not contratos:
                return False
            df_status, erro = consultar_autenticador_status(contratos)
            if erro:
                return False
            status_por_contrato = {}
            for _, row in df_status.iterrows():
                contrato = str(row.get('CONTRATO', '')).strip()
                status = str(row.get('STATUS', '')).strip().upper()
                if contrato:
                    status_por_contrato[contrato] = status
            idade, agendamento = calcular_backlog_reparo(
                lista_chamados,
                status_autenticador_por_contrato=status_por_contrato,
                conveniencias=conveniencias,
                contratos_ofs_d0=contratos_ofs_d0,
            )
            # ============ CORRIGIDO: idem acima, agora só 'Upgrade' ============
            categorias = {"Upgrade": CATEGORIAS_REPARO["Upgrade"]}
            regioes = REGIOES
            prefixo = "backlog_upgrade"
            titulo = "UPGRADE"
        elif tipo == 'mudanca_comodo':
            if "Mudança de cômodo" not in CATEGORIAS_REPARO:
                logger.warning("Mudança de cômodo não habilitado.")
                return False
            contratos = coletar_contratos_reparo_abertos(lista_chamados)
            if not contratos:
                return False
            df_status, erro = consultar_autenticador_status(contratos)
            if erro:
                return False
            status_por_contrato = {}
            for _, row in df_status.iterrows():
                contrato = str(row.get('CONTRATO', '')).strip()
                status = str(row.get('STATUS', '')).strip().upper()
                if contrato:
                    status_por_contrato[contrato] = status
            idade, agendamento = calcular_backlog_reparo(
                lista_chamados,
                status_autenticador_por_contrato=status_por_contrato,
                conveniencias=conveniencias,
                contratos_ofs_d0=contratos_ofs_d0,
            )
            # ============ CORRIGIDO: idem acima, agora só 'Mudança de cômodo' ============
            categorias = {"Mudança de cômodo": CATEGORIAS_REPARO["Mudança de cômodo"]}
            regioes = REGIOES
            prefixo = "backlog_mudanca_comodo"
            titulo = "MUDANÇA DE CÔMODO"
        else:
            logger.error(f"Tipo desconhecido: {tipo}")
            return False

        # Gerar imagens
        try:
            caminhos = gerar_imagens_backlog_generico(
                idade,
                agendamento,
                categorias,
                regioes,
                pasta_saida=BACKLOG_PASTA_SAIDA,
                prefixo=prefixo,
            )
        except Exception as e:
            logger.exception(f"Erro ao gerar imagens {tipo}: {e}")
            return False

        sucesso = True
        for (categoria, regiao), caminho in caminhos.items():
            legenda = f"{LEGENDAS_CATEGORIA.get(categoria, categoria)} — {regiao}"
            ok_tg = enviar_foto_telegram(caminho, legenda)
            ok_wpp = enviar_imagem_whatsapp_grupo(caminho, legenda)
            if not (ok_tg or ok_wpp):
                sucesso = False
                logger.error(f"Falha no envio de {categoria} / {regiao}")
        return sucesso

# ---- Função legada (CAPEX) ----
def gerar_e_enviar_backlog(lista_chamados):
    return gerar_e_enviar_backlog_tipo(lista_chamados, 'capex')

# Ordem de envio quando o agendador dispara "todos juntos".
TIPOS_BACKLOG_AGENDADOS = ['capex', 'reparo', 'upgrade', 'mudanca_comodo']

# ---- Agendador automático (corrigido) ----
def thread_agendador_backlog(obter_lista_chamados_callback, intervalo_seg=None, atraso_inicial=False):
    """
    Roda em background: dispara o backlog de TODOS os tipos juntos (capex,
    reparo, upgrade, mudança de cômodo), um atrás do outro, a partir da
    inicialização do sistema -- e repete a cada `intervalo_seg` (padrão:
    2h30). Não usa mais horários fixos de relógio.

    Se os chamados ainda não tiverem sido carregados quando esta thread
    começar a rodar (bot acabou de subir), espera em ciclos curtos até a
    lista existir, em vez de consumir o intervalo inteiro sem enviar nada.

    atraso_inicial: se True, espera `intervalo_seg` ANTES do primeiro
    disparo, em vez de disparar assim que os chamados forem carregados.
    Serve pra ambientes onde o processo reinicia várias vezes ao dia --
    sem isso, cada reinício gerava um backlog completo duplicado no grupo
    logo em seguida. Default False preserva o comportamento antigo
    (dispara assim que possível) pra quem já chama esta função hoje.
    """
    intervalo_seg = intervalo_seg or BACKLOG_INTERVALO_SEG

    logger.info(
        f"Agendador iniciado: todos os tipos juntos ({', '.join(TIPOS_BACKLOG_AGENDADOS)}), "
        f"a cada {intervalo_seg / 3600:.1f}h"
        + (", aguardando um intervalo antes do primeiro envio." if atraso_inicial
           else ", começando assim que os chamados forem carregados.")
    )

    if atraso_inicial:
        logger.info(f"Agendador: aguardando {intervalo_seg / 60:.0f} min antes do primeiro envio.")
        time.sleep(intervalo_seg)

    while True:
        lista = obter_lista_chamados_callback()
        if not lista:
            logger.info("Agendador: aguardando primeira carga de chamados...")
            time.sleep(30)
            continue

        logger.info("Agendador: disparando backlog completo (todos os tipos).")
        for tipo in TIPOS_BACKLOG_AGENDADOS:
            try:
                gerar_e_enviar_backlog_tipo(lista, tipo)
            except Exception:
                logger.exception(f"Agendador: erro ao gerar/enviar backlog '{tipo}'.")

        time.sleep(intervalo_seg)