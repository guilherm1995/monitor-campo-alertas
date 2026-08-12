# ================= BACKLOG CAPEX: conveniências (fonte: planilha Google Sheets) =================
# Alinhado 1:1 com a implementação já validada em produção no operacional.py
# (mesmo SHEET_ID, mesma aba "CONVENIENCIA", mesma lib de credencial —
# oauth2client — e os mesmos parsers de contrato/data). Só adiciona cache
# com TTL e um fallback local que de fato funciona (no operacional.py o fallback
# pro .xlsx nunca chega a disparar, porque carregar_google_sheet já engole
# a exceção internamente e devolve DataFrame vazio -- aqui o fallback é
# acionado por "df vazio", não só por exceção, pra realmente funcionar).
import os
import logging
import threading
from datetime import datetime

import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

# ---- Mesmos valores usados no operacional.py ----
PLANILHA_CONVENIENCIA_ID = os.environ.get(
    'PLANILHA_CONVENIENCIA_ID', 'SEU_ID_DA_PLANILHA_GOOGLE'
)
PLANILHA_CONVENIENCIA_ABA = os.environ.get('PLANILHA_CONVENIENCIA_ABA', 'CONVENIENCIA')
GOOGLE_CREDENCIAIS_JSON = os.environ.get(
    'GOOGLE_CREDENCIAIS_JSON', os.path.join(os.getcwd(), 'dados', 'google_credentials.json')
)
PLANILHA_CONVENIENCIA_BACKUP_LOCAL = os.environ.get(
    'PLANILHA_CONVENIENCIA_BACKUP_LOCAL',
    os.path.join(os.getcwd(), 'dados', 'PLANILHA DE CONVENIENCIA.xlsx'),
)
CONVENIENCIA_CACHE_TTL_SEG = float(os.environ.get('CONVENIENCIA_CACHE_TTL_SEG', '300'))

SCOPE_GOOGLE_SHEETS = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
NOMES_COLUNA_CONTRATO = ['CONTRATO', 'Contrato', 'CÓDIGO CONTRATO']
NOMES_COLUNA_DATA = ['DATA', 'Data', 'Data de Vencimento', 'Vencimento']

_lock_cache = threading.Lock()
_cache = {"conveniencias": None, "ts": 0.0}


def _cache_valido():
    if _cache["conveniencias"] is None:
        return False
    return (datetime.now().timestamp() - _cache["ts"]) < CONVENIENCIA_CACHE_TTL_SEG


def carregar_google_sheet(sheet_id, aba="Sheet1"):
    """Idêntica à do operacional.py: usa oauth2client + gspread."""
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENCIAIS_JSON, SCOPE_GOOGLE_SHEETS)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id)
        worksheet = sheet.worksheet(aba)
        dados = worksheet.get_all_records()
        df = pd.DataFrame(dados)
        df.columns = df.columns.str.strip()
        return df
    except Exception:
        logger.exception(f"Falha ao carregar Google Sheet {sheet_id}/{aba}")
        return pd.DataFrame()


def carregar_excel_local(caminho):
    try:
        df = pd.read_excel(caminho)
        df.columns = df.columns.str.strip()
        return df
    except Exception:
        logger.exception(f"Erro ao carregar arquivo local {caminho}")
        return pd.DataFrame()


def encontrar_coluna(df, possibilidades):
    """Idêntica à do operacional.py: match exato (case-insensitive) pelo nome."""
    for p in possibilidades:
        for col in df.columns:
            if col.strip().lower() == p.lower():
                return col
    return None


def tratar_contrato(coluna):
    """Idêntica à do operacional.py: remove o '.0' que o Excel/Sheets injeta
    quando o número do contrato vira float."""
    return coluna.astype(str).str.split('.').str[0].str.strip()


def parse_data_flexivel(data_str):
    """Idêntica à do operacional.py: aceita dd/mm/aaaa, dd/mm/aa, dd-mm-aaaa,
    dd.mm.aaaa, e também 'dd/mm' ou 'dd-mm' sem ano (assume o ano que faz
    a data cair no futuro). Retorna NaT se não conseguir interpretar."""
    if pd.isna(data_str) or str(data_str).strip() == '':
        return pd.NaT

    texto = str(data_str).strip()

    for fmt in ['%d/%m/%Y', '%d/%m/%y', '%d-%m-%Y', '%d-%m-%y', '%d.%m.%Y', '%d.%m.%y']:
        try:
            return pd.to_datetime(texto, format=fmt, dayfirst=True, errors='raise').normalize()
        except (ValueError, TypeError):
            continue

    for separador in ('/', '-'):
        if separador in texto and texto.count(separador) == 1:
            partes = texto.split(separador)
            if len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
                dia, mes = int(partes[0]), int(partes[1])
                if not (1 <= mes <= 12 and 1 <= dia <= 31):
                    return pd.NaT
                hoje = pd.Timestamp.now().normalize()
                try:
                    tentativa = pd.Timestamp(year=hoje.year, month=mes, day=dia)
                    if tentativa > hoje:
                        return tentativa
                    tentativa = pd.Timestamp(year=hoje.year + 1, month=mes, day=dia)
                    return tentativa if tentativa > hoje else pd.NaT
                except (ValueError, TypeError):
                    return pd.NaT

    return pd.NaT


def _extrair_validos(df):
    """Mesma regra de negócio do operacional.py: só é conveniência se está na
    planilha E a data associada é FUTURA (maior que hoje)."""
    col_contrato = encontrar_coluna(df, NOMES_COLUNA_CONTRATO)
    col_data = encontrar_coluna(df, NOMES_COLUNA_DATA)

    if not col_contrato or not col_data:
        logger.warning("Colunas não encontradas por nome, usando índices fixos (A e C).")
        col_contrato = df.columns[0] if len(df.columns) > 0 else None
        col_data = df.columns[2] if len(df.columns) > 2 else None

    if not col_contrato or not col_data:
        logger.error("Não foi possível identificar colunas de contrato e data na planilha de conveniência.")
        return []

    contratos = tratar_contrato(df[col_contrato])
    datas = df[col_data].apply(parse_data_flexivel)

    hoje = pd.Timestamp.now().normalize()
    valid_mask = datas > hoje
    invalidas = datas.isna() | (datas <= hoje)
    logger.info(f"Conveniências: {valid_mask.sum()} válidas, {invalidas.sum()} ignoradas (datas inválidas/passadas).")

    validos = contratos[valid_mask]
    return validos.tolist()


def carregar_conveniencias(forcar_atualizacao=False):
    """Devolve a lista de contratos considerados 'conveniência' hoje.

    Tenta primeiro o Google Sheets (fonte da verdade, mantida manualmente
    na aba "CONVENIENCIA"); se vier vazio (rede, credencial, planilha
    renomeada, etc.), cai pro backup .xlsx local. Resultado fica em cache
    por CONVENIENCIA_CACHE_TTL_SEG, pra não bater no Sheets toda hora."""
    with _lock_cache:
        if not forcar_atualizacao and _cache_valido():
            return _cache["conveniencias"]

        logger.info("Carregando planilha de conveniência do Google...")
        df = carregar_google_sheet(PLANILHA_CONVENIENCIA_ID, PLANILHA_CONVENIENCIA_ABA)

        if df.empty:
            logger.warning("Google Sheets de conveniência vazio/indisponível, usando backup local.")
            df = carregar_excel_local(PLANILHA_CONVENIENCIA_BACKUP_LOCAL)
        else:
            logger.info("Planilha de conveniência carregada online.")

        if df.empty:
            logger.warning("Backup local de conveniência também vazio/ausente.")
            if _cache["conveniencias"] is not None:
                logger.warning("Devolvendo cache de conveniências vencido, por falta de fonte disponível.")
                return _cache["conveniencias"]
            return []

        try:
            validos = _extrair_validos(df)
        except Exception:
            logger.exception("Erro ao processar dados da conveniência.")
            validos = _cache["conveniencias"] or []

        _cache["conveniencias"] = validos
        _cache["ts"] = datetime.now().timestamp()
        return validos
