# ============ PAINEL DE RESULTADOS NO AUTOMÁTICO (20/08/2026) ============
#
# De hora em hora, das 7h às 22h: baixa a extração do OFS, gera as telas do
# painel e manda as três no grupo de resultados.
#
# O que isto substitui: alguém abrindo o OFS, rodando a extensão de exportação,
# salvando o CSV, subindo o arquivo em painel.example.com/painel e mandando as
# imagens no grupo -- uma vez por hora, o dia inteiro. Às vezes o horário
# passava e o grupo ficava sem o painel.
#
# Três decisões que valem explicação:
#
# 1. O motor do painel NÃO é reimplementado aqui. As telas saem do mesmo
#    `painel.gerar` que o botão do site usa, rodado num subprocesso -- bot e
#    site dividem o venv. Duas implementações da mesma conta dariam dois
#    números, como já aconteceu com a lista de garantias. E o subprocesso
#    morre no fim, então o Chromium do render devolve a memória.
#
# 2. Sessão vencida do OFS responde 200 OK com corpo vazio, nunca um erro (ver
#    ofs_extracao). Aqui isso vira aviso no grupo principal, porque o modo de
#    falha natural deste caminho é o silêncio: sem aviso, o painel simplesmente
#    pararia de chegar e ninguém saberia por quê.
#
# 3. O aviso não se repete de hora em hora. Um lembrete por hora vira ruído e
#    ruído se ignora -- ver INTERVALO_AVISO_SEG.

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import ofs_extracao
from garantias_envio import enviar_arquivo, enviar_imagem, enviar_texto

logger = logging.getLogger(__name__)

# Onde mora o site (o motor do painel e a pasta saida/). No servidor é
# /opt/operacional/site; no Windows, a pasta do porte.
SITE_DIR = os.environ.get('OPERACIONAL_SITE_DIR', '/opt/operacional/site')

# Grupo que recebe as telas: "Resultados Operacional", o mesmo onde a operação
# mandava à mão. Rodou no grupo de teste (000000000000000000@g.us) antes de vir
# para cá -- é para lá que se volta com PAINEL_RESULTADOS_DESTINO se algo sair
# torto, sem precisar mexer no código.
#
# O JID vem da escuta do serviço do WhatsApp: uma mensagem enviada de dentro do
# grupo prova qual é. Nome de grupo não prova nada -- dois grupos podem ter o
# mesmo nome, e mandar o painel no grupo errado é pior do que não mandar.
DESTINO = os.environ.get('PAINEL_RESULTADOS_DESTINO', '000000000000000000@g.us')

# Grupo que recebe a EXTRAÇÃO CRUA (o CSV do OFS), de hora em hora: "Relatório
# OFS HxH". É outro público e outro uso -- quem quer o número olha a imagem no
# grupo de resultados; quem quer conferir linha a linha abre o CSV aqui. Vazio
# desliga o envio do arquivo sem mexer no resto.
DESTINO_EXTRACAO = os.environ.get('PAINEL_EXTRACAO_DESTINO', '000000000000000000@g.us')

# A janela de envio é o expediente: 7h às 20h. As duas pontas não são só o
# começo e o fim da janela -- são horários com nome, e mandam telas diferentes
# (ver `_telas_do_ciclo`). Mudar estes números muda o que sai na abertura e no
# fechamento, não apenas quando o envio começa e para.
HORA_INICIO = int(os.environ.get('PAINEL_RESULTADOS_HORA_INICIO', '7'))
HORA_FIM = int(os.environ.get('PAINEL_RESULTADOS_HORA_FIM', '20'))

# Alguns minutos depois da hora cheia: a extração pega o que já foi fechado na
# hora anterior, e o envio não briga com o da lista de garantias (hora cheia).
MINUTO_ENVIO = int(os.environ.get('PAINEL_RESULTADOS_MINUTO', '3'))

PAINEL_RESULTADOS_ATIVO = os.environ.get('PAINEL_RESULTADOS_ATIVO', '1') != '0'

# Quanto tempo esperar o subprocesso do painel. Ele abre Chromium para
# renderizar 4 telas; 2,4s numa máquina folgada, mas o servidor às vezes está
# gerando backlog ao mesmo tempo.
TIMEOUT_GERACAO_SEG = 300

# De quanto em quanto tempo repetir o aviso enquanto a sessão do OFS continua
# caída. Três horas: lembra sem virar paisagem.
INTERVALO_AVISO_SEG = 3 * 60 * 60

# A capa. Se ela vier zerada, NADA vai: as outras duas são recortes do dia que
# ela resume, e mandar recorte sem o quadro geral é pé sem cabeça.
TELA_CAPA = '01_painel_acompanhamento'
TELA_PRAZOS = '02_gestao_prazos'
TELA_TEMPO = '03_tempo_execucao'
TELA_CARGA = '04_carga_servicos'


def _telas_do_ciclo(agora, com_agenda=False):
    """Quais telas vão neste horário, e com que legenda.

    De hora em hora vai o de sempre. Nas duas pontas do expediente o dia ganha
    um contorno, que é como a operação já falava dele à mão:

    - ABERTURA: a agenda do dia -- o que está marcado -- antes de qualquer
      número de execução. O fechamento da véspera vai junto, mas por outro
      caminho (`_enviar_fechamento_ontem`), porque exige a extração de OUTRO
      dia. Quem decide se a agenda entra é `com_agenda`, não o relógio: até
      26/08/2026 a abertura era amarrada às 7h em ponto, e no dia em que a
      sessão do OFS estava vencida às 7h e às 8h ela simplesmente não saiu.
      Agora a abertura é uma vez por DIA, no primeiro ciclo que der certo.
    - FECHAMENTO (HORA_FIM): a mesma capa de sempre, com o nome que ela tem às
      20h: fechamento parcial. Parcial porque serviço lançado depois do
      expediente ainda entra, e o número definitivo só existe no dia seguinte.
    """
    dia = f'{agora:%d/%m}'
    hora = agora.hour
    telas = []
    if com_agenda:
        telas.append((TELA_CARGA, f'Agenda {dia} {hora}h'))
    if hora == HORA_FIM:
        telas.append((TELA_CAPA, f'Fechamento Parcial {dia} {hora}h'))
    else:
        telas.append((TELA_CAPA, f'Painel de acompanhamento {hora}h'))
    telas.append((TELA_PRAZOS, 'Acompanhamento de prazos'))
    telas.append((TELA_TEMPO, 'Tempo de execução'))
    return telas


def _tem_conteudo(contagens, nome):
    """A tela tem linha para mostrar?

    Ausência de contagem vale como "tem": um site mais antigo, sem o campo
    `contagens`, não pode virar motivo para o painel parar de ser enviado.
    Silêncio por falta de informação é o pior desfecho possível aqui.
    """
    return int(contagens.get(nome, 1)) > 0

_lock_ciclo = threading.Lock()
_estado_aviso = {'falhando': False, 'ultimo_aviso': 0.0}

# Ciclo que morreu por sessão vencida, esperando cookie novo para ser refeito.
#
# Em 26/08/2026 os ciclos das 07:03 e 08:03 falharam por sessão vencida, o
# operador entrou no OFS às 08:12 e o painel só voltou às 09:03 -- duas horas
# de painel perdidas com a chave já na mão desde as 08:12. Cookie novo é a
# única prova barata de que existe sessão viva AGORA; esperar a próxima hora
# cheia depois disso é perder painel à toa.
_pendente_por_sessao = {'sim': False}

# A abertura do dia (fechamento da véspera + agenda de hoje) sai UMA vez por
# dia, no primeiro ciclo que der certo -- não mais às 7h em ponto.
#
# O registro vai para o disco, e não para uma variável, porque o serviço
# reinicia várias vezes ao dia: em memória, um reinício às 10h faria a abertura
# sair de novo. As duas peças são marcadas separadamente porque saem por
# caminhos diferentes -- se o fechamento for enviado e o ciclo de hoje falhar
# em seguida, a próxima tentativa não pode repetir o fechamento.
ARQUIVO_ABERTURA = os.environ.get(
    'PAINEL_ABERTURA_ARQUIVO',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dados',
                 'painel_abertura.json'))

# Mas "no primeiro ciclo que der certo" precisa de limite: abertura às 19h não
# abre nada. O fechamento da véspera e a agenda do dia são leitura de manhã --
# fora dessa janela deixam de informar e viram só mensagem atrasada no grupo,
# ao lado do painel da tarde, dizendo o que já não interessa.
#
# 7h às 9h, inclusive: 7h é a abertura normal, e 9h dá espaço para o dia em que
# a sessão do OFS só é renovada depois (em 26/08/2026 o primeiro ciclo bom foi
# 09:03). Passou disso, o dia fica sem abertura -- de propósito.
HORA_ABERTURA_INICIO = int(os.environ.get('PAINEL_ABERTURA_HORA_INICIO', '7'))
HORA_ABERTURA_FIM = int(os.environ.get('PAINEL_ABERTURA_HORA_FIM', '9'))


def _abertura_lida():
    try:
        with open(ARQUIVO_ABERTURA, encoding='utf-8') as arquivo:
            return json.load(arquivo)
    except (OSError, ValueError):
        return {}


def dentro_da_janela_de_abertura(agora=None):
    hora = (agora or datetime.now()).hour
    return HORA_ABERTURA_INICIO <= hora <= HORA_ABERTURA_FIM


def abertura_pendente(peca, agora=None):
    """Esta peça da abertura ainda não saiu hoje E ainda dá para mandar?

    As duas condições juntas de propósito. Só "não saiu hoje" faria a abertura
    ficar esperando o dia inteiro e sair às 19h; só a janela faria ela repetir a
    cada ciclo entre 7h e 9h.
    """
    agora = agora or datetime.now()
    if not dentro_da_janela_de_abertura(agora):
        return False
    return _abertura_lida().get(peca) != f'{agora:%Y-%m-%d}'


def marcar_abertura(peca, agora=None):
    hoje = f'{(agora or datetime.now()):%Y-%m-%d}'
    estado = _abertura_lida()
    estado[peca] = hoje
    try:
        os.makedirs(os.path.dirname(ARQUIVO_ABERTURA), exist_ok=True)
        with open(ARQUIVO_ABERTURA, 'w', encoding='utf-8') as arquivo:
            json.dump(estado, arquivo)
    except OSError as falha:
        # Perder o registro custa uma repetição no grupo, não um erro. Mas vale
        # log: repetição no grupo é o tipo de coisa que alguém vai perguntar.
        logger.warning('Não consegui gravar %s: %s',
                       os.path.basename(ARQUIVO_ABERTURA), falha)


def carimbo_cookie():
    """mtime do ofs_cookies.json, ou 0 se não existir. Mudou = login novo."""
    try:
        return os.path.getmtime(ofs_extracao.ARQUIVO_COOKIES)
    except OSError:
        return 0.0


def gerar_telas(caminho_csv):
    """Roda o motor do painel do site sobre o CSV. Devolve o dicionário dele.

    Subprocesso em vez de import: o motor carrega pandas e sobe um Chromium
    para renderizar. Dentro do bot isso ficaria residente para sempre; num
    processo separado, morre junto com ele.
    """
    programa = (
        'import json, sys; '
        'from pathlib import Path; '
        'sys.path.insert(0, sys.argv[1]); '
        'from web.fontes import painel; '
        'print("__RESULTADO__" + json.dumps(painel.gerar(Path(sys.argv[2]))))'
    )
    processo = subprocess.run(
        [sys.executable, '-c', programa, SITE_DIR, str(caminho_csv)],
        cwd=SITE_DIR, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=TIMEOUT_GERACAO_SEG,
    )
    if processo.returncode != 0:
        # A última linha do traceback diz mais do que o traceback inteiro no
        # log do bot, e o traceball completo fica no stderr capturado.
        ultima = (processo.stderr or '').strip().splitlines()
        raise RuntimeError(f'painel.gerar falhou: {ultima[-1] if ultima else "sem saída"}')

    for linha in (processo.stdout or '').splitlines():
        if linha.startswith('__RESULTADO__'):
            return json.loads(linha[len('__RESULTADO__'):])
    raise RuntimeError('painel.gerar não devolveu resultado')


def _caminho_tela(nome):
    return os.path.join(SITE_DIR, 'saida', f'{nome}.png')


def enviar_telas(resultado, destino=None, telas=None, com_agenda=False):
    """Manda as telas no grupo.

    Devolve {'enviadas', 'puladas', 'falhas'}. Pulada e falha são coisas
    diferentes: pulada é a regra funcionando (tela zerada não vai), falha é o
    WhatsApp recusando. Somar as duas num número só faria a regra parecer
    problema e o problema parecer regra.
    """
    destino = destino or DESTINO
    if telas is None:
        # A hora da legenda é a do RELÓGIO, não a do carimbo do painel.
        #
        # O carimbo é o último dado da extração: às 12:03 ele costuma marcar
        # 11:5x, e a legenda saía "Painel de acompanhamento 11h" numa mensagem
        # enviada ao meio-dia -- quem lê no grupo compara com o próprio relógio,
        # não com o rodapé da imagem.
        #
        # E o estrago maior era no fechamento: às 20:03, com carimbo 19:5x, a
        # capa sairia como "Painel de acompanhamento 19h" e o "Fechamento
        # Parcial" simplesmente não apareceria naquele dia.
        telas = _telas_do_ciclo(datetime.now(), com_agenda=com_agenda)

    contagens = resultado.get('contagens') or {}
    placar = {'enviadas': 0, 'puladas': 0, 'falhas': 0}
    for nome, legenda in telas:
        caminho = _caminho_tela(nome)
        if not os.path.isfile(caminho):
            logger.warning('Tela %s não foi gerada.', nome)
            placar['falhas'] += 1
            continue
        if not _tem_conteudo(contagens, nome):
            logger.info('Tela %s está zerada; não vai para o grupo.', nome)
            placar['puladas'] += 1
            continue
        if enviar_imagem(caminho, legenda, destino):
            placar['enviadas'] += 1
        else:
            placar['falhas'] += 1
        time.sleep(1)   # uma atrás da outra, na ordem, como quem manda à mão
    return placar


# As duas mensagens do privado, escritas pela operação em 01/09/2026.
#
# Elas são deliberadamente diferentes das do grupo, e não é enfeite: no grupo o
# aviso divide espaço com dezenas de mensagens por dia e precisa do procedimento
# inteiro junto. No privado ele é para UMA pessoa que vai agir agora, e o que
# funciona ali é curto e humano.
#
# O texto é o da operação. Não mexer sem falar com ela: quem escreveu conhece
# quem lê.
AVISO_PV_CAIU = (
    'Oi OPERADOR!  😭' + chr(10) + chr(10) +
    'A seção do OFS caiu 🥹 preciso que você use a extensão para renova a '
    'seção 😭😭😭'
)

AVISO_PV_VOLTOU = (
    'obrigaduuuuuuu OPERADOR  💙🩵 agora os relatórios automáticos voltaram 🤖 '
    '"abraço robótico" 🫂'
)


def _avisar_no_privado(mensagem):
    """Manda o aviso no privado de quem pode renovar, além do grupo.

    Quem chama decide QUANDO: esta função manda sempre que for chamada. O
    represamento mora no `_avisar_falha`, e o ponto de chamada só dispara o
    privado quando o aviso do grupo saiu de verdade.

    Falha aqui não pode derrubar o ciclo: o aviso no grupo já saiu, e o
    privado é reforço.
    """
    try:
        destinos = ofs_extracao.destinos_de_aviso_no_privado()
    except Exception:
        logger.exception('Não consegui montar a lista de avisos no privado.')
        return
    if not destinos:
        logger.info('Aviso de sessão vencida: nenhum privado conhecido ainda.')
        return
    for jid in destinos:
        try:
            enviar_texto(mensagem, jid)
        except Exception:
            logger.exception('Falha ao avisar %s no privado.', jid)


def _avisar_falha(mensagem):
    """Avisa no grupo principal, sem repetir de hora em hora.

    O primeiro aviso sai na hora. Enquanto a falha continuar, repete só a cada
    INTERVALO_AVISO_SEG -- lembrete de hora em hora vira paisagem, e paisagem
    ninguém lê.
    """
    agora = time.time()
    primeira_vez = not _estado_aviso['falhando']
    enviou = False
    if primeira_vez or agora - _estado_aviso['ultimo_aviso'] >= INTERVALO_AVISO_SEG:
        enviar_texto(mensagem, None)      # None = grupo principal
        _estado_aviso['ultimo_aviso'] = agora
        enviou = True
    _estado_aviso['falhando'] = True
    return enviou

def _avisar_volta():
    """Avisa que voltou -- e só quem soube que tinha caído.

    O `if` é a regra inteira: sem ele, todo ciclo bem-sucedido mandaria um
    "voltou" para quem nunca viu nada quebrar. Agradecimento que chega sem
    problema antes é ruído, e ruído gasta a atenção que a próxima queda vai
    precisar.
    """
    if _estado_aviso['falhando']:
        enviar_texto('✅ Sessão do OFS voltou: o painel de resultados está sendo enviado de novo.',
                     None)
        _avisar_no_privado(AVISO_PV_VOLTOU)
    _estado_aviso['falhando'] = False
    _estado_aviso['ultimo_aviso'] = 0.0


def _enviar_extracao(caminho_csv, agora=None, info=None):
    """Manda o CSV cru no grupo da extração, como documento.

    Vai SEMPRE que a extração baixa, mesmo quando nenhuma tela é enviada: as
    telas dependem de ter conteúdo, o arquivo não. Um dia zerado é justamente
    quando alguém quer abrir o CSV e ver o que aconteceu.

    O nome carrega data e hora porque é por ele que se acha uma extração
    específica na busca do grupo depois -- 14 arquivos por dia com o mesmo nome
    seriam um histórico inútil.
    """
    if not DESTINO_EXTRACAO:
        return False
    agora = agora or datetime.now()
    nome = f'OFS {agora:%d-%m} {agora:%H}h.csv'
    total = (info or {}).get('atividades')
    legenda = f'Extração do OFS · {agora:%d/%m} {agora:%H}h'
    if total is not None:
        legenda += f' · {total} atividades'
    enviou = enviar_arquivo(caminho_csv, nome, legenda, DESTINO_EXTRACAO, mimetype='text/csv')
    if not enviou:
        # Não avisa no grupo: o CSV é conferência, não operação. Se o WhatsApp
        # estiver fora, o painel do mesmo ciclo já vai gritar por conta dele.
        logger.warning('Extração %s não foi enviada ao grupo %s.', nome, DESTINO_EXTRACAO)
    return enviou


def _enviar_fechamento_ontem(destino=None):
    """A capa do dia anterior, uma vez por dia, na abertura do expediente.

    Por que no dia seguinte e não às 20h: serviço lançado depois do expediente
    ainda entra na conta da véspera. O que vai às 20h é PARCIAL; o número que
    fecha o dia só existe na manhã seguinte.

    Roda ANTES da geração de hoje de propósito: as duas escrevem na mesma pasta
    de saída, e quem termina por último é quem fica lá -- tem de ser o dia
    corrente, que é o que o site mostra.

    Falha aqui não interrompe o ciclo do dia e não avisa no grupo: se a sessão
    do OFS caiu, o ciclo de hoje avisa em seguida, e dois avisos pela mesma
    causa é ruído.
    """
    ontem = datetime.now().date() - timedelta(days=1)
    rotulo = f'{ontem:%d/%m}'
    try:
        caminho_csv, info = ofs_extracao.baixar_extracao(f'{ontem:%Y-%m-%d}')
        resultado = gerar_telas(caminho_csv)
    except Exception as falha:
        logger.warning('Fechamento de %s: não consegui gerar (%s). Sigo com o dia de hoje.',
                       rotulo, falha)
        return False

    if not _tem_conteudo(resultado.get('contagens') or {}, TELA_CAPA):
        logger.info('Fechamento de %s: capa zerada (%s atividades); não enviado.',
                    rotulo, resultado.get('atividades'))
        return False

    placar = enviar_telas(resultado, destino, telas=[(TELA_CAPA, f'Fechamento {rotulo}')])
    logger.info('Fechamento de %s: %s enviada(s), %s falha(s), %s atividades.',
                rotulo, placar['enviadas'], placar['falhas'], resultado.get('atividades'))
    return bool(placar['enviadas'])


def _atualizar_ofs_geral():
    """Refaz o 'OFS GERAL.csv' de carona no ciclo, agora que a sessão provou estar viva.

    Pega carona em vez de ter agendador próprio porque a única condição para
    conseguir baixar é a mesma: sessão do OFS de pé. Um segundo agendador só
    duplicaria a lógica de tentar, esperar e avisar.

    Falhar aqui NÃO derruba o ciclo. O painel é o que a operação está esperando
    às 7h03; o OFS GERAL alimenta telas que ninguém está olhando naquele
    segundo, e ele volta na hora seguinte. Por isso o except é largo e o
    resultado é uma linha de log, não um aviso no grupo.
    """
    try:
        caminho, info = ofs_extracao.gravar_ofs_geral()
        logger.info('OFS GERAL atualizado: %s atividades, %s a %s (%s dias) em %s.',
                    info['atividades'], info['de'], info['ate'], info['dias'], caminho)
    except Exception as falha:
        logger.warning('Não consegui atualizar o OFS GERAL nesta hora (%s). '
                       'A confirmação de agenda e o Enviado D0 seguem com o arquivo '
                       'anterior.', falha)


def rodar_ciclo(destino=None):
    """Ciclo completo: OFS -> CSV -> telas -> grupo. Devolve um resumo."""
    with _lock_ciclo:
        agora = datetime.now()

        # A abertura do dia não espera mais as 7h em ponto: ela sai no primeiro
        # ciclo que der certo. O fechamento da véspera vai primeiro e é marcado
        # na hora, para não repetir se o ciclo de hoje falhar logo em seguida.
        if abertura_pendente('fechamento', agora):
            if _enviar_fechamento_ontem(destino):
                marcar_abertura('fechamento', agora)

        levar_agenda = abertura_pendente('agenda', agora)

        try:
            caminho_csv, info = ofs_extracao.baixar_extracao()
        except ofs_extracao.SessaoVencida as falha:
            logger.warning('Painel de resultados: sessão do OFS caiu — %s', falha)
            # Fica marcado: quando o cookie novo chegar, este ciclo é refeito
            # na hora, sem esperar a próxima hora cheia.
            _pendente_por_sessao['sim'] = True
            saiu_no_grupo = _avisar_falha(
                '⚠️ *Painel de resultados parado*\n\n'
                'A sessão do OFS venceu, então não consigo baixar a extração sozinho.\n\n'
                f'{ofs_extracao.INSTRUCAO_RECONEXAO}\n\n'
                'Assim que a sessão nova chegar, eu retomo o painel na hora — sem '
                'esperar a próxima hora cheia.\n\n'
                f'Motivo: {falha}'
            )
            # Só quando o aviso do grupo saiu de verdade. O `_avisar_falha`
            # represa a repetição (a primeira vez, e depois a cada
            # INTERVALO_AVISO_SEG). Sem esta amarra o privado receberia a
            # mensagem TODA HORA enquanto a sessão seguisse vencida -- e
            # lembrete de hora em hora vira paisagem, que é exatamente o
            # que aquele represamento existe para evitar.
            if saiu_no_grupo:
                _avisar_no_privado(AVISO_PV_CAIU)
            return {'ok': False, 'motivo': 'sessao'}
        except Exception as falha:
            logger.exception('Painel de resultados: falha ao baixar a extração.')
            _avisar_falha(f'⚠️ *Painel de resultados*: falha ao baixar a extração do OFS.\n\n{falha}')
            return {'ok': False, 'motivo': 'download'}

        # O arquivo vai antes de gerar as telas: é o dado bruto, não depende de
        # nada dar certo depois. Se a geração falhar, o grupo da extração já
        # tem o CSV para alguém olhar.
        _enviar_extracao(caminho_csv, info=info)
        _atualizar_ofs_geral()

        try:
            resultado = gerar_telas(caminho_csv)
        except Exception as falha:
            logger.exception('Painel de resultados: falha ao gerar as telas.')
            _avisar_falha(f'⚠️ *Painel de resultados*: a extração baixou, mas as telas não foram '
                          f'geradas.\n\n{falha}')
            return {'ok': False, 'motivo': 'geracao'}

        if not resultado.get('atividades'):
            # Extração sem nenhuma atividade não é dia parado: é base errada.
            # Mandar um painel zerado no grupo seria pior do que não mandar.
            _avisar_falha('⚠️ *Painel de resultados*: a extração do OFS veio sem atividade '
                          'nenhuma. Não mandei o painel para não publicar um quadro vazio.')
            return {'ok': False, 'motivo': 'vazio'}

        if not _tem_conteudo(resultado.get('contagens') or {}, TELA_CAPA):
            # Capa zerada e SEM aviso de propósito: pode ser dia sem serviço
            # concluído ainda (7h da manhã), e não é problema para acordar
            # ninguém. Só não vai para o grupo.
            logger.info('Painel de acompanhamento zerado (%s atividades na extração); '
                        'nada enviado nesta hora.', resultado.get('atividades'))
            return {'ok': False, 'motivo': 'painel_zerado', 'baixadas': info['atividades'],
                    **resultado}

        placar = enviar_telas(resultado, destino, com_agenda=levar_agenda)
        if not placar['enviadas']:
            # Gerar e não conseguir mandar é falha, não sucesso. Sem isto o
            # ciclo diria "ok" com o grupo vazio -- e o único jeito de alguém
            # descobrir seria estranhar a falta do painel.
            logger.warning('Painel de resultados: telas geradas, mas nenhuma foi enviada.')
            _avisar_falha('⚠️ *Painel de resultados*: gerei as telas mas não consegui enviar '
                          'nenhuma delas no grupo.')
            return {'ok': False, 'motivo': 'envio', 'baixadas': info['atividades'],
                    **placar, **resultado}

        if placar['falhas']:
            logger.warning('Painel de resultados: %s tela(s) falharam no envio.', placar['falhas'])
        _pendente_por_sessao['sim'] = False
        # A agenda é marcada aqui, e não em enviar_telas: vale quando o CICLO
        # deu certo. Se a carga do dia veio zerada e a tela foi pulada, ainda
        # assim a abertura aconteceu -- insistir de hora em hora numa agenda que
        # não tem linha nenhuma só encheria o log.
        if levar_agenda:
            marcar_abertura('agenda', agora)
        _avisar_volta()
        logger.info('Painel de resultados: %s enviada(s), %s pulada(s) por estarem zeradas, '
                    '%s falha(s). %s atividades, carimbo %s.',
                    placar['enviadas'], placar['puladas'], placar['falhas'],
                    resultado.get('atividades'), resultado.get('carimbo'))
        return {'ok': True, 'baixadas': info['atividades'], **placar, **resultado}


def _proximo_horario(agora):
    """O próximo MINUTO_ENVIO de hora cheia dentro da janela HORA_INICIO..HORA_FIM."""
    candidato = agora.replace(minute=MINUTO_ENVIO, second=0, microsecond=0)
    if candidato <= agora:
        candidato += timedelta(hours=1)
    if candidato.hour < HORA_INICIO:
        return candidato.replace(hour=HORA_INICIO)
    if candidato.hour > HORA_FIM:
        return (candidato + timedelta(days=1)).replace(hour=HORA_INICIO)
    return candidato


def thread_agendador_painel_resultados():
    """Roda em background: manda o painel de hora em hora, das 7h às 22h.

    Não dispara ao subir, pelo mesmo motivo do agendador de garantias: o
    serviço reinicia várias vezes ao dia e um disparo por reinício encheria o
    grupo de painéis repetidos fora de hora.
    """
    if not PAINEL_RESULTADOS_ATIVO:
        logger.info('Painel de resultados automático desligado (PAINEL_RESULTADOS_ATIVO=0).')
        return
    if not DESTINO:
        logger.info('Painel de resultados sem destino (PAINEL_RESULTADOS_DESTINO vazio): '
                    'agendador não iniciado.')
        return

    logger.info('Agendador do painel de resultados iniciado: %02dmin de cada hora, das %dh às %dh, '
                'para %s.', MINUTO_ENVIO, HORA_INICIO, HORA_FIM, DESTINO)

    while True:
        agora = datetime.now()
        alvo = _proximo_horario(agora)
        logger.info('Próximo painel de resultados: %s (em %.0f min).',
                    alvo.strftime('%d/%m %H:%M'), (alvo - agora).total_seconds() / 60)

        # Dorme em fatias: numa espera longa (a noite inteira), um relógio
        # corrigido ou uma máquina que suspendeu fariam a thread acordar muito
        # depois da hora. Em fatias, o alvo é reconferido.
        cookie_visto = carimbo_cookie()
        recuperar = False
        while True:
            restante = (alvo - datetime.now()).total_seconds()
            if restante <= 0:
                break
            # Fatias de 60s em vez de 300: é o que faz o cookie novo parecer
            # imediato. O custo é um os.stat por minuto.
            time.sleep(min(restante, 60))

            agora = datetime.now()
            if (_pendente_por_sessao['sim']
                    and carimbo_cookie() != cookie_visto
                    and HORA_INICIO <= agora.hour <= HORA_FIM):
                cookie_visto = carimbo_cookie()
                recuperar = True
                break

        try:
            if recuperar:
                logger.info('Cookie do OFS renovado: refazendo o ciclo do painel '
                            'que tinha falhado, sem esperar a hora cheia.')
            rodar_ciclo()
        except Exception:
            logger.exception('Falha no ciclo agendado do painel de resultados.')

        time.sleep(61)   # não dispara duas vezes no mesmo minuto


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    resumo = rodar_ciclo(destino=(sys.argv[1] if len(sys.argv) > 1 else None))
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    raise SystemExit(0 if resumo.get('ok') else 1)
