# ================= BACKLOG REPARO / UPGRADE / MUDANÇA DE CÔMODO: motor de cálculo =================
#
# Módulo novo e separado do backlog_capex.py (Ativação/Mudança de Endereço),
# mas reaproveita de lá a região/unidade e os helpers que não mudam
# (dedução de contrato, bucket de agendamento etc.) -- pra não duplicar a
# lista de siglas e correr o risco de as duas ficarem dessincronizadas de
# novo (foi exatamente esse tipo de bug que corrigimos há pouco).
#
# Agora com UPGRADE (UP02) e MUDANÇA DE CÔMODO (ES15) ativados.
import logging
from datetime import datetime

from backlog_capex import (
    REGIOES,
    _normalizar_contrato,
    _chamado_esta_aberto,
    _codigo_fila,
    _bucket_agendamento,
    _linha_vazia_agendamento,
    _percentual,
)

logger = logging.getLogger(__name__)

# Códigos de fila confirmados:
# - Reparo: ES05
# - Upgrade: UP02 (nome "UPGRADE NÃO LÓGICO")
# - Mudança de cômodo: ES15
CODIGOS_REPARO = ['ES05']
CODIGOS_UPGRADE = ['UP02']
CODIGOS_MUDANCA_COMODO = ['ES15']

CATEGORIAS = {"Reparo": CODIGOS_REPARO}
if CODIGOS_UPGRADE:
    CATEGORIAS["Upgrade"] = CODIGOS_UPGRADE
if CODIGOS_MUDANCA_COMODO:
    CATEGORIAS["Mudança de cômodo"] = CODIGOS_MUDANCA_COMODO

# Limites de bucket POR CATEGORIA, em horas -- diferente do backlog_capex.py
# (que tinha um único LIMITE_BUCKET2_DIAS pras duas categorias): aqui Reparo
# usa 24h/48h, e Upgrade/Mudança de Cômodo usam 4 dias/7 dias, então os dois
# limites (não só o do bucket 1) precisam variar por categoria.
LIMITE_BUCKET1_HORAS = {
    "Reparo": 24,
    "Upgrade": 4 * 24,             # "Até 4 dias"
    "Mudança de cômodo": 4 * 24,   # "Até 4 dias"
}
LIMITE_BUCKET2_HORAS = {
    "Reparo": 48,                  # "Até 48hrs"
    "Upgrade": 7 * 24,             # "Até 7 dias"
    "Mudança de cômodo": 7 * 24,   # "Até 7 dias"
}


def _bucket_idade(chamado, agora, categoria):
    """Bucket de idade do chamado (desde dataAbertura), usando os limites
    específicos da categoria (Reparo=24h/48h, Upgrade/Mudança de
    Cômodo=4 dias/7 dias)."""
    data_abertura_ms = chamado.get("dataAbertura")
    if not data_abertura_ms:
        return None
    try:
        data_abertura = datetime.fromtimestamp(data_abertura_ms / 1000)
    except (TypeError, ValueError, OSError):
        return None

    idade_horas = (agora - data_abertura).total_seconds() / 3600
    if idade_horas <= LIMITE_BUCKET1_HORAS[categoria]:
        return "bucket1"
    if idade_horas <= LIMITE_BUCKET2_HORAS[categoria]:
        return "bucket2"
    return "bucket3"


def _linha_vazia():
    return {
        "bucket1": 0, "bucket2": 0, "bucket3": 0, "total": 0,
        "enviado_d0": 0, "conveniencia": 0, "oportunidade_injecao": 0,
        # Bloco extra que só existe aqui (não no backlog_capex.py): % do
        # backlog que está conectado (ONLINE no Autenticador) vs. em Loss
        # (qualquer coisa que não seja ONLINE -- OFFLINE, não localizado,
        # erro de consulta etc. conta como Loss, pra "conectados + loss"
        # sempre fechar com o "total" da linha).
        "conectados": 0, "loss": 0,
    }


def calcular_backlog_reparo(lista_chamados, status_autenticador_por_contrato=None, conveniencias=None,
                            agora=None, contratos_ofs_d0=None):
    """Monta a estrutura do backlog de Reparo (e Upgrade/Mudança de Cômodo,
    quando habilitados), separados por região/unidade.

    status_autenticador_por_contrato: dict {contrato_normalizado: 'ONLINE'/'OFFLINE'/...}
    -- já resolvido ANTES de chamar esta função (ex: o chamador faz uma
    única consultar_autenticador_status() em lote com todos os contratos dos
    chamados abertos, e monta esse dict). Esta função não faz nenhuma
    chamada de rede -- só calcula, igual ao calcular_backlog_capex.

    conveniencias: mesma coisa do backlog_capex.py (códigos de contrato já
    filtrados por data válida, vindos da planilha do Google Sheets).

    contratos_ofs_d0: contratos agendados para hoje no OFS GERAL, que entram no
    "Enviado D0" mesmo sem o CAMPO marcar D0 -- mesma regra do backlog_capex.py.

    Retorna (idade, agendamento) com o mesmo formato do backlog_capex.py,
    mais "conectados"/"loss"/"pct_conectado"/"pct_loss" dentro de cada
    linha de `idade`.
    """
    agora = agora or datetime.now()
    hoje = agora.date()
    conveniencias = {_normalizar_contrato(c) for c in (conveniencias or [])}
    contratos_ofs_d0 = {_normalizar_contrato(c) for c in (contratos_ofs_d0 or [])}
    status_autenticador_por_contrato = status_autenticador_por_contrato or {}

    idade = {}
    agendamento = {}
    for nome_categoria in CATEGORIAS:
        idade[nome_categoria] = {}
        agendamento[nome_categoria] = {}
        for nome_regiao, unidades in REGIOES.items():
            idade[nome_categoria][nome_regiao] = {u: _linha_vazia() for u in unidades}
            agendamento[nome_categoria][nome_regiao] = {u: _linha_vazia_agendamento() for u in unidades}

    # ============ DIAGNÓSTICO (temporário): ajuda a entender contagens
    # zeradas em Upgrade/Mudança de Cômodo -- mostra quantos chamados
    # abertos batem o código de fila de cada categoria, e quantos deles
    # são descartados por estar numa unidade fora do escopo de REGIOES
    # (hoje só LITORAL NORTE e SUL RJ). Se "fora_regiao" for igual (ou
    # perto) do "abertos_com_codigo", o problema é escopo de região, não
    # o código de fila. ============
    diag_abertos_com_codigo = {c: 0 for c in CATEGORIAS}
    diag_fora_regiao = {c: 0 for c in CATEGORIAS}
    diag_unidades_fora_regiao = {c: set() for c in CATEGORIAS}

    for chamado in lista_chamados or []:
        if not isinstance(chamado, dict):
            continue
        if not _chamado_esta_aberto(chamado):
            continue

        codigo = _codigo_fila(chamado)
        categoria = None
        for nome_categoria, codigos in CATEGORIAS.items():
            if codigo in codigos:
                categoria = nome_categoria
                break
        if categoria is None:
            continue

        diag_abertos_com_codigo[categoria] += 1

        unidade = str(chamado.get("enderecoUnidade", "")).upper().strip()
        regiao = None
        for nome_regiao, unidades in REGIOES.items():
            if unidade in unidades:
                regiao = nome_regiao
                break
        if regiao is None:
            diag_fora_regiao[categoria] += 1
            diag_unidades_fora_regiao[categoria].add(unidade)
            continue  # unidade fora do escopo -> ignorada

        # ---- bloco de idade ----
        linha_idade = idade[categoria][regiao][unidade]
        bucket_idade = _bucket_idade(chamado, agora, categoria)
        if bucket_idade:
            linha_idade[bucket_idade] += 1
        linha_idade["total"] += 1

        # ---- bloco "Próximo agendamento" (D0/D+1/D+2/D+3/>D+3) -- baseado
        # em agendamentoData, SEM MUDANÇA (mesma lógica do backlog_capex.py):
        # continua sendo sobre a próxima visita agendada, não sobre a idade.
        linha_agenda = agendamento[categoria][regiao][unidade]
        bucket_agenda = _bucket_agendamento(chamado, hoje)
        if bucket_agenda and bucket_agenda != "vencida":
            # "vencida" (retorno possível de _bucket_agendamento) é excluído
            # de propósito -- quem decide "vencida" agora é o bloco de
            # idade abaixo, não mais este (evita contar "vencida" duas
            # vezes por dois critérios diferentes).
            linha_agenda[bucket_agenda] += 1

        # ---- bloco "Vencida / No prazo" -- CORRIGIDO (mesma mudança do
        # backlog_capex.py): agora usa a IDADE do chamado (dataAbertura via
        # bucket_idade), não mais o agendamentoData -- que o CAMPO reagenda
        # automaticamente pra uma data futura sempre que a visita é
        # perdida, então nunca ficava no passado de verdade. "Vencida" =
        # chamado já passou do prazo do bucket3 (mesmo limite de "Acima
        # de X" já usado na Idade do Chamado, por categoria); "no prazo"
        # = ainda dentro do prazo (bucket1 ou bucket2).
        if bucket_idade == "bucket3":
            linha_agenda["vencida"] += 1
        elif bucket_idade is not None:
            linha_agenda["no_prazo"] += 1

        # Enviado D0 também pelo OFS GERAL: O.S. com erro de integração é
        # puxada manualmente pelo OFS e o CAMPO não registra o D0. Ver
        # backlog_ofs.py e o mesmo trecho em backlog_capex.py.
        contrato_chamado = _normalizar_contrato(chamado.get("codigoContrato"))
        if bucket_agenda == "d0" or contrato_chamado in contratos_ofs_d0:
            linha_idade["enviado_d0"] += 1
        elif contrato_chamado in conveniencias:
            linha_idade["conveniencia"] += 1
        else:
            linha_idade["oportunidade_injecao"] += 1

        # ---- bloco de Autenticador: Conectado (ONLINE) x Loss (tudo o mais) ----
        contrato = _normalizar_contrato(chamado.get("codigoContrato"))
        status = status_autenticador_por_contrato.get(contrato)
        if status == "ONLINE":
            linha_idade["conectados"] += 1
        else:
            linha_idade["loss"] += 1

    for nome_categoria in CATEGORIAS:
        logger.info(
            f"Backlog Reparo/Upgrade/MudançaComodo [{nome_categoria}]: "
            f"{diag_abertos_com_codigo[nome_categoria]} chamado(s) aberto(s) com o código da fila; "
            f"{diag_fora_regiao[nome_categoria]} descartado(s) por unidade fora de REGIOES "
            f"(unidades vistas: {sorted(diag_unidades_fora_regiao[nome_categoria]) or '-'})."
        )

    # ---- totais + percentuais por região ----
    for nome_categoria, regioes in idade.items():
        for nome_regiao, unidades in regioes.items():
            totalizador = _linha_vazia()
            for linha in unidades.values():
                for chave in totalizador:
                    totalizador[chave] += linha[chave]
                linha["pct_bucket1"] = _percentual(linha["bucket1"], linha["total"])
                linha["pct_bucket2"] = _percentual(linha["bucket2"], linha["total"])
                linha["pct_bucket3"] = _percentual(linha["bucket3"], linha["total"])
                linha["pct_conectado"] = _percentual(linha["conectados"], linha["total"])
                linha["pct_loss"] = _percentual(linha["loss"], linha["total"])
            totalizador["pct_bucket1"] = _percentual(totalizador["bucket1"], totalizador["total"])
            totalizador["pct_bucket2"] = _percentual(totalizador["bucket2"], totalizador["total"])
            totalizador["pct_bucket3"] = _percentual(totalizador["bucket3"], totalizador["total"])
            totalizador["pct_conectado"] = _percentual(totalizador["conectados"], totalizador["total"])
            totalizador["pct_loss"] = _percentual(totalizador["loss"], totalizador["total"])
            unidades["TOTAL"] = totalizador

    for nome_categoria, regioes in agendamento.items():
        for nome_regiao, unidades in regioes.items():
            totalizador = _linha_vazia_agendamento()
            for linha in unidades.values():
                for chave in totalizador:
                    totalizador[chave] += linha[chave]
            unidades["TOTAL"] = totalizador

    return idade, agendamento


def coletar_contratos_reparo_abertos(lista_chamados):
    """Helper pro chamador (ex: bot_campo_monitoramento.py) montar a lista de
    contratos que precisa passar pro consultar_autenticador_status() ANTES de
    chamar calcular_backlog_reparo -- só os contratos de chamados de Reparo
    (e Upgrade/Mudança de Cômodo, quando habilitados) que estão de fato
    abertos e dentro do escopo de região/unidade."""
    codigos_alvo = [c for codigos in CATEGORIAS.values() for c in codigos]
    unidades_no_escopo = {u for unidades in REGIOES.values() for u in unidades}

    contratos = set()
    for chamado in lista_chamados or []:
        if not isinstance(chamado, dict):
            continue
        if not _chamado_esta_aberto(chamado):
            continue
        if _codigo_fila(chamado) not in codigos_alvo:
            continue
        unidade = str(chamado.get("enderecoUnidade", "")).upper().strip()
        if unidade not in unidades_no_escopo:
            continue

        contrato = _normalizar_contrato(chamado.get("codigoContrato"))
        if contrato:
            contratos.add(contrato)

    return list(contratos)