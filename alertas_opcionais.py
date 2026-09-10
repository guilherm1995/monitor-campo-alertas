# ================= ALERTAS OPCIONAIS: Upgrade e Mudança de cômodo =================
#
# Os dois tipos que o bot vê há tempos no backlog, mas nunca avisou um a um:
# UPGRADE (UP02) e MUDANÇA DE CÔMODO (ES15). O alerta individual deles é
# LIGADO PELO GRUPO, com o comando /alertas, e por região.
#
# POR QUE LIGADO E DESLIGADO, E NÃO SEMPRE LIGADO
# -----------------------------------------------
# Entrante de CAPEX avisa sozinho porque cliente novo entrando é a coisa que a
# operação persegue o dia inteiro. Upgrade e mudança de cômodo não são isso:
# são serviço de cliente que já existe, e o volume varia. Ligados o tempo todo
# em época cheia, viram ruído -- e alerta que vira ruído estraga também os
# outros, porque o grupo passa a ler tudo por cima.
#
# Então quem decide é a coordenação, no momento em que ela quer olhar. E
# decide POR REGIÃO, porque litoral e RJ têm equipes e volumes diferentes:
# ligar no RJ não pode encher o litoral.
#
# A ESCOLHA SOBREVIVE AO REINÍCIO
# -------------------------------
# O estado mora em disco (dados/alertas_opcionais.json). Se ficasse só na
# memória, todo restart do serviço desligaria os alertas em silêncio -- e o
# silêncio é exatamente o que ninguém percebe. Quem ligou continuaria achando
# que está ligado.
#
# Este módulo NÃO importa o bot_campo_monitoramento, igual aos backlog_*.py: ele
# recebe as listas de unidades por parâmetro. Serve para não criar import
# circular e para dar para ensaiar a regra inteira sem subir o bot.

import json
import logging
import os
import time
import unicodedata

logger = logging.getLogger(__name__)

base_dir = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_ESTADO = os.path.join(base_dir, 'dados', 'alertas_opcionais.json')

# Os códigos de fila do CAMPO que este comando liga, e o nome que aparece no
# alerta. Confirmados na produção em 09/09/2026: UP02 chega com o nome interno
# "UPGRADE NÃO LÓGICO", e é o único upgrade que o CAMPO devolve para as nossas
# unidades -- não existe uma segunda fila de "upgrade lógico" na varredura.
#
# ANTES DE ACRESCENTAR UM CÓDIGO AQUI: confirme que o CAMPO realmente o emite, e
# que ele está na `filas_alvo` da busca (bot_campo_monitoramento). Um código que
# a busca não pede nunca chega, e um código que o CAMPO não emite nunca casa --
# nos dois casos o alerta simplesmente não sai, sem erro nenhum no log.
CODIGOS = {
    'UP02': 'Upgrade',
    'ES15': 'Mudança de cômodo',
}

# As escolhas que o operador tem. 'nenhuma' é o estado de fábrica: até alguém
# pedir, nada muda no grupo.
LITORAL = 'litoral'
RJ = 'rj'
AMBAS = 'ambas'
NENHUMA = 'nenhuma'

NOMES_DAS_REGIOES = {
    LITORAL: 'Litoral Norte',
    RJ: 'Sul RJ',
    AMBAS: 'as duas regiões',
    NENHUMA: 'desligado',
}

# O que o operador pode digitar para cada escolha. O número existe porque o
# menu é numerado; as palavras existem porque metade das pessoas responde
# "litoral" em vez de "1", e as duas formas têm de funcionar.
FORMAS = {
    LITORAL: ('1', 'litoral', 'litoral norte', 'sp', 'litoral sp', 'norte'),
    RJ: ('2', 'rj', 'sul rj', 'rio', 'sul do rio'),
    AMBAS: ('3', 'ambas', 'as duas', 'as duas regioes', 'duas', 'todas',
            'tudo', 'ambos'),
    NENHUMA: ('0', 'nenhuma', 'desligar', 'desliga', 'off', 'nao', 'parar',
              'cancelar'),
}


def achatar(texto):
    """Minúscula, sem acento, sem espaço sobrando. Igual ao resto do bot."""
    bruto = unicodedata.normalize('NFD', str(texto or '').strip().lower())
    sem_acento = ''.join(c for c in bruto if unicodedata.category(c) != 'Mn')
    return ' '.join(sem_acento.split())


def interpretar(texto):
    """O que o operador digitou -> a região, ou None se não for uma escolha.

    None é resposta legítima e não é erro: quem digitou outra coisa no meio da
    espera está falando de outro assunto, e quem chama decide o que fazer.
    """
    achatado = achatar(texto)
    if not achatado:
        return None
    for regiao, formas in FORMAS.items():
        if achatado in formas:
            return regiao
    return None


def estado_vazio():
    return {'regiao': NENHUMA, 'ativado_em': None, 'por': None}


def carregar(caminho=None):
    """A escolha em vigor. Nunca levanta: sem arquivo, o alerta fica desligado.

    Desligado é o padrão seguro dos dois lados -- um arquivo corrompido não
    pode nem calar um alerta que alguém ligou sem avisar ninguém, nem começar a
    despejar mensagem no grupo por conta própria. Entre os dois, o silêncio é
    o que a operação percebe na hora em que for procurar o alerta.
    """
    caminho = caminho or ARQUIVO_ESTADO
    if not os.path.exists(caminho):
        return estado_vazio()
    try:
        with open(caminho, 'r', encoding='utf-8') as f:
            bruto = json.load(f)
    except Exception as e:
        # `warning` e nao `exception`: a mensagem do JSON ja diz a linha e a
        # coluna do estrago, e a pilha aqui so encheria o log de uma falha que
        # se resolve mandando /alertas de novo.
        logger.warning("Falha ao ler %s (%s) — alertas opcionais ficam "
                       "desligados.", caminho, e)
        return estado_vazio()

    if not isinstance(bruto, dict):
        return estado_vazio()
    estado = estado_vazio()
    regiao = bruto.get('regiao')
    if regiao in NOMES_DAS_REGIOES:
        estado['regiao'] = regiao
    try:
        estado['ativado_em'] = float(bruto['ativado_em'])
    except (KeyError, TypeError, ValueError):
        estado['ativado_em'] = None
    por = bruto.get('por')
    estado['por'] = str(por) if por else None
    return estado


def gravar(regiao, por=None, agora=None, caminho=None):
    """Guarda a escolha. Devolve o estado gravado.

    `ativado_em` é o carimbo da hora da escolha, e ele não é enfeite: é o que
    impede a enxurrada. Ver `deve_alertar`.
    """
    if regiao not in NOMES_DAS_REGIOES:
        raise ValueError('região desconhecida: %r' % (regiao,))
    caminho = caminho or ARQUIVO_ESTADO
    estado = {
        'regiao': regiao,
        'ativado_em': float(agora if agora is not None else time.time()),
        'por': str(por) if por else None,
    }
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.isdir(pasta):
        os.makedirs(pasta)

    # Escrita atômica: o arquivo é lido a cada varredura, e um JSON pela metade
    # seria lido como "desligado" -- o alerta sumiria sem ninguém saber por quê.
    temporario = caminho + '.tmp'
    with open(temporario, 'w', encoding='utf-8') as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)
    os.replace(temporario, caminho)
    logger.info("Alertas de %s: %s (por %s).",
                ' e '.join(CODIGOS.values()), NOMES_DAS_REGIOES[regiao],
                estado['por'] or 'alguém no grupo')
    return estado


def unidade_na_regiao(unidade, estado, unidades_litoral, unidades_rj):
    """A unidade está na região que foi ligada?"""
    regiao = (estado or {}).get('regiao', NENHUMA)
    if regiao == NENHUMA:
        return False
    unidade = str(unidade or '').upper().strip()
    no_litoral = unidade in unidades_litoral
    no_rj = unidade in unidades_rj
    if regiao == AMBAS:
        return no_litoral or no_rj
    if regiao == LITORAL:
        return no_litoral
    return no_rj


def deve_alertar(codigo, unidade, aberto_em, estado, unidades_litoral,
                 unidades_rj):
    """Este chamado vira alerta agora?

    `aberto_em` é a data de abertura do chamado, em segundos (o CAMPO manda em
    milissegundos; quem chama divide).

    A REGRA QUE MAIS IMPORTA AQUI É A ÚLTIMA: só entra chamado aberto DEPOIS
    de alguém ligar o alerta. No dia em que isto foi escrito havia 32 upgrades
    e 18 mudanças de cômodo em aberto -- ligar sem esse corte despejaria 50
    mensagens no grupo de uma vez, e a primeira reação de quem visse aquilo
    seria desligar. O alerta é de ENTRANTE: serve para contar o que chegou
    agora, não para recitar o que já estava lá. Para ver o que já está aberto
    existe o backlog, que é feito para isso.

    Chamado sem data de abertura NÃO alerta. É o mesmo raciocínio: sem data
    não dá para saber se ele é novo, e supor que sim é justamente o caso que
    produz a enxurrada.
    """
    if codigo not in CODIGOS:
        return False
    if not unidade_na_regiao(unidade, estado, unidades_litoral, unidades_rj):
        return False
    ativado_em = (estado or {}).get('ativado_em')
    if ativado_em is None or aberto_em is None:
        return False
    return float(aberto_em) >= float(ativado_em)


def descricao_do_estado(estado, agora=None):
    """Uma linha dizendo como o alerta está agora, para o grupo ler."""
    estado = estado or estado_vazio()
    regiao = estado.get('regiao', NENHUMA)
    if regiao == NENHUMA:
        return '🔕 Agora: *desligado*'
    quando = ''
    ativado_em = estado.get('ativado_em')
    if ativado_em:
        from datetime import datetime
        quando = datetime.fromtimestamp(ativado_em).strftime(' desde %d/%m %H:%M')
    quem = estado.get('por')
    autor = (' (por %s)' % quem) if quem else ''
    return '🔔 Agora: *%s*%s%s' % (NOMES_DAS_REGIOES[regiao], quando, autor)


def texto_menu(estado=None):
    """O menu que o grupo recebe ao digitar /alertas."""
    tipos = ' e '.join(CODIGOS.values())
    return '\n'.join([
        '🔔 *Alertas de %s*' % tipos,
        '',
        descricao_do_estado(estado),
        '',
        'Responda com o número da região:',
        '*1* — Litoral Norte',
        '*2* — Sul RJ',
        '*3* — as duas regiões',
        '*0* — desligar',
        '',
        '_Vale para os chamados que entrarem a partir de agora. '
        'O que já está aberto continua no backlog._',
    ])


def texto_confirmacao(estado):
    """A resposta depois da escolha."""
    tipos = ' e '.join(CODIGOS.values())
    regiao = (estado or {}).get('regiao', NENHUMA)
    if regiao == NENHUMA:
        return ('🔕 Alertas de *%s* desligados.\n'
                '_Os dois continuam no backlog, como sempre._' % tipos)
    return ('🔔 Alertas de *%s* ligados para *%s*.\n'
            '_Só os chamados que entrarem a partir de agora. Para desligar, '
            'mande */alertas* e responda 0._'
            % (tipos, NOMES_DAS_REGIOES[regiao]))
