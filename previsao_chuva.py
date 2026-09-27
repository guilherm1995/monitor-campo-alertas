# -*- coding: utf-8 -*-
"""Risco de chuva por rota, colado na legenda da prévia da carga.

O QUE É
-------
A prévia da carga (`carga_litoral.py`) já diz QUANTO trabalho o dia seguinte
tem, por cidade e por turno. Este módulo cruza isso com a previsão de chuva do
Open-Meteo -- a mesma fonte que já alimenta o relógio do painel de TV, só que
aqui é hora a hora, não "agora", e para as quatro rotas do litoral, não uma.

O objetivo não é decidir nada sozinho. É dar à operação, ANTES de montar a
rota de amanhã, o mesmo tipo de contexto que ela já tem de cor sobre chuva em
época de temporal -- só que escrito, e sem precisar abrir outro app.

UM PONTO POR ROTA, E NÃO UM POR BAIRRO
---------------------------------------
A prévia não fala de bairro, fala das rotas que a prévia da carga já usa para
agrupar tudo -- as quatro do litoral (CARAGUATATUBA, ILHABELA, TOPO, COSTA
SUL) e uma por cidade no Sul RJ. A resolução do Open-Meteo é de uns 11 km,
então um ponto por bairro seria precisão falsa: o modelo não vê a diferença
entre Boiçucanga e Maresias, e fingir que vê só tornaria o aviso mais frágil
sem ficar mais certo.

No litoral os pontos são representativos da rota, escolhidos a olho. No Rio
são o CENTRO de cada cidade, e ali isso é mais honesto do que no litoral: as
cidades do Sul Fluminense estão a dezenas de quilômetros umas das outras, bem
acima da resolução do modelo, então cada uma tem previsão própria de verdade
-- o que não acontece entre Boiçucanga e Maresias.

As coordenadas do Rio foram buscadas no geocodificador do próprio Open-Meteo
em 23/09/2026 (Penedo veio do OpenStreetMap: é distrito de Itatiaia, não
município, e o gazetteer não o tem). Nenhuma delas foi medida pela operação --
ao contrário das listas de bairro de `carga_litoral.py`, que vieram de achado
real. Se algum dia isso incomodar (chuva que não chega, ou que não avisa),
ajustar a coordenada aqui é o primeiro lugar a olhar.

JANELA DO TURNO: TAMBÉM UM CHUTE
---------------------------------
`carga_litoral.ORDEM_TURNOS` diz a ordem, não o horário -- o OFS nunca
carimba hora de início e fim de turno, só o rótulo. `JANELA_DO_TURNO` abaixo é
uma estimativa de jornada de campo, não uma regra ditada pela operação como as
de `carga_litoral.py`. Está aqui para o aviso poder discriminar manhã de
tarde; se a operação corrigir os horários, é só ajustar a tabela.

LIGADO OU DESLIGADO
-------------------
Desde 23/09/2026 este aviso está DESLIGADO nas duas regionais, por decisão do
dono -- ver `CHUVA_ATIVA`. O módulo inteiro continua de pé e testado; só não
entra na legenda. Religar é uma variável de ambiente, não um rollback.

QUANDO NÃO AVISA (estando ligado)
----------------------------------
Nenhuma cidade acima do limiar: função devolve texto vazio, e a legenda da
prévia sai como sempre saiu. Falha ao consultar o Open-Meteo: mesma coisa --
warning no log, prévia sai sem o aviso de chuva. A prévia em si NUNCA pode
deixar de sair por causa disto (mesma regra do aviso de frescor do OFS, em
`atualizar_bases.py`) -- ver `linha_de_risco_de_chuva`, que é a única função
que quem manda a carga precisa chamar.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

# (cidade, latitude, longitude) -- mesma grafia de cidade que sai em
# `carga['capa']` (ver CARAGUATATUBA/ILHABELA/TOPO/COSTA_SUL em
# carga_litoral.py). Ordem não importa; é só a chave que precisa bater.
COORDENADAS_LITORAL = (
    # Mesmo ponto do relógio do painel de TV (LATITUDE_CLIMA/LONGITUDE_CLIMA
    # em bot_campo_monitoramento.py) -- de propósito, para não existirem dois
    # números "a temperatura de Caraguatatuba" divergindo no mesmo sistema.
    ('CARAGUATATUBA', -23.6208, -45.4131),
    # Ilhabela -- Vila, o centro histórico/porto da balsa.
    ('ILHABELA', -23.7778, -45.3572),
    # TOPO é o centro de São Sebastião (Centro, Varadouro, Topolândia --
    # ver TOPO_BAIRROS em carga_litoral.py), não um lugar chamado "Topo".
    ('TOPO', -23.8038, -45.4118),
    # COSTA SUL é a faixa de praia ao sul de São Sebastião (Boiçucanga,
    # Maresias, Juquehy...). Boiçucanga como ponto central da faixa -- é
    # também a cidade que dá nome à unidade SSTBO.
    ('COSTA SUL', -23.7908, -45.6144),
)

# HH inicial (inclusive) e final (exclusivo) de cada turno. Turno que não
# aparece aqui (rótulo novo da base, ou "Sem intervalo") cai em JANELA_PADRAO.
JANELA_DO_TURNO = {
    'Inicio Manhã': (6, 8),
    'Manhã': (8, 12),
    'Almoço': (12, 14),
    'Tarde': (14, 18),
}
JANELA_PADRAO = (7, 18)

# % de probabilidade de chuva (Open-Meteo) a partir da qual o turno entra no
# aviso. Env var para a operação ajustar sem depender de novo deploy -- mesma
# ideia de CARGA_AUTOMATICA_HORARIOS.
# (cidade, latitude, longitude) do Sul RJ -- a grafia é a que sai de
# `garantias_lista.CIDADES`, que é de onde `carga_rj.py` tira o nome da rota.
# Uma linha por cidade da regional, inclusive as que quase não aparecem na
# base: cidade sem atividade no dia simplesmente não entra no aviso, e deixá-la
# de fora daqui é que criaria o dia em que a carga aparece e a chuva não.
COORDENADAS_RJ = (
    ('Resende', -22.4689, -44.4467),
    ('Itatiaia', -22.4961, -44.5633),
    # Penedo é DISTRITO de Itatiaia, não município -- por isso não está no
    # geocodificador do Open-Meteo e veio do OpenStreetMap. Fica a ~9 km da
    # sede de Itatiaia, dentro da mesma célula do modelo; está aqui pela
    # grafia, para o caso de a sigla PNDO aparecer na base.
    ('Penedo', -22.4410, -44.5241),
    ('Porto Real', -22.4197, -44.2903),
    ('Volta Redonda', -22.5231, -44.1042),
    ('Barra Mansa', -22.5442, -44.1714),
    ('Pinheiral', -22.5128, -44.0006),
    ('Barra do Piraí', -22.4700, -43.8256),
    ('Vassouras', -22.4039, -43.6625),
    ('Valença', -22.2456, -43.7003),
    ('Miguel Pereira', -22.4539, -43.4689),
    ('Paty do Alferes', -22.4286, -43.4186),
    ('Três Rios', -22.1167, -43.2092),
    ('Paraíba do Sul', -22.1585, -43.2932),
    ('Comendador Levy Gasparian', -22.0286, -43.2050),
    ('Cabo Frio', -22.8872, -42.0262),
)

# A chave é a mesma `carga['regiao']` que o recorte da prévia carrega, para não
# existir um segundo nome de regional neste sistema.
COORDENADAS_POR_REGIAO = {
    'litoral': COORDENADAS_LITORAL,
    'rj': COORDENADAS_RJ,
}

# DESLIGADO em 23/09/2026, a pedido do dono, nas duas regionais.
#
# Nada foi removido: as coordenadas, o limiar, as janelas de turno e os testes
# continuam aqui e continuam passando. O que este interruptor faz é impedir que
# a linha de chuva entre na legenda da prévia -- e é o único ponto em que isso
# é decidido, porque `linha_de_risco_de_chuva` é a única porta que o envio da
# carga usa.
#
# O padrão mora NO CÓDIGO, e não num drop-in do systemd, de propósito: assim
# quem abrir este arquivo vê que está desligado, em vez de ler uma tabela de
# coordenadas em pleno uso e concluir que o aviso sai. Para religar:
# `CARGA_CHUVA_ATIVO=1` no ambiente do campo-bot.
CHUVA_ATIVA = os.environ.get('CARGA_CHUVA_ATIVO', '0') != '0'

LIMIAR_CHUVA_PADRAO = int(os.environ.get('CARGA_CHUVA_LIMIAR', '60'))

# Quantos trechos (cidade + turno) o aviso lista antes de resumir o resto.
#
# No litoral são 4 rotas e o aviso nunca passou disso. O Rio tem 16 cidades, e
# num dia de frente fria a lista inteira viraria trinta linhas grudadas na
# legenda de uma imagem -- que ninguém lê. O que importa é o PIOR, e ele está
# no topo: a ordenação é por probabilidade.
TETO_LINHAS_CHUVA = int(os.environ.get('CARGA_CHUVA_TETO_LINHAS', '10'))


def buscar_chuva_por_cidade(dia, cidades=None):
    """Probabilidade de chuva (%), hora a hora, no dia pedido, por rota.

    Devolve `{cidade: {'HH': probabilidade_0_a_100}}`, ou `None` se a consulta
    falhar -- nunca levanta exceção, mesma regra de `buscar_previsao_tempo`
    em bot_campo_monitoramento.py.

    Um pedido só para as quatro rotas: o Open-Meteo aceita latitude/longitude
    em lista separada por vírgula e devolve os blocos na mesma ordem, em vez
    de precisar de quatro requisições.
    """
    cidades = cidades or COORDENADAS_LITORAL
    data_str = dia.strftime('%Y-%m-%d') if hasattr(dia, 'strftime') else str(dia)
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=%s&longitude=%s"
            "&hourly=precipitation_probability"
            "&timezone=America%%2FSao_Paulo"
            "&start_date=%s&end_date=%s"
            % (','.join(str(lat) for _, lat, _ in cidades),
               ','.join(str(lon) for _, _, lon in cidades),
               data_str, data_str)
        )
        resposta = requests.get(url, timeout=15)
        resposta.raise_for_status()
        dados = resposta.json()
    except Exception as e:
        logger.warning('Previsão de chuva: falha ao consultar o Open-Meteo: %s', e)
        return None

    # Uma cidade só -> a API devolve um objeto; mais de uma -> uma lista, na
    # mesma ordem do pedido. Uniformiza para sempre tratar lista daqui pra
    # frente.
    if isinstance(dados, dict):
        dados = [dados]

    resultado = {}
    for (cidade, _, _), bloco in zip(cidades, dados):
        horario = (bloco or {}).get('hourly', {})
        horas = horario.get('time', [])
        probs = horario.get('precipitation_probability', [])
        resultado[cidade] = {
            h.split('T')[1][:2]: p for h, p in zip(horas, probs) if 'T' in h
        }
    return resultado


def risco_por_cidade_turno(carga, previsao, limiar=None):
    """Cruza a prévia com a previsão, turno a turno, cidade a cidade.

    Devolve uma lista de `{'cidade', 'turno', 'probabilidade', 'os'}`, do pior
    para o melhor, só com quem passou do limiar -- turno sem chuva relevante
    não aparece, e cidade sem previsão (falha parcial da consulta) é pulada
    em vez de contar como "sem risco".

    A contagem de O.S. por turno é somada aqui na mão (em vez de chamar
    `carga_litoral.total_da_cidade`) de propósito: este módulo não precisa de
    tudo que `carga_litoral.py` carrega (pandas incluso) só para uma soma de
    quatro linhas, e um módulo de previsão do tempo não tem por que depender
    de leitor de planilha.
    """
    limiar = LIMIAR_CHUVA_PADRAO if limiar is None else limiar
    if not previsao:
        return []

    riscos = []
    for cidade, tipos in carga.get('capa', {}).items():
        chuva_cidade = previsao.get(cidade)
        if not chuva_cidade:
            continue
        contagem_por_turno = {}
        for turno_contagem in tipos.values():
            for turno, n in turno_contagem.items():
                contagem_por_turno[turno] = contagem_por_turno.get(turno, 0) + n
        for turno, total_os in contagem_por_turno.items():
            hora_ini, hora_fim = JANELA_DO_TURNO.get(turno, JANELA_PADRAO)
            probs = [p for h, p in chuva_cidade.items()
                     if p is not None and hora_ini <= int(h) < hora_fim]
            if not probs:
                continue
            pico = max(probs)
            if pico >= limiar:
                riscos.append({
                    'cidade': cidade,
                    'turno': turno,
                    'probabilidade': pico,
                    'os': total_os,
                })
    riscos.sort(key=lambda r: -r['probabilidade'])
    return riscos


def texto_risco_chuva(carga, riscos):
    """O bloco pronto para colar na legenda da prévia, ou '' se não há risco.

    Reordena do pior para o melhor por conta própria -- não confia que quem
    chamou já mandou `riscos` na ordem de `risco_por_cidade_turno`.
    """
    if not riscos:
        return ''
    dia = carga['data'].strftime('%d/%m')
    linhas = ['⛈️ *Risco de chuva — %s:*' % dia]
    ordenados = sorted(riscos, key=lambda r: -r['probabilidade'])
    for r in ordenados[:TETO_LINHAS_CHUVA]:
        linhas.append('%s — %s, %d%% de chance (%d O.S.)' % (
            r['cidade'].title(), r['turno'], r['probabilidade'], r['os']))
    sobrando = ordenados[TETO_LINHAS_CHUVA:]
    if sobrando:
        # O que sobrou não some: vira uma linha com a conta, para quem lê saber
        # que o risco é mais largo do que a lista mostra.
        linhas.append('_e mais %d trecho(s) acima de %d%%, somando %d O.S._' % (
            len(sobrando), min(r['probabilidade'] for r in sobrando),
            sum(r['os'] for r in sobrando)))
    return '\n'.join(linhas)


def linha_de_risco_de_chuva(carga):
    """O que `gerar_e_enviar_carga` chama: nunca levanta exceção.

    Uma previsão de chuva que falha não pode derrubar o envio da carga, que é
    o que importa de verdade -- mesma regra de `atualizar_bases.py` com o OFS
    vencido. Em qualquer falha, devolve '' e a legenda sai como sempre saiu.
    """
    if not CHUVA_ATIVA:
        # Uma linha por envio (três por dia, por regional) -- barato, e é o que
        # responde "por que o aviso sumiu?" sem ninguém precisar ler o código.
        logger.info('Aviso de chuva desligado (CARGA_CHUVA_ATIVO=0). A prévia '
                    'sai sem ele.')
        return ''
    try:
        # A regional vem da própria prévia (`Recorte.chave`, em
        # carga_litoral.py). Sem esta escolha, o Rio seria consultado nos
        # pontos do litoral e NENHUMA cidade casaria -- o aviso sairia sempre
        # vazio, calado, parecendo "não vai chover".
        regiao = (carga.get('regiao') or 'litoral')
        cidades = COORDENADAS_POR_REGIAO.get(regiao)
        if cidades is None:
            logger.warning('Previsão de chuva: regional %r sem tabela de '
                           'coordenadas. Aviso não sai.', regiao)
            return ''
        previsao = buscar_chuva_por_cidade(carga['data'], cidades=cidades)
        riscos = risco_por_cidade_turno(carga, previsao)
        return texto_risco_chuva(carga, riscos)
    except Exception:
        logger.exception('Previsão de chuva: falha ao montar o aviso '
                         '(a prévia da carga segue sem ele).')
        return ''
