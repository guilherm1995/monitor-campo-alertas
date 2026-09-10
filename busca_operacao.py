# -*- coding: utf-8 -*-
"""As buscas que o assistente pode pedir enquanto pensa.

POR QUE ISTO EXISTE
-------------------
O dossiê (dossie_operacao.py) é um retrato: ele mostra o backlog aberto, a
agenda de hoje e os totais apurados, tudo de uma vez, antes de a pergunta ser
lida. Isso resolve a maior parte das perguntas de operação -- e resolve bem,
porque o modelo não precisa procurar nada nem fazer conta.

Mas há uma classe de pergunta que o retrato nunca vai responder, por mais que
ele cresça: a pergunta sobre UM caso. "Quando foi feita a ativação do contrato
6907317?" fala de uma O.S. já concluída, que por definição não está no backlog
aberto. A resposta estava no disco o tempo todo, no OFS GERAL, mas o dossiê
não a carregava -- e não deve carregar: são milhares de linhas históricas, e
enfiar tudo no contexto para o caso de alguém perguntar de uma seria caro em
toda pergunta e inútil em quase todas.

Então o assistente ganha um braço. Em vez de receber tudo mastigado, ele pode
PEDIR: "buscar_contrato('6907317')". O código aqui atende, lendo os mesmos
arquivos que o resto do bot já mantém, e devolve as linhas. Ele conclui.

Isto não é o desenho de roteador que foi recusado antes, em que o modelo
escolhia um comando pronto e o comando é que respondia -- ali o modelo não
pensava, só encaminhava. Aqui quem raciocina continua sendo ele; a busca é
só o alcance.

O DADO DO CLIENTE SAI -- MAS SÓ NA BUSCA
----------------------------------------
Nome, endereço, telefone, celular e CEP saem nas buscas. A operação precisa
deles: quem liga para confirmar a agenda precisa do telefone, e quem vai ao
endereço precisa do endereço. O bot já mandava esses campos nos alertas
individuais de garantia e de improdutiva; o assistente estar mais restrito que
os próprios alertas era incoerente, e obrigava a operação a sair do grupo para
consultar o que o bot tinha na mão.

A separação que continua valendo, e que é a que importa:

    o DOSSIÊ não leva dado de cliente. A BUSCA leva.

O dossiê viaja em TODA pergunta, inclusive nas que não têm nada a ver com
cliente nenhum. Pôr telefone e endereço ali mandaria a base inteira de clientes
para fora da máquina a cada "como está o backlog de VRD" -- centenas de
pessoas, para responder uma pergunta que não fala de nenhuma delas.

A busca é o contrário: ela só acontece quando alguém perguntou de um contrato,
de uma O.S. ou de uma rota, e devolve no máximo TETO_LINHAS_BUSCA linhas. O
dado sai porque foi pedido, e sai na medida do pedido.

Vale saber, e está escrito aqui porque ninguém deve descobrir isso depois: o
resultado da busca é enviado ao provedor de IA junto com a pergunta. Trocar o
motor por um serviço que treina em cima do que recebe muda o alcance dessa
decisão -- ver regras/06-privacidade.md.
"""
import logging
import os
import unicodedata
from datetime import datetime, timedelta

# As consultas que saem pela rede moram em outro modulo, e sao importadas
# aqui so quando alguem chama -- dentro do metodo, nao no topo. O motivo e o
# custo: o modulo de consultas puxa `requests` e, no caminho do Autenticador,
# `pandas.read_html`, que arrasta lxml. Uma pergunta que nao fale de conexao
# nao deve pagar por isso.


# A janela padrao da operacao, em dias. NAO se escreve o numero aqui: quem o
# define e a base de improdutivas, e ele ja mudou uma vez (era 30 ate
# 20/08/2026). Escrever "60" nesta linha criaria a segunda copia, e a segunda
# copia e o defeito que o diretorio regras/ existe para nao ter.
try:
    from improdutivas import DIAS_JANELA as JANELA_DA_OPERACAO
except ImportError:  # pragma: sem cobertura -- so se o modulo sumir
    JANELA_DA_OPERACAO = 60

logger = logging.getLogger(__name__)

# Teto de linhas por busca. Um contrato costuma ter de 1 a 10 atividades e a
# rota de um técnico, umas 10 -- 40 dá folga larga para os dois casos sem
# risco de uma busca infeliz (um número curto que casa com muita coisa)
# despejar meio arquivo dentro do contexto.
TETO_LINHAS_BUSCA = 40

# As colunas do OFS GERAL que podem sair desta máquina. A lista continua sendo
# BRANCA: coluna nova que apareça na exportação fica de fora até alguém decidir
# que ela pode sair, em vez de vazar por padrão. O que mudou em 30/08/2026 foi
# o conteúdo dela, não o critério -- os campos de cliente entraram porque a
# operação precisa deles para agir, e o bot já os mandava nos alertas.
COLUNAS_LIBERADAS = (
    # --- o cliente, para quem vai ligar ou ir até lá ---
    'Nome',
    'Endereço',
    'CEP/Código Postal',
    'Telefone',
    'Telefone Celular',
    'E-mail',
    # --- a operação ---
    'Recurso',
    'Data',
    'Status da Atividade',
    'Cidade',
    'Data Abertura Chamado',
    'Início',
    'Fim',
    'Duração',
    'Tipo de Atividade.1',
    'Ordem de Serviço',
    'Motivo de Encerramento das atividades',
    'Número do contrato',
    'Plano de Contrato',
    'ID da Ordem de Serviço',
)

# O nome sem sufixo é a coluna que vem inteira como "Normal"; a de verdade é a
# segunda, que o pandas renomeia para '.1' por serem duas colunas com o mesmo
# nome no cabeçalho. Ler a errada devolve tudo "Normal" e nenhum erro.
COLUNA_TIPO = 'Tipo de Atividade.1'
COLUNA_DATA = 'Data'
COLUNA_STATUS = 'Status da Atividade'
COLUNA_CONTRATO = 'Número do contrato'
COLUNA_OS = 'Ordem de Serviço'
COLUNA_ID_OS = 'ID da Ordem de Serviço'
COLUNA_RECURSO = 'Recurso'

# Estas duas nunca são pessoa: são a fila da praça. O assistente precisa saber
# a diferença para não dizer "o técnico CARAGUATATUBA".
TIPOS_NAO_PRODUTIVOS = {'Almoço', 'Consulta Médica'}

# O nome comercial do plano tem mais de cem caracteres por linha e não ajuda a
# entender uma rota. Numa lista de 15 atividades ele sozinho ocupava metade do
# resultado. Continua saindo na busca por contrato, onde a pergunta pode ser
# justamente qual é o plano.
COLUNAS_FORA_DA_ROTA = ('Plano de Contrato',)

# Onde a busca livre procura. Uma pergunta do grupo raramente traz o numero do
# contrato -- traz o nome de quem ligou, ou o telefone que aparece no visor.
# A lista cobre todo campo que identifica um cliente ou um atendimento, porque
# adivinhar qual deles a pessoa tem na mao e o que fazia a busca falhar.
COLUNAS_BUSCA_LIVRE = (
    'Nome',
    'Endereço',
    'CEP/Código Postal',
    'Telefone',
    'Telefone Celular',
    'E-mail',
    'Cidade',
    'Número do contrato',
    'Ordem de Serviço',
    'ID da Ordem de Serviço',
    'Recurso',
)

# As colunas que sao numero e devem ser comparadas so pelos digitos. Telefone
# aparece na base como "DDD9XXXXXXXX" e a pessoa digita "(DDD) 9XXXX-XXXX" -- sao
# o mesmo numero, e comparar o texto cru nunca casaria.
COLUNAS_SO_DIGITOS = (
    'CEP/Código Postal',
    'Telefone',
    'Telefone Celular',
    'Número do contrato',
    'Ordem de Serviço',
    'ID da Ordem de Serviço',
)

# Quantos digitos um termo precisa ter para ser tratado como numero. Menos que
# isso ("30", de "30 dias") casaria com meio arquivo.
MINIMO_DIGITOS_BUSCA = 4

# Quantas linhas de amostra sobram quando a busca casa em coisa demais. Um
# primeiro nome comum trouxe 964 linhas e 518 nomes distintos, e as 40 linhas
# de sempre viravam 23 KB de gente diferente -- caro e enganoso. Oito bastam
# para o modelo mostrar do que se trata antes de pedir o nome inteiro.
TETO_AMOSTRA_BUSCA_LARGA = 8

# Uma leitura do OFS GERAL custa alguns segundos (o arquivo passa de 4 MB), e
# uma pergunta pode pedir duas ou três buscas seguidas. O cache guarda a
# última leitura junto com o mtime do arquivo: enquanto o arquivo não mudar, a
# segunda busca é instantânea; quando o site publica um OFS novo, o mtime muda
# e a leitura acontece de novo sozinha.
_CACHE = {}


# ============================== as bases onde se procura =====================
# Todas tem as MESMAS 34 colunas -- sao exportacoes do mesmo OFS, recortadas
# por periodo diferente. Por isso o mesmo codigo de busca serve nas tres sem
# nenhuma adaptacao, e por isso deixar duas de fora nao economizava trabalho,
# so escondia atendimento.
#
# A ordem importa: a primeira que tiver uma linha e a que aparece como origem
# dela. OFS GERAL vem primeiro por ser a mais recente e a que o resto do bot
# ja trata como corrente.
#
# Os nomes de arquivo saem das mesmas variaveis de ambiente que o
# ofs_base_historica.py usa para gravar, para nao existirem dois lugares
# dizendo onde a base mora.
def _bases_padrao(pasta):
    """(rotulo, caminho, o que e) de cada base, na ordem de procura."""
    return (
        ('OFS GERAL',
         os.path.join(pasta, 'OFS GERAL.csv'),
         'agenda corrente por contrato, e o historico recente'),
        ('base de improdutivas',
         os.environ.get('BASE_IMPRODUTIVAS_ARQUIVO')
         or os.path.join(pasta, 'base improdutivas 60 dias.xlsx'),
         'a janela de improdutivas da operacao'),
        ('base de garantias',
         os.environ.get('BASE_OFS_ARQUIVO')
         or os.path.join(pasta, 'base OFS ok.xlsx'),
         'os atendimentos que sustentam a conta de garantia'),
        ('exportacao do dia',
         os.environ.get('OFS_EXTRACAO_ARQUIVO')
         or os.path.join(pasta, 'OPERACIONAL.csv'),
         'a agenda de campo de hoje, atividade por atividade'),
    )


# A coluna que identifica uma atividade sem ambiguidade, para a mesma linha
# vista em duas bases nao ser contada duas vezes. Cai para o par O.S.+Data
# quando o id nao existir naquele arquivo.
COLUNA_CHAVE = 'ID da Ordem de Serviço'

# Coluna que a busca acrescenta para lembrar de onde a linha veio. O nome
# comeca com underline para nunca colidir com uma coluna de verdade da
# exportacao, hoje ou depois.
COLUNA_ORIGEM = '_base'


# ============================ o que o modelo enxerga ==========================
# As descrições são o manual da ferramenta: é por elas que o modelo decide se
# chama e com quê. Valem o cuidado de um texto de interface -- dizem o que a
# busca cobre, e principalmente o que ela NÃO cobre, para ele não pedir onde
# não vai achar.
DECLARACOES = [{'functionDeclarations': [
    {
        'name': 'buscar_contrato',
        'description': (
            'Busca tudo o que a operação tem sobre UM número de contrato: as '
            'O.S. em aberto agora no CAMPO e as atividades de campo do OFS '
            'GERAL, inclusive as já concluídas e as canceladas, de qualquer '
            'data. Use quando a pergunta citar um contrato, ou perguntar '
            'quando algo foi feito, se já foi atendido, ou o histórico de um '
            'cliente. É a única forma de alcançar o que já foi concluído: o '
            'dossiê só traz o que está em aberto.'
        ),
        'parameters': {
            'type': 'OBJECT',
            'properties': {'contrato': {
                'type': 'STRING',
                'description': 'Só os dígitos do contrato, ex.: "6907317".',
            }},
            'required': ['contrato'],
        },
    },
    {
        'name': 'buscar_ordem_servico',
        'description': (
            'Busca uma O.S. pelo número, tanto o número do CAMPO quanto o do '
            'OFS. Use quando a pergunta citar um número de O.S. e você '
            'precisar do que aconteceu com ela.'
        ),
        'parameters': {
            'type': 'OBJECT',
            'properties': {'numero': {
                'type': 'STRING',
                'description': 'Só os dígitos da O.S.',
            }},
            'required': ['numero'],
        },
    },
    {
        'name': 'listar_atividades_do_recurso',
        'description': (
            'Lista, uma a uma, as atividades de hoje de um técnico ou de uma '
            'fila de praça, com horário e status. O dossiê só traz a '
            'contagem por recurso; use esta busca quando a pergunta pedir o '
            'detalhe da rota -- o que já foi feito, o que falta, a que horas. '
            'O nome pode vir pela metade: "Izaias" acha "Izaias Nobrega de '
            'Almeida".'
        ),
        'parameters': {
            'type': 'OBJECT',
            'properties': {
                'recurso': {
                    'type': 'STRING',
                    'description': ('Nome do técnico ou da fila, inteiro ou '
                                    'em parte.'),
                },
                'data': {
                    'type': 'STRING',
                    'description': ('Dia da rota, no formato dd/mm/aa. Deixe '
                                    'em branco para hoje.'),
                },
            },
            'required': ['recurso'],
        },
    },
    {
        'name': 'buscar_cliente',
        'description': (
            'Busca por QUALQUER dado, sem precisar do numero do contrato: '
            'nome do cliente, telefone, celular, e-mail, endereco, CEP, '
            'cidade, numero de O.S. ou nome de tecnico. Procura em todas '
            'essas colunas ao mesmo tempo e diz em qual casou. '
            'Use sempre que a pergunta identificar alguem por algo que nao '
            'seja o contrato -- "ja atendemos a Maria Silva?", "de quem e o '
            'telefone DDD9XXXXXXXX?", "teve atendimento na Rua das Flores?". '
            'O nome pode vir pela metade e o telefone pode vir formatado: '
            '"(DDD) 9XXXX-XXXX" acha "DDD9XXXXXXXX". '
            'Alcanca todo o historico do OFS GERAL, inclusive concluidos e '
            'cancelados, e tambem as O.S. em aberto no CAMPO.'
        ),
        'parameters': {
            'type': 'OBJECT',
            'properties': {
                'termo': {
                    'type': 'STRING',
                    'description': ('O que procurar: nome, telefone, '
                                    'endereco, e-mail, CEP ou numero.'),
                },
                'dias': {
                    'type': 'STRING',
                    'description': ('Janela em dias contados de hoje para '
                                    'tras. A janela da operacao e 60 dias, '
                                    'que e a mesma da base de improdutivas: '
                                    'use "60" quando a pergunta disser '
                                    '"recentemente", "ultimamente" ou nao '
                                    'disser prazo nenhum mas quiser saber se '
                                    'ja atendemos. Deixe em branco para '
                                    'procurar em TODO o historico -- o '
                                    'resultado conta os ultimos 60 dias '
                                    'separado de qualquer jeito.'),
                },
            },
            'required': ['termo'],
        },
    },
    {
        'name': 'consultar_autenticador',
        'description': (
            'Consulta o Autenticador AO VIVO e diz se o contrato esta online ou '
            'offline agora e, quando esta offline, A HORA EM QUE CAIU. '
            'Use para "esse cliente esta online?", "quando ele caiu?", "ele '
            'caiu de novo?" -- e antes de mandar tecnico para reparo, porque '
            'cliente online raramente precisa de visita. '
            'E a UNICA fonte sobre conexao: isso nao existe em arquivo '
            'nenhum, e o dossie nunca traz. '
            'Custa alguns segundos e depende da VPN. Nao use para pergunta '
            'que nao seja de conexao.'
        ),
        'parameters': {
            'type': 'OBJECT',
            'properties': {'contrato': {
                'type': 'STRING',
                'description': 'So os digitos do contrato.',
            }},
            'required': ['contrato'],
        },
    },
    {
        'name': 'carga_do_dia_seguinte',
        'description': (
            'A PREVIA DA CARGA: toda a carga do Litoral Norte num dia, ja '
            'contada por cidade, por tipo e por turno. Use para "como esta a '
            'carga de amanha", "previa de amanha", "quantas O.S. temos amanha '
            'no litoral", "quanto tem no balde". '
            'Atualiza a base no OFS antes de contar. '
            'Entra o que esta no balde esperando alguem E o que ja esta na '
            'rota de um tecnico -- e tudo carga do dia, e a previa nao separa '
            'as duas coisas. Tudo menos cancelado, e so Ativacao, Mudanca '
            'de Endereco e Reparo. Sao Sebastiao aparece partida em TOPO e '
            'COSTA SUL, e Bertioga entra por bairro. '
            'Os totais ja vem somados: use os numeros como estao, nao some '
            'nada por conta.'
        ),
        'parameters': {
            'type': 'OBJECT',
            'properties': {'data': {
                'type': 'STRING',
                'description': ('O dia, em dd/mm/aaaa. Deixe vazio para '
                                'amanha, que e o caso normal.'),
            }},
            'required': [],
        },
    },
    {
        'name': 'consultar_wifi',
        'description': (
            'Traz o NOME DA REDE e a SENHA do wi-fi do cliente, como foram '
            'provisionados no sistema. Use quando pedirem a rede ou a senha '
            'do wi-fi de um contrato -- o tecnico em campo e quem mais '
            'pergunta isso. '
            'Sao os dados provisionados, nao uma leitura do roteador agora: '
            'se o cliente trocou no aparelho, o que volta e o original, e '
            'isso precisa ser dito junto da resposta.'
        ),
        'parameters': {
            'type': 'OBJECT',
            'properties': {'contrato': {
                'type': 'STRING',
                'description': 'So os digitos do contrato.',
            }},
            'required': ['contrato'],
        },
    },
]}]

NOMES = {declaracao['name']
         for grupo in DECLARACOES
         for declaracao in grupo['functionDeclarations']}


def _tipos_minusculos(no):
    """O mesmo esquema, com os tipos em minúscula.

    O dialeto do Gemini escreve 'OBJECT'/'STRING'; o JSON Schema que a OpenAI
    (e portanto o 9router) espera quer 'object'/'string'. É a única diferença
    real entre as duas declarações, então converte-se em vez de manter duas
    listas que iriam divergir na primeira busca nova.
    """
    if isinstance(no, dict):
        convertido = {}
        for chave, valor in no.items():
            if chave == 'type' and isinstance(valor, str):
                convertido[chave] = valor.lower()
            else:
                convertido[chave] = _tipos_minusculos(valor)
        return convertido
    if isinstance(no, list):
        return [_tipos_minusculos(item) for item in no]
    return no


# As mesmas ferramentas no formato /v1/chat/completions, que é o que o 9router
# e qualquer serviço compatível com a OpenAI entendem.
FERRAMENTAS_OPENAI = [
    {'type': 'function', 'function': _tipos_minusculos(declaracao)}
    for grupo in DECLARACOES
    for declaracao in grupo['functionDeclarations']
]


# ================================= leitura ===================================
def _so_digitos(texto):
    return ''.join(c for c in str(texto or '') if c.isdigit())


def _sem_acento(texto):
    """Minusculas e sem acento, para comparar nome do jeito que se digita.

    Quem pergunta no grupo escreve "marinalva farias"; a base guarda
    "MARINALVA FARIAS DE MORAES SILVA". Sem isto, a busca por nome erraria
    justamente no caso comum.
    """
    cru = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in cru
                   if unicodedata.category(c) != 'Mn').lower().strip()


def _dentro_da_janela(tabela, dias, agora):
    """Máscara das linhas cuja Data cai nos ultimos `dias` dias.

    Devolve None quando nao ha janela a aplicar -- e quem chama trata None
    como "todas as linhas", em vez de receber uma mascara toda verdadeira que
    custaria uma varredura a toa.
    """
    if not dias or COLUNA_DATA not in tabela.columns:
        return None
    try:
        quantos = int(str(dias).strip())
    except (TypeError, ValueError):
        return None
    if quantos <= 0:
        return None

    limite = agora - timedelta(days=quantos)

    def dentro(texto):
        try:
            return datetime.strptime(str(texto).strip(), '%d/%m/%y') >= limite
        except (TypeError, ValueError):
            # Data ilegivel nao e motivo para sumir com a linha: e melhor
            # mostrar de mais e dizer que a data nao deu para ler do que
            # esconder um atendimento que existiu.
            return True

    return tabela[COLUNA_DATA].fillna('').map(dentro)


def _ler_base(caminho, rotulo='base'):
    """Uma base do OFS, csv ou xlsx, com cache por mtime. None se nao der.

    O cache e por caminho, e nao um so: uma pergunta pode varrer as quatro
    bases, e um cache de uma entrada faria a segunda leitura expulsar a
    primeira -- reler 3,5 MB de xlsx a cada busca.
    """
    if not caminho or not os.path.exists(caminho):
        return None
    try:
        mtime = os.path.getmtime(caminho)
    except OSError:
        return None

    guardado = _CACHE.get(caminho)
    if guardado and guardado[0] == mtime:
        return guardado[1]

    import pandas as pd
    try:
        if str(caminho).lower().endswith(('.xlsx', '.xls')):
            tabela = pd.read_excel(caminho, dtype=str)
        else:
            tabela = pd.read_csv(caminho, sep=None, engine='python',
                                 encoding='utf-8-sig', dtype=str)
    except Exception:
        logger.exception('Busca: falha ao ler %s em %s.', rotulo, caminho)
        return None

    _CACHE[caminho] = (mtime, tabela)
    logger.info('Busca: %s lida (%s linhas) para consulta do assistente.',
                rotulo, len(tabela))
    return tabela


def _ler_ofs_geral(caminho):
    """Compatibilidade: o OFS GERAL continua tendo nome proprio."""
    return _ler_base(caminho, 'OFS GERAL')


def _data_ofs(quando):
    """A data no formato do OFS GERAL ('29/08/26'), venha ela como vier."""
    if isinstance(quando, datetime):
        return quando.strftime('%d/%m/%y')
    texto = str(quando or '').strip()
    for formato in ('%d/%m/%y', '%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(texto, formato).strftime('%d/%m/%y')
        except ValueError:
            continue
    return texto


def _contar(tabela, filtro, coluna):
    """Contagem apurada de uma coluna, para o modelo não ter de contar linha.

    Existe pelo mesmo motivo que os totais do dossiê vêm prontos, e por um
    erro medido: pedindo a rota de um técnico com 10 atividades, o modelo
    respondeu "4 de 7". Ele lê bem e conta mal. Contar é de graça aqui.
    """
    if coluna not in tabela.columns:
        return {}
    valores = tabela[filtro][coluna].fillna('(vazio)')
    return {str(chave): int(quantas)
            for chave, quantas in valores.value_counts().items()}


def _linhas_liberadas(tabela, filtro, descartar=()):
    """Aplica o filtro e devolve só as colunas que podem sair da máquina."""
    achadas = tabela[filtro]
    total = int(len(achadas))
    colunas = [c for c in COLUNAS_LIBERADAS
               if c in achadas.columns and c not in descartar]
    linhas = []
    for _, linha in achadas.head(TETO_LINHAS_BUSCA).iterrows():
        registro = {}
        # De qual base a linha veio. Sem isto, "achei 3" nao diz se as 3 sao
        # do OFS corrente ou da base de improdutivas, e sao leituras
        # diferentes: a segunda quer dizer que aquele cliente ja teve visita
        # improdutiva.
        origem = linha.get(COLUNA_ORIGEM) if COLUNA_ORIGEM in achadas.columns \
            else None
        if origem:
            registro['base'] = str(origem)
        for coluna in colunas:
            valor = linha.get(coluna)
            if valor is None:
                continue
            texto = str(valor).strip()
            if texto and texto.lower() != 'nan':
                registro[coluna] = texto
        if registro:
            linhas.append(registro)
    return linhas, total


class Buscador:
    """As buscas de uma pergunta, presas aos dados daquele momento.

    Recebe a lista de chamados em aberto já projetada pelo bot -- a mesma que
    alimentou o dossiê -- para que a busca e o retrato nunca discordem sobre o
    que está aberto. O OFS GERAL é lido sob demanda.
    """

    def __init__(self, chamados=None, caminho_ofs_geral=None, agora=None,
                 bases=None):
        self.chamados = list(chamados or ())
        self.caminho_ofs_geral = caminho_ofs_geral
        self.agora = agora or datetime.now()
        self.feitas = []
        # As bases moram ao lado do OFS GERAL. Quem chama pode passar outra
        # lista -- e o que a avaliacao faz, para nao ler as bases de verdade.
        pasta = (os.path.dirname(os.path.abspath(caminho_ofs_geral))
                 if caminho_ofs_geral else
                 os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'dados'))
        self.bases = tuple(bases) if bases is not None else _bases_padrao(pasta)

    def _tabelas(self):
        """(rotulo, tabela) de cada base que deu para ler, na ordem."""
        lidas = []
        for rotulo, caminho, _ in self.bases:
            tabela = _ler_base(caminho, rotulo)
            if tabela is not None and len(tabela):
                lidas.append((rotulo, tabela))
        return lidas

    # ------------------------------------------------- as que saem pela rede
    def consultar_autenticador(self, contrato):
        """Situacao de conexao agora. Vive em consultas_ao_vivo.py."""
        import consultas_ao_vivo
        return consultas_ao_vivo.consultar_autenticador(contrato)

    def consultar_wifi(self, contrato):
        """Rede e senha provisionadas. Vive em consultas_ao_vivo.py."""
        import consultas_ao_vivo
        return consultas_ao_vivo.consultar_wifi(contrato)

    # ------------------------------------------- a que atualiza antes de olhar
    def _base_fresca(self):
        """Refaz o OFS GERAL se ele estiver velho, e conta o que aconteceu.

        Regra da operacao: rota de tecnico, backlog e previa da carga sao
        perguntas sobre AGORA, e agora muda o tempo todo. Responder por um
        arquivo de uma hora atras devolve um numero plausivel e errado -- o
        pior tipo, porque nao tem como quem le desconfiar.

        Nunca levanta: base velha ainda responde quase tudo, e a resposta sai
        com o aviso de quando ela e.
        """
        try:
            import atualizar_bases
        except Exception:
            logger.exception('Nao consegui importar o atualizar_bases.')
            return None
        estado = atualizar_bases.garantir_ofs_fresco()
        if estado.get('atualizou'):
            # A leitura das bases e cacheada por mtime; o arquivo novo
            # invalida o cache sozinho, sem ninguem precisar limpar nada.
            logger.info('Busca: base do OFS atualizada antes de responder.')
        return estado

    @staticmethod
    def _nota_de_frescor(estado):
        """A linha que vai junto do resultado dizendo de quando e a base."""
        if not estado:
            return None
        if estado.get('atualizou'):
            return 'A base foi atualizada no OFS agora, antes desta contagem.'
        gravado = estado.get('gravado_em')
        quando = gravado.strftime('%d/%m as %H:%M') if gravado else 'desconhecida'
        if estado.get('motivo') in (None, 'ja estava fresca', 'já estava fresca'):
            return 'A base ja estava fresca: ela e de %s.' % quando
        return ('NAO consegui atualizar a base agora (%s). Os numeros sao da '
                'ultima carga, de %s, e voce PRECISA dizer isso na resposta.'
                % (estado.get('motivo'), quando))

    def carga_do_dia_seguinte(self, data=None):
        """A previa da carga, ja contada. O motor vive em carga_litoral.py."""
        estado = self._base_fresca()
        try:
            import carga_litoral
        except Exception:
            logger.exception('Nao consegui importar o carga_litoral.')
            return {'erro': 'O modulo da previa da carga nao esta disponivel.'}

        dia = None
        if data:
            for formato in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d'):
                try:
                    dia = datetime.strptime(str(data).strip(), formato).date()
                    break
                except ValueError:
                    continue
            if dia is None:
                return {'erro': 'Nao entendi a data %r. Use dd/mm/aaaa.' % data}

        # O caminho vem do proprio Buscador quando ele foi construido com um:
        # e assim que a avaliacao aponta a previa para uma base de ensaio em
        # vez da base de producao.
        carga = carga_litoral.levantar_carga(
            quando=dia, agora=self.agora,
            caminho=self.caminho_ofs_geral or None)
        resultado = {
            'data': carga['data'].strftime('%d/%m/%Y'),
            'recorte': ('Litoral Norte, balde e rotas de tecnico, tudo menos '
                        'cancelado, so Ativacao / Mudanca de Endereco / Reparo'),
            'total': carga['total'],
            'por_turno': {turno: carga_litoral.total_do_turno(carga, turno)
                          for turno in carga['turnos']},
            'por_cidade': carga['por_cidade'],
            'por_tipo': carga['por_tipo'],
            'capa': carga['capa'],
            'base_de': self._nota_de_frescor(estado),
            'observacao': (
                'TODOS os numeros acima ja estao somados. "capa" e '
                'cidade -> tipo -> turno -> quantidade; "por_cidade" e '
                '"por_tipo" sao os mesmos dados ja totalizados. Leia o numero '
                'que a pergunta pede e nao refaca nenhuma soma. '
                'COSTA SUL e TOPO sao as duas metades de Sao Sebastiao, e '
                'Bertioga esta dentro de COSTA SUL.'),
        }
        if carga.get('aviso'):
            resultado['aviso'] = carga['aviso']
        # A lista nominal so entra quando e curta: a previa costuma passar de
        # trinta linhas com nome e endereco, e despejar isso no contexto custa
        # caro e nao e o que a pergunta pediu. Quem quer a lista pede /carga.
        if carga['total'] and carga['total'] <= 25:
            resultado['atividades'] = carga['linhas']
        elif carga['total']:
            resultado['atividades'] = carga['linhas'][:10]
            resultado['sobre_a_lista'] = (
                'Sao %d atividades e acima estao so as 10 primeiras. A lista '
                'inteira, com nome e endereco de cada uma, sai no comando '
                '/carga, que desenha a capa e a lista detalhada.'
                % carga['total'])
        return resultado

    def _varrer(self, montar_filtro):
        """O mesmo filtro em TODAS as bases, junto num quadro so, sem repetir.

        `montar_filtro` recebe uma tabela e devolve a mascara daquela base, ou
        None quando a base nao tem a coluna que o filtro precisa.

        Devolve (quadro, por_base). O quadro ja vem sem duplicata: a mesma
        atividade aparece no OFS GERAL e na base de improdutivas, e conta-la
        duas vezes faria "2 atendimentos" de um so. `por_base` guarda quantas
        cada base tinha ANTES da deduplicacao, que e a informacao util --
        estar na base de improdutivas quer dizer uma coisa, estar na de
        garantias quer dizer outra.
        """
        import pandas as pd

        pedacos, por_base = [], {}
        for rotulo, tabela in self._tabelas():
            try:
                filtro = montar_filtro(tabela)
            except Exception:
                logger.exception('Busca: filtro falhou em %s.', rotulo)
                continue
            if filtro is None:
                continue
            achadas = tabela[filtro]
            if not len(achadas):
                continue
            por_base[rotulo] = int(len(achadas))
            pedaco = achadas.copy()
            pedaco[COLUNA_ORIGEM] = rotulo
            pedacos.append(pedaco)

        if not pedacos:
            return None, por_base

        junto = pd.concat(pedacos, ignore_index=True, sort=False)
        if COLUNA_CHAVE in junto.columns:
            chave = junto[COLUNA_CHAVE].fillna('').astype(str).str.strip()
        else:
            chave = pd.Series([''] * len(junto), index=junto.index)
        # Sem id, o par O.S.+Data identifica a atividade. Linha sem os dois
        # fica: e melhor mostrar duas vezes do que sumir com um atendimento.
        vazia = chave == ''
        if vazia.any():
            partes = []
            for coluna in (COLUNA_OS, COLUNA_DATA):
                if coluna in junto.columns:
                    partes.append(junto[coluna].fillna('').astype(str))
            if partes:
                alternativa = partes[0]
                for parte in partes[1:]:
                    alternativa = alternativa + '|' + parte
                chave = chave.where(~vazia, alternativa)
        marcadas = chave != ''
        junto = junto[~(marcadas & chave.duplicated())]
        return junto, por_base

    @staticmethod
    def _todas(quadro):
        """Mascara de tudo, para reaproveitar os helpers que pedem filtro."""
        import pandas as pd
        return pd.Series(True, index=quadro.index)

    @staticmethod
    def _chave(registro):
        """O que identifica uma atividade, para nao contar a mesma duas vezes."""
        identificador = registro.get(COLUNA_CHAVE)
        if identificador:
            return ('id', str(identificador).strip())
        return ('os+data', str(registro.get(COLUNA_OS) or '').strip(),
                str(registro.get(COLUNA_DATA) or '').strip())

    # ----------------------------------------------------------- em aberto
    def _chamados_do_contrato(self, contrato):
        alvo = _so_digitos(contrato)
        achados = []
        for chamado in self.chamados:
            if not isinstance(chamado, dict):
                continue
            if _so_digitos(chamado.get('codigoContrato')) != alvo:
                continue
            achados.append(self._resumir_chamado(chamado))
        return achados

    def _resumir_chamado(self, chamado):
        fila = chamado.get('fila')
        classificacao = chamado.get('classificacao')
        abertura = chamado.get('dataAbertura')
        try:
            aberto_em = (datetime.fromtimestamp(abertura / 1000)
                         .strftime('%d/%m/%Y %H:%M')) if abertura else None
        except (TypeError, ValueError, OSError):
            aberto_em = None
        return {
            'os_campo': chamado.get('id'),
            'contrato': chamado.get('codigoContrato'),
            'fila': (fila or {}).get('codigo') if isinstance(fila, dict) else fila,
            'classificacao': ((classificacao or {}).get('nome')
                              if isinstance(classificacao, dict) else None),
            'aberto_em': aberto_em,
            'unidade': chamado.get('enderecoUnidade'),
            # O cache do CAMPO guarda estes três desde 28/08/2026, para o /risco
            # responder sem ir buscar tudo de novo na API. Como já estão em
            # memória, a busca por contrato os entrega junto: a pergunta "qual
            # o endereço do 6884951" não precisa de uma segunda consulta.
            # O telefone NÃO está aqui -- ele mora só no chamado cru, e a
            # projeção o descarta. O telefone vem do OFS GERAL.
            'cliente': chamado.get('nomeCliente'),
            'logradouro': chamado.get('enderecoLogradouro'),
            'bairro': chamado.get('enderecoBairro'),
            'agendamento': chamado.get('agendamentoData'),
            'turno': chamado.get('agendamentoTurno'),
            'tecnico': chamado.get('tecnico'),
        }

    # --------------------------------------------------------- as ferramentas
    def buscar_contrato(self, contrato):
        alvo = _so_digitos(contrato)
        if not alvo:
            return {'erro': 'O contrato veio sem dígito nenhum.'}

        resultado = {
            'contrato': alvo,
            'em_aberto_no_campo': self._chamados_do_contrato(alvo),
        }

        def filtro(tabela):
            if COLUNA_CONTRATO not in tabela.columns:
                return None
            return tabela[COLUNA_CONTRATO].fillna('').map(_so_digitos) == alvo

        quadro, por_base = self._varrer(filtro)
        resultado['bases_consultadas'] = [r for r, _, _ in self.bases]
        if quadro is None:
            resultado['atividades_ofs'] = []
            resultado['total_atividades_ofs'] = 0
            if not resultado['em_aberto_no_campo']:
                resultado['aviso'] = (
                    'Procurei este contrato em todas as bases -- OFS GERAL, '
                    'improdutivas, garantias e a exportação do dia -- e '
                    'também no que está aberto no CAMPO. Não há nada. Isto é '
                    'uma ausência APURADA.')
            return resultado

        resultado['achado_por_base'] = por_base
        todas = self._todas(quadro)
        linhas, total = _linhas_liberadas(quadro, todas)
        resultado['atividades_ofs'] = linhas
        resultado['total_atividades_ofs'] = total
        resultado['contagem_por_status'] = _contar(quadro, todas, COLUNA_STATUS)
        if COLUNA_TIPO in quadro.columns:
            resultado['contagem_por_tipo'] = _contar(quadro, todas, COLUNA_TIPO)
        resultado['observacao'] = (
            'Cada linha é uma TENTATIVA de atendimento, não uma O.S. '
            'distinta: a mesma Ordem de Serviço reaparece quando é repassada '
            'a outro técnico ou reagendada. Para saber se o serviço foi '
            'feito, olhe se existe linha com status "concluído", não a '
            'quantidade de linhas. As linhas já vêm sem repetição entre as '
            'bases, e o campo "base" de cada uma diz de onde veio.')
        if total > len(linhas):
            resultado['aviso'] = (f'São {total} atividades e só as '
                                  f'{len(linhas)} primeiras vieram.')
        return resultado
    def buscar_ordem_servico(self, numero):
        alvo = _so_digitos(numero)
        if not alvo:
            return {'erro': 'A O.S. veio sem dígito nenhum.'}

        resultado = {'numero': alvo, 'em_aberto_no_campo': [
            self._resumir_chamado(chamado)
            for chamado in self.chamados
            if isinstance(chamado, dict)
            and _so_digitos(chamado.get('id')) == alvo
        ]}

        def filtro(tabela):
            junta = None
            for coluna in (COLUNA_OS, COLUNA_ID_OS):
                if coluna not in tabela.columns:
                    continue
                bate = tabela[coluna].fillna('').map(_so_digitos) == alvo
                junta = bate if junta is None else (junta | bate)
            return junta

        quadro, por_base = self._varrer(filtro)
        resultado['bases_consultadas'] = [r for r, _, _ in self.bases]
        if quadro is None:
            resultado['atividades_ofs'] = []
            resultado['total_atividades_ofs'] = 0
            if not resultado['em_aberto_no_campo']:
                resultado['aviso'] = (
                    'Procurei esta O.S. em todas as bases e no que está '
                    'aberto no CAMPO. Não há nada. Isto é uma ausência '
                    'APURADA.')
            return resultado

        resultado['achado_por_base'] = por_base
        todas = self._todas(quadro)
        linhas, total = _linhas_liberadas(quadro, todas)
        resultado['atividades_ofs'] = linhas
        resultado['total_atividades_ofs'] = total
        resultado['contagem_por_status'] = _contar(quadro, todas, COLUNA_STATUS)
        return resultado
    def listar_atividades_do_recurso(self, recurso, data=None):
        procurado = str(recurso or '').strip()
        if not procurado:
            return {'erro': 'Veio sem nome de recurso.'}

        # Rota e pergunta sobre AGORA: a base e atualizada antes de olhar.
        # Regra da operacao, e a mesma que vale para backlog e para a previa
        # da carga.
        frescor = self._nota_de_frescor(self._base_fresca())

        alvo = _sem_acento(procurado)
        # Sem filtro de data isto devolvia a carreira inteira do técnico: as
        # bases guardam meses, e um pedido de "como está a rota do Izaias
        # hoje" voltou com 17 mil caracteres de julho em diante. Rota é do
        # dia; o dia é o de hoje, a menos que peçam outro.
        dia = _data_ofs(data) if data else self.agora.strftime('%d/%m/%y')

        def pelo_nome(tabela):
            if COLUNA_RECURSO not in tabela.columns:
                return None
            return tabela[COLUNA_RECURSO].fillna('').map(_sem_acento) \
                .str.contains(alvo, regex=False)

        def do_dia(tabela):
            bate = pelo_nome(tabela)
            if bate is None or COLUNA_DATA not in tabela.columns:
                return bate
            return bate & (tabela[COLUNA_DATA].fillna('').str.strip() == dia)

        resultado = {'recurso': procurado, 'data': dia,
                     'bases_consultadas': [r for r, _, _ in self.bases],
                     'base_de': frescor}

        quadro, por_base = self._varrer(do_dia)
        if quadro is None:
            # Nada NAQUELE dia não é a mesma coisa que recurso inexistente, e
            # as duas respostas são diferentes para quem lê. Uma segunda
            # varredura, sem a data, separa os dois casos.
            sem_data, _ = self._varrer(pelo_nome)
            if sem_data is None:
                nomes = set()
                for _, tabela in self._tabelas():
                    if COLUNA_RECURSO in tabela.columns:
                        nomes |= {str(n).strip()
                                  for n in tabela[COLUNA_RECURSO].fillna('')
                                  .unique() if str(n).strip()}
                resultado['atividades'] = []
                resultado['aviso'] = (
                    f'Nenhum recurso com "{procurado}" no nome, em base '
                    f'nenhuma. Existem {len(nomes)} recursos ao todo.')
                resultado['alguns_nomes_existentes'] = sorted(nomes)[:25]
                return resultado

            def ordem(texto):
                try:
                    return datetime.strptime(texto, '%d/%m/%y')
                except ValueError:
                    return datetime.min

            vistos = {str(v).strip()
                      for v in sem_data.get(COLUNA_DATA, [])
                      if str(v).strip()} if COLUNA_DATA in sem_data.columns \
                else set()
            resultado['atividades'] = []
            resultado['recursos_que_casaram'] = sorted(
                {str(n).strip() for n in sem_data[COLUNA_RECURSO].fillna('')
                 .unique() if str(n).strip()})
            resultado['aviso'] = (
                f'{procurado} não tem atividade nenhuma em {dia}, em base '
                'nenhuma.')
            resultado['ultimos_dias_com_atividade'] = sorted(
                vistos, key=ordem)[-8:]
            return resultado

        # O nome que veio pode casar com mais de um recurso ("Silva"). Dizer
        # quais casaram evita o modelo somar duas rotas e chamar de uma.
        resultado['recursos_que_casaram'] = sorted(
            {str(n).strip() for n in quadro[COLUNA_RECURSO].fillna('').unique()
             if str(n).strip()})
        resultado['achado_por_base'] = por_base
        todas = self._todas(quadro)
        linhas, total = _linhas_liberadas(quadro, todas,
                                          descartar=COLUNAS_FORA_DA_ROTA)
        produtivas = (~quadro[COLUNA_TIPO].isin(TIPOS_NAO_PRODUTIVOS)
                      if COLUNA_TIPO in quadro.columns else todas)
        resultado['total_atividades'] = total
        resultado['total_produtivas'] = int(produtivas.sum())
        resultado['contagem_por_status'] = _contar(quadro, todas, COLUNA_STATUS)
        if COLUNA_TIPO in quadro.columns:
            resultado['contagem_por_tipo'] = _contar(quadro, todas, COLUNA_TIPO)
        resultado['atividades'] = linhas
        resultado['observacao'] = (
            'Cada linha é uma TENTATIVA, não uma O.S. distinta: a mesma '
            'Ordem de Serviço reaparece quando é repassada ou reagendada. '
            'Almoço e Consulta Médica não são serviço e ficam fora de '
            'total_produtivas.')
        return resultado
    def buscar_cliente(self, termo, dias=None):
        """Procura um termo em todas as colunas, em todas as bases."""
        procurado = str(termo or '').strip()
        if not procurado:
            return {'erro': 'A busca veio sem termo nenhum.'}

        resultado = {'termo': procurado,
                     'bases_consultadas': [r for r, _, _ in self.bases]}
        if dias:
            resultado['janela_dias'] = str(dias).strip()

        # O CAMPO primeiro: o que esta ABERTO agora e a resposta mais urgente,
        # e ele so tem nome e endereco -- telefone nao sobrevive a projecao.
        alvo_texto = _sem_acento(procurado)
        abertos = []
        for chamado in self.chamados:
            if not isinstance(chamado, dict):
                continue
            campos = (chamado.get('nomeCliente'),
                      chamado.get('enderecoLogradouro'),
                      chamado.get('enderecoBairro'),
                      chamado.get('codigoContrato'))
            if any(alvo_texto in _sem_acento(c) for c in campos if c):
                abertos.append(self._resumir_chamado(chamado))
        resultado['em_aberto_no_campo'] = abertos

        alvo_digitos = _so_digitos(procurado)
        usa_digitos = len(alvo_digitos) >= MINIMO_DIGITOS_BUSCA
        # Onde casou, somado entre as bases. Serve para o modelo saber se
        # achou pelo nome ou pelo endereco -- sao conclusoes diferentes.
        casou_em = {}

        def filtro(tabela):
            junta = None
            for coluna in COLUNAS_BUSCA_LIVRE:
                if coluna not in tabela.columns:
                    continue
                valores = tabela[coluna].fillna('')
                if coluna in COLUNAS_SO_DIGITOS:
                    if not usa_digitos:
                        continue
                    mascara = valores.map(_so_digitos).str.contains(
                        alvo_digitos, regex=False)
                else:
                    mascara = valores.map(_sem_acento).str.contains(
                        alvo_texto, regex=False)
                quantas = int(mascara.sum())
                if quantas:
                    casou_em[coluna] = casou_em.get(coluna, 0) + quantas
                    junta = mascara if junta is None else (junta | mascara)
            if junta is None:
                return None
            janela = _dentro_da_janela(tabela, dias, self.agora)
            return junta if janela is None else (junta & janela)

        quadro, por_base = self._varrer(filtro)

        if quadro is None:
            resultado['atividades_ofs'] = []
            resultado['total_atividades_ofs'] = 0
            if casou_em:
                # Casou, mas a janela comeu tudo. Dizer isso e diferente de
                # dizer que nao ha registro -- e a segunda frase seria falsa.
                resultado['aviso'] = (
                    f'Existe registro de "{procurado}" nas bases, mas nenhum '
                    'dentro da janela pedida. Diga as duas coisas: que existe '
                    'historico e que ele e mais antigo que a janela.')
                resultado['casou_nas_colunas'] = casou_em
            else:
                resultado['aviso'] = (
                    f'Procurei "{procurado}" em nome, telefone, celular, '
                    'e-mail, endereco, CEP, cidade, contrato, O.S. e recurso, '
                    'em TODAS as bases -- OFS GERAL, improdutivas, garantias '
                    'e a exportacao do dia -- e nao ha nenhuma linha. Isto e '
                    'uma ausencia APURADA: pode responder que procurou e nao '
                    'achou.')
            return resultado

        resultado['casou_nas_colunas'] = casou_em
        resultado['achado_por_base'] = por_base
        todas = self._todas(quadro)
        linhas, total = _linhas_liberadas(quadro, todas,
                                          descartar=COLUNAS_FORA_DA_ROTA)
        resultado['atividades_ofs'] = linhas
        resultado['total_atividades_ofs'] = total
        resultado['contagem_por_status'] = _contar(quadro, todas, COLUNA_STATUS)
        if COLUNA_TIPO in quadro.columns:
            resultado['contagem_por_tipo'] = _contar(quadro, todas, COLUNA_TIPO)

        # A janela da operacao, sempre, mesmo quando ninguem pediu janela.
        #
        # A alternativa seria fazer 60 dias o padrao, e ela e pior: alguem
        # atendido ha 70 dias voltaria como "nenhum registro", que e uma
        # resposta errada com cara de certa -- exatamente o erro que este
        # sistema inteiro persegue. Assim a busca continua alcancando todo o
        # historico, e o numero que a operacao usa vem ao lado, apurado.
        if not dias:
            recente = _dentro_da_janela(quadro, JANELA_DA_OPERACAO, self.agora)
            if recente is not None:
                dentro = int(recente.sum())
                resultado['nos_ultimos_%s_dias' % JANELA_DA_OPERACAO] = dentro
                if total and not dentro:
                    resultado['so_fora_da_janela'] = (
                        f'Ha {total} registro(s), mas NENHUM nos ultimos '
                        f'{JANELA_DA_OPERACAO} dias, que e a janela da '
                        'operacao. Diga as duas coisas: que existe historico '
                        'e que ele e antigo.')

        if 'Nome' in quadro.columns:
            nomes = sorted({str(n).strip() for n in quadro['Nome'].fillna('')
                            .unique() if str(n).strip()})
            resultado['nomes_que_casaram'] = nomes[:15]
            resultado['quantos_nomes_distintos'] = len(nomes)

        if total > len(linhas):
            resultado['observacao'] = (
                f'Sao {total} linhas e so as primeiras {len(linhas)} estao '
                'acima. Diga isso na resposta; nao afirme nada sobre as que '
                'faltam.')

        if len(casou_em) > 1 and resultado.get('quantos_nomes_distintos', 0) > 3:
            # Larga demais para 40 linhas serem resposta: elas seriam 40
            # pessoas diferentes apresentadas como se fossem uma. Sobram
            # poucas, de amostra, e o resultado diz em alto e bom som que
            # cortou, quantas existem e como estreitar. Nada e escondido --
            # os numeros e os nomes que casaram continuam todos aqui.
            resultado['atividades_ofs'] = linhas[:TETO_AMOSTRA_BUSCA_LARGA]
            resultado['busca_ficou_larga'] = (
                f'"{procurado}" casou em {len(casou_em)} coluna(s) diferentes '
                f'e trouxe {resultado["quantos_nomes_distintos"]} nomes '
                'distintos -- pegou nome de rua ou de tecnico junto. Acima ha '
                f'so {TETO_AMOSTRA_BUSCA_LARGA} linhas de amostra, de '
                f'{total}. NAO responda como se fossem de uma pessoa so: diga '
                'quantas apareceram, mostre os nomes que casaram e peca o '
                'nome inteiro, o telefone ou o contrato.')
        return resultado
    def executar(self, nome, argumentos):
        """Roda a busca pedida pelo modelo. Nunca levanta exceção.

        Erro aqui não pode derrubar a resposta: o modelo lida bem com um
        resultado que diz "não deu", e mal com um bot que emudece.
        """
        argumentos = argumentos if isinstance(argumentos, dict) else {}
        self.feitas.append((nome, argumentos))
        metodo = {
            'buscar_contrato': lambda: self.buscar_contrato(
                argumentos.get('contrato')),
            'buscar_ordem_servico': lambda: self.buscar_ordem_servico(
                argumentos.get('numero')),
            'listar_atividades_do_recurso': lambda:
                self.listar_atividades_do_recurso(argumentos.get('recurso'),
                                                  argumentos.get('data')),
            'buscar_cliente': lambda: self.buscar_cliente(
                argumentos.get('termo'), argumentos.get('dias')),
            'consultar_autenticador': lambda: self.consultar_autenticador(
                argumentos.get('contrato')),
            'consultar_wifi': lambda: self.consultar_wifi(
                argumentos.get('contrato')),
            'carga_do_dia_seguinte': lambda: self.carga_do_dia_seguinte(
                argumentos.get('data')),
        }.get(nome)
        if metodo is None:
            return {'erro': f'Busca desconhecida: {nome}.'}
        try:
            return metodo()
        except Exception:
            logger.exception('Busca: %s falhou com %s.', nome, argumentos)
            return {'erro': 'A busca falhou por um erro interno. Responda '
                            'com o que você já tem e diga que este dado não '
                            'pôde ser conferido.'}
