# -*- coding: utf-8 -*-
"""Confere PARA ONDE cada resposta do WhatsApp volta. Sem rede, sem WhatsApp.

Roda com `python teste_comandos_pv.py`: sai com 0 quando cada resposta volta
para onde o comando veio, e com 1 quando alguma se perde.

Substitui a sessao HTTP que fala com o Node por uma que entrega um lote de
mensagens fabricado, e substitui o envio por um gravador. Depois olha o que foi
mandado e para qual destino.

O que este teste protege: a mudanca de hoje faz o privado cair no MESMO
despacho de comandos do grupo. Se o `destino` se perder em algum ramo, a
resposta de uma conversa privada vai parar no grupo da operacao inteira -- e
isso nao levanta erro nenhum, so aparece no grupo errado.
"""
import json
import os
import sys
import threading
import time

os.environ.setdefault('BOT_PV_LIBERADOS', '5512999999999')

import assistente_ia
import bot_campo_monitoramento as b

PV = 'operador@provedor.example'
GRUPO_PRINCIPAL = 'grupo-principal@g.us'
GRUPO_REGIAO = 'grupo-litoral@g.us'

enviados = []
disparos = []


def gravar_envio(mensagem, reenfileirar_se_falhar=False, destino=None):
    enviados.append((destino, str(mensagem)[:60]))
    return True


class ThreadFalsa:
    """Registra o que teria rodado em vez de rodar de verdade."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.alvo = getattr(target, '__name__', str(target))
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        disparos.append((self.alvo, self.kwargs))


class SessaoFalsa:
    def __init__(self, lote):
        self.lote = lote
        self.entregue = False

    def get(self, url, timeout=None):
        class R:
            status_code = 200

            def __init__(self, dados):
                self._d = dados

            def json(self):
                return self._d
        if self.entregue:
            # Encerra ESTA thread de escuta. Sem isso, cada caso deixava um
            # ouvinte vivo compartilhando os mesmos gravadores, e os casos
            # seguintes liam o que o ouvinte anterior gravou -- o teste
            # acusava falha onde o codigo estava certo.
            raise SystemExit
        self.entregue = True
        return R({'mensagens': self.lote})


def rodar(lote):
    del enviados[:], disparos[:]
    # Cada caso comeca do zero. Sem isto, o /autenticador de um caso deixava a
    # conversa "esperando um contrato", e o texto solto do caso seguinte era
    # lido como numero de contrato -- comportamento certo do bot, mas que
    # tornava o teste dependente da ordem.
    b.AGUARDANDO_CONTRATO_AUTENTICADOR_WHATSAPP.clear()
    b.AGUARDANDO_RESPOSTA_BOT_WHATSAPP.clear()
    b._SESSAO_WHATSAPP = SessaoFalsa(lote)
    b.enviar_alerta_whatsapp_grupo = gravar_envio
    b.threading = type('T', (), {'Thread': ThreadFalsa,
                                 'Event': threading.Event,
                                 'Lock': threading.Lock})
    alvo = threading.Thread(target=b.escutar_comandos_whatsapp, daemon=True)
    alvo.start()
    alvo.join(6)
    if alvo.is_alive():
        raise RuntimeError('a thread de escuta nao encerrou -- teste invalido')
    return list(enviados), list(disparos)


def msg(texto, privado=False, principal=False, conversa=None, participante=None):
    return {'participante': participante or (PV if privado else 'alguem@x'),
            'texto': texto, 'privado': privado, 'principal': principal,
            'conversa': conversa or (PV if privado
                                     else (GRUPO_PRINCIPAL if principal
                                           else GRUPO_REGIAO))}


erros = []


def conferir(nome, lote, esperado_destino, esperado_alvo=None,
             esperado_kwargs=None):
    enviados_, disparos_ = rodar(lote)
    destinos = {d for d, _ in enviados_}
    if destinos != {esperado_destino}:
        erros.append('%s: respondeu para %r, esperava %r (%s)'
                     % (nome, destinos, {esperado_destino},
                        [t for _, t in enviados_]))
    if esperado_alvo:
        alvos = [a for a, _ in disparos_]
        if esperado_alvo not in alvos:
            erros.append('%s: nao disparou %s (disparou %s)'
                         % (nome, esperado_alvo, alvos))
        else:
            kw = dict(disparos_[alvos.index(esperado_alvo)][1])
            for chave, valor in (esperado_kwargs or {}).items():
                if kw.get(chave) != valor:
                    erros.append('%s: %s=%r, esperava %r'
                                 % (nome, chave, kw.get(chave), valor))
    print('%-42s destino=%r  disparos=%s'
          % (nome, list(destinos), [a for a, _ in disparos_]))


# ---- comando no PRIVADO: responde no privado, nunca no grupo
conferir('/comandos no privado', [msg('/comandos', privado=True)], PV)
conferir('/status no privado', [msg('/status', privado=True)], PV)
conferir('/carga no privado', [msg('/carga', privado=True)], PV,
         'gerar_e_enviar_carga', {'destino_whatsapp': PV})
conferir('/improdutivas no privado', [msg('/improdutivas', privado=True)], PV,
         'responder_improdutivas', {'destino': PV})
conferir('/risco no privado', [msg('/risco', privado=True)], PV,
         'responder_area_risco', {'destino': PV})
conferir('/autenticador no privado', [msg('/autenticador', privado=True)], PV)
conferir('pergunta solta no privado', [msg('quantas O.S. em CGT?', privado=True)],
         PV, 'responder_pergunta_bot', {'destino': PV})
# O /bot so dispara a IA quando ha chave configurada. Sem chave -- que e o caso
# nesta maquina -- ele avisa isso, e o que este teste mede e que o aviso vai
# para o PRIVADO, nao para o grupo.
conferir('/bot no privado', [msg('/bot e a VRD?', privado=True)], PV,
         'responder_pergunta_bot' if assistente_ia.disponivel() else None,
         {'destino': PV})

# ---- o mesmo comando no GRUPO principal: destino None (grupo de sempre)
conferir('/comandos no grupo principal',
         [msg('/comandos', principal=True)], None)
conferir('/carga no grupo principal', [msg('/carga', principal=True)], None,
         'gerar_e_enviar_carga', {'destino_whatsapp': None})
conferir('/improdutivas no grupo principal',
         [msg('/improdutivas', principal=True)], None,
         'responder_improdutivas', {'destino': None})

# ---- grupo de regiao: so /bot e /carga, e sai no proprio grupo
conferir('/carga no grupo de regiao', [msg('/carga')], GRUPO_REGIAO,
         'gerar_e_enviar_carga', {'destino_whatsapp': GRUPO_REGIAO})

enviados_, disparos_ = rodar([msg('/reiniciar')])
if enviados_ or disparos_:
    erros.append('grupo de regiao aceitou /reiniciar: %s %s'
                 % (enviados_, disparos_))
else:
    print('%-42s ignorado, como tem de ser' % '/reiniciar no grupo de regiao')

# ---- privado nao liberado: silencio
enviados_, disparos_ = rodar([msg('/comandos', privado=True,
                                  conversa='operador@provedor.example')])
if enviados_ or disparos_:
    erros.append('privado NAO liberado foi atendido: %s %s'
                 % (enviados_, disparos_))
else:
    print('%-42s ignorado, como tem de ser' % 'privado nao liberado')

print()
if erros:
    print('ERROS:')
    for e in erros:
        print(' -', e)
else:
    print('CONFERE: cada resposta volta para onde o comando veio')
sys.exit(1 if erros else 0)
