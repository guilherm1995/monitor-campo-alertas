"""Baixa a extração do OFS sem navegador, usando os cookies de uma sessão já aberta.

Como a exportação funciona (descoberto na extensão "Exportação Automática de CSV"
que a operação usa no Chrome): não existe botão nem tela para automatizar. Cada
área é um GET só, autenticado pelos cookies da sessão, que devolve o CSV em texto:

    https://campo.provedor.example/?m=gridexport&a=download&itype=manage
        &providerId=<área>&date=<AAAA-MM-DD>&panel=top&view=time
        &downloadId=<qualquer>&dates=<AAAA-MM-DD>&recursively=1&&<timestamp>

"Área: TODOS + CSV consolidado" é baixar as duas áreas e juntar mantendo um único
cabeçalho -- a mesma regra do `consolidarAreas` da extensão.

ARMADILHA CENTRAL: sessão vencida NÃO vem como erro. O OFS responde 200 OK com
corpo VAZIO e um cabeçalho `refresh: ...force=logout`. Quem não conferir o corpo
gera o painel sobre uma base vazia sem receber aviso nenhum. Por isso toda
resposta passa por `validar_csv` antes de virar arquivo, e "vazio" aqui tem nome
próprio: SessaoVencida.

O login do OFS pede código (MFA), então ele é sempre humano -- o mesmo arranjo do
bot do CAMPO, que guarda a sessão num perfil de navegador persistente. A sessão
NÃO é amarrada ao IP: um `cookies.json` gerado no Windows funciona no servidor
Linux (medido em 20/08/2026, mesmas 330 atividades nas duas máquinas).

Uso direto (fora do bot):

    python ofs_extracao.py login     # abre o navegador e espera VOCÊ entrar
    python ofs_extracao.py baixar    # usa só os cookies guardados, sem navegador
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent
PASTA_DADOS = Path(os.environ.get('OFS_PASTA_DADOS', RAIZ / 'dados'))
ARQUIVO_COOKIES = Path(os.environ.get('OFS_COOKIES_ARQUIVO', PASTA_DADOS / 'ofs_cookies.json'))
ARQUIVO_CSV = Path(os.environ.get('OFS_EXTRACAO_ARQUIVO', PASTA_DADOS / 'OPERACIONAL.csv'))

# A agenda de hoje e amanhã, com o nome que o resto do sistema já procura.
#
# Quem lê: a tela de confirmação de agenda do site (ligar para o cliente ANTES
# do serviço, por isso amanhã entra) e o "Enviado D0" do backlog, no bot.
#
# Fica na pasta do BOT de propósito. Era o site que mantinha este arquivo, por
# upload, e o bot só o encontrava seguindo um `painel_config.json` que aponta
# para a pasta do site -- arquivo que não existe no servidor Linux. Resultado
# medido em 26/08/2026: o bot procurava em /opt/operacional/bot/dados e
# /opt/operacional/bot, não achava nada, e o cruzamento do "Enviado D0" estava
# desligado havia dias, avisando no log quatro vezes por hora. Gravando aqui,
# ele acha no primeiro lugar em que olha.
ARQUIVO_OFS_GERAL = Path(os.environ.get('OFS_GERAL_ARQUIVO', PASTA_DADOS / 'OFS GERAL.csv'))

# A janela do arquivo, em dias para trás e para frente de hoje.
#
# Para FRENTE só 1: a confirmação de agenda não passa de D+1 -- regra da
# operação, dita em 26/08/2026. Baixar D+2 em diante seria trazer agenda que
# ninguém vai confirmar e engordar o arquivo por nada.
#
# Para TRÁS 34: é o que sustenta as visões retrospectivas (semana corrente, mês
# parcial, e o detalhe dia a dia dos últimos dias). 34 + hoje + amanhã dá 36
# dias, que sempre cobre o mês corrente inteiro, mesmo no dia 31.
#
# Custo medido em 26/08/2026: 32 dias saíram em 7,4s (64 requisições, 3,6 MB).
OFS_GERAL_DIAS_ATRAS = int(os.environ.get('OFS_GERAL_DIAS_ATRAS', '34'))
OFS_GERAL_DIAS_FRENTE = int(os.environ.get('OFS_GERAL_DIAS_FRENTE', '1'))
ARQUIVO_LOG = Path(os.environ.get('OFS_SESSAO_LOG', RAIZ / 'logs' / 'ofs_sessao.log'))
PASTA_PERFIL = RAIZ / 'perfil_ofs'

# Para onde o cookie recém-nascido é publicado. O login acontece NUMA MÁQUINA
# COM TELA (o OFS pede código, e alguém tem de digitar), mas quem usa o cookie é
# o servidor. Sem este passo a renovação fica dependendo de alguém copiar o
# arquivo à mão -- e no dia em que essa pessoa não estiver, o painel para.
SERVIDOR = os.environ.get('OFS_SERVIDOR', 'operador@provedor.example')
COOKIES_NO_SERVIDOR = os.environ.get('OFS_COOKIES_REMOTO', '/opt/operacional/bot/dados/ofs_cookies.json')

BASE = 'https://campo.provedor.example'

# A receita de renovar a sessão, escrita uma vez só e repetida por quem avisa.
#
# Vai em TODO aviso de sessão vencida, com o caminho inteiro e pronto para
# colar: quem recebe o alerta no grupo está no celular, longe do teclado, e
# muitas vezes não é quem escreveu isto aqui. "Rode o login" é instrução para
# quem já sabe; o comando é instrução para qualquer um.
#
# O login roda no WINDOWS, não no servidor: o OFS pede código, e alguém tem de
# digitar numa tela. O `publicar_cookies` no fim manda o cookie para o Linux
# sozinho -- por isso o comando não menciona o servidor.
#
# Desde 01/09/2026 este NÃO é mais o caminho normal: a extensão do Chrome
# renova sem Python e sem esta máquina. Ele fica porque continua sendo o modo
# de renovar quando o site está fora do ar -- que é justamente quando a
# extensão não tem para onde mandar.
COMANDO_LOGIN = ('cd "C:\\caminho\\para\\Documents\\migracao_linux\\bot"; '
                 'python ofs_extracao.py login')

# Quem pode reconectar. Isto MUDOU em 01/09/2026, e a mudança é o ponto:
# antes a resposta era "só o administrador", porque a renovação exigia Python,
# Playwright e a máquina certa. Hoje qualquer operador renova pela extensão do
# Chrome, usando o login do OFS que ele já faz todo dia (ver
# regras/12-renovar-a-sessao-do-ofs.md).
#
# O aviso é o único lugar onde a operação inteira lê o que fazer, e enquanto ele
# dissesse "chame o operador" ninguém tentaria -- por mais que a extensão
# estivesse instalada em todas as máquinas.
RESPONSAVEL_RECONEXAO = ('*Qualquer um do grupo pode reconectar*, pela extensão '
                         'do Chrome — não precisa esperar o administrador.')

# A receita inteira, num lugar só. Os dois avisos que existem (painel de
# resultados e bases históricas) montavam o texto cada um do seu jeito a partir
# de duas constantes; agora eles colam esta. Duas receitas divergiriam, e a que
# ficasse velha mandaria a operação fazer o que não funciona mais.
INSTRUCAO_RECONEXAO = (
    RESPONSAVEL_RECONEXAO + '\n\n'
    '1. abra o OFS no Chrome e entre normalmente, com o código\n'
    '2. espere a página carregar\n'
    '3. clique no ícone *OPERACIONAL — Renovar sessão do OFS*\n'
    '4. clique em *Renovar agora*\n\n'
    'Não tem o ícone nessa máquina? Peça a instalação ao administrador — leva '
    'um minuto e é uma vez só.'
)

# providerId -> nome da área, como está na extensão (content.js, var AREAS).
AREAS = {
    '9163': 'OPERACIONAL CGT',
    '9301': 'OPERACIONAL RJ',
}

# Primeira coluna do cabeçalho da extração, para separar "CSV de verdade" de
# "página de login devolvida com 200".
INICIO_CABECALHO = 'Recurso,'

# O OFS trata requisição sem User-Agent de navegador como robô.
NAVEGADOR = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# Quantas vezes repetir um GET do OFS quando a CONEXÃO nem se abre
# (ConnectTimeout / ConnectionError / ReadTimeout). É a rota
# servidor -> VPN Provedor (túnel-cheio) -> Oracle Cloud que às vezes engasga
# por alguns segundos: medido 3x numa semana (05, 06 e 10/09/2026), sempre
# passageiro. Sem retry, um único engasgo mata o ciclo inteiro do painel de
# resultados -- visto 10/09/2026, o grupo levou "falha ao baixar a extração"
# e a hora passou. NÃO cobre SessaoVencida: essa vem de uma resposta HTTP que
# CHEGOU (200 com corpo vazio/login), e repetir não reloga.
GET_TENTATIVAS = int(os.environ.get('OFS_GET_TENTATIVAS', '3'))
GET_ESPERA_SEG = float(os.environ.get('OFS_GET_ESPERA_SEG', '4'))
# (connect, read): o connect curto faz um engasgo transitório falhar em 15s e
# dar lugar à retentativa, em vez de queimar os 90s inteiros como em 10/09.
GET_TIMEOUT = (15, 90)


def _get_ofs(sessao, url: str, rotulo: str):
    """GET no OFS com algumas tentativas quando a conexão falha antes da resposta.

    Só repete erro de transporte (a conexão não abriu / não respondeu). Erro de
    conteúdo -- sessão vencida, área vazia -- é decidido por quem lê o corpo, e
    repetir não muda nada.
    """
    espera = GET_ESPERA_SEG
    for tentativa in range(1, GET_TENTATIVAS + 1):
        try:
            return sessao.get(url, timeout=GET_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as falha:
            if tentativa >= GET_TENTATIVAS:
                raise
            logger.warning(
                'OFS: %s não respondeu (%s, tentativa %d/%d). Repetindo em %.0fs.',
                rotulo, type(falha).__name__, tentativa, GET_TENTATIVAS, espera)
            time.sleep(espera)
            espera *= 2


class SessaoVencida(RuntimeError):
    """A resposta chegou, mas chegou deslogada."""


# Quem recebe o aviso de sessão vencida NO PRIVADO, além do grupo.
#
# POR QUE NÃO BASTA O GRUPO
# -------------------------
# O aviso sempre saiu no grupo de comandos, junto de todo o resto. Quem tem de
# agir é quem consegue renovar -- e essa pessoa pode estar sem olhar o grupo,
# que recebe dezenas de mensagens por dia. No privado a mensagem não divide
# espaço com nada.
#
# POR QUE UM ARQUIVO, E NÃO SÓ UMA VARIÁVEL
# -----------------------------------------
# O WhatsApp entrega conversa privada com um identificador `@lid` opaco --
# `34132138688546@lid` -- que NÃO é o telefone e não se deduz dele. A variável
# BOT_PV_LIBERADOS guarda só os dígitos, e montar o JID grudando um sufixo é
# exatamente o erro que já aconteceu aqui: o `@lid` foi tratado na entrada e
# esquecido na saída, e o envio respondeu HTTP 500 sem ninguém perceber.
#
# Então o bot APRENDE: toda vez que alguém liberado escreve no privado, o JID
# inteiro que o WhatsApp mandou é guardado neste arquivo. Nada é construído, e
# no dia em que o WhatsApp mudar o formato de novo isso continua funcionando.
ARQUIVO_PV_CONHECIDOS = Path(os.environ.get(
    'BOT_PV_CONHECIDOS_ARQUIVO', PASTA_DADOS / 'pv_conhecidos.json'))


def lembrar_pv(jid: str) -> None:
    """Guarda o JID inteiro de uma conversa privada liberada."""
    jid = str(jid or '').strip()
    if not jid or '@' not in jid:
        return
    conhecidos = set(destinos_de_aviso_no_privado(so_arquivo=True))
    if jid in conhecidos:
        return
    conhecidos.add(jid)
    try:
        ARQUIVO_PV_CONHECIDOS.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_PV_CONHECIDOS.write_text(
            json.dumps(sorted(conhecidos), ensure_ascii=False, indent=1),
            encoding='utf-8')
    except OSError:
        logger.warning('Não consegui guardar o JID do privado em %s.',
                       ARQUIVO_PV_CONHECIDOS)


def destinos_de_aviso_no_privado(so_arquivo: bool = False) -> list[str]:
    """Os JIDs que recebem o aviso de sessão vencida no privado.

    OFS_AVISO_PV MANDA, quando está preenchida: os JIDs dela são os únicos que
    recebem. Quem escreve a lista à mão está escolhendo os destinatários, e
    somar os aprendidos por cima faria a mensagem chegar a quem foi
    deliberadamente deixado de fora.

    Sem ela, valem os aprendidos -- todo mundo que já escreveu no privado.
    """
    if not so_arquivo:
        bruto = os.environ.get('OFS_AVISO_PV', '')
        escolhidos = [p.strip() for p in bruto.replace(';', ',').split(',')
                      if p.strip()]
        if escolhidos:
            return [j for j in escolhidos if '@' in j]

    achados: list[str] = []
    try:
        achados += [str(j).strip() for j in
                    json.loads(ARQUIVO_PV_CONHECIDOS.read_text(encoding='utf-8'))
                    if str(j).strip()]
    except (OSError, ValueError):
        pass
    # Sem '@' não é JID, e mandar isso para a ponte devolve erro. Filtrar aqui
    # é mais barato do que descobrir pelo log que o aviso não saiu.
    vistos, limpos = set(), []
    for jid in achados:
        if '@' in jid and jid not in vistos:
            vistos.add(jid)
            limpos.append(jid)
    return limpos


def montar_url(data_str: str, provider_id: str) -> str:
    return (
        f'{BASE}/?m=gridexport&a=download&itype=manage&providerId={provider_id}'
        f'&date={data_str}&panel=top&view=time'
        f'&downloadId={uuid.uuid4().hex[:19]}&dates={data_str}&recursively=1&&{int(time.time() * 1000)}'
    )


def anotar(mensagem: str) -> None:
    """Uma linha no log do bot e outra no arquivo, com carimbo de hora.

    O arquivo existe para o caso standalone (rodar na mão no Windows), onde não
    há logger configurado, e para poder olhar a validade da sessão sem abrir o
    log inteiro do monitoramento.
    """
    logger.info(mensagem)
    linha = f'{datetime.now():%Y-%m-%d %H:%M:%S} {mensagem}'
    print(linha)
    try:
        ARQUIVO_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ARQUIVO_LOG, 'a', encoding='utf-8') as arquivo:
            arquivo.write(linha + '\n')
    except OSError as falha:
        logger.warning(f'Não consegui escrever em {ARQUIVO_LOG}: {falha}')


def validar_csv(texto: str, area: str) -> str:
    """Confere que a resposta é mesmo a extração, não a porta de saída do OFS."""
    if not texto or not texto.strip():
        raise SessaoVencida(f'{area}: resposta vazia (sessão do OFS vencida)')
    primeira = texto.lstrip('﻿').splitlines()[0]
    # O OFS devolve o cabeçalho com aspas ("Recurso","Data",...); o arquivo que
    # a extensão salvava vinha sem elas. As duas formas são a mesma extração,
    # então a conferência ignora as aspas em vez de eleger uma delas.
    if not primeira.replace('"', '').startswith(INICIO_CABECALHO):
        amostra = primeira[:80].replace('\r', ' ')
        raise SessaoVencida(f'{area}: resposta não é CSV da extração — começa com {amostra!r}')
    return texto


def linhas_de_dados(csv: str) -> int:
    """Quantas linhas o CSV tem ALÉM do cabeçalho."""
    linhas = [l for l in csv.splitlines() if l.strip()]
    return max(0, len(linhas) - 1)


def conferir_areas(por_area: list[tuple[str, str]]) -> dict[str, int]:
    """Recusa quando uma ÁREA INTEIRA vier sem nenhuma linha.

    POR QUE ISTO EXISTE
    -------------------
    A extração pede as áreas por `providerId` fixo, e não pelo que a pessoa
    logada enxerga. Desde 01/09/2026 a sessão pode vir de qualquer operador,
    pela extensão do Chrome -- e se a conta dele não tiver acesso a uma das
    áreas, o OFS devolve aquela área com o cabeçalho e ZERO linhas.

    Essa resposta passa no `validar_csv`: ela não está vazia e começa com o
    cabeçalho certo. A base seria refeita com metade da operação faltando, o
    backlog do Rio sumiria, e o painel mostraria um número menor com cara de
    número certo. É o mesmo erro que assusta em todo o resto deste sistema: o
    que sai calado.

    A conferência é sobre a JANELA INTEIRA, e não por dia, de propósito: um dia
    isolado sem atividade em uma área acontece (feriado, domingo), e recusar ali
    derrubaria a extração por um motivo legítimo.
    """
    total_por_area: dict[str, int] = {}
    for nome, csv in por_area:
        # `baixar_dias` nomeia cada pedaço como "ÁREA AAAA-MM-DD"; o nome da
        # área é o que vem antes da data.
        area = nome.rsplit(' ', 1)[0] if ' ' in nome else nome
        total_por_area[area] = total_por_area.get(area, 0) + linhas_de_dados(csv)

    vazias = sorted(a for a, quantas in total_por_area.items() if quantas == 0)
    if vazias:
        raise SessaoVencida(
            'a extração voltou sem NENHUMA linha em: %s. Isso costuma ser uma '
            'sessão de um usuário do OFS que não enxerga essa(s) área(s) -- '
            'quem renovou por último não tem acesso a tudo. Peça a renovação a '
            'quem enxerga as duas áreas. Nada foi gravado.'
            % ', '.join(vazias))
    return total_por_area


def consolidar(por_area: list[tuple[str, str]]) -> str:
    """Junta as áreas num CSV só, com um cabeçalho apenas.

    Mesma regra da extensão: linhas em branco fora, cabeçalho do primeiro que
    veio, o resto empilhado. O .strip() de cada linha também tira o \\r do CRLF.
    """
    linhas: list[str] = []
    cabecalho_posto = False
    for _area, csv in por_area:
        uteis = [linha for linha in csv.lstrip('﻿').split('\n') if linha.strip()]
        for indice, linha in enumerate(uteis):
            if indice == 0:
                if not cabecalho_posto:
                    linhas.append(linha.strip())
                    cabecalho_posto = True
            else:
                linhas.append(linha.strip())
    return '\n'.join(linhas)


def gravar(csv: str, destino: Path | None = None) -> Path:
    """Grava com BOM, como a extensão fazia, e sem deixar arquivo pela metade.

    O BOM não é enfeite: os leitores do site e do bot foram escritos contra o
    arquivo que a extensão salvava, e é assim que ele vem.
    """
    destino = destino or ARQUIVO_CSV
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + '.novo')
    temporario.write_text('﻿' + csv, encoding='utf-8', newline='\n')
    os.replace(temporario, destino)
    return destino


def carregar_cookies() -> dict[str, str]:
    if not ARQUIVO_COOKIES.exists():
        raise SessaoVencida(
            f'Não existe {ARQUIVO_COOKIES}: ninguém entrou no OFS nesta máquina ainda'
        )
    bruto = json.loads(ARQUIVO_COOKIES.read_text(encoding='utf-8'))
    cookies = {c['name']: c['value'] for c in bruto.get('cookies', [])}
    if not cookies:
        raise SessaoVencida(f'{ARQUIVO_COOKIES} não tem cookie nenhum dentro')
    return cookies


def baixar_extracao(data_str: str | None = None) -> tuple[Path, dict]:
    """Baixa as duas áreas e grava o CSV consolidado. Levanta SessaoVencida.

    É esta a função que o bot chama. Não abre navegador: dois GETs e um arquivo.
    """
    data_str = data_str or f'{datetime.now():%Y-%m-%d}'
    sessao = requests.Session()
    sessao.cookies.update(carregar_cookies())
    sessao.headers.update({'User-Agent': NAVEGADOR, 'Referer': f'{BASE}/'})

    por_area: list[tuple[str, str]] = []
    for provider_id, nome in AREAS.items():
        resposta = _get_ofs(sessao, montar_url(data_str, provider_id), nome)
        por_area.append((nome, validar_csv(resposta.text, nome)))
        time.sleep(0.5)

    detalhe = conferir_areas(por_area)
    csv = consolidar(por_area)
    caminho = gravar(csv)
    return caminho, {'atividades': csv.count('\n'), 'por_area': detalhe, 'data': data_str}


# Pausa entre pedidos quando são muitos. O `baixar_extracao` usa 0,5s entre as
# duas áreas; aqui são dezenas, e 0,5 viraria meio minuto de espera pura. 0,15s
# ainda é um pedido a cada sete por segundo, e a medição de 26/08/2026 mostrou
# o OFS respondendo em 0,115s cada, sem reclamar de 64 seguidos.
PAUSA_ENTRE_PEDIDOS = float(os.environ.get('OFS_PAUSA_ENTRE_PEDIDOS', '0.15'))


def baixar_dias(datas: list[str], sessao=None, pausa: float | None = None) -> str:
    """Baixa vários dias, as duas áreas de cada um, num CSV só.

    Um GET devolve UM dia -- medido em 26/08/2026 tentando `dates` com lista,
    `dateFrom`/`dateTo` e afins: nada disso muda o resultado. Então vários dias
    são vários pedidos, e a consolidação é a mesma da extensão.
    """
    pausa = PAUSA_ENTRE_PEDIDOS if pausa is None else pausa
    if sessao is None:
        sessao = requests.Session()
        sessao.cookies.update(carregar_cookies())
        sessao.headers.update({'User-Agent': NAVEGADOR, 'Referer': f'{BASE}/'})

    pedacos: list[tuple[str, str]] = []
    for data_str in datas:
        for provider_id, nome in AREAS.items():
            rotulo = f'{nome} {data_str}'
            resposta = _get_ofs(sessao, montar_url(data_str, provider_id), rotulo)
            pedacos.append((rotulo, validar_csv(resposta.text, rotulo)))
            time.sleep(pausa)
    # Antes de consolidar: nenhuma área pode ter vindo vazia na janela inteira.
    # Levanta SessaoVencida, e quem chama já sabe avisar no grupo.
    conferir_areas(pedacos)
    return consolidar(pedacos)


def dias_do_ofs_geral(hoje=None) -> list[str]:
    """As datas que o 'OFS GERAL.csv' cobre, da mais antiga para a mais nova."""
    from datetime import timedelta

    referencia = (hoje or datetime.now()).date()
    inicio = referencia - timedelta(days=OFS_GERAL_DIAS_ATRAS)
    total = OFS_GERAL_DIAS_ATRAS + 1 + OFS_GERAL_DIAS_FRENTE
    return [f'{inicio + timedelta(days=i):%Y-%m-%d}' for i in range(total)]


def gravar_ofs_geral(hoje=None) -> tuple[Path, dict]:
    """Monta o 'OFS GERAL.csv' com a janela inteira, e devolve (caminho, info).

    Levanta SessaoVencida como o resto: quem chama decide se isso derruba o
    ciclo ou é só um aviso. Nada é gravado se algum dia falhar -- meia agenda
    faria a confirmação ligar para parte dos clientes e achar que ligou para
    todos, que é pior do que não ter o arquivo.

    A janela inteira é refeita a cada vez, e não só o dia novo, pelo mesmo
    motivo das bases históricas: atividade muda depois do fato. Um serviço de
    ontem que foi concluído hoje de manhã só aparece certo se ontem for baixado
    de novo -- e a efetividade do dia anterior depende exatamente disso.
    """
    datas = dias_do_ofs_geral(hoje)
    csv = baixar_dias(datas)
    caminho = gravar(csv, ARQUIVO_OFS_GERAL)
    return caminho, {'dias': len(datas), 'de': datas[0], 'ate': datas[-1],
                     'atividades': max(0, csv.count('\n'))}


# ---------------------------------------------------------------------------
# Login: só aqui existe navegador, e só porque o OFS pede código (MFA)
# ---------------------------------------------------------------------------

def _guardar_cookies(contexto) -> list[str]:
    ARQUIVO_COOKIES.parent.mkdir(parents=True, exist_ok=True)
    cookies = contexto.cookies(BASE)
    ARQUIVO_COOKIES.write_text(
        json.dumps({'quando': datetime.now().isoformat(timespec='seconds'), 'cookies': cookies},
                   ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    try:
        os.chmod(ARQUIVO_COOKIES, 0o600)   # é credencial de sessão, não dado comum
    except OSError:
        pass
    return [c['name'] for c in cookies]


def fazer_login() -> None:
    """Abre o OFS na tela e espera você entrar. Senha e código não passam por aqui."""
    from playwright.sync_api import sync_playwright

    PASTA_PERFIL.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=str(PASTA_PERFIL / 'chrome'),
            headless=False,
            args=['--start-maximized'],
            no_viewport=True,
        )
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            pagina.goto(BASE, wait_until='domcontentloaded')

            print()
            print('=' * 70)
            print('Entre no OFS nesta janela: usuário, senha e o código do autenticador.')
            print('Quando o OFS terminar de abrir, volte aqui e tecle ENTER.')
            print('=' * 70)
            input('ENTER depois de entrar > ')

            nomes = _guardar_cookies(contexto)
            anotar(f'login: cookies guardados ({", ".join(nomes) or "NENHUM"})')
        finally:
            contexto.close()

    # A prova real é a mesma que o bot faz: baixar sem navegador.
    try:
        caminho, info = baixar_extracao()
        anotar(f'login: OK {info["atividades"]} atividades -> {caminho}')
    except SessaoVencida as falha:
        anotar(f'login: NÃO baixou — {falha}')
        return   # cookie que não baixa aqui também não vai servir no servidor

    # Publicar faz parte do login, não é um segundo comando: renovação em dois
    # passos é renovação que um dia fica pela metade -- e o pela metade aqui é
    # silencioso, porque o cookie novo funciona NESTA máquina e o servidor
    # continua com o velho.
    if rodando_no_servidor():
        anotar('login: já é a cópia do servidor; nada a publicar.')
    else:
        publicar_cookies()


def rodando_no_servidor() -> bool:
    """Este arquivo é o que está instalado em produção?

    Serve para o `login` não tentar publicar o cookie no próprio servidor: lá
    ele já está no lugar certo, e um scp de uma máquina para ela mesma só
    produziria um erro confuso na hora errada.
    """
    return str(RAIZ) == str(Path(COOKIES_NO_SERVIDOR).parent.parent)


def publicar_cookies() -> bool:
    """Leva o cookie desta máquina para o servidor, com dono e modo certos.

    600 e dono `operacional` porque o arquivo É a sessão: quem o lê entra no OFS
    como quem logou. Vai por /tmp e é instalado com `install` em vez de escrito
    direto -- assim o bot nunca lê um arquivo pela metade se um ciclo cair
    exatamente no meio da cópia.
    """
    if not ARQUIVO_COOKIES.is_file():
        anotar('publicar: não há cookie local para publicar.')
        return False

    temporario = '/tmp/ofs_cookies.publicar.json'
    passos = [
        ['scp', '-q', str(ARQUIVO_COOKIES), f'{SERVIDOR}:{temporario}'],
        ['ssh', SERVIDOR,
         f'sudo install -o operacional -g operacional -m 600 {temporario} {COOKIES_NO_SERVIDOR}'
         f' && rm -f {temporario}'],
    ]
    for passo in passos:
        resultado = subprocess.run(passo, capture_output=True, text=True)
        if resultado.returncode != 0:
            erro = (resultado.stderr or resultado.stdout or '').strip().splitlines()
            anotar(f'publicar: FALHOU em {passo[0]} — {erro[-1] if erro else "sem mensagem"}')
            return False

    anotar(f'publicar: cookie enviado para {SERVIDOR}:{COOKIES_NO_SERVIDOR}')
    return True


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING)
    comando = argv[1] if len(argv) > 1 else 'baixar'
    if comando == 'login':
        fazer_login()
        return 0
    if comando == 'publicar':
        return 0 if publicar_cookies() else 1
    if comando == 'baixar':
        try:
            caminho, info = baixar_extracao()
        except SessaoVencida as falha:
            anotar(f'baixar: SESSÃO VENCIDA — {falha}')
            return 2
        except Exception as falha:   # noqa: BLE001 - aqui o log é o produto
            anotar(f'baixar: FALHA {type(falha).__name__}: {falha}')
            return 3
        detalhe = ' + '.join(f'{nome}: {linhas}' for nome, linhas in info['por_area'].items())
        anotar(f'baixar: OK {info["atividades"]} atividades ({detalhe}) -> {caminho}')
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
