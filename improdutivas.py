# ============ IMPRODUTIVAS: base de 30 dias, consultada por entrante ============
#
# O que este módulo faz: dado um entrante (contrato + nome do cliente), dizer
# se aquele cliente JÁ TEVE uma improdutiva TÉCNICA nos últimos 30 dias.
#
# O que ele deixou de fazer: até 13/08/2026 isto aqui era o /improdutivas --
# um relatório de lote, disparado à mão no grupo do WhatsApp com um CSV
# anexado, que classificava tudo e devolvia listas por região. Aquilo não
# tinha memória: analisava, imprimia e esquecia. O comando foi aposentado.
#
# O que sobreviveu, e é a razão de o arquivo continuar existindo, são os dois
# dicionários de classificação -- MOTIVO_PRODUTIVO e MAPEAMENTO_MOTIVOS. Eles
# são a mesma tabela usada na visão "Efetividade Geral" do operacional.py, e é
# deles que sai a única pergunta que interessa agora: este motivo de
# encerramento é improdutivo E de origem TÉCNICA?
import logging
import os
import re
import threading
import unicodedata

import pandas as pd

logger = logging.getLogger(__name__)

# Janela da regra. Vale para as duas chaves (contrato e nome).
DIAS_JANELA = 30

# ---- Mesmo dicionário do operacional.py (visão Efetividade) ----
MOTIVO_PRODUTIVO = {
    'Troca de ONU': True, 'Troca de conector interno': True, 'Cliente ausente': False,
    'Troca de conector externo': True, 'Drop refeito': True, 'Concluída': True,
    'Área de risco': False, 'Entrada não autorizada': False, 'Problema de infraestrutura': False,
    'Não cumprimento de agenda': False, 'Interna cliente': False, 'Normalizado sem intervenção técnica': True,
    'Chuva': False, 'Provisionamento ONU': True, 'Problema CTO': False, 'Falha massiva': False,
    'Solicitação de reagendamento': False, 'Reconexão externa - CTO': True, 'Limpeza de conector interno': True,
    'Desistiu do serviço': False, 'Reposição ONU': True, 'Tubulação obstruída': False,
    'Abertura indevida': False, 'Endereço não localizado': False, 'Reconexão interna': True,
    'Troca de fonte': True, 'Endereço incorreto': False, 'Troca de cabo telefônico': True,
    'Reconfiguração ONU': True, 'Situação de risco': False,
    # Os dois abaixo apareceram na primeira base real (13/08/2026) e não
    # estavam em tabela nenhuma -- foram pescados pelo aviso de "motivo fora
    # das tabelas", que existe exatamente para isto. Uma ocorrência cada.
    # 'Limpeza de conector externo' é irmã da interna, que já era produtiva.
    'Limpeza de conector externo': True,
    'Suspeita de fraude': False,
}

# ---- Mesmo dicionário do operacional.py (TÉCNICA / COMERCIAL / CLIENTE) ----
#
# Duas grafias convivem de propósito, e vale saber de quem é cada uma:
#
#   'Interna cliente'            -> vocabulário do OFS. É o que aparece na
#                                   coluna 'Motivo de Encerramento das
#                                   atividades' da base (conferido em
#                                   13/08/2026: 'Concluída', 'Drop refeito',
#                                   'Normalizado sem intervenção técnica').
#                                   É ESTE que alimenta a regra.
#   'CLIENTE - INTERNA CLIENTE'  -> vocabulário do CAMPO, onde o chamado traz
#                                   'motivoConclusao' e 'submotivoConclusao'
#                                   separados e a composição vira
#                                   'MOTIVO - SUBMOTIVO'.
#
# O do CAMPO fica aqui de rede de proteção: se um dia a exportação sair nesse
# formato, os motivos casam sozinhos em vez de o bot emudecer sem explicação.
#
# As duas tabelas discordam em dois pontos, e a discordância é do operacional.py
# original, não daqui: 'CLIENTE - ENTRADA / AREA NAO LIBERADA' é TÉCNICA
# enquanto 'Entrada não autorizada' é COMERCIAL; 'CLIENTE - DESISTIU DO
# SERVICO' é CLIENTE enquanto 'Desistiu do serviço' é COMERCIAL. Como a base
# vem do OFS, quem vale na prática é a grafia de caixa mista -- e por ela
# nenhum dos dois alerta.
MAPEAMENTO_MOTIVOS = {
    'CLIENTE - AUSENTE': 'CLIENTE', 'REDE - FALHA MASSIVA': 'TÉCNICA',
    'CLIENTE - CAIXA CHEIA': 'TÉCNICA', 'REDE - PROBLEMA DE INFRAESTRUTURA': 'TÉCNICA',
    'CLIENTE - REAGENDOU': 'CLIENTE', 'CLIENTE - ENDERECO NAO LOCALIZADO': 'COMERCIAL',
    'CLIENTE - ENTRADA / AREA NAO LIBERADA': 'TÉCNICA', 'REDE - PREDIO SEM MDU': 'TÉCNICA',
    'REDE - PROBLEMA DE REDE': 'TÉCNICA', 'CLIENTE - ABERTURA INDEVIDA': 'COMERCIAL',
    'CLIENTE - DESISTIU DO SERVICO': 'CLIENTE', 'CLIENTE - TUBULAÇÃO OBSTRUÍDA': 'TÉCNICA',
    'CLIENTE - INTERNA CLIENTE': 'TÉCNICA', 'CAMPO - NÃO CUMPRIMENTO DE AGENDA': 'TÉCNICA',
    'CLIENTE - AREA/SITUAÇÃO DE RISCO': 'TÉCNICA', 'CLIENTE - ABERTURA INDEVIDA REPARO': 'CLIENTE',
    'CAMPO - CHUVA': 'TÉCNICA', 'CLIENTE - SUSPEITA DE FRAUDE': 'CLIENTE',
    'CAMPO - FALTA MATERIAL': 'TÉCNICA', 'CLIENTE - MUDOU DE ENDERECO': 'CLIENTE',
    'CLIENTE - AREA/SITUAÇÃO DE RISCO RETIRADA': 'TÉCNICA', 'CLIENTE - ENDEREÇO INCORRETO': 'COMERCIAL',
    'CLIENTE - SOLICITAÇÃO DE REAGENDAMENTO': 'CLIENTE',
    'Cliente ausente': 'CLIENTE', 'Área de risco': 'TÉCNICA',
    'Entrada não autorizada': 'COMERCIAL', 'Problema de infraestrutura': 'TÉCNICA',
    'Não cumprimento de agenda': 'TÉCNICA', 'Interna cliente': 'TÉCNICA',
    'Chuva': 'TÉCNICA', 'Problema CTO': 'TÉCNICA', 'Falha massiva': 'TÉCNICA',
    'Solicitação de reagendamento': 'CLIENTE', 'Desistiu do serviço': 'COMERCIAL',
    'Tubulação obstruída': 'TÉCNICA', 'Abertura indevida': 'COMERCIAL',
    'Endereço não localizado': 'COMERCIAL', 'Endereço incorreto': 'COMERCIAL',
    'Situação de risco': 'TÉCNICA',
    # Mesma origem que o 'CLIENTE - SUSPEITA DE FRAUDE' da grafia do CAMPO.
    'Suspeita de fraude': 'CLIENTE',
}

# Motivos que NÃO geram alerta, decididos com a operação em 13/08/2026.
#
# É lista de exclusão, não de inclusão: alerta toda improdutiva, menos estas.
# A régua não é a origem (TÉCNICA/COMERCIAL/CLIENTE) e sim "esta visita
# perdida diz alguma coisa sobre a próxima?". Remarcação e cliente ausente
# não dizem -- o cliente vai remarcar de novo, e pronto. Chuva e falta de
# material também não: são do dia, não do caso.
#
# As duas grafias entram juntas porque o mesmo motivo pode chegar do OFS
# ('Chuva') ou do CAMPO ('CAMPO - CHUVA'). Excluir só uma delas deixaria a
# regra dependendo de qual sistema exportou o arquivo.
MOTIVOS_SEM_ALERTA = {
    'CLIENTE - SOLICITAÇÃO DE REAGENDAMENTO', 'Solicitação de reagendamento',
    'CLIENTE - REAGENDOU',
    'CLIENTE - AUSENTE', 'Cliente ausente',
    'CAMPO - CHUVA', 'Chuva',
    'CAMPO - FALTA MATERIAL',
    'CAMPO - NÃO CUMPRIMENTO DE AGENDA', 'Não cumprimento de agenda',
}

# As tabelas indexadas em CAIXA ALTA, montadas uma vez. Sem isto, cada
# consulta de motivo varria os dicionários inteiros comparando string a
# string -- e isso acontece uma vez por linha da base, a cada recarga.
_PRODUTIVO_POR_CHAVE = {k.upper(): v for k, v in MOTIVO_PRODUTIVO.items()}
_CATEGORIA_POR_CHAVE = {k.upper(): v for k, v in MAPEAMENTO_MOTIVOS.items()}
_SEM_ALERTA = {m.upper() for m in MOTIVOS_SEM_ALERTA}

# Todo motivo que se sabe improdutivo, nas duas grafias.
#
# A tabela produtivo/improdutivo só conhece a grafia do OFS. A do CAMPO
# ('CLIENTE - AUSENTE', 'REDE - PROBLEMA DE REDE') existe apenas na tabela de
# categorias -- e ali TODO motivo é improdutivo: não há equivalente de
# 'Concluída' naquele conjunto, porque o CAMPO só registra motivo e submotivo
# quando a visita não resolveu. Por isso a grafia do CAMPO entra inteira.
_IMPRODUTIVOS = (
    {k.upper() for k, produtivo in MOTIVO_PRODUTIVO.items() if not produtivo}
    | {k.upper() for k in MAPEAMENTO_MOTIVOS if k.upper() not in _PRODUTIVO_POR_CHAVE}
)


# --------------------------------------------------------------------------
# normalização
# --------------------------------------------------------------------------
def _sem_acento(texto):
    return ''.join(
        c for c in unicodedata.normalize('NFKD', str(texto))
        if not unicodedata.combining(c)
    )


def normalizar_nome(nome):
    """Nome comparável entre o CAMPO e o OFS.

    Os dois sistemas escrevem o mesmo cliente de jeitos diferentes: acento,
    espaço duplo, ponto em abreviação. Sem achatar isso, 'JOSÉ DA SILVA' e
    'JOSE DA  SILVA' seriam duas pessoas e a regra do nome nunca casaria.
    Devolve '' para nome que não serve de chave (vazio, 'N/D', 'nan').
    """
    if nome is None:
        return ''
    texto = _sem_acento(nome).upper()
    texto = re.sub(r'[^A-Z0-9 ]+', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    if texto in ('', 'N D', 'ND', 'NAN', 'NONE', 'NAO INFORMADO'):
        return ''
    return texto


def normalizar_contrato(valor):
    """'6899504.0' (como o Excel devolve) e 6899504 têm de virar a mesma chave."""
    if valor is None:
        return ''
    texto = str(valor).strip()
    if not texto or texto.lower() in ('nan', 'none', 'n/d'):
        return ''
    return texto.split('.')[0].strip()


def categorizar_motivo(motivo):
    """TÉCNICA / COMERCIAL / CLIENTE / OUTROS."""
    if motivo is None or (isinstance(motivo, float) and pd.isna(motivo)):
        return 'OUTROS'
    return _CATEGORIA_POR_CHAVE.get(str(motivo).strip().upper(), 'OUTROS')


def deve_alertar(motivo):
    """Este motivo, visto na janela, merece aviso no grupo?

    Improdutivo e fora da lista de exclusão. Motivo desconhecido não conta:
    sem estar em tabela nenhuma não dá para afirmar que foi improdutivo, e
    chutar aqui viraria alarme falso no grupo -- ele vai para o log, que é
    onde alguém pode decidir de que lado ele fica.
    """
    if motivo is None:
        return False
    m = str(motivo).strip().upper()
    if not m or m in _SEM_ALERTA:
        return False
    return m in _IMPRODUTIVOS


# --------------------------------------------------------------------------
# base de 30 dias
# --------------------------------------------------------------------------
def _encontrar_coluna(df, possibilidades):
    for p in possibilidades:
        for col in df.columns:
            if str(col).strip().lower() == p.strip().lower():
                return col
    return None


_CACHE = {"mtime": None, "indice": None, "diagnostico": {}}
_TRAVA = threading.Lock()


def _indice_vazio():
    return {"por_contrato": {}, "por_nome": {}}


def carregar_base(caminho):
    """Lê a base de improdutivas e monta o índice de consulta.

    Devolve (indice, diagnostico). O índice guarda SÓ as improdutivas
    técnicas — é a única coisa que a regra pergunta, e filtrar na carga deixa
    a consulta O(1) por entrante em vez de varrer a planilha a cada chamado
    (foi a mesma lição do índice da Base OFS, que rendeu 486x).

    Releitura só quando o arquivo muda de mtime: é o mesmo gatilho que faz o
    site atualizar a base e o bot perceber sozinho.
    """
    if not caminho or not os.path.exists(caminho):
        return _indice_vazio(), {"existe": False}

    try:
        mtime = os.path.getmtime(caminho)
    except OSError:
        return _indice_vazio(), {"existe": False}

    with _TRAVA:
        if _CACHE["indice"] is not None and _CACHE["mtime"] == mtime:
            return _CACHE["indice"], _CACHE["diagnostico"]

        try:
            if str(caminho).lower().endswith(('.csv', '.txt')):
                try:
                    df = pd.read_csv(caminho, encoding='utf-8-sig', dtype=str)
                except UnicodeDecodeError:
                    df = pd.read_csv(caminho, encoding='latin-1', dtype=str)
            else:
                df = pd.read_excel(caminho)
        except Exception:
            logger.exception(f"Falha ao abrir a base de improdutivas '{caminho}'.")
            return _indice_vazio(), {"existe": True, "erro": "não consegui abrir"}

        df.columns = df.columns.str.strip()

        col_motivo = _encontrar_coluna(df, ['Motivo de Encerramento das atividades', 'Motivo'])
        col_data = _encontrar_coluna(df, ['Data'])
        col_nome = _encontrar_coluna(df, ['Nome'])
        col_contrato = _encontrar_coluna(df, ['Número do contrato', 'Contrato'])
        col_cidade = _encontrar_coluna(df, ['Cidade', 'Área de Trabalho', 'Chave Workzone'])
        col_os = _encontrar_coluna(df, ['Ordem de Serviço', 'ID da Ordem de Serviço'])
        col_tecnico = _encontrar_coluna(df, ['Recurso', 'Técnico', 'Nome do Técnico'])

        faltando = [rotulo for rotulo, col in [
            ('Motivo de Encerramento das atividades', col_motivo),
            ('Data', col_data),
        ] if col is None]
        if faltando:
            logger.error(
                "Base de improdutivas sem a(s) coluna(s) "
                + ", ".join(faltando)
                + f". Colunas do arquivo: {list(df.columns)}"
            )
            _CACHE.update({"mtime": mtime, "indice": _indice_vazio(),
                           "diagnostico": {"existe": True, "erro": "faltam colunas"}})
            return _CACHE["indice"], _CACHE["diagnostico"]

        if col_contrato is None and col_nome is None:
            logger.error("Base de improdutivas sem 'Número do contrato' e sem 'Nome': "
                         "não há por onde cruzar com o entrante.")
            _CACHE.update({"mtime": mtime, "indice": _indice_vazio(),
                           "diagnostico": {"existe": True, "erro": "sem chave de cruzamento"}})
            return _CACHE["indice"], _CACHE["diagnostico"]

        datas = pd.to_datetime(df[col_data], errors='coerce', dayfirst=True)

        indice = _indice_vazio()
        total_linhas = len(df)
        alertaveis = 0
        improdutivas_total = 0
        excluidas = 0
        # Motivo que não está em nenhuma das duas tabelas. Não vira alerta --
        # sem saber se foi produtivo, chutar daria alarme falso no grupo --
        # mas TEM de aparecer no log: é o único jeito de descobrir que o OFS
        # passou a escrever um motivo novo, em vez de o bot só ficar quieto.
        desconhecidos = {}

        for posicao in range(total_linhas):
            motivo = df[col_motivo].iloc[posicao]
            if motivo is None or (not isinstance(motivo, str) and pd.isna(motivo)):
                continue
            motivo = str(motivo).strip()
            if not motivo:
                continue

            chave_motivo = motivo.upper()
            if chave_motivo not in _IMPRODUTIVOS:
                # Não é improdutivo conhecido: ou é produtivo (a maioria das
                # linhas) ou é motivo que nenhuma tabela conhece. Só o segundo
                # caso interessa ao log.
                if chave_motivo not in _PRODUTIVO_POR_CHAVE:
                    desconhecidos[motivo] = desconhecidos.get(motivo, 0) + 1
                continue

            improdutivas_total += 1
            if chave_motivo in _SEM_ALERTA:
                excluidas += 1
                continue

            data = datas.iloc[posicao]
            if pd.isna(data):
                continue

            registro = {
                'data': data.to_pydatetime(),
                'motivo': motivo,
                'cidade': str(df[col_cidade].iloc[posicao]).strip() if col_cidade else '',
                'os': normalizar_contrato(df[col_os].iloc[posicao]) if col_os else '',
                'tecnico': str(df[col_tecnico].iloc[posicao]).strip() if col_tecnico else '',
            }
            alertaveis += 1

            if col_contrato is not None:
                contrato = normalizar_contrato(df[col_contrato].iloc[posicao])
                if contrato:
                    indice['por_contrato'].setdefault(contrato, []).append(registro)
            if col_nome is not None:
                nome = normalizar_nome(df[col_nome].iloc[posicao])
                if nome:
                    indice['por_nome'].setdefault(nome, []).append(registro)

        diagnostico = {
            'existe': True,
            'linhas': total_linhas,
            'improdutivas': improdutivas_total,
            'alertaveis': alertaveis,
            'excluidas': excluidas,
            'contratos': len(indice['por_contrato']),
            'nomes': len(indice['por_nome']),
            'periodo': (datas.min(), datas.max()),
            'desconhecidos': desconhecidos,
        }

        if desconhecidos:
            resumo = ", ".join(
                f"'{mot}' ({n}x)" for mot, n in
                sorted(desconhecidos.items(), key=lambda par: -par[1])[:12]
            )
            logger.warning(
                f"Base de improdutivas: {len(desconhecidos)} motivo(s) de encerramento "
                f"fora das tabelas de classificação -- {resumo}. "
                "Nenhum deles gera alerta. Se algum for improdutiva que deveria "
                "avisar, acrescente em MAPEAMENTO_MOTIVOS (ou em "
                "MOTIVOS_SEM_ALERTA, se for para calar de vez)."
            )

        if alertaveis == 0:
            # O modo de falha mais provável desta função, e o mais silencioso:
            # a exportação do OFS sai filtrada por atividades concluídas, e aí
            # a base inteira é produtiva. Nada quebra, nenhum erro aparece --
            # o bot só nunca alerta. Por isso este aviso é WARNING e não INFO.
            logger.warning(
                f"Base de improdutivas com {total_linhas} linha(s) e NENHUM motivo "
                f"que gere alerta ({improdutivas_total} improdutiva(s) no total, "
                f"{excluidas} na lista de exclusão). Confira se a exportação do OFS "
                "saiu sem o filtro de status 'concluído' -- do jeito que está, "
                "nenhum entrante será alertado."
            )
        else:
            logger.info(
                f"Base de improdutivas carregada: {total_linhas} linhas, "
                f"{improdutivas_total} improdutiva(s), {alertaveis} que geram alerta "
                f"({excluidas} na lista de exclusão), em {len(indice['por_contrato'])} "
                f"contrato(s) e {len(indice['por_nome'])} nome(s)."
            )

        _CACHE.update({"mtime": mtime, "indice": indice, "diagnostico": diagnostico})
        return indice, diagnostico


def consultar(caminho, contrato, nome, quando, dias=DIAS_JANELA):
    """Este entrante já foi improdutiva (das que avisam) na janela?

    Devolve o registro da improdutiva MAIS RECENTE dentro da janela, ou None.
    Contrato tem prioridade sobre nome: é chave exata, enquanto homônimo
    existe. Quando o casamento vem só pelo nome, o campo 'casou_por' diz isso
    — quem lê a mensagem no grupo precisa saber o quanto confiar nela.
    """
    indice, _ = carregar_base(caminho)
    if not indice['por_contrato'] and not indice['por_nome']:
        return None

    try:
        referencia = quando.date() if hasattr(quando, 'date') else quando
    except Exception:
        return None

    def melhor(registros):
        candidatos = []
        for registro in registros or []:
            atraso = (referencia - registro['data'].date()).days
            # atraso < 0 = improdutiva depois do entrante: é o futuro em
            # relação a ele, não um histórico. Acontece de verdade quando a
            # base é atualizada no meio do dia.
            if 0 <= atraso <= dias:
                candidatos.append((atraso, registro))
        if not candidatos:
            return None
        candidatos.sort(key=lambda par: par[0])
        return candidatos[0]

    contrato_norm = normalizar_contrato(contrato)
    nome_norm = normalizar_nome(nome)

    for chave, registros in (
        ('contrato', indice['por_contrato'].get(contrato_norm) if contrato_norm else None),
        ('nome', indice['por_nome'].get(nome_norm) if nome_norm else None),
    ):
        achado = melhor(registros)
        if achado:
            atraso, registro = achado
            return {
                'casou_por': chave,
                'dias': atraso,
                'data': registro['data'],
                'motivo': registro['motivo'],
                'cidade': registro['cidade'],
                'os': registro['os'],
                'tecnico': registro['tecnico'],
                'quantas': len([r for r in registros
                                if 0 <= (referencia - r['data'].date()).days <= dias]),
            }
    return None
