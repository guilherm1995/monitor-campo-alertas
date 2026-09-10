# -*- coding: utf-8 -*-
"""Deixar a base fresca ANTES de responder, e não depois de alguém reclamar.

POR QUE ISTO EXISTE
-------------------
O "OFS GERAL.csv" é refeito de hora em hora por um serviço à parte. Isso é o
bastante para painel, mas não é o bastante para pergunta: rota de técnico e
backlog mudam o tempo todo, e uma resposta montada sobre um arquivo de 50
minutos atrás está errada de um jeito particularmente ruim -- ela é PLAUSÍVEL.
Quem lê não tem como desconfiar, porque o número tem a cara de um número certo.

Regra da operação, dita em 31/08/2026: *pergunta sobre rota de técnico, sobre
backlog ou sobre a carga do dia seguinte atualiza a base antes de responder.*

O QUE ISTO NÃO FAZ
------------------
Não baixa a cada pergunta. Refazer a janela inteira do OFS são 72 pedidos e uns
8 segundos; fazer isso três vezes seguidas porque alguém mandou três perguntas
seria castigo para o OFS e espera para quem perguntou. Se o arquivo tem menos
de IDADE_MAXIMA_MIN minutos, ele já é fresco e a função devolve isso sem tocar
na rede.

E não deixa a pergunta morrer quando a atualização falha. Sessão vencida no OFS
é rotina (o login pede código e expira), e a base de 40 minutos atrás ainda
responde quase tudo. Nesse caso a resposta sai com o aviso de que a base é de
tal hora -- que é bem diferente de sair calada.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# A partir de quantos minutos vale a pena ir na rede. Quinze porque é metade do
# intervalo em que a operação percebe mudança de rota na prática, e porque duas
# perguntas seguidas sobre o mesmo técnico não devem custar dois downloads.
IDADE_MAXIMA_MIN = float(os.environ.get('BASES_IDADE_MAXIMA_MIN', '15'))

# A chave que DESLIGA a atualização. Existe para a avaliação: a bateria roda
# sobre um recorte congelado do OFS, e sair na rede ali substituiria a base de
# ensaio pela de produção no meio do teste -- além de gastar 72 pedidos por
# caso. Em produção ela nunca é definida.
ATUALIZAR = os.environ.get('BASES_ATUALIZAR', '1') != '0'

# Uma atualização de cada vez, no processo inteiro. Duas perguntas que chegam
# juntas nos grupos rodariam duas atualizações concorrentes sobre o MESMO
# arquivo de saída -- e a segunda a gravar deixaria o arquivo pela metade para
# quem estivesse lendo. A trava também faz a segunda pergunta aproveitar o
# download da primeira em vez de refazê-lo.
_TRAVA = threading.Lock()
_ULTIMA_FALHA = {'quando': 0.0, 'motivo': None}

# Depois de uma falha, quanto tempo esperar antes de tentar de novo. Sem isto,
# uma sessão vencida faria CADA pergunta esperar o timeout do OFS antes de
# responder -- transformando um aviso em lentidão para todo mundo.
ESPERA_APOS_FALHA_SEG = float(os.environ.get('BASES_ESPERA_APOS_FALHA_SEG', '300'))


def idade_em_minutos(caminho):
    """Há quantos minutos o arquivo foi gravado. None se ele não existe."""
    try:
        return (time.time() - os.path.getmtime(caminho)) / 60.0
    except OSError:
        return None


def _resultado(atualizou, caminho, motivo=None):
    idade = idade_em_minutos(caminho)
    return {
        'atualizou': atualizou,
        'arquivo': caminho,
        'idade_min': idade,
        'gravado_em': (datetime.fromtimestamp(os.path.getmtime(caminho))
                       if idade is not None else None),
        'motivo': motivo,
    }


def garantir_ofs_fresco(idade_maxima_min=None, forcar=False):
    """Atualiza o OFS GERAL se ele estiver velho. Nunca levanta exceção.

    Devolve um dicionário com o que aconteceu: se atualizou, de quando é o
    arquivo agora, e o motivo quando não deu. Quem chama usa o motivo para
    avisar junto da resposta -- nunca para esconder a resposta.
    """
    try:
        import ofs_extracao
    except Exception:
        logger.exception('Não consegui importar o ofs_extracao para atualizar a base.')
        return {'atualizou': False, 'arquivo': None, 'idade_min': None,
                'gravado_em': None, 'motivo': 'módulo de extração indisponível'}

    caminho = str(ofs_extracao.ARQUIVO_OFS_GERAL)
    if not ATUALIZAR:
        return _resultado(False, caminho, motivo='já estava fresca')

    limite = IDADE_MAXIMA_MIN if idade_maxima_min is None else idade_maxima_min

    idade = idade_em_minutos(caminho)
    if not forcar and idade is not None and idade < limite:
        return _resultado(False, caminho, motivo='já estava fresca')

    agora = time.time()
    if (not forcar and _ULTIMA_FALHA['motivo']
            and (agora - _ULTIMA_FALHA['quando']) < ESPERA_APOS_FALHA_SEG):
        return _resultado(False, caminho, motivo=_ULTIMA_FALHA['motivo'])

    with _TRAVA:
        # Outra thread pode ter atualizado enquanto esta esperava a trava.
        idade = idade_em_minutos(caminho)
        if not forcar and idade is not None and idade < limite:
            return _resultado(False, caminho, motivo='já estava fresca')

        inicio = time.time()
        try:
            _, info = ofs_extracao.gravar_ofs_geral()
        except ofs_extracao.SessaoVencida:
            _ULTIMA_FALHA.update({'quando': time.time(),
                                  'motivo': 'a sessão do OFS venceu'})
            logger.warning('Sessão do OFS vencida ao atualizar a base para responder.')
            return _resultado(False, caminho, motivo='a sessão do OFS venceu')
        except Exception as erro:
            _ULTIMA_FALHA.update({'quando': time.time(),
                                  'motivo': 'a atualização do OFS falhou'})
            logger.exception('Falha ao atualizar o OFS GERAL antes de responder: %s', erro)
            return _resultado(False, caminho, motivo='a atualização do OFS falhou')

        _ULTIMA_FALHA.update({'quando': 0.0, 'motivo': None})
        logger.info('Base do OFS atualizada antes de responder: %s atividades, %s dias, %.1fs.',
                    info.get('atividades'), info.get('dias'), time.time() - inicio)
        return _resultado(True, caminho)


def aviso_de_frescor(resultado):
    """Uma linha para colar no fim da resposta, ou vazio quando não há o que dizer.

    Só fala quando tem notícia: base recém-atualizada não merece uma linha, e
    base velha merece. O silêncio aqui é a informação -- se não apareceu aviso,
    a base está fresca.
    """
    if not resultado or resultado.get('atualizou'):
        return ''
    motivo = resultado.get('motivo')
    if motivo in (None, 'já estava fresca'):
        return ''
    gravado = resultado.get('gravado_em')
    quando = gravado.strftime('%d/%m às %H:%M') if gravado else 'data desconhecida'
    return ('⚠️ Não consegui atualizar a base agora (%s). Os números abaixo são '
            'da última carga, de %s.' % (motivo, quando))
