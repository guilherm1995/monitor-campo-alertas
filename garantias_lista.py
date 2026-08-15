# ================= LISTA DE GARANTIAS: os dados =================
#
# Monta a mesma lista que a página "Garantias" do site mostra, só que a partir
# de uma fonte diferente -- e é essa diferença que dá razão a este arquivo.
#
# O SITE cruza duas planilhas: chamados_abertos_field_service.xlsx (os reparos
# em aberto) com a base OFS (o histórico concluído). Funciona, mas o "em
# aberto" ali vale a data em que alguém exportou aquele arquivo. Entre uma
# exportação e outra a lista envelhece calada: reparo fechado ontem continua
# aparecendo, reparo aberto hoje de manhã não aparece.
#
# Para uma lista que vai sozinha ao grupo de hora em hora isso não serve. Um
# grupo de roteirização age sobre o que lê; mandar contrato já resolvido custa
# deslocamento de técnico. Então aqui o "em aberto" vem do CAMPO, ao vivo:
#
#   garantia = uma O.S. que o próprio bot JÁ NOTIFICOU como garantia
#              E que a última varredura ainda viu aberta no CAMPO.
#
# As duas metades já existiam no bot e não foram criadas para isto:
#   - reparos_avaliados.json guarda toda garantia notificada (notificado=True);
#   - reparos_abertos_conhecidos() devolve as O.S. de reparo que a última
#     varredura COMPLETA encontrou abertas no CAMPO.
# A interseção das duas é a lista. Nenhuma planilha no caminho, nada para
# alguém lembrar de exportar.
#
# Este módulo não importa o bot_campo_monitoramento: recebe tudo por parâmetro,
# igual aos backlog_*.py. Serve para não criar import circular e para dar para
# ensaiar a lista inteira sem subir o bot.

import logging
import unicodedata
from datetime import datetime

logger = logging.getLogger(__name__)

# ONDE A GARANTIA APARECE -- que não é a mesma coisa que onde o bot monitora.
#
# Estas listas são MAIORES que as do bot_campo_monitoramento de propósito. Lá
# LITORAL_SP e RJ dizem onde fazemos monitoramento completo: alerta de entrante
# CAPEX, backlog, tudo. Aqui elas dizem só em qual grupo uma garantia é
# exibida.
#
# BERT e BERTN entram no litoral pelo mesmo motivo do termômetro: fazem parte
# do LITORAL_SP que a notificação individual monitora, e uma garantia dessas
# unidades tem de cair no grupo do litoral em vez de sumir da lista.
#
# UTB e CBF entraram em 14/08/2026, quando a busca de reparo passou a cobrir as
# siglas fora da nossa área (SIGLAS_GARANTIA_EXTRA, no bot). Sem eles aqui, uma
# garantia em Ubatuba ou Cabo Frio seria capturada e notificada individualmente,
# mas cairia em `sem_regiao` na lista de hora em hora -- avisada uma vez e
# ausente da consulta consolidada. Ampliar a captura sem dar destino é meio
# caminho.
#
# NÃO copie estas listas de volta para o bot: lá elas ligariam alerta de CAPEX
# e backlog em Ubatuba e Cabo Frio, que não roteirizamos.
LITORAL_SP = ['CGT', 'BASE', 'SST', 'SSTBO', 'IBL', 'BERT', 'BERTN', 'UTB']
RJ = ['RSD', 'MPE', 'VAS', 'VRD', 'PNDO', 'VLC', 'IZA', 'TRS', 'BMA',
      'PORE', 'COLG', 'BPI', 'PFS', 'PDS', 'PNHE', 'CBF']

# chave -> (nome que aparece no título, siglas). A chave é o que o serviço do
# WhatsApp entende como "destino" (ver REGRAS_GRUPO_REGIAO no index.js).
REGIOES = {
    'litoral': ('Litoral Norte SP', LITORAL_SP),
    'rj': ('Sul RJ', RJ),
}

CIDADES = {
    "CGT": "Caraguatatuba", "BASE": "Caraguatatuba 1", "SST": "São Sebastião",
    "SSTBO": "Boiçucanga", "IBL": "Ilhabela", "BERT": "Bertioga",
    "BERTN": "Bertioga Norte", "RSD": "Resende", "MPE": "Miguel Pereira",
    "VAS": "Vassouras", "VRD": "Volta Redonda", "PNDO": "Penedo",
    "VLC": "Valença", "IZA": "Itatiaia", "TRS": "Três Rios",
    "BMA": "Barra Mansa", "PORE": "Porto Real",
    "COLG": "Comendador Levy Gasparian", "BPI": "Barra do Piraí",
    "PFS": "Paty do Alferes", "PDS": "Paraíba do Sul", "PNHE": "Pinheiral",
    "UTB": "Ubatuba", "CBF": "Cabo Frio",
}


def _normalizar(texto):
    if texto is None:
        return ''
    bruto = unicodedata.normalize('NFD', str(texto))
    return ''.join(c for c in bruto if unicodedata.category(c) != 'Mn').lower()


def rotulo_servico(tipo_anterior):
    """O serviço que gerou a garantia, na sigla que a operação usa.

    Mesmos rótulos da tela do site (IRR / IFI / IFI de MDE) de propósito: a
    lista que chega no grupo tem de poder ser conferida contra a tela sem
    ninguém precisar traduzir nada.
    """
    texto = _normalizar(tipo_anterior)
    if not texto:
        return 'N/D'
    if 'reparo' in texto:
        return 'IRR'
    if 'mudanca' in texto or 'mde' in texto:
        return 'IFI de MDE'
    if 'ativacao' in texto:
        return 'IFI'
    return str(tipo_anterior)[:16].upper()


def _limpar_contrato(valor):
    """Contrato como texto. Vem ora inteiro, ora '908088.0' do pandas."""
    texto = str(valor or '').strip()
    if texto.endswith('.0'):
        texto = texto[:-2]
    return texto


def montar(reparos_avaliados, os_abertas, status_por_contrato=None, regioes=None):
    """A lista de garantias em aberto, agrupada por região e cidade.

    reparos_avaliados: o dict de reparos_avaliados.json.
    os_abertas: conjunto de O.S. de reparo vistas abertas na última varredura
        COMPLETA do CAMPO (reparos_abertos_conhecidos()[0]).
    status_por_contrato: opcional, {contrato: 'ONLINE'|'OFFLINE'|...}.

    Devolve {'gerado_em', 'regioes': [...], 'total', 'sem_regiao'}.

    Tudo aqui sai do REGISTRO da garantia, nada é recalculado. Técnico, aging e
    tipo do serviço anterior descrevem o mesmo serviço original e foram
    apurados juntos, na hora da notificação; reapurar um deles contra a Base
    OFS de hoje poderia casar outro serviço do contrato e misturar os três.
    (Quem preenche o técnico das garantias antigas é o
    preencher_tecnico_ofs_faltante, no bot, uma vez e gravando.)
    """
    regioes = regioes or REGIOES
    status_por_contrato = status_por_contrato or {}
    abertas = {str(x) for x in (os_abertas or set())}

    itens = []
    for chave, info in (reparos_avaliados or {}).items():
        if not isinstance(info, dict):
            continue
        # As duas condições são o coração da lista: notificada como garantia,
        # e ainda viva no CAMPO. Tirar qualquer uma delas muda o que a lista é.
        if not info.get('notificado'):
            continue
        if str(chave) not in abertas:
            continue

        contrato = _limpar_contrato(info.get('codigo_contrato'))
        tecnico = (info.get('tecnico_ofs') or '').strip()

        itens.append({
            'os_id': str(chave),
            'contrato': contrato or 'N/D',
            'unidade': str(info.get('unidade') or '').upper().strip(),
            'cliente': str(info.get('nome_cliente') or 'N/D'),
            'bairro': str(info.get('bairro') or 'N/D'),
            'telefones': str(info.get('telefones') or 'N/D'),
            'aging': info.get('dias_aging'),
            'servico': rotulo_servico(info.get('tipo_anterior')),
            'tecnico': (tecnico or 'N/D').upper()[:40],
            'status': status_por_contrato.get(contrato, '—'),
        })

    saida_regioes = []
    usadas = set()
    for chave_regiao, (nome_regiao, siglas) in regioes.items():
        cidades = []
        for sigla in siglas:
            do_lugar = [i for i in itens if i['unidade'] == sigla]
            if not do_lugar:
                continue
            # Mais velha primeiro: numa lista de roteirização, quem está há
            # mais tempo esperando é quem tem de ser lido primeiro.
            do_lugar.sort(key=lambda i: (-(i['aging'] or 0), i['contrato']))
            usadas.update(i['os_id'] for i in do_lugar)
            cidades.append({
                'sigla': sigla,
                'nome': CIDADES.get(sigla, sigla).upper(),
                'itens': do_lugar,
            })
        saida_regioes.append({
            'chave': chave_regiao,
            'nome': nome_regiao,
            'cidades': cidades,
            'total': sum(len(c['itens']) for c in cidades),
            'offline': sum(1 for c in cidades for i in c['itens']
                           if i['status'] == 'OFFLINE'),
        })

    # Sigla que não cai em nenhuma região não é descartada em silêncio: ou o
    # CAMPO passou a usar uma unidade nova, ou alguém digitou errado, e as duas
    # coisas só aparecem se a lista disser quantas ficaram de fora.
    sem_regiao = [i for i in itens if i['os_id'] not in usadas]
    if sem_regiao:
        logger.warning(
            "%d garantia(s) em aberto com unidade fora das regiões conhecidas: %s",
            len(sem_regiao),
            ", ".join(sorted({i['unidade'] or '(vazia)' for i in sem_regiao})),
        )

    return {
        'gerado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'regioes': saida_regioes,
        'total': len(itens),
        'sem_regiao': sem_regiao,
    }


def contratos_da_regiao(dados, chave_regiao):
    """Só os contratos de uma região, na ordem em que aparecem na imagem."""
    for regiao in dados.get('regioes', []):
        if regiao['chave'] == chave_regiao:
            return [i['contrato'] for c in regiao['cidades'] for i in c['itens']]
    return []


def todos_os_contratos(dados):
    return [i['contrato']
            for r in dados.get('regioes', [])
            for c in r['cidades']
            for i in c['itens']]


def texto_contratos(dados, chave_regiao):
    """A lista de contratos em texto, para colar em outro sistema.

    Vai junto da imagem porque imagem não se copia: quem precisa consultar o
    contrato no Autenticador ou no OFS teria de digitar de novo, olhando a foto.
    Agrupado por cidade e só os números -- qualquer outro campo aqui é ruído,
    já que tudo o mais está na imagem logo acima.
    """
    regiao = next((r for r in dados.get('regioes', []) if r['chave'] == chave_regiao), None)
    if not regiao or not regiao['cidades']:
        return ''

    linhas = []
    for cidade in regiao['cidades']:
        contratos = [i['contrato'] for i in cidade['itens']]
        linhas.append(f"*{cidade['nome']}* ({len(contratos)})")
        # Um por linha: no WhatsApp, tocar e segurar copia a linha inteira, e
        # contrato colado junto de outro vira erro de digitação de quem consulta.
        linhas.extend(contratos)
        linhas.append("")

    return "\n".join(linhas).strip()
