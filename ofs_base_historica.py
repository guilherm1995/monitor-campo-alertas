"""Reconstrói as bases históricas do OFS sozinho, sem ninguém exportar à mão.

O QUE ISTO SUBSTITUI
--------------------
Duas planilhas que alguém exportava do OFS e subia pelo site:

  base OFS ok.xlsx             -> concluídos, alimenta a lista de garantias
  base improdutivas 60 dias.xlsx -> tudo, alimenta a consulta de reincidência

Enquanto dependeram de mão humana, atrasaram: em 26/08/2026 a das improdutivas
estava parada desde 24/08, e a das garantias cobria 44 dias em vez de 30 --
a janela manual escorrega e ninguém percebe, porque base velha não dá erro,
só responde errado.

POR QUE DÁ PARA FAZER ISTO AGORA
--------------------------------
Porque `ofs_extracao` sabe pedir QUALQUER dia, não só hoje. Medido em
26/08/2026, comparando com a base manual que estava no servidor:

    dia      automática   manual
    20/08       331        331
    19/08       309        309
    18/08       315        315
    13/08       324        324

Quatro de quatro, sem uma linha de diferença. As duas áreas que o bot baixa
(9163 + 9301) são o mesmo conjunto do "Área: TODOS" da extensão, e as 34
colunas saem com os mesmos nomes -- inclusive o `Tipo de Atividade.1`, que é
o que vale, e o `Motivo de Encerramento das atividades`, de que as
improdutivas dependem.

RECONSTRUÇÃO INTEIRA, NÃO ACRÉSCIMO
-----------------------------------
Todo dia a janela inteira é baixada de novo. Parece desperdício e não é:
atividade muda depois do fato -- um "não concluído" que reabre e conclui na
semana seguinte, um cancelamento retroativo. Acrescentar o dia anterior a um
arquivo que já existe congelaria o engano para sempre; refazer absorve. E o
preço é baixo: 0,15s por requisição reusando a conexão, 120 requisições para
60 dias.

A JANELA TERMINA ONTEM
----------------------
De propósito, nos dois arquivos: é o que a operação fazia à mão ("os
concluídos do dia anterior") e é o que o operador pediu para manter em
26/08/2026. O dia de hoje ainda está se mexendo -- serviço que ainda vai ser
concluído, cancelado, reagendado -- e entrar com ele daria garantia a partir
de um estado que ainda vai mudar. Quem quer o dia corrente já tem o painel,
que baixa a extração de hora em hora.

O QUE NUNCA PODE ACONTECER
--------------------------
Gravar base pela metade. Base vazia ou truncada não dá erro em lugar nenhum:
a garantia simplesmente para de reconhecer, a improdutiva simplesmente deixa
de reincidir, e ninguém fica sabendo. Por isso a gravação só acontece depois
de o arquivo inteiro passar na conferência (`_conferir`), e a troca é atômica.
Falhou? O arquivo de ontem continua no lugar. Desatualizado é ruim; vazio é
pior, porque mente com cara de resposta.

Uso direto (fora do bot):

    python ofs_base_historica.py reconstruir     # refaz as duas bases
    python ofs_base_historica.py conferir        # só mostra o que existe hoje
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import ofs_extracao as ofs

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent
PASTA_DADOS = Path(os.environ.get('OFS_PASTA_DADOS', RAIZ / 'dados'))

# Os mesmos nomes de arquivo que o bot já lê, de propósito: assim
# `carregar_base_ofs` e `improdutivas.carregar_base` não mudam uma linha, e o
# envio pela tela do site continua funcionando como saída de emergência.
ARQUIVO_IMPRODUTIVAS = Path(os.environ.get(
    'BASE_IMPRODUTIVAS_ARQUIVO', PASTA_DADOS / 'base improdutivas 60 dias.xlsx'))
ARQUIVO_GARANTIAS = Path(os.environ.get(
    'BASE_OFS_ARQUIVO', PASTA_DADOS / 'base OFS ok.xlsx'))

DIAS_IMPRODUTIVAS = int(os.environ.get('BASE_HISTORICA_DIAS_IMPRODUTIVAS', '60'))

# 35 e não 30: a garantia de reparo dura 30 dias contados da conclusão, então a
# base precisa alcançar D-30 TODO dia. Cinco dias de folga custam ~600 linhas e
# compram tolerância a alguns dias seguidos de reconstrução falhando -- sem
# eles, dois dias de sessão vencida já começariam a perder garantia pela borda.
DIAS_GARANTIAS = int(os.environ.get('BASE_HISTORICA_DIAS_GARANTIAS', '35'))

# Pausa entre requisições. O painel usa 0,5s entre duas; aqui são 120, e 0,5
# somaria um minuto de espera pura. 0,3s continua sendo um pedido a cada três
# décimos -- longe de parecer varredura, e o trabalho é de madrugada mesmo.
PAUSA_ENTRE_PEDIDOS_SEG = float(os.environ.get('BASE_HISTORICA_PAUSA', '0.3'))

# QUANDO RODAR -- e por que não é de madrugada.
#
# A primeira versão disto rodava às 04:20: dia virado, ninguém usando a máquina.
# Errado, e o log provou. A sessão do OFS dura ~14h30 e nasce quando alguém
# entra de manhã; às 04:20 ela está morta quase todo dia. Em 26/08/2026 o cookie
# foi publicado às 08:12, e os ciclos do painel das 07:03 e 08:03 falharam por
# sessão vencida -- 04:20 seria o único horário em que a reconstrução nunca
# funcionaria.
#
# Então são dois gatilhos, e o segundo é o que importa:
#
#   1. HORÁRIO: às 07:40, se a base ainda não cobre ontem. Serve para o dia em
#      que a sessão da véspera ainda está viva e ninguém precisa entrar.
#   2. COOKIE NOVO: assim que `ofs_cookies.json` muda, se a base ainda estiver
#      atrasada, tenta na hora. Cookie novo é a única prova barata de que
#      existe sessão viva agora -- esperar o próximo horário depois disso seria
#      ficar parado com a chave na mão.
HORA_ALVO = int(os.environ.get('BASE_HISTORICA_HORA', '7'))
MINUTO_ALVO = int(os.environ.get('BASE_HISTORICA_MINUTO', '40'))

# Enquanto a base estiver atrasada, tenta de novo de tempos em tempos: um login
# às 8h12 como o de 26/08 não pode depender de alguém avisar o bot.
INTERVALO_TENTATIVA_SEG = int(os.environ.get('BASE_HISTORICA_INTERVALO', '1800'))

# Depois desta hora o dia é dado por perdido e o bot volta a esperar o próximo.
HORA_DESISTIR = int(os.environ.get('BASE_HISTORICA_HORA_LIMITE', '20'))

# Só avisa no grupo se passar desta hora sem conseguir. Antes disso, atraso é
# rotina -- alguém ainda não entrou no OFS. Aviso que toca toda manhã de login
# atrasado vira paisagem, e paisagem ninguém lê.
HORA_AVISO = int(os.environ.get('BASE_HISTORICA_HORA_AVISO', '12'))

# De quanto em quanto tempo olhar o relógio e o cookie. Um minuto é barato
# (dois os.stat) e faz o gatilho do cookie parecer imediato.
INTERVALO_VERIFICACAO_SEG = int(os.environ.get('BASE_HISTORICA_VERIFICACAO', '60'))

# O cookie que o login publica. Mudou de mtime = alguém entrou no OFS agora.
ARQUIVO_COOKIES = ofs.ARQUIVO_COOKIES

# Onde fica registrado até que dia a base chegou. Um JSON de três campos em vez
# de abrir o .xlsx: a thread do bot precisa responder "já fiz hoje?" a cada
# minuto, e carregar pandas para isso seria pagar caro por uma data.
ARQUIVO_ESTADO = Path(os.environ.get(
    'BASE_HISTORICA_ESTADO', PASTA_DADOS / 'base_historica_estado.json'))

BASE_HISTORICA_ATIVA = os.environ.get('BASE_HISTORICA_ATIVA', '1') != '0'

# Quanto o arquivo novo pode encolher em relação ao que está no disco antes de
# eu desconfiar. Encolher um pouco é normal (a janela anda e o dia que sai
# pode ser mais cheio que o que entra); encolher um terço não é.
ENCOLHIMENTO_ACEITAVEL = float(os.environ.get('BASE_HISTORICA_ENCOLHIMENTO', '0.7'))

# Os três tipos que a exportação manual trazia, conferidos na base que estava
# no servidor em 26/08/2026 -- ela tinha exatamente Ativação (2064), Mudança de
# Endereço (1100) e Reparo Corretivo (317), e mais nada.
#
# Não é enfeite: `verificar_garantia_reparo` decide por PEDAÇO do nome do tipo
# ('reparo' in tipo, 'mudanca' in tipo). Deixar a base crua entrar aqui faria
# "Reparo Preventivo - Cliente Atenuado" virar garantia de reparo e "Mudança de
# Cômodo" virar garantia de mudança -- duas garantias que hoje não existem,
# criadas sem ninguém pedir e sem nada no log dizendo isso.
TIPOS_GARANTIA = tuple(
    t.strip() for t in os.environ.get(
        'BASE_HISTORICA_TIPOS_GARANTIA',
        'Ativação,Mudança de Endereço,Reparo Corretivo').split(',') if t.strip()
)

# Colunas sem as quais o arquivo não serve para nada. São as que
# `carregar_base_ofs` e `improdutivas.carregar_base` procuram.
COLUNAS_OBRIGATORIAS = (
    'Recurso',
    'Data',
    'Status da Atividade',
    'Número do contrato',
    'Motivo de Encerramento das atividades',
    'Tipo de Atividade.1',
)

TIMEOUT_SUBPROCESSO_SEG = int(os.environ.get('BASE_HISTORICA_TIMEOUT', '600'))


class BaseSuspeita(RuntimeError):
    """O arquivo até ficou pronto, mas não passou na conferência."""


def concluido(status) -> 'object':
    """Máscara de "concluído" que NÃO engole "não concluído".

    A armadilha: o OFS usa os dois textos, e `contains('conclu')` casa com os
    dois. Enquanto a base vinha filtrada da mão de alguém, isso nunca apareceu
    -- a exportação manual já chegava só com concluído. Gerando a base aqui,
    apareceria na primeira noite: medido em 26/08/2026, o filtro frouxo trazia
    8.340 linhas contra 7.131 do firme, ou seja 1.209 serviços NÃO concluídos
    entrando como se tivessem sido, cada um valendo uma garantia que não existe.

    Comparar por pedaço em vez de por igualdade é de propósito: o texto varia em
    acento e caixa entre exportações, e um "concluído com ressalva" que apareça
    amanhã deve continuar contando. O que não pode entrar é a negação.
    """
    import pandas as pd

    normalizado = (pd.Series(status).astype(str).str.strip().str.lower()
                   .str.normalize('NFKD').str.encode('ascii', 'ignore').str.decode('ascii'))
    return normalizado.str.contains('conclu', na=False) & ~normalizado.str.startswith('nao')


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

def dias_da_janela(dias: int, ate: 'datetime | None' = None) -> list[str]:
    """Os `dias` dias que terminam ONTEM, do mais antigo para o mais novo."""
    fim = (ate or datetime.now()).date() - timedelta(days=1)
    return [f'{fim - timedelta(days=i):%Y-%m-%d}' for i in range(dias - 1, -1, -1)]


def coletar(dias: int, ate=None, aviso=None) -> tuple[str, dict]:
    """Baixa a janela inteira e devolve um CSV consolidado, com um cabeçalho.

    Qualquer dia que não valide interrompe tudo: meia janela não é uma base
    menor, é uma base que responde "não" para quem estava lá no pedaço que
    faltou.
    """
    datas = dias_da_janela(dias, ate)
    sessao = requests.Session()
    sessao.cookies.update(ofs.carregar_cookies())
    sessao.headers.update({'User-Agent': ofs.NAVEGADOR, 'Referer': f'{ofs.BASE}/'})

    pedacos: list[tuple[str, str]] = []
    por_dia: dict[str, int] = {}
    inicio = time.time()

    for indice, data_str in enumerate(datas, 1):
        linhas_do_dia = 0
        for provider_id, nome in ofs.AREAS.items():
            resposta = sessao.get(ofs.montar_url(data_str, provider_id), timeout=90)
            texto = ofs.validar_csv(resposta.text, f'{nome} {data_str}')
            pedacos.append((f'{nome} {data_str}', texto))
            linhas_do_dia += max(0, texto.count('\n') - 1)
            time.sleep(PAUSA_ENTRE_PEDIDOS_SEG)
        por_dia[data_str] = linhas_do_dia
        if aviso and indice % 10 == 0:
            aviso(f'{indice}/{len(datas)} dias baixados')

    csv = ofs.consolidar(pedacos)
    return csv, {
        'dias': len(datas),
        'de': datas[0],
        'ate': datas[-1],
        'linhas': max(0, csv.count('\n')),
        'por_dia': por_dia,
        'segundos': round(time.time() - inicio, 1),
    }


# ---------------------------------------------------------------------------
# Conferência e gravação
# ---------------------------------------------------------------------------

def _conferir(df, caminho: Path, dias_pedidos: int, rotulo: str) -> dict:
    """Diz se este DataFrame pode virar arquivo. Levanta BaseSuspeita se não.

    Três perguntas, e cada uma já deu problema em base montada à mão:
    tem as colunas? cobre a janela? não encolheu do nada?
    """
    import pandas as pd

    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        raise BaseSuspeita(f'{rotulo}: faltam colunas {faltando}')

    if df.empty:
        raise BaseSuspeita(f'{rotulo}: nenhuma linha')

    datas = pd.to_datetime(df['Data'], errors='coerce', dayfirst=True)
    distintas = datas.dt.date.nunique()
    # Metade da janela: fim de semana e feriado rendem dias magros, mas não
    # somem do arquivo -- dia sem atividade nenhuma é raro. Perder metade dos
    # dias seria a extração vindo pela metade, não a operação parada.
    if distintas < dias_pedidos * 0.5:
        raise BaseSuspeita(
            f'{rotulo}: só {distintas} dia(s) distintos para uma janela de {dias_pedidos}')

    anterior = None
    if caminho.exists():
        try:
            anterior = len(pd.read_excel(caminho))
        except Exception as falha:      # arquivo velho ilegível não impede o novo
            logger.warning('Não consegui ler %s para comparar: %s', caminho.name, falha)

    if anterior and len(df) < anterior * ENCOLHIMENTO_ACEITAVEL:
        raise BaseSuspeita(
            f'{rotulo}: {len(df)} linhas contra {anterior} do arquivo atual '
            f'-- encolheu demais, não vou trocar')

    return {'linhas': len(df), 'dias': int(distintas), 'linhas_antes': anterior,
            'de': str(datas.min().date()), 'ate': str(datas.max().date())}


def _gravar_xlsx(df, caminho: Path) -> None:
    """Grava sem deixar arquivo pela metade: escreve ao lado e troca."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix('.xlsx.novo')
    df.to_excel(temporario, index=False)
    os.replace(temporario, caminho)


def reconstruir(ate=None, aviso=None) -> dict:
    """Refaz as duas bases a partir do OFS. Devolve um resumo do que gravou.

    Roda a coleta UMA vez, na janela maior, e recorta as duas visões dela: não
    há por que pedir os mesmos 35 dias duas vezes ao OFS.
    """
    import pandas as pd
    from io import StringIO

    aviso = aviso or (lambda _mensagem: None)

    dias = max(DIAS_IMPRODUTIVAS, DIAS_GARANTIAS)
    aviso(f'baixando {dias} dias do OFS...')
    csv, info = coletar(dias, ate=ate, aviso=aviso)

    # dtype=str para o número do contrato não virar float e perder o formato
    # no caminho. Quem lê depois normaliza com _tratar_contrato_serie, que
    # aceita as duas formas -- mas gravar já certo evita a dúvida.
    df = pd.read_csv(StringIO(csv), dtype=str)
    df.columns = df.columns.str.strip()
    datas = pd.to_datetime(df['Data'], errors='coerce', dayfirst=True)

    resumo = {'coleta': info, 'arquivos': {}}

    # --- improdutivas: a janela inteira, sem filtro de status ---------------
    df_improdutivas = df[datas >= datas.max() - timedelta(days=DIAS_IMPRODUTIVAS - 1)]
    conferido = _conferir(df_improdutivas, ARQUIVO_IMPRODUTIVAS,
                          DIAS_IMPRODUTIVAS, 'improdutivas')
    _gravar_xlsx(df_improdutivas, ARQUIVO_IMPRODUTIVAS)
    resumo['arquivos'][ARQUIVO_IMPRODUTIVAS.name] = conferido

    # --- garantias: janela menor, SÓ concluído, SÓ os três tipos ------------
    #
    # Os dois filtros existem para o arquivo sair com a mesma cara da exportação
    # manual. Conferido dia a dia contra a base do servidor em 26/08/2026:
    #
    #     dia      manual   gerado aqui
    #     20/08      95        95
    #     19/08      91        91
    #     18/08      93        93
    #     13/08     104       104
    #
    # E a comparação por ordem de serviço não achou UMA linha que a manual
    # tivesse e esta não: o que muda de um lado para o outro é só a mão que faz.
    recorte = datas >= datas.max() - timedelta(days=DIAS_GARANTIAS - 1)
    df_garantias = df[recorte
                      & concluido(df['Status da Atividade'])
                      & df['Tipo de Atividade.1'].isin(TIPOS_GARANTIA)]
    conferido = _conferir(df_garantias, ARQUIVO_GARANTIAS, DIAS_GARANTIAS, 'garantias')
    _gravar_xlsx(df_garantias, ARQUIVO_GARANTIAS)
    resumo['arquivos'][ARQUIVO_GARANTIAS.name] = conferido

    # O carimbo vai por último, depois de os dois arquivos estarem no lugar:
    # ele é a resposta a "já fiz hoje?", e responder "sim" antes de ter feito
    # faria o bot parar de tentar justamente no dia em que faltou.
    gravar_estado(str(datas.max().date()), resumo)
    return resumo


def _salvar_estado(campos: dict) -> None:
    """Junta `campos` ao que já está gravado, sem apagar o resto.

    Mescla em vez de sobrescrever porque há DOIS escritores: a thread do bot
    anota a hora de cada tentativa, e o subprocesso anota até onde a base
    chegou. Escrevendo o arquivo inteiro, o segundo apagaria o do primeiro --
    e o campo perdido seria justamente o que segura a próxima tentativa.
    """
    estado = estado_atual()
    estado.update(campos)
    try:
        ARQUIVO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_ESTADO.write_text(json.dumps(estado), encoding='utf-8')
    except OSError as falha:
        # Não é motivo para desfazer nada: os arquivos bons já estão gravados.
        # O custo de perder o carimbo é uma reconstrução repetida, não um erro.
        logger.warning('Não consegui gravar %s: %s', ARQUIVO_ESTADO.name, falha)


def gravar_estado(ate: str, resumo: dict) -> None:
    _salvar_estado({
        'ate': ate,
        'quando': datetime.now().isoformat(timespec='seconds'),
        'linhas': {nome: dados['linhas'] for nome, dados in resumo['arquivos'].items()},
    })


def registrar_tentativa(agora=None) -> None:
    """Anota que uma tentativa começou AGORA -- tenha ela dado certo ou não.

    Vai para o disco, e não para uma variável, porque o serviço reinicia várias
    vezes ao dia: em memória, cada reinício zeraria o intervalo entre tentativas.
    Num dia de sessão morta, cinco reinícios seriam cinco coletas de 120
    requisições cada, todas condenadas a falhar.
    """
    _salvar_estado({'ultima_tentativa': (agora or datetime.now()).isoformat(timespec='seconds')})


def ultima_tentativa() -> 'datetime | None':
    bruto = estado_atual().get('ultima_tentativa')
    if not bruto:
        return None
    try:
        return datetime.fromisoformat(bruto)
    except ValueError:
        return None


def estado_atual() -> dict:
    try:
        return json.loads(ARQUIVO_ESTADO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def base_cobre_ontem(agora=None) -> bool:
    """A base já foi refeita para a janela que termina ontem?"""
    ontem = (agora or datetime.now()).date() - timedelta(days=1)
    return estado_atual().get('ate') == f'{ontem:%Y-%m-%d}'


# ---------------------------------------------------------------------------
# Como o bot chama: subprocesso, para o pandas não morar no processo do bot
# ---------------------------------------------------------------------------

def reconstruir_em_subprocesso() -> dict:
    """Roda `reconstruir` num processo separado e devolve o resumo.

    Import em vez de subprocesso seria mais simples e mais caro: são ~14 mil
    linhas em DataFrame uma vez por dia, e o que o pandas aloca para isso ficaria
    fragmentando o heap do bot para sempre. Em processo separado, morre junto.
    """
    processo = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), 'reconstruir', '--json'],
        cwd=str(RAIZ), capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=TIMEOUT_SUBPROCESSO_SEG,
    )
    for linha in (processo.stdout or '').splitlines():
        if linha.startswith('__RESULTADO__'):
            return json.loads(linha[len('__RESULTADO__'):])

    ultima = (processo.stderr or '').strip().splitlines()
    raise RuntimeError(
        f'reconstrução falhou: {ultima[-1] if ultima else "sem saída"}')


# ---------------------------------------------------------------------------
# Agendador
# ---------------------------------------------------------------------------

_estado_aviso = {'falhando': False}


def carimbo_cookie() -> float:
    """mtime do ofs_cookies.json, ou 0 se não existir. Mudou = login novo."""
    try:
        return ARQUIVO_COOKIES.stat().st_mtime
    except OSError:
        return 0.0


def _motivo_para_tentar(agora, cookie_visto, tentada_em=None):
    """Devolve o motivo de tentar agora, ou None para continuar esperando.

    A ordem importa: cookie novo vem antes do relógio porque é informação, não
    calendário. Saber que existe sessão viva agora vale mais do que a hora que
    eu escolhi meses atrás.

    Sobre "roda ao subir": roda, sim, e é para rodar. Se o serviço reinicia às
    15h de um dia em que a base ainda não foi feita, esperar até amanhã seria
    passar o dia com a garantia respondendo por uma base velha à toa. O que
    segura a repetição é o intervalo -- e ele vem do DISCO, não da memória,
    senão cada reinício zeraria a conta (foi o que aconteceu em 26/08/2026).
    """
    if base_cobre_ontem(agora):
        return None
    if agora.hour >= HORA_DESISTIR:
        return None

    tentada_em = tentada_em if tentada_em is not None else ultima_tentativa()
    cedo_demais = (tentada_em is not None
                   and (agora - tentada_em).total_seconds() < INTERVALO_TENTATIVA_SEG)

    # Cookie novo fura o intervalo de propósito: o intervalo existe para não
    # repetir uma tentativa que vai falhar pelo mesmo motivo, e cookie novo é
    # exatamente a notícia de que o motivo mudou.
    if carimbo_cookie() != cookie_visto:
        return 'cookie do OFS foi renovado'

    if cedo_demais:
        return None

    alvo = agora.replace(hour=HORA_ALVO, minute=MINUTO_ALVO, second=0, microsecond=0)
    if agora >= alvo:
        return 'horário' if tentada_em is None else 'nova tentativa'
    return None


def thread_agendador_base_historica(avisar=None):
    """Mantém as bases cobrindo até ontem, uma vez por dia.

    Pode disparar logo depois de subir, se a base do dia ainda não foi feita e
    já passou do horário alvo -- ver `_motivo_para_tentar`. O que impede isso de
    virar uma coleta por reinício é o intervalo gravado em disco.
    """
    if not BASE_HISTORICA_ATIVA:
        logger.info('Reconstrução automática das bases do OFS desligada '
                    '(BASE_HISTORICA_ATIVA=0).')
        return

    avisar = avisar or (lambda _mensagem: None)
    logger.info('Agendador das bases do OFS iniciado: alvo %02d:%02d, e na hora em '
                'que o cookie do OFS for renovado (%s dias de improdutivas, %s de '
                'garantias).', HORA_ALVO, MINUTO_ALVO, DIAS_IMPRODUTIVAS, DIAS_GARANTIAS)

    # O cookie que já está no disco não conta como novidade: senão a primeira
    # volta do laço dispararia uma reconstrução a cada reinício do serviço.
    cookie_visto = carimbo_cookie()
    dia_avisado = None

    while True:
        time.sleep(INTERVALO_VERIFICACAO_SEG)
        agora = datetime.now()

        motivo = _motivo_para_tentar(agora, cookie_visto)
        if motivo is None:
            continue

        cookie_visto = carimbo_cookie()
        # Anota ANTES de começar: a coleta leva quase um minuto, e um reinício
        # no meio dela não pode fazer o próximo processo começar tudo de novo.
        registrar_tentativa(agora)
        logger.info('Reconstruindo as bases do OFS (%s).', motivo)

        try:
            resumo = reconstruir_em_subprocesso()
            for nome, dados in resumo['arquivos'].items():
                logger.info('Base %s: %s linhas, %s dias (%s a %s).',
                            nome, dados['linhas'], dados['dias'],
                            dados['de'], dados['ate'])
            if _estado_aviso['falhando']:
                avisar('✅ As bases do OFS (garantias e improdutivas) voltaram a ser '
                       'reconstruídas sozinhas.')
            _estado_aviso['falhando'] = False
            dia_avisado = None
        except Exception as falha:
            logger.warning('Falha ao reconstruir as bases do OFS (%s): %s', motivo, falha)
            # Antes do meio-dia, atraso é rotina: ninguém entrou no OFS ainda, e
            # a próxima tentativa sai sozinha. Avisar aqui seria alarme diário.
            # Um aviso por DIA, e só depois de a manhã inteira ter passado.
            if agora.hour >= HORA_AVISO and dia_avisado != agora.date():
                avisar('⚠️ *Bases do OFS não foram atualizadas hoje.*\n\n'
                       'A lista de garantias e a consulta de improdutivas seguem '
                       'respondendo pela base de ontem, que vai envelhecendo.\n\n'
                       f'{ofs.INSTRUCAO_RECONEXAO}\n\n'
                       'Assim que a sessão nova chegar, eu refaço as bases '
                       'sozinho — não precisa avisar nem esperar horário.\n\n'
                       f'Motivo: {falha}')
                dia_avisado = agora.date()
                _estado_aviso['falhando'] = True


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------

def _descrever_arquivo(caminho: Path) -> str:
    if not caminho.exists():
        return f'{caminho.name}: não existe'
    try:
        import pandas as pd
        df = pd.read_excel(caminho)
        df.columns = df.columns.str.strip()
        datas = pd.to_datetime(df['Data'], errors='coerce', dayfirst=True)
        idade = datetime.now() - datetime.fromtimestamp(caminho.stat().st_mtime)
        return (f'{caminho.name}: {len(df)} linhas, '
                f'{datas.min().date()} a {datas.max().date()}, '
                f'gravado há {idade.days}d {idade.seconds // 3600}h')
    except Exception as falha:
        return f'{caminho.name}: não consegui ler ({falha})'


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    comando = argv[1] if len(argv) > 1 else 'conferir'
    como_json = '--json' in argv

    if comando == 'conferir':
        for caminho in (ARQUIVO_GARANTIAS, ARQUIVO_IMPRODUTIVAS):
            print(_descrever_arquivo(caminho))
        return 0

    if comando == 'reconstruir':
        def aviso(mensagem):
            if not como_json:
                print(mensagem)

        try:
            resumo = reconstruir(aviso=aviso)
        except ofs.SessaoVencida as falha:
            print(f'sessão do OFS vencida: {falha}', file=sys.stderr)
            return 2
        except BaseSuspeita as falha:
            print(f'não gravei: {falha}', file=sys.stderr)
            return 3

        if como_json:
            print('__RESULTADO__' + json.dumps(resumo))
        else:
            coleta = resumo['coleta']
            print(f"baixados {coleta['dias']} dias ({coleta['de']} a {coleta['ate']}) "
                  f"em {coleta['segundos']}s, {coleta['linhas']} linhas")
            for nome, dados in resumo['arquivos'].items():
                antes = dados['linhas_antes']
                comparacao = f' (antes: {antes})' if antes else ''
                print(f"  {nome}: {dados['linhas']} linhas{comparacao}, "
                      f"{dados['dias']} dias, {dados['de']} a {dados['ate']}")
        return 0

    print(f'comando desconhecido: {comando}', file=sys.stderr)
    print('use: reconstruir | conferir', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
