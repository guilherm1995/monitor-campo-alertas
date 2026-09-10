# -*- coding: utf-8 -*-
"""As consultas que saem da máquina: Autenticador e Wi-Fi.

POR QUE FICAM SEPARADAS DAS OUTRAS
----------------------------------
O `busca_operacao.py` lê arquivo em disco: é rápido, é barato, e a resposta é
a mesma se você perguntar duas vezes seguidas. Estas duas não são nada disso.

Elas falam com outro sistema pela rede, dependem da VPN estar de pé, demoram
segundos, e a resposta muda de um minuto para o outro -- é justamente esse o
valor delas, porque "o cliente está online AGORA" não existe em arquivo
nenhum.

Misturar as duas naturezas no mesmo módulo faria uma busca de contrato herdar
o custo e a fragilidade de uma consulta de rede. Ficam aqui, e quem chama sabe
o que está pagando.

O QUE CADA UMA RESPONDE
-----------------------
- consultar_autenticador(contrato): está online? se caiu, quando? Responde a
  pergunta que a operação faz o dia inteiro -- "quando esse cliente caiu?".
- consultar_wifi(contrato): o nome da rede e a senha que foram provisionados.
"""
import io
import logging
import os
import re
import threading
import time

import requests

logger = logging.getLogger(__name__)

# O Autenticador responde só para quem está dentro do túnel. Sem VPN, a resposta
# volta sem a tabela, e o erro diz exatamente isso em vez de um 500 mudo.
AUTENTICADOR_URL_SAVE = os.environ.get(
    'AUTENTICADOR_URL_SAVE',
    'https://provedor.example/status.php?action=save')
AUTENTICADOR_URL_PROCESSA = os.environ.get(
    'AUTENTICADOR_URL_PROCESSA',
    'https://provedor.example/processa.php?bg=1')
AUTENTICADOR_URL_LER_CSV = os.environ.get(
    'AUTENTICADOR_URL_LER_CSV',
    'https://provedor.example/ler_csv.php')

ESPERA_MAX_CSV_SEG = float(os.environ.get('AUTENTICADOR_ESPERA_MAX_SEG', '5'))
INTERVALO_LEITURA_SEG = float(os.environ.get('AUTENTICADOR_INTERVALO_SEG', '0.8'))
TENTATIVAS = int(os.environ.get('AUTENTICADOR_TENTATIVAS', '3'))

PROVEDOR_BASE_URL = os.environ.get('PROVEDOR_AUTO_BASE_URL',
                                 'https://auto.provedor.example')
PROVEDOR_SESSAO_TTL_SEG = 20 * 60

# O login do Provedor, o MESMO que a consulta de dBm usa.
#
# Esta declarado aqui e tambem no bot_campo_monitoramento.py: sao duas copias, e
# isso e uma escolha, nao um descuido. A alternativa tentada antes era ler a
# constante do outro arquivo por analise sintatica, e ela custava montar em
# memoria a arvore de um modulo de 9.000 linhas a cada subida do bot -- caro
# demais para duas strings, num processo que ja tem historico de fragmentacao
# de heap.
#
# O preco da escolha: quando esta senha mudar, ela muda nos DOIS lugares.
# PROVEDOR_AUTO_EMAIL e PROVEDOR_AUTO_SENHA no ambiente vencem as duas, e sao o
# jeito de trocar sem tocar em codigo.
PROVEDOR_EMAIL = os.environ.get('PROVEDOR_AUTO_EMAIL', 'operador.ss.operacional@provedor.example')
PROVEDOR_SENHA = os.environ.get('PROVEDOR_AUTO_SENHA', 'SUA_SENHA_DO_PORTAL')

_provedor_sessao = None
_provedor_sessao_ts = 0
_provedor_lock = threading.Lock()


# ================================== AUTENTICADOR ===================================
def _ler_tabela(sessao):
    """Uma leitura do CSV do Autenticador, normalizada. Devolve (tabela, erro)."""
    import pandas as pd

    resposta = sessao.get(AUTENTICADOR_URL_LER_CSV, verify=False, timeout=(5, 35))
    corpo = resposta.text
    if '<table>' not in corpo:
        return None, ('O Autenticador respondeu sem tabela. Quase sempre é a VPN '
                      'fora do ar.')
    try:
        tabelas = pd.read_html(io.StringIO(corpo))
    except ValueError:
        # Tabela presente e ainda sem linha: o servidor não terminou de
        # escrever. Não é erro, é "espere mais um pouco".
        return None, None
    if not tabelas:
        return None, None

    tabela = tabelas[0]
    tabela.columns = [c.lower().strip() for c in tabela.columns]
    tabela.rename(columns={
        'contrato': 'CONTRATO', 'username': 'USUARIO',
        'acctstarttime': 'INICIO', 'acctstoptime': 'FIM',
        'circuitid': 'CIRCUITO', 'callingstationid': 'MAC',
        'trafego': 'TRAFEGO', 'servidor': 'SERVIDOR',
    }, inplace=True)
    for coluna in ('CONTRATO', 'USUARIO', 'INICIO', 'FIM', 'CIRCUITO', 'MAC',
                   'TRAFEGO', 'SERVIDOR'):
        if coluna not in tabela.columns:
            tabela[coluna] = ''
    tabela['CONTRATO'] = tabela['CONTRATO'].map(
        lambda v: str(v).replace('.0', '').strip())
    return tabela, None


def _esperar_resultado(sessao, contratos):
    """Espera o CSV virar o resultado DESTA consulta.

    `refazer=True` quer dizer que o arquivo está com resultado de outra
    pessoa: reler não adianta, é preciso refazer o pedido.
    """
    import pandas as pd

    pedidos = {str(c).strip() for c in contratos}
    limite = time.time() + ESPERA_MAX_CSV_SEG
    assinatura_anterior = None
    ultima_tabela = None
    ultimo_erro = None

    while True:
        tabela, erro = _ler_tabela(sessao)
        if erro:
            ultimo_erro = erro
        elif tabela is not None:
            encontrados = {str(c).strip() for c in tabela['CONTRATO']}
            if encontrados - pedidos:
                # Contrato que ninguém aqui pediu só pode ter vindo de outra
                # consulta: o arquivo do servidor é global.
                return None, None, True
            if pedidos.issubset(encontrados):
                return tabela, None, False
            # Tabela VAZIA não é resposta: é o instante em que o servidor
            # está reescrevendo o arquivo. Aceitá-la fazia a retentativa ser
            # engolida e todo contrato virar NÃO LOCALIZADO.
            if encontrados:
                ultima_tabela = tabela
                assinatura = (len(tabela), tuple(sorted(encontrados)))
                if assinatura == assinatura_anterior:
                    return tabela, None, False
                assinatura_anterior = assinatura

        if time.time() >= limite:
            break
        time.sleep(INTERVALO_LEITURA_SEG)

    return ultima_tabela, ultimo_erro, ultima_tabela is None


def consultar_autenticador(contrato):
    """Situação de conexão de um contrato, agora.

    O CSV de resultado do Autenticador é UM arquivo só no servidor, compartilhado
    por todos os usuários daquela ferramenta. Quando alguém dispara uma
    consulta grande, ela sobrescreve a nossa, e sem tratar isso todo contrato
    "some" e vira NÃO LOCALIZADO -- indistinguível de cliente sem sessão. É a
    causa de o status oscilar sem nada mudar na rede. Reler não resolve,
    porque o pedido foi atropelado; o que resolve é refazer o ciclo inteiro.
    """
    alvo = ''.join(c for c in str(contrato or '') if c.isdigit())
    if not alvo:
        return {'erro': 'O contrato veio sem dígito nenhum.'}

    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass

    sessao = requests.Session()
    corpo = {'contratos': alvo}
    ultimo_erro = None

    for tentativa in range(1, TENTATIVAS + 1):
        try:
            sessao.post(AUTENTICADOR_URL_SAVE, data=corpo, verify=False,
                        timeout=(5, 35))
            sessao.get(AUTENTICADOR_URL_PROCESSA, verify=False, timeout=(5, 35))
            tabela, erro, refazer = _esperar_resultado(sessao, [alvo])
        except Exception as falha:
            logger.warning('Autenticador: falha na consulta do %s: %s', alvo, falha)
            return {'contrato': alvo, 'erro': f'Não consegui falar com o '
                                              f'Autenticador ({falha}).',
                    'aviso': 'Sem esta consulta não dá para dizer se o '
                             'cliente está online. Não afirme nem uma coisa '
                             'nem outra.'}
        if erro:
            ultimo_erro = erro
        if not refazer and tabela is not None:
            return _resumir_autenticador(alvo, tabela)
        if tentativa < TENTATIVAS:
            logger.info('Autenticador: resultado veio de outra origem '
                        '(tentativa %s/%s); refazendo.', tentativa, TENTATIVAS)
            time.sleep(1.5)

    return {'contrato': alvo,
            'erro': ultimo_erro or ('O Autenticador não devolveu o resultado desta '
                                    'consulta em %s tentativas.' % TENTATIVAS),
            'aviso': 'O arquivo de resultado do Autenticador é compartilhado e foi '
                     'sobrescrito por outra consulta. Isto NÃO quer dizer que '
                     'o cliente não existe nem que está offline -- quer dizer '
                     'que não deu para saber. Diga isso.'}


def _resumir_autenticador(contrato, tabela):
    """As sessões do contrato viram uma resposta que a operação usa."""
    import pandas as pd

    linhas = tabela[tabela['CONTRATO'] == contrato]
    if linhas.empty:
        return {
            'contrato': contrato,
            'situacao': 'NAO LOCALIZADO',
            'aviso': 'O Autenticador respondeu, mas não trouxe sessão nenhuma para '
                     'este contrato. Pode ser cliente sem sessão registrada, '
                     'ou contrato que não existe lá. Não afirme que ele está '
                     'offline -- diga que não foi localizado no Autenticador.',
        }

    # Sessão sem hora de FIM é sessão aberta: é assim que se sabe que está
    # online, e não por um campo de status, que o Autenticador não devolve.
    def vazio(valor):
        return pd.isna(valor) or str(valor).strip() in ('', 'nan')

    abertas = linhas[linhas['FIM'].map(vazio)]
    online = not abertas.empty
    principal = abertas.iloc[0] if online else linhas.iloc[0]

    resultado = {
        'contrato': contrato,
        'situacao': 'ONLINE' if online else 'OFFLINE',
        'sessoes_no_registro': int(len(linhas)),
        'circuito': str(principal.get('CIRCUITO') or '').strip() or None,
        'mac': str(principal.get('MAC') or '').strip() or None,
        'servidor': str(principal.get('SERVIDOR') or '').strip() or None,
    }
    if online:
        resultado['conectado_desde'] = str(principal.get('INICIO') or '').strip()
        resultado['trafego'] = str(principal.get('TRAFEGO') or '').strip()
        resultado['observacao'] = ('Está online agora. "conectado_desde" é '
                                   'quando esta sessão começou.')
    else:
        # A pergunta que a operação faz: "quando esse cliente caiu?"
        resultado['caiu_em'] = str(principal.get('FIM') or '').strip()
        resultado['ultima_conexao_comecou_em'] = str(
            principal.get('INICIO') or '').strip()
        resultado['observacao'] = (
            'Está offline. "caiu_em" é o fim da última sessão, que é a hora '
            'em que ele caiu. Uma queda antiga com o cliente reclamando agora '
            'é sinal diferente de uma queda de minutos atrás.')
    return resultado


# ================================== WI-FI ====================================
def _sessao_provedor():
    """Sessão autenticada no auto.provedor.example, reaproveitada por 20 minutos."""
    global _provedor_sessao, _provedor_sessao_ts
    with _provedor_lock:
        agora = time.time()
        if _provedor_sessao and (agora - _provedor_sessao_ts) < PROVEDOR_SESSAO_TTL_SEG:
            return _provedor_sessao
        if not PROVEDOR_EMAIL or not PROVEDOR_SENHA:
            raise RuntimeError(
                'Sem credencial do Provedor. Defina PROVEDOR_AUTO_EMAIL e '
                'PROVEDOR_AUTO_SENHA no ambiente.')

        sessao = requests.Session()
        pagina = sessao.get(f'{PROVEDOR_BASE_URL}/users/sign_in', timeout=15)
        token = re.search(r'name="authenticity_token" value="([^"]+)"',
                          pagina.text)
        dados = {'user[email]': PROVEDOR_EMAIL, 'user[password]': PROVEDOR_SENHA,
                 'commit': 'Entrar'}
        if token:
            dados['authenticity_token'] = token.group(1)
        entrada = sessao.post(f'{PROVEDOR_BASE_URL}/users/sign_in', data=dados,
                              timeout=20, allow_redirects=True)
        if '/users/sign_in' in entrada.url:
            raise RuntimeError('O Provedor recusou o login.')

        _provedor_sessao, _provedor_sessao_ts = sessao, agora
        logger.info('Provedor: sessão aberta para consulta de Wi-Fi.')
        return sessao


# As chaves do registro de provisionamento, na ordem de preferência. A que
# começa com ap_ é a do roteador entregue pela operação; a sem prefixo é a da
# própria ONU.
_CHAVES_WIFI = (
    ('rede', ('ap_wifi_ssid', 'wifi_ssid')),
    ('senha', ('ap_wifi_password', 'wifi_password')),
)


def _valor_escapado(html, chave):
    """O valor de uma chave dentro do registro serializado da página.

    O registro vem escapado em HTML (&quot;chave&quot; =&gt; &quot;valor&quot;),
    porque é um dump gravado dentro da própria tela.
    """
    achado = re.search(r'&quot;%s&quot;\s*=&gt;\s*&quot;([^&]*)&quot;'
                       % re.escape(chave), html)
    if achado:
        return achado.group(1).strip() or None
    # Alguns registros vêm sem escape, quando a tela monta o JSON direto.
    achado = re.search(r'"%s"\s*(?:=>|:)\s*"([^"]*)"' % re.escape(chave), html)
    return (achado.group(1).strip() or None) if achado else None


def consultar_wifi(contrato):
    """Nome da rede e senha do Wi-Fi provisionados para o contrato."""
    alvo = ''.join(c for c in str(contrato or '') if c.isdigit())
    if not alvo:
        return {'erro': 'O contrato veio sem dígito nenhum.'}

    try:
        sessao = _sessao_provedor()
    except Exception as falha:
        return {'contrato': alvo, 'erro': f'Não consegui entrar no Provedor '
                                          f'({falha}).',
                'aviso': 'Sem isto não dá para dizer a rede nem a senha. Não '
                         'invente nenhuma das duas.'}

    try:
        pagina = sessao.get(f'{PROVEDOR_BASE_URL}/contracted_services/{alvo}',
                            timeout=20, allow_redirects=True)
    except Exception as falha:
        return {'contrato': alvo,
                'erro': f'O Provedor não respondeu ({falha}).'}

    if '/users/sign_in' in pagina.url:
        return {'contrato': alvo, 'erro': 'A sessão do Provedor venceu.'}
    if pagina.status_code == 404:
        return {'contrato': alvo,
                'aviso': 'Este contrato não existe no Provedor. Isto é uma '
                         'ausência APURADA.'}

    html = pagina.content.decode('utf-8', errors='ignore')
    resultado = {'contrato': alvo}
    for rotulo, chaves in _CHAVES_WIFI:
        for chave in chaves:
            valor = _valor_escapado(html, chave)
            if valor:
                resultado[rotulo] = valor
                resultado[rotulo + '_veio_de'] = chave
                break

    if 'rede' not in resultado and 'senha' not in resultado:
        resultado['aviso'] = (
            'O contrato existe no Provedor, mas a página não traz rede nem '
            'senha de Wi-Fi. Costuma ser instalação sem roteador nosso, ou '
            'provisionamento feito por outro caminho. Diga isso em vez de '
            'dizer que não há Wi-Fi.')
        return resultado

    resultado['observacao'] = (
        'Estes são os dados PROVISIONADOS no sistema, e não uma leitura do '
        'roteador agora. Se o cliente trocou o nome ou a senha no aparelho, '
        'o que está aqui é o original. Diga isso quando entregar.')
    return resultado
