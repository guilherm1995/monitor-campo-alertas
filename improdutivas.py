# ================= IMPRODUTIVAS: análise do relatório OFS (CSV) =================
#
# Reaproveita a MESMA lógica de classificação já usada no operacional.py (visão
# "Efetividade Geral"): o dicionário MOTIVO_PRODUTIVO decide se um motivo de
# encerramento é produtivo ou não, e o MAPEAMENTO_MOTIVOS decide se uma
# improdutiva é de origem TÉCNICA, COMERCIAL ou CLIENTE. A classificação de
# região usa a coluna "Cidade" (mesma prioridade de colunas do operacional.py).
import logging

import pandas as pd

logger = logging.getLogger(__name__)

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
}

# ---- Mesmo dicionário do operacional.py (TÉCNICA / COMERCIAL / CLIENTE) ----
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
}

# Mesma lista do operacional.py (classificar_regiao), com UBATUBA e BERTIOGA
# adicionados -- apareceram no CSV de exemplo e são cidades do Litoral Norte,
# mas não estavam na lista original do operacional.py (teriam caído em "OUTROS" e
# sumido silenciosamente do relatório). Se o operacional.py também for corrigido,
# vale manter as duas listas iguais.
LITORAL_NOMES = [
    'CARAGUATATUBA', 'CARAGUATATUBA1', 'SÃO SEBASTIÃO', 'SAO SEBASTIAO', 'BOIÇUCANGA',
    'ILHABELA', 'UBATUBA', 'BERTIOGA', 'CGT', 'BASE', 'SST', 'SSTBO', 'IBL',
]
RJ_NOMES = [
    'RESENDE', 'MIGUEL PEREIRA', 'VASSOURAS', 'VOLTA REDONDA', 'PENEDO', 'VALENÇA', 'VALENCA',
    'ITATIAIA', 'TRÊS RIOS', 'TRES RIOS', 'BARRA MANSA', 'PORTO REAL', 'COMENDADOR LEVY GASPARIAN',
    'BARRA DO PIRAÍ', 'BARRA DO PIRAI', 'PATY DO ALFERES', 'PARAÍBA DO SUL', 'PARAIBA DO SUL',
    'PINHEIRAL', 'RSD', 'MPE', 'VAS', 'VRD', 'PNDO', 'VLC', 'IZA', 'TRS', 'BMA', 'PORE', 'COLG',
    'BPI', 'PFS', 'PDS', 'PNHE', 'RIO', 'RJ', 'SUL RJ',
]

REGIOES_VALIDAS = ['LITORAL NORTE SP', 'SUL RJ']
CATEGORIAS_IMPRODUTIVA = ['TÉCNICA', 'COMERCIAL', 'CLIENTE', 'OUTROS']


def encontrar_coluna(df, possibilidades):
    """Mesma lógica do operacional.py: primeira coluna (na ordem de prioridade da
    lista) que existir de fato no arquivo, ignorando maiúsc./espaços."""
    for p in possibilidades:
        for col in df.columns:
            if col.strip().lower() == p.lower():
                return col
    return None


def classificar_regiao(valor):
    v = str(valor).upper().strip()
    if any(x in v for x in LITORAL_NOMES):
        return 'LITORAL NORTE SP'
    if any(x in v for x in RJ_NOMES):
        return 'SUL RJ'
    return 'OUTROS'


def categorizar_motivo(motivo):
    if pd.isna(motivo):
        return 'OUTROS'
    m = str(motivo).strip()
    for chave, categoria in MAPEAMENTO_MOTIVOS.items():
        if chave.upper() == m.upper():
            return categoria
    return 'OUTROS'


def analisar_improdutivas(caminho_csv):
    """Lê o CSV do relatório OFS e devolve um dicionário estruturado com as
    improdutivas separadas por região (Litoral Norte SP / Sul RJ) e por
    categoria (TÉCNICA / COMERCIAL / CLIENTE).

    Levanta ValueError com uma mensagem amigável se alguma coluna essencial
    não for encontrada (pra virar uma mensagem de erro clara no WhatsApp).
    """
    try:
        df = pd.read_csv(caminho_csv, encoding='utf-8-sig', dtype=str)
    except UnicodeDecodeError:
        df = pd.read_csv(caminho_csv, encoding='latin-1', dtype=str)

    df.columns = df.columns.str.strip()

    col_motivo = encontrar_coluna(df, ['Motivo de Encerramento das atividades', 'Motivo'])
    col_cidade = encontrar_coluna(df, ['Cidade', 'AREA', 'UNIDADE', 'Área de Trabalho', 'Chave Workzone'])
    col_nome = encontrar_coluna(df, ['Nome'])
    col_os = encontrar_coluna(df, ['Ordem de Serviço', 'ID da Ordem de Serviço'])
    col_contrato = encontrar_coluna(df, ['Número do contrato', 'Contrato'])
    col_tipo = encontrar_coluna(df, ['Tipo de Atividade.1', 'Tipo de Atividade'])
    col_data = encontrar_coluna(df, ['Data'])

    faltando = [nome for nome, col in [
        ('Motivo de Encerramento das atividades', col_motivo),
        ('Cidade / Área de Trabalho', col_cidade),
        ('Nome', col_nome),
    ] if col is None]
    if faltando:
        raise ValueError(
            "Não encontrei a(s) coluna(s) essencial(is) no arquivo: "
            + ", ".join(faltando)
            + f". Colunas encontradas no arquivo: {list(df.columns)}"
        )

    data_referencia = None
    if col_data and not df[col_data].empty:
        contagem_datas = df[col_data].value_counts()
        if not contagem_datas.empty:
            data_referencia = contagem_datas.index[0]

    df['MOTIVO_CLEAN'] = df[col_motivo].astype(str).str.strip()
    total_bruto = len(df)

    # Só entram na análise OS com um motivo de encerramento reconhecido
    # (mesmo filtro do operacional.py) -- ordens ainda em aberto, canceladas antes
    # de acontecer (sem motivo), etc. ficam de fora, não contam nem como
    # produtiva nem como improdutiva.
    df_validas = df[df['MOTIVO_CLEAN'].isin(MOTIVO_PRODUTIVO.keys())].copy()

    if df_validas.empty:
        raise ValueError(
            "Nenhuma linha do arquivo tem um motivo de encerramento reconhecido "
            "(coluna 'Motivo de Encerramento das atividades' vazia ou com valores "
            "não mapeados). Confirme se é o arquivo certo."
        )

    df_validas['PRODUTIVO'] = df_validas['MOTIVO_CLEAN'].map(MOTIVO_PRODUTIVO)
    df_validas['REGIAO'] = df_validas[col_cidade].apply(classificar_regiao)
    df_validas['MOTIVO_MACRO'] = df_validas['MOTIVO_CLEAN'].apply(categorizar_motivo)

    fora_do_escopo = df_validas[df_validas['REGIAO'] == 'OUTROS']
    cidades_fora_do_escopo = sorted(fora_do_escopo[col_cidade].dropna().unique().tolist()) if col_cidade else []

    resultado = {
        'data_referencia': data_referencia,
        'total_bruto_arquivo': total_bruto,
        'total_analisado': len(df_validas),
        'fora_do_escopo': {
            'quantidade': len(fora_do_escopo),
            'cidades': cidades_fora_do_escopo,
        },
        'regioes': {},
    }

    for regiao in REGIOES_VALIDAS:
        df_reg = df_validas[df_validas['REGIAO'] == regiao]
        total = len(df_reg)
        produtivas = int(df_reg['PRODUTIVO'].sum())
        improdutivas_df = df_reg[~df_reg['PRODUTIVO']]

        categorias = {}
        for cat in CATEGORIAS_IMPRODUTIVA:
            df_cat = improdutivas_df[improdutivas_df['MOTIVO_MACRO'] == cat]
            itens = []
            for _, linha in df_cat.iterrows():
                itens.append({
                    'nome': str(linha.get(col_nome, '')).strip(),
                    'cidade': str(linha.get(col_cidade, '')).strip(),
                    'motivo': linha['MOTIVO_CLEAN'],
                    'os': str(linha.get(col_os, '')).strip() if col_os else '',
                    'contrato': str(linha.get(col_contrato, '')).strip() if col_contrato else '',
                    'tipo': str(linha.get(col_tipo, '')).strip() if col_tipo else '',
                })
            categorias[cat] = itens

        resultado['regioes'][regiao] = {
            'total': total,
            'produtivas': produtivas,
            'improdutivas': total - produtivas,
            'categorias': categorias,
        }

    return resultado


EMOJI_CATEGORIA = {
    'TÉCNICA': '🔧',
    'COMERCIAL': '🏢',
    'CLIENTE': '👤',
    'OUTROS': '❓',
}


def _dividir_mensagem_longa(texto, limite=3500):
    """WhatsApp aceita mensagens bem longas, mas pra não virar uma parede de
    texto ilegível, quebra em pedaços de até `limite` caracteres, sempre
    numa quebra de linha (nunca no meio de uma linha)."""
    if len(texto) <= limite:
        return [texto]

    partes = []
    linhas = texto.split('\n')
    atual = ''
    for linha in linhas:
        candidato = (atual + '\n' + linha) if atual else linha
        if len(candidato) > limite and atual:
            partes.append(atual)
            atual = linha
        else:
            atual = candidato
    if atual:
        partes.append(atual)
    return partes


def formatar_mensagens_whatsapp(resultado):
    """Monta uma lista de mensagens de texto prontas pra enviar no grupo do
    WhatsApp (uma por região, quebradas em pedaços menores se ficarem muito
    longas)."""
    mensagens = []

    data_ref = resultado.get('data_referencia') or '?'
    resumo = (
        f"📋 *IMPRODUTIVAS — Relatório OFS ({data_ref})*\n"
        f"Total no arquivo: {resultado['total_bruto_arquivo']} | "
        f"Analisadas (com motivo de encerramento): {resultado['total_analisado']}"
    )
    fora = resultado.get('fora_do_escopo', {})
    if fora.get('quantidade'):
        resumo += (
            f"\n⚠️ {fora['quantidade']} OS fora do escopo Litoral Norte SP / Sul RJ "
            f"(cidades: {', '.join(fora['cidades'][:10])}"
            f"{'...' if len(fora['cidades']) > 10 else ''})"
        )
    mensagens.append(resumo)

    for regiao in REGIOES_VALIDAS:
        dados_regiao = resultado['regioes'].get(regiao)
        if not dados_regiao or dados_regiao['total'] == 0:
            mensagens.append(f"📍 *{regiao}*\nNenhuma OS com motivo de encerramento nessa região.")
            continue

        linhas = [
            f"📍 *{regiao}*",
            f"Total: {dados_regiao['total']} | Produtivas: {dados_regiao['produtivas']} | "
            f"Improdutivas: {dados_regiao['improdutivas']}",
            "",
        ]

        for cat in CATEGORIAS_IMPRODUTIVA:
            itens = dados_regiao['categorias'].get(cat, [])
            if not itens:
                continue
            emoji = EMOJI_CATEGORIA.get(cat, '•')
            linhas.append(f"{emoji} *{cat}* ({len(itens)})")
            for item in itens:
                contrato_txt = item['contrato'] or '(sem contrato)'
                linhas.append(f"  • {contrato_txt} — {item['motivo']}")
            linhas.append("")

        texto_regiao = "\n".join(linhas).rstrip()
        mensagens.extend(_dividir_mensagem_longa(texto_regiao))

    return mensagens
