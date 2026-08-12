# ================= OFS GERAL: contratos agendados para hoje =================
# Por que este módulo existe:
#
# No CAMPO, nem tudo que está agendado para uma data aparece como D0 no chamado.
# Quando a O.S. dá erro de integração, ela é puxada manualmente pelo OFS e o
# 'agendamentoData' do CAMPO não acompanha -- o backlog então conta menos
# "Enviado D0" do que realmente foi enviado para o dia.
#
# Cruzar o número do contrato do backlog com os contratos que o OFS GERAL tem
# agendados para HOJE recupera essa visão real. É o mesmo tipo de cruzamento
# que já é feito com a planilha de Conveniência, e depende do 'OFS GERAL.csv'
# estar atualizado -- por isso o site avisa a idade do arquivo na tela de
# backlog.
import os
import sys
import json
import logging
import warnings
from datetime import datetime

logger = logging.getLogger(__name__)

NOME_ARQUIVO_OFS = "OFS GERAL.csv"

# Idade máxima do OFS GERAL para o cruzamento valer. Acima disso o arquivo não
# descreve mais a agenda de hoje com confiança, e o cruzamento é DESCARTADO por
# inteiro: o "Enviado D0" volta a valer só pelo agendamento do CAMPO -- a regra
# antiga, que já funcionava. É de propósito tudo-ou-nada: um OFS de anteontem
# traria só parte dos contratos do dia, e um número meio certo é pior que o
# número antigo, porque ninguém desconfia dele.
#
# 24h e não "tem que ser de hoje": é comum exportar o OFS no fim da tarde já
# com a agenda do dia seguinte, e esse arquivo é válido na manhã seguinte.
MAX_IDADE_OFS_HORAS = float(os.environ.get("OFS_MAX_IDADE_HORAS", "24"))

# Regra de negócio (definida pela operação):
#
#   O CAMPO é a fonte de verdade. Quando o contrato do OFS bate com o do CAMPO,
#   a SIGLA e o TIPO DE SERVIÇO que valem são os do CAMPO -- o chamado já sabe
#   a que categoria e unidade pertence. O OFS responde uma pergunta só:
#   "esse contrato está na agenda de hoje e não foi cancelado?"
#
# Por isso aqui não se compara tipo de atividade, cidade nem workzone. Além de
# ser o que a regra manda, comparar localidade quebraria o cruzamento: a
# workzone é o território de quem EXECUTA e o enderecoUnidade é onde o cliente
# ESTÁ, e eles divergem de propósito (PDS/PBS para a mesma Paraíba do Sul,
# SSTBO sob SST, BERTN sob CGT, BMA sob RSD) -- 7 de 47 casos reais medidos.

# A data do agendamento é a COLUNA B do OFS. Fixada por posição porque é assim
# que a operação especifica; o nome serve de conferência e de plano B caso
# alguma exportação venha com as colunas em outra ordem.
INDICE_COLUNA_DATA_OFS = 1


def _raiz_do_bot():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _normalizar_contrato(valor):
    """Mesma normalização de backlog_capex._normalizar_contrato: o CSV traz o
    contrato como float ('6471681.0') e a API do CAMPO traz int -- sem tirar o
    '.0' a comparação falha calada, que foi exatamente o bug da Conveniência
    zerada."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto.endswith(".0"):
        texto = texto[:-2]
    return texto or None


def _ler_json(caminho):
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo) or {}
    except (OSError, ValueError):
        return {}


def _pastas_candidatas():
    """Onde procurar o OFS GERAL, na MESMA ordem que o site usa.

    O arquivo é mantido pelo site (tela de Fontes de dados), então a ordem de
    busca espelha web/config.py::pastas_de_dados() -- inclusive lendo o
    config/site.json do site para pegar 'pasta_database' e
    'pastas_dados_extra'. Assim quem configurar o caminho num lugar não
    precisa repetir no outro, e o bot nunca lê um OFS diferente do que o site
    está mostrando.

    O caminho do site sai de dados/painel_config.json, gravado pelo instalador
    do site dentro da pasta do bot.
    """
    raiz = _raiz_do_bot()
    pastas = []

    pasta_site = _ler_json(os.path.join(raiz, "dados", "painel_config.json")).get("pasta")
    if pasta_site:
        # 1º: o que foi enviado pelo site manda (mesma regra de lá)
        pastas.append(os.path.join(pasta_site, "dados"))

        site_json = _ler_json(os.path.join(pasta_site, "config", "site.json"))
        pasta_database = site_json.get("pasta_database")
        if pasta_database:
            pastas.append(pasta_database)
            pastas.append(os.path.join(pasta_database, "dados"))
        for extra in site_json.get("pastas_dados_extra") or []:
            if extra:
                pastas.append(str(extra))

    pastas.append(os.path.join(raiz, "dados"))
    pastas.append(raiz)

    vistas, ordenadas = set(), []
    for pasta in pastas:
        chave = os.path.normcase(os.path.abspath(pasta))
        if chave not in vistas:
            vistas.add(chave)
            ordenadas.append(pasta)
    return ordenadas


def localizar_ofs_geral():
    """Primeiro caminho existente do OFS GERAL.csv, ou None."""
    for pasta in _pastas_candidatas():
        caminho = os.path.join(pasta, NOME_ARQUIVO_OFS)
        if os.path.isfile(caminho):
            return caminho
    return None


def _achar_coluna(df, nomes_possiveis):
    """Casa o nome da coluna sem depender de acento/caixa/espaço extra."""
    def limpar(texto):
        return "".join(c for c in str(texto).lower().strip() if c.isalnum())

    mapa = {limpar(c): c for c in df.columns}
    for nome in nomes_possiveis:
        achado = mapa.get(limpar(nome))
        if achado:
            return achado
    return None


def _coluna_data(df):
    """A coluna B do OFS, conferindo se ela realmente parece a de data.

    A operação especifica a data do agendamento pela POSIÇÃO (coluna B). Se
    alguma exportação vier com as colunas em outra ordem, cai na busca por
    nome em vez de ler silenciosamente a coluna errada.
    """
    nomes = ["Data", "Data Agendamento", "DATA DE AGENDAMENTO", "Data da Atividade"]
    por_nome = _achar_coluna(df, nomes)

    if len(df.columns) > INDICE_COLUNA_DATA_OFS:
        col_b = df.columns[INDICE_COLUNA_DATA_OFS]
        if por_nome is not None and col_b != por_nome:
            logger.warning(
                f"A coluna B do '{NOME_ARQUIVO_OFS}' é {col_b!r}, mas a coluna de data "
                f"pelo nome é {por_nome!r}. Usando {por_nome!r} -- confira se o arquivo "
                "veio com as colunas fora de ordem."
            )
            return por_nome
        return col_b

    return por_nome


def _ler_csv(caminho):
    """Lê o CSV testando separador e codificação, igual o site faz."""
    import pandas as pd

    for sep in (",", ";"):
        for enc in ("utf-8", "utf-8-sig", "latin1"):
            try:
                df = pd.read_csv(caminho, sep=sep, encoding=enc, on_bad_lines="skip")
                if len(df.columns) > 3:
                    return df
            except Exception:
                continue
    return None


def carregar_contratos_ofs_do_dia(hoje=None):
    """Contratos que o OFS GERAL tem agendados para hoje.

    Devolve (contratos, info):
      contratos: set de contratos já normalizados (vazio se o arquivo faltar)
      info: dict com 'disponivel', 'caminho', 'atualizado_em', 'total_dia' e,
            quando algo impede a leitura, 'erro' -- é o que a tela de backlog
            do site usa para lembrar de atualizar o arquivo.
    """
    import pandas as pd

    hoje = hoje or datetime.now().date()
    info = {"disponivel": False, "caminho": None, "atualizado_em": None,
            "idade_horas": None, "desatualizado": False, "total_dia": 0, "erro": None}

    caminho = localizar_ofs_geral()
    if caminho is None:
        info["erro"] = (
            f"'{NOME_ARQUIVO_OFS}' não encontrado. Procurei em: "
            + " · ".join(_pastas_candidatas())
        )
        logger.warning(f"OFS GERAL não encontrado; 'Enviado D0' fica só com o agendamento do CAMPO. {info['erro']}")
        return set(), info

    info["caminho"] = caminho
    try:
        info["atualizado_em"] = datetime.fromtimestamp(os.path.getmtime(caminho))
        info["idade_horas"] = (datetime.now() - info["atualizado_em"]).total_seconds() / 3600
    except OSError:
        pass

    # OFS velho -> cai na regra antiga, sem cruzamento. Melhor um "Enviado D0"
    # reconhecidamente conservador do que um número montado com agenda vencida.
    if info["idade_horas"] is not None and info["idade_horas"] > MAX_IDADE_OFS_HORAS:
        info["desatualizado"] = True
        info["erro"] = (
            f"OFS GERAL desatualizado ({info['idade_horas']:.0f}h, de "
            f"{info['atualizado_em'].strftime('%d/%m/%Y %H:%M')}; limite "
            f"{MAX_IDADE_OFS_HORAS:.0f}h)."
        )
        logger.warning(
            f"{info['erro']} Cruzamento ignorado: 'Enviado D0' será calculado só "
            "pelo agendamento do CAMPO (regra antiga). Atualize o OFS GERAL pelo site."
        )
        return set(), info

    df = _ler_csv(caminho)
    if df is None or df.empty:
        info["erro"] = f"Não consegui ler '{NOME_ARQUIVO_OFS}' (arquivo vazio ou formato inesperado)."
        logger.warning(info["erro"])
        return set(), info

    col_contrato = _achar_coluna(df, ["Número do contrato", "Numero do contrato",
                                      "CÓDIGO CONTRATO", "Codigo contrato", "Contrato"])
    col_data = _coluna_data(df)
    if not col_contrato or not col_data:
        info["erro"] = (
            f"'{NOME_ARQUIVO_OFS}' não tem as colunas de contrato e data "
            "(esperado algo como 'Número do contrato' e 'Data')."
        )
        logger.warning(info["erro"])
        return set(), info

    # Atividade cancelada não é envio: mesmo critério que o site usa na
    # Confirmação de Agenda.
    col_status = _achar_coluna(df, ["Status da Atividade", "STATUS"])
    if col_status:
        df = df[~df[col_status].astype(str).str.contains("cancelad", case=False, na=False)]

    # dayfirst=True sem 'format' fixo de propósito: o OFS já saiu com formatos
    # diferentes entre exportações, e o fallback do dateutil aguenta os dois.
    # O aviso do pandas sobre "could not infer format" é só isso -- um aviso --
    # e sairia no log a cada backlog, então fica abafado aqui.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        datas = pd.to_datetime(df[col_data], errors="coerce", dayfirst=True).dt.date
    do_dia = df[datas == hoje]

    contratos = set()
    for valor in do_dia[col_contrato]:
        if pd.isna(valor):
            continue        # linha sem contrato: almoço, checklist, manutenção
        contrato = _normalizar_contrato(valor)
        if contrato:
            contratos.add(contrato)

    info["disponivel"] = True
    info["total_dia"] = len(contratos)
    logger.info(
        f"OFS GERAL: {len(contratos)} contrato(s) na agenda de hoje "
        f"({hoje.strftime('%d/%m/%Y')}, coluna {col_data!r}, cancelados descartados) "
        "entram no 'Enviado D0'."
    )
    return contratos, info
