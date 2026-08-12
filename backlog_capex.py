# ================= BACKLOG CAPEX: motor de cálculo (Ativação / Mudança de Endereço) =================
from datetime import datetime

CODIGOS_ATIVACAO = ['ES02', 'ES02PV', 'ATV1', 'ATB2B', 'ATVPME', 'ATVPRE']
CODIGOS_MUDANCA_ENDERECO = ['ES04']

# Mesma composição de unidades já usada no bot para CAPEX (LITORAL_SP / RJ)
REGIOES = {
    "LITORAL NORTE": ['CGT', 'BASE', 'SST', 'SSTBO', 'IBL'],
    "SUL RJ": ['RSD', 'MPE', 'VAS', 'VRD', 'PNDO', 'VLC', 'IZA', 'TRS',
               'BMA', 'PORE', 'COLG', 'BPI', 'PFS', 'PDS', 'PNHE'],
}

# Prazo do 1º bucket de idade, por categoria. Ativação = "Até 48hrs",
# Mudança de Endereço = "Até 2 dias" -- são o MESMO valor (48h = 2 dias),
# só nomeados separado pra não depender de coincidência numérica se um dia
# um dos dois prazos mudar.
LIMITE_BUCKET1_HORAS = {
    "Ativação": 48,
    "Mudança de endereço": 48,  # = 2 dias
}
LIMITE_BUCKET2_DIAS = 7  # "Até 7 dias" (igual pras duas categorias)

CATEGORIAS = {
    "Ativação": CODIGOS_ATIVACAO,
    "Mudança de endereço": CODIGOS_MUDANCA_ENDERECO,
}


def _normalizar_contrato(valor):
    """Normaliza o número de contrato pra string, do mesmo jeito que
    tratar_contrato() faz do lado da planilha (backlog_conveniencia.py):
    remove o '.0' que aparece quando o valor passa por float, e espaços
    nas pontas. Sem isso, comparar int (vindo da API) com str (vindo do
    pandas) falha silenciosamente sempre -- era exatamente o bug da
    Conveniência zerada."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto.endswith(".0"):
        texto = texto[:-2]
    return texto


def _chamado_esta_aberto(chamado):
    """Backlog = só chamados ainda abertos. Considera fechado SÓ quando o
    chamado tem dataConclusao preenchida.

    Antes também considerava fechado quando a última OS estava
    "ST_OS_CONCLUIDA", mas isso é um falso-positivo: um chamado de
    ATIVAÇÃO/ATIVAÇÃO-PRÉ-VENDA pode ter a OS técnica (a parte física, em
    campo) concluída e o chamado continuar aberto — por exemplo aguardando
    o backoffice configurar o plano/pacote do cliente. Fechar o chamado
    nesse momento faz ele sumir do backlog antes da hora."""
    return not chamado.get("dataConclusao")


def _tem_pacote(chamado):
    """Verifica se o chamado tem 'pacote' (plano) preenchido na última OS.
    Chamados sem pacote definido ainda (ex: aguardando configuração do
    plano no backoffice, ou sem ordemServicos criada) são expurgados da
    visão de CAPEX -- não são um entrante "de verdade" enquanto o plano
    não está definido.

    A lista que chega do cache do bot vem projetada com este booleano já
    calculado (ver projetar_para_cache): o 'ordemServicos' é a parte pesada
    do chamado e não sobrevive à varredura. O caminho antigo continua aqui
    como plano B, para quem chamar esta função com o chamado cru."""
    if "tem_pacote" in chamado:
        return bool(chamado["tem_pacote"])
    ordens = chamado.get("ordemServicos") or []
    if not ordens or not isinstance(ordens[-1], dict):
        return False
    pacote = ordens[-1].get("pacote")
    return bool(pacote and str(pacote).strip())


def _codigo_fila(chamado):
    fila = chamado.get("fila")
    if isinstance(fila, dict):
        return fila.get("codigo")
    if isinstance(fila, str):
        return fila
    return chamado.get("codigo")


def _bucket_idade(chamado, agora, categoria):
    """Bucket de idade do chamado (desde dataAbertura), usando o limite
    do 1º bucket específico da categoria (Ativação=48h, Mudança=2 dias)."""
    data_abertura_ms = chamado.get("dataAbertura")
    if not data_abertura_ms:
        return None
    try:
        data_abertura = datetime.fromtimestamp(data_abertura_ms / 1000)
    except (TypeError, ValueError, OSError):
        return None

    limite_bucket1_horas = LIMITE_BUCKET1_HORAS[categoria]
    idade_horas = (agora - data_abertura).total_seconds() / 3600
    if idade_horas <= limite_bucket1_horas:
        return "bucket1"
    if idade_horas <= LIMITE_BUCKET2_DIAS * 24:
        return "bucket2"
    return "bucket3"


def _bucket_agendamento(chamado, hoje):
    """Classifica o chamado pela distância entre a data agendada
    (agendamentoData, 'YYYY-MM-DD') e hoje.

    D0 = agendado pra hoje, D+1 = amanhã, D+2, D+3, >D+3 = daqui a mais de
    3 dias. Se a data agendada já passou, é 'vencida'. Sem agendamentoData
    -> não entra em nenhum bucket (None)."""
    agendamento_str = chamado.get("agendamentoData")
    if not agendamento_str:
        return None
    try:
        data_agendada = datetime.strptime(agendamento_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None

    diferenca_dias = (data_agendada - hoje).days

    if diferenca_dias < 0:
        return "vencida"
    if diferenca_dias == 0:
        return "d0"
    if diferenca_dias == 1:
        return "d1"
    if diferenca_dias == 2:
        return "d2"
    if diferenca_dias == 3:
        return "d3"
    return "mais_d3"


def _linha_vazia():
    return {
        "bucket1": 0, "bucket2": 0, "bucket3": 0, "total": 0,
        "enviado_d0": 0, "conveniencia": 0, "oportunidade_injecao": 0,
    }


def _linha_vazia_agendamento():
    return {
        "d0": 0, "d1": 0, "d2": 0, "d3": 0, "mais_d3": 0,
        "vencida": 0, "no_prazo": 0,
    }


def _percentual(parte, total):
    return round((parte / total) * 100, 1) if total else 0.0


def calcular_backlog_capex(lista_chamados, conveniencias=None, agora=None,
                           contratos_ofs_d0=None):
    """Monta a estrutura do backlog de CAPEX (Ativação e Mudança de Endereço,
    separados por região/unidade), a partir da lista de chamados crua da API.

    conveniencias: coleção de códigos de contrato (já filtrados por data válida,
    vindos da planilha do Google Sheets).

    contratos_ofs_d0: contratos que o OFS GERAL tem agendados para hoje. Entram
    no "Enviado D0" mesmo quando o agendamentoData do CAMPO não diz D0 -- é o caso
    da O.S. que deu erro de integração e foi puxada manualmente pelo OFS. Ver
    backlog_ofs.py.

    Retorna (idade, agendamento):
      idade[categoria][regiao][unidade] = buckets de idade + D0/conveniência/oportunidade
      agendamento[categoria][regiao][unidade] = buckets D0/D+1/D+2/D+3/>D+3 + vencida/no_prazo
    """
    agora = agora or datetime.now()
    hoje = agora.date()
    conveniencias = {_normalizar_contrato(c) for c in (conveniencias or [])}
    contratos_ofs_d0 = {_normalizar_contrato(c) for c in (contratos_ofs_d0 or [])}

    idade = {}
    agendamento = {}
    for nome_categoria in CATEGORIAS:
        idade[nome_categoria] = {}
        agendamento[nome_categoria] = {}
        for nome_regiao, unidades in REGIOES.items():
            idade[nome_categoria][nome_regiao] = {u: _linha_vazia() for u in unidades}
            agendamento[nome_categoria][nome_regiao] = {u: _linha_vazia_agendamento() for u in unidades}

    for chamado in lista_chamados or []:
        if not isinstance(chamado, dict):
            continue
        if not _chamado_esta_aberto(chamado):
            continue
        if not _tem_pacote(chamado):
            continue

        codigo = _codigo_fila(chamado)
        categoria = None
        for nome_categoria, codigos in CATEGORIAS.items():
            if codigo in codigos:
                categoria = nome_categoria
                break
        if categoria is None:
            continue

        unidade = str(chamado.get("enderecoUnidade", "")).upper().strip()
        regiao = None
        for nome_regiao, unidades in REGIOES.items():
            if unidade in unidades:
                regiao = nome_regiao
                break
        if regiao is None:
            continue  # unidade fora do escopo (ex: SIU/CMO/ALP/SPC) -> ignorada

        # ---- bloco de idade (48h ou 2 dias / 7 dias / acima) ----
        linha_idade = idade[categoria][regiao][unidade]
        bucket_idade = _bucket_idade(chamado, agora, categoria)
        if bucket_idade:
            linha_idade[bucket_idade] += 1
        linha_idade["total"] += 1

        # ---- bloco "Próximo agendamento" (D0/D+1/D+2/D+3/>D+3) -- baseado
        # em agendamentoData, SEM MUDANÇA: continua sendo sobre a PRÓXIMA
        # VISITA agendada (pra saber quantos tem marcado pra cada dia), e
        # não sobre a idade do chamado.
        linha_agenda = agendamento[categoria][regiao][unidade]
        bucket_agenda = _bucket_agendamento(chamado, hoje)
        if bucket_agenda and bucket_agenda != "vencida":
            # "vencida" (retorno possível de _bucket_agendamento, quando o
            # agendamento já passou) é excluído de propósito aqui -- quem
            # decide "vencida" agora é o bloco de idade abaixo, não mais
            # este. Sem essa exclusão, "vencida" seria somada duas vezes
            # por dois critérios diferentes.
            linha_agenda[bucket_agenda] += 1

        # ---- bloco "Vencida / No prazo" -- CORRIGIDO: agora usa a IDADE
        # do chamado (dataAbertura via bucket_idade), não mais o
        # agendamentoData. Antes, "vencida" comparava a data agendada com
        # hoje -- só que o CAMPO reagenda automaticamente a visita pra uma
        # data futura sempre que ela é perdida, então esse campo
        # praticamente nunca fica no passado, e "vencida" saía sempre
        # zerado mesmo com chamado antigo de verdade. Agora "vencida" =
        # chamado já passou do prazo do bucket3 (mesmo limite de "Acima
        # de X" já usado na Idade do Chamado, por categoria); "no prazo"
        # = ainda dentro do prazo (bucket1 ou bucket2).
        if bucket_idade == "bucket3":
            linha_agenda["vencida"] += 1
        elif bucket_idade is not None:
            linha_agenda["no_prazo"] += 1

        # Partição: Enviado D0 > Conveniência > Oportunidade de Injeção
        # (continua baseada no agendamento -- "Enviado D0" é sobre a
        # próxima visita ser hoje, não sobre a idade do chamado)
        # - Enviado D0: agendamentoData == hoje OU o contrato está agendado
        #   para hoje no OFS GERAL. A segunda condição existe porque O.S. que
        #   dá erro de integração é puxada manualmente pelo OFS e o CAMPO não
        #   registra o D0 -- sem cruzar, o "Enviado D0" fica menor que o real.
        # - Conveniência: não é D0, mas o contrato está na planilha (data válida)
        # - Oportunidade de Injeção: não é D0 e não é conveniência -- inclui
        #   tanto agendamento futuro (D+1/D+2/D+3/>D+3) quanto VENCIDO, e
        #   também chamados sem agendamentoData (ainda sem O.S.)
        contrato_chamado = _normalizar_contrato(chamado.get("codigoContrato"))
        if bucket_agenda == "d0" or contrato_chamado in contratos_ofs_d0:
            linha_idade["enviado_d0"] += 1
        elif contrato_chamado in conveniencias:
            linha_idade["conveniencia"] += 1
        else:
            linha_idade["oportunidade_injecao"] += 1

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
            totalizador["pct_bucket1"] = _percentual(totalizador["bucket1"], totalizador["total"])
            totalizador["pct_bucket2"] = _percentual(totalizador["bucket2"], totalizador["total"])
            totalizador["pct_bucket3"] = _percentual(totalizador["bucket3"], totalizador["total"])
            unidades["TOTAL"] = totalizador

    for nome_categoria, regioes in agendamento.items():
        for nome_regiao, unidades in regioes.items():
            totalizador = _linha_vazia_agendamento()
            for linha in unidades.values():
                for chave in totalizador:
                    totalizador[chave] += linha[chave]
            unidades["TOTAL"] = totalizador

    return idade, agendamento