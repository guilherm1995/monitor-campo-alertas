# ================= LISTA DE GARANTIAS: envio e agendador =================
#
# De hora em hora, das 7h às 19h, cada grupo regional recebe a sua lista:
# uma imagem (a tabela, igual à tela do site) e, logo abaixo, os números de
# contrato em texto -- porque imagem não se copia, e quem vai consultar o
# contrato no Autenticador ou no OFS teria de digitar tudo de novo olhando a foto.
#
# Envio direto, SEM a fila de reenvio dos alertas. É de propósito: aquela fila
# existe para alerta de CAPEX e de garantia, que são fato único e não se
# repetem -- perder um é perder a informação. Esta lista é um retrato do
# momento, e um retrato das 10h que chega às 11h30 é pior do que retrato
# nenhum: manda a operação rodar atrás de contrato que já fechou. Se falhar,
# fica no log e a próxima hora resolve.

import base64
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import requests

import garantias_lista
import garantias_render
from backlog_envio import (
    WHATSAPP_ALERTA_URL,
    WHATSAPP_ALERTA_IMAGEM_URL,
    WHATSAPP_ALERTA_ATIVO,
    consultar_autenticador_status,
)

logger = logging.getLogger(__name__)

PASTA_SAIDA = os.environ.get(
    'GARANTIAS_PASTA_SAIDA', os.path.join(os.getcwd(), 'relatorios')
)

# A janela pedida pela operação: primeiro envio às 7h, último às 19h.
HORA_INICIO = int(os.environ.get('GARANTIAS_HORA_INICIO', '7'))
HORA_FIM = int(os.environ.get('GARANTIAS_HORA_FIM', '19'))

# Desligar o envio sem mexer no código (mesma convenção do WHATSAPP_ALERTA_ATIVO).
GARANTIAS_ENVIO_ATIVO = os.environ.get('GARANTIAS_ENVIO_ATIVO', '1') != '0'

# Uma mensagem de texto por vez não passa disso. O limite do WhatsApp é muito
# maior; o corte aqui é de legibilidade -- lista gigante vira parede de números.
LIMITE_TEXTO = 3500

_lock_envio = threading.Lock()


# --------------------------------------------------------------------------
# envio
# --------------------------------------------------------------------------
def _postar(url, corpo, descricao):
    if not WHATSAPP_ALERTA_ATIVO:
        logger.info("WhatsApp desligado (WHATSAPP_ALERTA_ATIVO=0): %s não enviado.", descricao)
        return False
    try:
        resposta = requests.post(url, json=corpo, timeout=45)
        if resposta.status_code != 200:
            # O corpo da resposta diz QUAL destino não resolveu, que é
            # exatamente o que se precisa saber quando um grupo não recebe.
            logger.warning("Falha ao enviar %s: HTTP %s — %s",
                           descricao, resposta.status_code, resposta.text[:300])
            return False
        return True
    except Exception:
        logger.warning("Falha ao enviar %s.", descricao, exc_info=True)
        return False


def enviar_imagem(caminho, legenda, destino):
    try:
        with open(caminho, 'rb') as arquivo:
            imagem_base64 = base64.b64encode(arquivo.read()).decode('ascii')
    except Exception:
        logger.exception("Não consegui ler a imagem gerada em %s.", caminho)
        return False
    return _postar(
        WHATSAPP_ALERTA_IMAGEM_URL,
        {'imagemBase64': imagem_base64, 'legenda': legenda or '', 'destino': destino},
        f"imagem de garantias ({destino})",
    )


def enviar_texto(mensagem, destino):
    return _postar(
        WHATSAPP_ALERTA_URL,
        {'mensagem': mensagem, 'destino': destino},
        f"texto de garantias ({destino})",
    )


def _partir(texto, limite=LIMITE_TEXTO):
    """Quebra em pedaços SEM cortar uma linha no meio -- um contrato partido
    entre duas mensagens é um contrato que ninguém consegue copiar."""
    if len(texto) <= limite:
        return [texto]
    partes, atual = [], []
    tamanho = 0
    for linha in texto.split('\n'):
        if tamanho + len(linha) + 1 > limite and atual:
            partes.append('\n'.join(atual))
            atual, tamanho = [], 0
        atual.append(linha)
        tamanho += len(linha) + 1
    if atual:
        partes.append('\n'.join(atual))
    return partes


# --------------------------------------------------------------------------
# geração
# --------------------------------------------------------------------------
def montar_dados(reparos_avaliados, os_abertas, com_autenticador=True):
    """A lista pronta, já com o status de conexão de cada contrato.

    Uma consulta ao Autenticador só, cobrindo as duas regiões: ela leva ~35s e
    depende da VPN, então fazer uma por região dobraria o custo para responder
    a mesma pergunta.
    """
    dados = garantias_lista.montar(reparos_avaliados, os_abertas)

    contratos = sorted({c for c in garantias_lista.todos_os_contratos(dados)
                        if c and c != 'N/D'})
    if com_autenticador and contratos:
        try:
            df_status, erro = consultar_autenticador_status(contratos)
            if erro:
                logger.warning("Autenticador indisponível para a lista de garantias: %s", erro)
            else:
                status = {}
                for _, linha in df_status.iterrows():
                    contrato = str(linha.get('CONTRATO', '')).strip()
                    if contrato:
                        status[contrato] = str(linha.get('STATUS', '')).strip().upper()
                # Remonta com o status em mãos. Custa uma segunda passada por
                # uma lista de dezenas de itens -- barato perto de espalhar o
                # status por dentro da montagem e ter duas verdades sobre a
                # ordem das linhas.
                dados = garantias_lista.montar(
                    reparos_avaliados, os_abertas, status_por_contrato=status
                )
        except Exception:
            logger.exception(
                "Falha ao consultar o Autenticador para a lista de garantias. "
                "A lista vai sem o status de conexão."
            )
    return dados


def gerar_e_enviar(reparos_avaliados, os_abertas, carimbo_varredura=None,
                   com_autenticador=True, regioes=None):
    """Gera e manda a lista de cada região para o grupo dela.

    Devolve o número de regiões enviadas com sucesso.
    """
    with _lock_envio:
        if carimbo_varredura is None:
            # Antes da primeira varredura completa o conjunto de O.S. abertas
            # está vazio -- e uma lista vazia no grupo se lê como "nenhuma
            # garantia pendente", que é o oposto de "ainda não sei".
            logger.info(
                "Lista de garantias adiada: nenhuma varredura completa do CAMPO ainda."
            )
            return 0

        dados = montar_dados(reparos_avaliados, os_abertas, com_autenticador=com_autenticador)

        lido_em = carimbo_varredura.strftime('%H:%M')
        enviadas = 0
        alvos = regioes or list(garantias_lista.REGIOES.keys())

        for chave in alvos:
            regiao = next((r for r in dados['regioes'] if r['chave'] == chave), None)
            if regiao is None:
                logger.warning("Região %r não existe na lista de garantias.", chave)
                continue

            # Região sem garantia nenhuma manda TEXTO, não imagem: uma tabela
            # sem linhas é uma imagem de nada, e pesa o mesmo no grupo.
            #
            # Mas manda -- não pode simplesmente calar. No grupo, mensagem que
            # não chega não se distingue de bot parado, e é justamente quando
            # não há pendência que alguém precisa poder confiar que não há
            # mesmo. Daí o horário da conferência ir no texto: é o que separa
            # "conferi agora e está limpo" de "faz três horas que não sei".
            if regiao['total'] == 0:
                if enviar_texto(
                    f"🛠️ *GARANTIAS EM ABERTO — {regiao['nome']}*\n"
                    f"✅ Nenhuma garantia em aberto.\n"
                    f"_Conferido às {dados['gerado_em']} · CAMPO lido às {lido_em}_",
                    chave,
                ):
                    enviadas += 1
                continue

            try:
                caminho = garantias_render.gerar_imagem_garantias(
                    dados, chave, pasta_saida=PASTA_SAIDA
                )
            except Exception:
                logger.exception("Falha ao gerar a imagem de garantias de %s.", chave)
                continue

            resumo = f"{regiao['total']} contrato(s)"
            if regiao['offline']:
                resumo += f", {regiao['offline']} offline"

            legenda = (
                f"🛠️ *GARANTIAS EM ABERTO — {regiao['nome']}*\n"
                f"{resumo}"
                f"\n_CAMPO lido às {lido_em} · lista de {dados['gerado_em']}_"
            )

            if not enviar_imagem(caminho, legenda, chave):
                continue
            enviadas += 1

            texto = garantias_lista.texto_contratos(dados, chave)
            if texto:
                cabecalho = f"📋 *Contratos — {regiao['nome']}*\n\n"
                for i, parte in enumerate(_partir(cabecalho + texto)):
                    if i:
                        # Sem pausa, as partes chegam fora de ordem no grupo.
                        time.sleep(1.0)
                    enviar_texto(parte, chave)

        logger.info(
            "Lista de garantias enviada para %d de %d região(ões). Total em aberto: %d.",
            enviadas, len(alvos), dados['total']
        )
        return enviadas


# --------------------------------------------------------------------------
# agendador
# --------------------------------------------------------------------------
def _proximo_horario(agora):
    """A próxima hora cheia dentro da janela HORA_INICIO..HORA_FIM."""
    candidato = (agora + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if candidato.hour < HORA_INICIO:
        return candidato.replace(hour=HORA_INICIO)
    if candidato.hour > HORA_FIM:
        # Passou das 19h: só amanhã às 7h.
        return (candidato + timedelta(days=1)).replace(hour=HORA_INICIO)
    return candidato


def thread_agendador_garantias(obter_estado):
    """Roda em background: manda a lista na hora cheia, das 7h às 19h.

    obter_estado: `f() -> (reparos_avaliados, os_abertas, carimbo)`. Recebido
    como função, e não como valor, porque a thread vive o dia inteiro e o que
    interessa é o estado NA HORA do envio, não o de quando ela subiu.

    Não dispara ao subir de propósito: o serviço reinicia várias vezes ao dia
    (bot, VPN, máquina), e um disparo por reinício encheria os grupos de listas
    repetidas fora de hora. Quem quiser uma agora tem o comando.
    """
    if not GARANTIAS_ENVIO_ATIVO:
        logger.info("Envio automático da lista de garantias desligado (GARANTIAS_ENVIO_ATIVO=0).")
        return

    logger.info(
        "Agendador da lista de garantias iniciado: hora cheia, das %dh às %dh.",
        HORA_INICIO, HORA_FIM
    )

    while True:
        agora = datetime.now()
        alvo = _proximo_horario(agora)
        espera = (alvo - agora).total_seconds()
        logger.info("Próxima lista de garantias: %s (em %.0f min).",
                    alvo.strftime('%d/%m %H:%M'), espera / 60)

        # Dorme em fatias em vez de uma vez só: numa espera de 12h (após as
        # 19h), um relógio corrigido ou uma máquina que suspendeu fariam a
        # thread acordar muito depois da hora. Em fatias, o alvo é reconferido.
        while True:
            agora = datetime.now()
            restante = (alvo - agora).total_seconds()
            if restante <= 0:
                break
            time.sleep(min(restante, 300))

        try:
            reparos_avaliados, os_abertas, carimbo = obter_estado()
            gerar_e_enviar(reparos_avaliados, os_abertas, carimbo_varredura=carimbo)
        except Exception:
            logger.exception("Falha no envio agendado da lista de garantias.")

        # Garante que não dispare duas vezes na mesma hora cheia se o envio
        # inteiro levar menos de um minuto.
        time.sleep(61)
