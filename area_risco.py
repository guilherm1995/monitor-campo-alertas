# ============ ÁREA DE RISCO: o mapa do RJ, consultado por entrante ============
#
# O que este módulo faz: dado um entrante, dizer se o endereço dele cai numa
# área marcada como risco pela operação do Rio.
#
# É o mesmo desenho da reincidência de improdutiva (improdutivas.py): uma
# pergunta de sim/não por entrante, respondida em memória, que vira um
# segundo alerta no grupo da região.
#
# A pergunta é respondida de DOIS jeitos, nesta ordem:
#
#   1. Pelo POLÍGONO. O CAMPO manda a coordenada do endereço em
#      ordemServicos[].lat/lng, e o mapa da operação (Google My Maps) é um
#      desenho no chão. Ponto dentro do desenho = risco. É a resposta boa:
#      não depende de como o endereço foi escrito, e quando a operação
#      redesenha a área o mapa é a verdade.
#
#   2. Pela LISTA DE RUAS, sempre que o polígono não disse sim. Ela cobre os
#      dois buracos do desenho: chamado sem coordenada (1 dos 7 da amostra de
#      28/08/2026 veio com lat/lng nulos, e chamado recém-aberto pode nem ter
#      ordemServicos ainda) e coordenada errada por uma quadra, que é comum
#      quando o número da casa está torto no cadastro.
#
# As duas somam, e nenhuma veta a outra: qualquer uma que aponte risco gera o
# aviso. Para área de risco o erro barato é avisar demais -- o caro é o
# técnico entrar sem saber. A mensagem diz por qual das duas casou, para quem
# lê saber o quanto conferir.
#
# ---------------------------------------------------------------------------
# De onde vêm os polígonos
#
# Mapa "ÁREA DE RISCO VRD/BMA", no Google My Maps da operação:
#   https://www.google.com/maps/d/u/0/viewer?mid=1gJ5vP1_-25WX19JI6ETKlsq23aDBrJE
#
# O KML sai em:
#   https://www.google.com/maps/d/kml?mid=1gJ5vP1_-25WX19JI6ETKlsq23aDBrJE&forcekml=1
#
# Os polígonos abaixo foram extraídos dele em 28/08/2026. Estão COPIADOS aqui,
# não lidos do ar, e a razão é a mesma que vale para o resto do bot: uma
# dependência de rede no caminho do alerta é um jeito novo de emudecer sem
# erro nenhum -- o Google fora do ar, a VPN atravessada, o mapa trocado de
# dono, e o bot para de avisar sem que ninguém veja. Copiado, ele responde
# igual todo dia.
#
# O preço é que o mapa e este arquivo podem divergir: se a operação redesenhar
# a área, aqui não muda sozinho. Quando redesenharem, rode
# `python atualizar_area_risco.py` (mesma pasta), que rebaixa o KML e reescreve
# o bloco POLIGONOS_DE_RISCO -- e publique o arquivo como qualquer outra
# mudança.
#
# ATENÇÃO ao que o mapa contém: além das áreas de risco, ele tem uma camada
# de polígonos "ÁREA n NOME" (Denny, Bruno, Paulo, Lucas, Marinor, Junior,
# Wallace) que são divisão de equipe, NÃO área de risco. A operação confirmou
# em 28/08/2026 que é para desconsiderar essa camada. Só entram as pastas
# "ÁREA DE BLOQUEIO (Área de risco)" e "ÁREA DE RISCO BMA" -- e é isso que o
# filtro de `atualizar_area_risco.py` procura, por BLOQUEIO ou RISCO no nome
# da pasta. Camada de risco criada com outro nome NÃO entra, e o script avisa
# quais pastas ignorou justamente por causa disso.
# ---------------------------------------------------------------------------
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# Cada polígono é uma lista de (lat, lng), na ordem do desenho. Os dois
# primeiros são quase iguais: o KML traz a mesma área em duas camadas, com uns
# 10 metros de diferença entre elas. Os dois ficam. Sobrepor área de risco com
# área de risco não custa nada (o aviso sai uma vez só), e escolher qual das
# duas está certa seria eu decidindo por quem desenhou.
POLIGONOS_DE_RISCO = [
    {
        'nome': 'ÁREA DE BLOQUEIO (Área de risco)',
        'pontos': [
            (-22.504006, -44.119317), (-22.505215, -44.122192), (-22.505938, -44.122182),
            (-22.506335, -44.121002), (-22.505730, -44.119607), (-22.505354, -44.118872),
            (-22.504957, -44.118384), (-22.503906, -44.118985), (-22.504006, -44.119317),
        ],
    },
    {
        'nome': 'ÁREA DE BLOQUEIO (Área de risco)',
        'pontos': [
            (-22.503907, -44.119298), (-22.505116, -44.122173), (-22.505839, -44.122163),
            (-22.506236, -44.120983), (-22.505631, -44.119588), (-22.505255, -44.118853),
            (-22.504858, -44.118365), (-22.503807, -44.118966), (-22.503907, -44.119298),
        ],
    },
    {
        'nome': 'ÁREA DE BLOQUEIO (Área de risco)',
        'pontos': [
            (-22.493060, -44.076103), (-22.493015, -44.075588), (-22.492973, -44.075095),
            (-22.492698, -44.074148), (-22.492393, -44.073550), (-22.492359, -44.072920),
            (-22.492613, -44.072344), (-22.492515, -44.071965), (-22.492693, -44.071542),
            (-22.493248, -44.071565), (-22.493545, -44.071130), (-22.494538, -44.069964),
            (-22.494329, -44.069141), (-22.494190, -44.068970), (-22.494031, -44.068970),
            (-22.493814, -44.068779), (-22.493258, -44.068650), (-22.492237, -44.068156),
            (-22.491467, -44.067829), (-22.491211, -44.068103), (-22.490661, -44.069079),
            (-22.489675, -44.070737), (-22.489754, -44.071563), (-22.489729, -44.071745),
            (-22.489686, -44.072351), (-22.489638, -44.073064), (-22.489629, -44.073570),
            (-22.489616, -44.073899), (-22.489613, -44.074658), (-22.489615, -44.075093),
            (-22.489671, -44.075511), (-22.489660, -44.075820), (-22.489738, -44.076204),
            (-22.489852, -44.076519), (-22.489846, -44.077292), (-22.490176, -44.078089),
            (-22.490115, -44.078446), (-22.490049, -44.078559), (-22.489983, -44.079660),
            (-22.489822, -44.080568), (-22.489833, -44.080899), (-22.489567, -44.081252),
            (-22.488954, -44.081570), (-22.487541, -44.082910), (-22.487509, -44.083172),
            (-22.487694, -44.083348), (-22.488262, -44.083369), (-22.489424, -44.082649),
            (-22.491550, -44.081811), (-22.492099, -44.079578), (-22.492076, -44.079191),
            (-22.492379, -44.078933), (-22.492788, -44.077731), (-22.493123, -44.076802),
            (-22.493060, -44.076103),
        ],
    },
    {
        'nome': 'ÁREA DE RISCO BMA',
        'pontos': [
            (-22.544521, -44.162484), (-22.545036, -44.162720), (-22.545343, -44.162817),
            (-22.545571, -44.162012), (-22.545739, -44.160854), (-22.545700, -44.160274),
            (-22.545155, -44.160274), (-22.545036, -44.160274), (-22.544848, -44.161411),
            (-22.544521, -44.162484),
        ],
    },
]

# ---------------------------------------------------------------------------
# A lista de ruas -- a segunda via, para endereço sem coordenada
#
# Estrutura: SIGLA DA UNIDADE -> BAIRRO -> ruas.
#
# O bairro faz parte da chave de propósito. Nome de rua se repete muito dentro
# da mesma cidade -- "Boa Vista", "São José" e "Três" existem em vários
# bairros de Volta Redonda e de Barra Mansa. Casar só pelo nome da rua
# alertaria endereço que não é de risco nenhum, e alerta que erra é alerta que
# a operação aprende a ignorar.
#
# Escreva como está no mapa da operação, com acento e com "R." / "Av." se vier
# assim: a normalização abaixo cuida disso. O que NÃO dá para inventar é o
# bairro -- ele tem de ser o bairro do endereço, porque é o que o CAMPO manda.
#
# Origem: quadro de áreas de risco do RJ, recebido em 28/08/2026.
MAPA_AREAS_DE_RISCO = {
    'VRD': {                                    # Volta Redonda
        'RETIRO': [
            'R. Pinheiral',
            'R. Frei Henrique Soares',
            'R. Frei Fabiano de Cristo',
            'Condomínio Girassol',
        ],
        'MINERLANDIA': [
            'R. Andrelândia',
            'Servidão Tiradentes',
        ],
        'SANTO AGOSTINHO': [
            'R. Vista Alegre',
            'Av. Dom João VI',
            'Escadaria Salvador José Roque',
            'R. Alvorada',
            'R. Bananal',
            'R. Barão de Mauá',
            'R. Bela Vista',
            'R. Boa Morada',
            'R. Boa Vista',
            'R. Cabo Manoel Biré Agnela',
            'R. Castelo',
            'R. Caviana',
            'R. Dom Pedro I',
            'R. Esperança',
            'R. Fernando de Noronha',
            'R. Francisca Oliveira Alves',
            'R. Ilda da Silva',
            'R. Ilha de Trindade',
            'R. Ilha do Governador',
            'R. Itamaracá',
            'R. Itaparica',
            'R. João do Prado',
            'R. José Espedito Dom Paula',
            'R. José Ramos de Macedo',
            # O quadro recebido termina aqui, em "R. José Ramos de Macedo", e
            # a coluna de Santo Agostinho parece continuar abaixo do corte da
            # imagem. Enquanto o resto não entrar, as ruas que faltam só serão
            # pegas pelo POLÍGONO -- que cobre o bairro inteiro e é a via
            # principal, então o buraco é pequeno. Ele aparece nos endereços
            # sem coordenada, e só neles.
        ],
    },
    'BMA': {                                    # Barra Mansa
        'SANTA OPERADOR': [
            'R. Antônio Luciano',
            'R. Antônio Rodrigues de Almeida',
            'R. José Coutinho de Carvalho',
            'R. Recife',
            'R. São Geraldo',
            'R. São José',
            'R. São Lucas',
            'R. São Marcos',
            'R. São Mateus',
            'R. São Simeão',
            'R. Três',
            'Rua Horácio Silva',
        ],
        # O quadro traz "SANTA OPERADOR e SÃO FRANCISCO DE ASSIS" numa coluna
        # só: é a mesma área de risco em dois bairros vizinhos. Aqui viram
        # duas entradas porque o CAMPO manda UM bairro por endereço, e o que
        # chega é um dos dois nomes -- nunca a dupla.
        'SAO FRANCISCO DE ASSIS': [
            'R. Antônio Luciano',
            'R. Antônio Rodrigues de Almeida',
            'R. José Coutinho de Carvalho',
            'R. Recife',
            'R. São Geraldo',
            'R. São José',
            'R. São Lucas',
            'R. São Marcos',
            'R. São Mateus',
            'R. São Simeão',
            'R. Três',
            'Rua Horácio Silva',
        ],
    },
}

# Grafias alternativas do MESMO bairro. O CAMPO escreve o bairro sem acento e em
# caixa alta, mas a escolha entre "OPERADOR" e "ISABEL" é de quem cadastrou o
# endereço, não do sistema. Cada linha aqui é uma grafia que já se sabe que
# existe, ou que é provável o bastante para não valer o risco de perder o
# alerta.
BAIRROS_SINONIMOS = {
    'SANTA ISABEL': 'SANTA OPERADOR',
    'STA OPERADOR': 'SANTA OPERADOR',
    'STA ISABEL': 'SANTA OPERADOR',
    'S FRANCISCO DE ASSIS': 'SAO FRANCISCO DE ASSIS',
    'SAO FRANCISCO': 'SAO FRANCISCO DE ASSIS',
    'MINERLANDIA': 'MINERLANDIA',
}

# Prefixos de tipo de logradouro. Somem dos DOIS lados da comparação, então
# "Av. Dom João VI" do quadro casa com "AVENIDA DOM JOAO VI" do CAMPO, e
# "R. Três" casa com "RUA TRES".
#
# Por que sumir em vez de normalizar para uma forma só: o CAMPO às vezes manda a
# rua SEM tipo nenhum ('RUI BARBOSA', 'DOUTOR JOÃO CABRAL FLEXA', vistos na
# amostra de 28/08/2026), e às vezes manda DUAS vezes ('RUA RUA PINHEIRO', na
# mesma amostra). Comparar sem o tipo é o único jeito de esses casarem com um
# quadro que escreve "R. Rui Barbosa".
#
# O preço é conhecido e aceito: um bairro que tenha "Rua Bela Vista" E
# "Travessa Bela Vista" veria as duas como a mesma. É raro, e erra para o lado
# de avisar demais -- que, para área de risco, é o lado certo de errar.
PREFIXOS_LOGRADOURO = (
    'RUA', 'R', 'AVENIDA', 'AV', 'TRAVESSA', 'TV', 'TRV', 'ALAMEDA', 'AL',
    'ESTRADA', 'EST', 'RODOVIA', 'ROD', 'PRACA', 'PC', 'LARGO', 'LADEIRA',
    'ESCADARIA', 'SERVIDAO', 'VIELA', 'BECO', 'CONDOMINIO', 'COND',
    'LOTEAMENTO', 'LOT', 'VILA', 'VL', 'CHACARA', 'SITIO',
)

# O CAMPO devolve logradouro com lixo de cadastro grudado na frente: 'CLT_RUA
# SANTA TEREZINHA' apareceu na amostra. É prefixo de sistema, e some antes de
# qualquer outra coisa.
_LIXO_INICIAL = re.compile(r'^(CLT|CLI|END|LOG)[_\-\s]+', re.IGNORECASE)

# Logradouro curto demais não é endereço, é cadastro pela metade -- a mesma
# amostra trouxe um chamado cujo logradouro era a letra 'C'. Comparar isso com
# o quadro não erra (nenhuma rua tem uma letra só), mas conta como "conferido"
# no log e esconde que o endereço nunca foi conferido de verdade.
TAMANHO_MINIMO_LOGRADOURO = 3

# Caixa em volta do Sul do RJ e do Litoral Norte de SP, as duas áreas onde o
# bot opera. Serve para descartar coordenada que o cadastro inventou -- (0, 0)
# é o caso clássico, e cai no Golfo da Guiné. Coordenada fora daqui não é
# tratada como "fora da área de risco" e sim como "não sei", que é diferente:
# manda a pergunta para a lista de ruas em vez de responder não.
LAT_MIN, LAT_MAX = -25.5, -19.0
LNG_MIN, LNG_MAX = -48.5, -40.0


def _sem_acento(texto):
    return ''.join(
        c for c in unicodedata.normalize('NFKD', str(texto))
        if not unicodedata.combining(c)
    )


def normalizar(texto):
    """Texto comparável: sem acento, sem pontuação, caixa alta, espaço único."""
    if texto is None:
        return ''
    limpo = _sem_acento(texto).upper()
    limpo = re.sub(r'[^A-Z0-9 ]+', ' ', limpo)
    return re.sub(r'\s+', ' ', limpo).strip()


def normalizar_bairro(bairro):
    """Bairro na grafia do quadro, resolvendo os sinônimos conhecidos."""
    chave = normalizar(bairro)
    return BAIRROS_SINONIMOS.get(chave, chave)


def normalizar_logradouro(logradouro):
    """Nome da rua sem o tipo e sem o lixo de cadastro.

    'CLT_RUA SANTA TEREZINHA' -> 'SANTA TEREZINHA'
    ' AVENIDA BRASIL'         -> 'BRASIL'
    'RUA RUA PINHEIRO'        -> 'RUA PINHEIRO'
    'R. Três'                 -> 'TRES'

    Devolve '' quando não sobra nome nenhum -- por exemplo um logradouro que
    era só 'RUA'. Quem chama trata '' como "não dá para dizer".
    """
    if logradouro is None:
        return ''
    texto = _LIXO_INICIAL.sub('', str(logradouro).strip())
    nome = normalizar(texto)
    # Um prefixo só. 'RUA RUA PINHEIRO' vira 'RUA PINHEIRO', não 'PINHEIRO':
    # tirar os dois transformaria a rua "Rua Pinheiro" na rua "Pinheiro", que
    # pode ser outra. O quadro é comparado com a mesma regra, então os dois
    # lados perdem exatamente um prefixo e continuam casando.
    partes = nome.split(' ')
    if partes and partes[0] in PREFIXOS_LOGRADOURO:
        partes = partes[1:]
    return ' '.join(partes).strip()


# --------------------------------------------------------------------------
# via 1: o polígono
# --------------------------------------------------------------------------
def coordenada_valida(lat, lng):
    """Dá para confiar nesta coordenada?

    Devolve (lat, lng) como float, ou None. None quer dizer "não sei", nunca
    "não é risco" -- ver LAT_MIN/LAT_MAX.
    """
    if lat is None or lng is None:
        return None
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return None
    if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
        return None
    return lat, lng


def dentro_do_poligono(lat, lng, pontos):
    """Lançamento de raio: conta quantas arestas o raio para o leste cruza.

    Ímpar = dentro. É o algoritmo clássico, e serve aqui porque as áreas têm
    algumas centenas de metros -- nessa escala tratar grau como plano não
    introduz erro que mude a resposta. Para áreas de dezenas de quilômetros
    isto precisaria de projeção; não é o caso de nenhum polígono do mapa.
    """
    dentro = False
    total = len(pontos)
    j = total - 1
    for i in range(total):
        lat_i, lng_i = pontos[i]
        lat_j, lng_j = pontos[j]
        if (lat_i > lat) != (lat_j > lat):
            corte = lng_i + (lat - lat_i) * (lng_j - lng_i) / (lat_j - lat_i)
            if lng < corte:
                dentro = not dentro
        j = i
    return dentro


def area_do_ponto(lat, lng):
    """O nome da área de risco que contém este ponto, ou None."""
    for area in POLIGONOS_DE_RISCO:
        if dentro_do_poligono(lat, lng, area['pontos']):
            return area['nome']
    return None


# --------------------------------------------------------------------------
# via 2: a lista de ruas
# --------------------------------------------------------------------------
def _montar_indice():
    """unidade -> bairro -> {rua normalizada: rua como está no quadro}.

    Guardar a grafia original junto é o que deixa a mensagem do grupo dizer
    "R. São Simeão" em vez de "SAO SIMEAO": quem lê o alerta está com o quadro
    da operação na mão, e o texto tem de bater com o quadro.
    """
    indice = {}
    for unidade, bairros in MAPA_AREAS_DE_RISCO.items():
        por_bairro = {}
        for bairro, ruas in bairros.items():
            ruas_norm = {}
            for rua in ruas:
                chave = normalizar_logradouro(rua)
                if not chave:
                    logger.error(
                        f"Área de risco: rua '{rua}' ({unidade}/{bairro}) não sobrou "
                        "nada depois de normalizar e foi ignorada."
                    )
                    continue
                ruas_norm[chave] = rua
            por_bairro[normalizar_bairro(bairro)] = ruas_norm
        indice[normalizar(unidade)] = por_bairro
    return indice


_INDICE = _montar_indice()


def rua_no_quadro(unidade, bairro, logradouro):
    """A rua do quadro que casa com este endereço, ou None."""
    bairros = _INDICE.get(normalizar(unidade))
    if not bairros:
        return None

    chave_bairro = normalizar_bairro(bairro)
    ruas = bairros.get(chave_bairro)
    if not ruas:
        return None

    chave_rua = normalizar_logradouro(logradouro)
    if len(chave_rua) < TAMANHO_MINIMO_LOGRADOURO:
        # Bairro de risco e endereço ilegível: pelo nome não dá para afirmar
        # nem negar. Vai para o log porque é exatamente o caso que alguém
        # precisa conferir na mão -- e que, sem esta linha, sumiria. Se o
        # chamado tiver coordenada, o polígono já respondeu antes de chegar
        # aqui; esta linha é sobre o que sobrou.
        logger.warning(
            f"Área de risco: {unidade}/{bairro} é bairro com ruas mapeadas, mas o "
            f"logradouro veio como {logradouro!r} -- curto demais para conferir. "
            "Nenhum alerta pela lista de ruas; confira este endereço na mão."
        )
        return None

    rua = ruas.get(chave_rua)
    if not rua:
        # Bairro mapeado, rua fora da lista. É o caso mais comum e é normal --
        # só uma parte das ruas do bairro é de risco. Fica em DEBUG para dar
        # onde olhar quando alguém disser "esse endereço era pra ter avisado":
        # o que aparece aqui é a grafia exata que o CAMPO mandou, que é o que
        # precisa entrar no quadro.
        logger.debug(
            f"Área de risco: {unidade}/{chave_bairro} sem casamento para logradouro "
            f"{logradouro!r} (normalizado: {chave_rua!r})."
        )
    return rua


# --------------------------------------------------------------------------
# a pergunta
# --------------------------------------------------------------------------
def unidades_cobertas():
    """As siglas que têm lista de ruas. Serve para o log de partida dizer o
    que está coberto pela segunda via -- e, por consequência, o que não está.
    O polígono não tem sigla: ele vale onde estiver desenhado."""
    return sorted(_INDICE)


def total_de_ruas():
    return sum(len(ruas) for bairros in _INDICE.values() for ruas in bairros.values())


def consultar(unidade, bairro, logradouro, lat=None, lng=None):
    """Este endereço está em área de risco?

    Devolve um dicionário com o que a mensagem precisa, ou None. Nunca
    levanta: este é um alerta EXTRA, e uma exceção aqui não pode derrubar a
    notificação do entrante, que é o alerta principal.

    'casou_por' diz por qual das duas vias -- 'mapa' ou 'rua'. Quem lê o
    alerta no grupo precisa disso: o mapa é o desenho da operação, a rua é uma
    tabela copiada à mão que pode estar velha.
    """
    try:
        ponto = coordenada_valida(lat, lng)
        texto_ponto = f"{ponto[0]:.6f},{ponto[1]:.6f}" if ponto else None

        if ponto:
            nome_area = area_do_ponto(*ponto)
            if nome_area:
                return {
                    'casou_por': 'mapa',
                    'area': nome_area,
                    'unidade': str(unidade).upper().strip(),
                    'bairro': bairro,
                    'rua': str(logradouro or '').strip() or 'N/D',
                    'coordenada': texto_ponto,
                }

        # A coordenada não vetar a lista de ruas é decisão, não descuido --
        # e decisão tomada com a operação em 28/08/2026, quando ficou claro
        # que as duas fontes discordam de verdade: o polígono de Barra Mansa
        # tem 2,3 ha, umas três quadras, e o quadro lista 12 ruas em Santa
        # OPERADOR e São Francisco de Assis, que não cabem ali. Perguntado qual
        # dos dois manda, a resposta foi "vale a soma dos dois". Então some.
        #
        # Ponto fora de todo polígono NÃO prova que o endereço está fora: a
        # coordenada vem de geocodificação do cadastro, e errar uma quadra é
        # comum -- basta o número da casa estar torto. Se o mapa pudesse dizer
        # "não" por cima da lista, o modo de falha seria justamente o caro:
        # técnico entrando numa rua marcada sem aviso nenhum, porque um
        # arredondamento de coordenada disse que ele estava a 40 metros dali.
        #
        # As duas vias somam. O que a mensagem diz é POR QUAL delas casou, e a
        # de rua com coordenada fora sai pedindo conferida -- ou o cadastro
        # está errado, ou o quadro está mais velho que o desenho.
        rua = rua_no_quadro(unidade, bairro, logradouro)
        if rua:
            return {
                'casou_por': 'rua',
                'area': None,
                'unidade': str(unidade).upper().strip(),
                'bairro': bairro,
                'rua': rua,
                'coordenada': texto_ponto,
            }
        return None
    except Exception:
        logger.exception(
            "Falha ao consultar o mapa de área de risco. O entrante segue sendo "
            "notificado normalmente, só sem este aviso."
        )
        return None
