# ============ IMPRODUTIVAS TÉCNICAS DO DIA ANTERIOR: lista diária ============
#
# Todo dia às 08:30, cada grupo regional recebe as improdutivas TÉCNICAS do dia
# anterior, agrupadas por técnico. Serve para o supervisor analisar: agrupado
# por técnico, "quantas ele informou ontem" se lê de imediato, e duas
# improdutivas do mesmo técnico na mesma rua aparecem lado a lado.
#
# 08:30 porque é depois da base, não por acaso. A base do OFS começa a se
# refazer às 07:40 (`ofs_base_historica`) e leva de 5 a 35 min: medido no log,
# ficou pronta 07:46 (13/09), 07:58 (14/09), 07:59 (11/09) e 08:15 (12/09). Às
# 08:30 ela normalmente já tem ontem, e a lista sai no próprio horário. No dia em
# que a reconstrução atrasar, a repescagem segura -- ver abaixo.
#
# Foi 10h até 14/09/2026, quando o dono pediu a lista mais cedo.
#
# Texto, não imagem. São ~6 linhas por grupo (736 técnicas em 59 dias, duas
# regionais) -- e texto no WhatsApp é pesquisável, imagem não. Quem procura um
# contrato no histórico do grupo meses depois precisa achar.

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

import pandas as pd

import garantias_envio
import garantias_lista
import improdutivas
import ofs_base_historica

logger = logging.getLogger(__name__)

COLUNA_MOTIVO = 'Motivo de Encerramento das atividades'

HORA_ENVIO = int(os.environ.get('IMPRODUTIVAS_DIA_HORA', '8'))
MINUTO_ENVIO = int(os.environ.get('IMPRODUTIVAS_DIA_MINUTO', '30'))
IMPRODUTIVAS_DIA_ATIVO = os.environ.get('IMPRODUTIVAS_DIA_ATIVO', '1') != '0'

# Se a sessão do OFS venceu naquela manhã, a base fica velha mesmo às 08:30, e
# uma lista montada em cima de base velha sai INCOMPLETA sem nada indicando
# isso. Lista incompleta que se parece com completa é pior do que lista nenhuma:
# o supervisor analisa o que chegou e conclui que o resto não existiu.
#
# 5 min, não 30: no dia atrasado o que interessa é a lista sair assim que a base
# chegar, não meia hora depois. Cada tentativa é um `read_excel` de ~16 mil
# linhas, alguns segundos. Ler durante a reconstrução é seguro:
# `ofs_base_historica` grava em `.novo` e faz `os.replace`, então nunca existe
# arquivo pela metade.
INTERVALO_REPESCAGEM_SEG = int(os.environ.get('IMPRODUTIVAS_DIA_REPESCAGEM', '300'))
HORA_DESISTIR = int(os.environ.get('IMPRODUTIVAS_DIA_HORA_DESISTIR', '14'))


# Quanto tempo depois do alvo da base o atraso deixa de ser rotina. A
# reconstrução leva até ~35 min (ver o cabeçalho), então 07:40 + 60 min = 08:40
# é o primeiro instante em que base velha significa mesmo que algo travou. Dá
# dois ciclos de repescagem de graça depois das 08:30 antes de qualquer alarme.
MARGEM_AVISO_MIN = int(os.environ.get('IMPRODUTIVAS_DIA_MARGEM_AVISO', '60'))


def _passou_da_hora_da_base(agora=None):
    """Já passou da hora em que a base do OFS deveria estar pronta, com margem?

    O aviso de "a base ainda não tem ontem" só vale depois disso. A
    reconstrução da base pode se estender até depois das 08:30 sem nada de
    errado -- avisar no primeiro tropeço seria alarme sobre funcionamento
    normal, e alarme que toca à toa vira paisagem. A hora vem de
    `ofs_base_historica` para não existirem dois lugares dizendo quando a base
    chega.
    """
    agora = agora or datetime.now()
    alvo = agora.replace(hour=ofs_base_historica.HORA_ALVO,
                         minute=ofs_base_historica.MINUTO_ALVO,
                         second=0, microsecond=0)
    return agora >= alvo + timedelta(minutes=MARGEM_AVISO_MIN)

# "Problema CTO" é o motivo mais próximo de caixa cheia que o OFS registra --
# mas não é sinônimo: pode ser CTO danificada, sem sinal, qualquer coisa. Por
# isso a observação é condicional, e não uma afirmação de que a caixa encheu.
MOTIVOS_COM_DICA_CTO = {'Problema CTO'}

_lock_envio = threading.Lock()

# O dia já publicado mora em DISCO, não na memória do processo. O campo-bot
# reinicia várias vezes ao dia; guardado só em memória, um reinício entre o
# envio e a meia-noite apagaria a marca e a repescagem republicaria a mesma lista no
# grupo. É a mesma lição do intervalo das bases (ofs_base_historica).
ARQUIVO_ENVIADO = os.environ.get(
    'IMPRODUTIVAS_DIA_ARQUIVO',
    os.path.join(os.getcwd(), 'dados', 'improdutivas_dia_enviado.json'))


def ultimo_dia_enviado():
    try:
        with open(ARQUIVO_ENVIADO, encoding='utf-8') as arquivo:
            return json.load(arquivo).get('dia')
    except (OSError, ValueError):
        return None


def marcar_enviado(dia):
    try:
        os.makedirs(os.path.dirname(ARQUIVO_ENVIADO), exist_ok=True)
        temporario = ARQUIVO_ENVIADO + '.novo'
        with open(temporario, 'w', encoding='utf-8') as arquivo:
            json.dump({'dia': str(dia)}, arquivo)
        os.replace(temporario, ARQUIVO_ENVIADO)
    except OSError:
        logger.exception('Não consegui gravar %s.', ARQUIVO_ENVIADO)

_TIPOS_LOGRADOURO = (r'(?:RUA|AVENIDA|AV|ALAMEDA|ESTRADA|TRAVESSA|RODOVIA|'
                     r'PRACA|PRAÇA|VIELA|LARGO|SERVIDAO|SERVIDÃO)')

# Preposições ficam minúsculas no meio do nome, nunca na primeira palavra.
_MINUSCULAS = {'de', 'da', 'do', 'das', 'dos', 'e', 'di', 'du', 'del'}


def limpar_endereco(bruto):
    """Conserta o tipo de logradouro duplicado que o OFS grava.

    Aparecem duas formas: o mesmo tipo repetido ("RUA RUA YOSHIZO SHIBATA") e
    um tipo seguido de outro ("RUA AVENIDA THEREZA ALBINO CHACON"). No segundo
    caso quem vale é o segundo -- o primeiro é entulho do cadastro.
    """
    texto = ' '.join(str(bruto or '').split())
    if not texto:
        return ''
    texto = re.sub(rf'^({_TIPOS_LOGRADOURO})\s+\1\s+', r'\1 ', texto, flags=re.I)
    texto = re.sub(rf'^{_TIPOS_LOGRADOURO}\s+({_TIPOS_LOGRADOURO}\s+)', r'\1',
                   texto, flags=re.I)
    return texto


def normalizar_nome(bruto):
    """Caixa uniforme. O OFS grava ora 'MARCIO CRUZ', ora 'Andre Gomes'.

    O campo inteiro é o nome, inclusive o CNPJ que vem colado na frente dos
    clientes PJ ('66.888.779 FULANA') -- ele fica, é parte do cadastro. Só não
    passa pelo capitalize, que estragaria os dígitos.
    """
    texto = ' '.join(str(bruto or '').split())
    if not texto:
        return ''
    palavras = []
    for posicao, palavra in enumerate(texto.split(' ')):
        if any(c.isdigit() for c in palavra):
            palavras.append(palavra)
        elif posicao and palavra.lower() in _MINUSCULAS:
            palavras.append(palavra.lower())
        else:
            palavras.append(palavra.capitalize())
    return ' '.join(palavras)


def _sigla_da_workzone(chave):
    """'SP-000CGT-WZ006' -> 'CGT'. É o que liga a atividade à regional."""
    achado = re.search(r'-0*([A-Z]{2,6})-WZ', str(chave or ''))
    return achado.group(1) if achado else None


def _regiao_da_sigla(sigla):
    for chave, (_nome, siglas) in garantias_lista.REGIOES.items():
        if sigla in siglas:
            return chave
    return None


def carregar(dia, caminho=None):
    """As improdutivas técnicas de `dia`, por região. Também diz se a base cobre o dia.

    `cobre` é separado de `regioes` de propósito: base que ainda não alcançou
    `dia` devolve zero linhas, exatamente como um dia que de fato não teve
    improdutiva nenhuma. Quem chama precisa distinguir as duas coisas.
    """
    caminho = caminho or ofs_base_historica.ARQUIVO_IMPRODUTIVAS
    tabela = pd.read_excel(caminho)
    tabela.columns = tabela.columns.str.strip()

    datas = pd.to_datetime(tabela['Data'], errors='coerce', dayfirst=True)
    ultima = datas.dt.date.max()
    cobre = bool(ultima is not None and not pd.isna(ultima) and ultima >= dia)

    motivos = tabela[COLUNA_MOTIVO]
    tabela = tabela[motivos.notna() & (motivos.astype(str).str.strip() != 'Concluída')]
    tabela = tabela.assign(_data=pd.to_datetime(tabela['Data'], errors='coerce',
                                                dayfirst=True))
    tabela = tabela[tabela['_data'].dt.date == dia]

    regioes = {chave: [] for chave in garantias_lista.REGIOES}
    for _, linha in tabela.iterrows():
        motivo = str(linha[COLUNA_MOTIVO]).strip()
        if improdutivas.MOTIVO_PRODUTIVO.get(motivo, False):
            continue
        if improdutivas.categorizar_motivo(motivo) != 'TÉCNICA':
            continue
        chave = _regiao_da_sigla(_sigla_da_workzone(linha.get('Chave Workzone')))
        if chave is None:
            # Fora das duas regionais: não some no silêncio, mas também não
            # entra num grupo que não roteiriza aquela praça.
            logger.info('Improdutiva técnica fora das regionais conhecidas: %s',
                        linha.get('Chave Workzone'))
            continue
        regioes[chave].append({
            'tecnico': normalizar_nome(linha.get('Recurso')),
            'motivo': motivo,
            'contrato': str(linha.get('Número do contrato', '')).split('.')[0],
            'cliente': normalizar_nome(linha.get('Nome')),
            'endereco': limpar_endereco(linha.get('Endereço')),
        })
    return {'cobre': cobre, 'ultima_data': ultima, 'regioes': regioes}


def montar_texto(dia, chave, linhas):
    """A mensagem de uma regional, agrupada por técnico (mais improdutivas primeiro)."""
    nome_regiao = garantias_lista.REGIOES[chave][0]
    cabecalho = ("Bom dia, segue a lista de improdutivas do dia anterior\n\n"
                 f"*IMPRODUTIVAS TÉCNICAS — {dia.strftime('%d/%m')}*")

    if not linhas:
        return (f"{cabecalho}\n\n_{nome_regiao}_\n\n"
                "✅ Nenhuma improdutiva técnica no dia.")

    por_tecnico = {}
    for item in linhas:
        por_tecnico.setdefault(item['tecnico'] or 'Sem técnico', []).append(item)
    ordem = sorted(por_tecnico.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    partes = [
        f"{cabecalho}\n"
        f"_{nome_regiao} · {len(linhas)} ocorrência(s) · {len(ordem)} técnico(s)_",
        "- - - - - - - - - - - - - - - - -",
    ]
    for tecnico, itens in ordem:
        bloco = [f"*{tecnico}* — {len(itens)}", ""]
        for item in itens:
            bloco.append(f"  *{item['motivo']}*")
            bloco.append(f"  Contrato: {item['contrato']}")
            bloco.append(f"  Cliente: {item['cliente']}")
            bloco.append(f"  Endereço: {item['endereco']}")
            if item['motivo'] in MOTIVOS_COM_DICA_CTO:
                bloco.append('  _Se for caixa cheia, valide no Associar Porta do OFS._')
            bloco.append("")
        partes.append("\n".join(bloco).rstrip())
        partes.append("- - - - - - - - - - - - - - - - -")
    return "\n\n".join(partes)


def enviar(dia=None, caminho=None):
    """Monta e manda a lista de cada regional. Devolve um resumo do que saiu."""
    with _lock_envio:
        dia = dia or (datetime.now() - timedelta(days=1)).date()
        dados = carregar(dia, caminho=caminho)
        if not dados['cobre']:
            logger.warning('Base de improdutivas ainda não cobre %s (vai até %s).',
                           dia, dados['ultima_data'])
            return {'ok': False, 'motivo': 'base_desatualizada',
                    'ultima_data': dados['ultima_data']}

        enviadas = 0
        for chave, linhas in dados['regioes'].items():
            texto = montar_texto(dia, chave, linhas)
            for i, parte in enumerate(garantias_envio._partir(texto)):
                if i:
                    # Sem pausa, as partes chegam fora de ordem no grupo.
                    time.sleep(1.0)
                garantias_envio.enviar_texto(parte, chave)
            enviadas += 1
            logger.info('Improdutivas técnicas de %s enviadas para %s: %d linha(s).',
                        dia, chave, len(linhas))
        return {'ok': True, 'dia': str(dia), 'enviadas': enviadas,
                'totais': {c: len(l) for c, l in dados['regioes'].items()}}


def _proximo_horario(agora):
    alvo = agora.replace(hour=HORA_ENVIO, minute=MINUTO_ENVIO, second=0, microsecond=0)
    if alvo <= agora:
        alvo += timedelta(days=1)
    return alvo


def thread_agendador_improdutivas_dia(avisar=None):
    """Manda a lista todo dia às 08:30, sobre o dia anterior.

    Não dispara ao subir: o serviço reinicia várias vezes ao dia e um disparo
    por reinício encheria os grupos da mesma lista. O dia já publicado fica em
    `ARQUIVO_ENVIADO`, para a repescagem não republicar o que já saiu.
    """
    if not IMPRODUTIVAS_DIA_ATIVO:
        logger.info('Lista diária de improdutivas técnicas desligada '
                    '(IMPRODUTIVAS_DIA_ATIVO=0).')
        return

    logger.info('Agendador das improdutivas técnicas iniciado: todo dia às %02d:%02d, '
                'sobre o dia anterior.', HORA_ENVIO, MINUTO_ENVIO)

    while True:
        agora = datetime.now()
        alvo = _proximo_horario(agora)
        logger.info('Próxima lista de improdutivas técnicas: %s (em %.0f min).',
                    alvo.strftime('%d/%m %H:%M'), (alvo - agora).total_seconds() / 60)
        while True:
            restante = (alvo - datetime.now()).total_seconds()
            if restante <= 0:
                break
            time.sleep(min(restante, 300))

        dia = (datetime.now() - timedelta(days=1)).date()
        avisado = False
        while True:
            if ultimo_dia_enviado() == str(dia):
                logger.info('Improdutivas técnicas de %s já foram enviadas; pulando.', dia)
                break
            try:
                resultado = enviar(dia=dia)
            except Exception:
                logger.exception('Falha ao enviar as improdutivas técnicas do dia.')
                break
            if resultado['ok']:
                marcar_enviado(dia)
                break
            # Base velha: avisa UMA vez e fica tentando até a base voltar.
            if not avisado and avisar and _passou_da_hora_da_base():
                try:
                    avisar('⚠️ *Improdutivas técnicas de ontem*\n\n'
                           'A base do OFS ainda não tem o dia de ontem, então a lista '
                           'sairia incompleta. Mando assim que a base for atualizada.')
                except Exception:
                    logger.exception('Falha ao avisar sobre a base desatualizada.')
                avisado = True
            if datetime.now().hour >= HORA_DESISTIR:
                logger.warning('Desisti da lista de improdutivas de %s: base não '
                               'atualizou até as %dh.', dia, HORA_DESISTIR)
                break
            time.sleep(INTERVALO_REPESCAGEM_SEG)

        time.sleep(61)
