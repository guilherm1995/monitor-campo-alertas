# -*- coding: utf-8 -*-
"""Retrato em texto da operação inteira, para o /bot responder em cima dele.

Este arquivo não fala com IA nenhuma e não decide nada. Ele pega as estruturas
que o bot já calcula para gerar as imagens de backlog e as listas dos comandos,
e escreve tudo em texto corrido. Quem consome é o assistente_ia.

Duas escolhas que valem explicação, porque são elas que separam "IA que
responde certo" de "IA que inventa":

1. TODO número aqui já vem apurado pelo código de sempre -- as mesmas funções
   que produzem as imagens que o grupo lê há meses. O modelo lê "VRD: 42
   abertas, 8 acima de 5 dias" pronto; ele não recebe 345 linhas para contar
   sozinho. Modelo de linguagem é péssimo em contar e ótimo em ler. O dossiê
   existe para deixá-lo só na segunda parte.

2. Não vai nome de cliente, telefone nem logradouro. Contrato e número da O.S.
   vão, porque sem eles a resposta não dá para agir. É o mínimo que responde a
   pergunta da operação sem transformar cada pergunta no grupo em uma remessa
   de cadastro de cliente para fora da máquina.

Fonte que faltar entra como "não disponível" em vez de sumir em silêncio: o
modelo é instruído a dizer que não sabe, e para isso ele precisa ver o buraco.
"""
import io
import logging
import os
from datetime import datetime, timedelta

from backlog_capex import (CATEGORIAS as CATEGORIAS_CAPEX, REGIOES,
                           LIMITE_BUCKET1_HORAS as _BUCKET1_CAPEX,
                           LIMITE_BUCKET2_DIAS as _BUCKET2_DIAS_CAPEX)
from backlog_reparo import (CATEGORIAS as CATEGORIAS_REPARO,
                            LIMITE_BUCKET1_HORAS as _BUCKET1_REPARO,
                            LIMITE_BUCKET2_HORAS as _BUCKET2_REPARO)

logger = logging.getLogger(__name__)

# Teto da tabela de O.S. Hoje a varredura vê algo em torno de 350, então o
# limite não deve morder; ele existe para o dia em que morder. Quando morde, o
# dossiê DIZ que cortou -- lista truncada em silêncio é a maneira mais fácil de
# fazer o modelo responder "não há mais nenhuma" com convicção e estar errado.
TETO_LINHAS_OS = 600

_CATEGORIA_POR_CODIGO = {}
for _nome, _codigos in list(CATEGORIAS_CAPEX.items()) + list(CATEGORIAS_REPARO.items()):
    for _codigo in _codigos:
        _CATEGORIA_POR_CODIGO[_codigo] = _nome

# Os limites dos baldes de idade, em horas, por categoria. Eles NÃO são iguais
# entre as categorias: Reparo muda de balde em 24h e 48h, Ativação em 48h e 7
# dias, Upgrade em 4 e 7 dias.
#
# Até 30/08/2026 o dossiê escrevia "até 48h / 2 a 5 dias / acima de 5 dias"
# para todas elas, com rótulo fixo no código. Nenhum dos três dizia a verdade
# para Reparo, e o de cima ainda estava desatualizado para Ativação, que passou
# de 5 para 7 dias. O número era o certo; o nome ao lado dele, não -- e o /bot
# repetia o nome errado com citação, que é a forma mais convincente de errar.
#
# Por isso os rótulos passam a ser DERIVADOS dos mesmos valores que fazem a
# classificação. Mudar um prazo em backlog_capex.py ou backlog_reparo.py muda o
# texto aqui junto, sem ninguém precisar lembrar.
LIMITES_IDADE_HORAS = {}
for _cat in CATEGORIAS_CAPEX:
    LIMITES_IDADE_HORAS[_cat] = (_BUCKET1_CAPEX[_cat], _BUCKET2_DIAS_CAPEX * 24)
for _cat in CATEGORIAS_REPARO:
    LIMITES_IDADE_HORAS[_cat] = (_BUCKET1_REPARO[_cat], _BUCKET2_REPARO[_cat])


def _prazo_por_extenso(horas):
    """48 -> '48h'; 96 -> '4 dias'. A operação fala assim."""
    if horas <= 48:
        return f'{horas}h'
    return f'{horas // 24} dias'


def _rotulos_idade(categoria):
    """Os três nomes de balde desta categoria, tirados dos limites dela."""
    limites = LIMITES_IDADE_HORAS.get(categoria)
    if not limites:
        return ('balde 1', 'balde 2', 'balde 3')
    um, dois = (_prazo_por_extenso(h) for h in limites)
    # "de 4 dias a 7 dias" é como a máquina escreveria; a operação diz
    # "de 4 a 7 dias". Some a unidade repetida quando as duas pontas usam a
    # mesma, e só nesse caso.
    meio = (f'de {um[:-5]} a {dois}' if um.endswith(' dias')
            and dois.endswith(' dias') else f'de {um} a {dois}')
    return (f'até {um}', meio, f'acima de {dois}')

# O glossário que vai dentro do dossiê é o arquivo regras/01-vocabulario.md,
# não uma cópia dele. Foram duas cópias até 30/08/2026, e elas discordavam:
# o glossário daqui dizia que Ativação muda de balde em 5 dias, e o
# backlog_capex.py -- que faz a classificação de verdade -- usava 7.
#
# A cópia embutida abaixo é o socorro para o arquivo sumir. O dossiê sai com
# glossário velho e um aviso no log, em vez de sair sem glossário nenhum, que
# foi o estado em que uma pergunta com a palavra "nota" recebia a resposta de
# que não havia notas.
_VOCABULARIO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'regras', '01-vocabulario.md')

_GLOSSARIO_EMBUTIDO = """GLOSSÁRIO (use estes termos exatamente com estes sentidos)
- Nota, ou nota de serviço: é o mesmo que O.S. (ordem de serviço). É como a
  operação chama no dia a dia -- "quantas notas o técnico tem hoje" quer dizer
  quantas O.S. Sem isto escrito, uma pergunta com "nota" recebia a resposta de
  que o dossiê não traz notas, quando traz todas.
- Chamado: o registro no CAMPO, que pode conter mais de uma O.S.
- Bucket de idade: tempo desde a abertura da O.S. Ativação e Mudança de
  endereço usam até 48h / de 2 a 5 dias / acima de 5 dias.
- D0: agendado para hoje. D+1, D+2, D+3: para os próximos dias. Vencida: a
  data agendada já passou e a O.S. continua aberta.
- Enviado D0: a O.S. está na agenda de hoje, seja pelo agendamento do CAMPO,
  seja porque o contrato aparece no OFS GERAL de hoje (O.S. que deu erro de
  integração é puxada à mão pelo OFS, e sem esse cruzamento o número sai menor
  que o real).
- Conveniência: não é D0, mas o contrato está na planilha de conveniência.
- Oportunidade de injeção: nem D0 nem conveniência. É o que dá para puxar
  para frente. Inclui agendamento futuro, vencida e O.S. ainda sem agenda.
- Improdutiva reincidente: O.S. aberta cujo contrato já teve visita
  improdutiva antes.
- Garantia: reparo que voltou dentro do prazo de garantia do atendimento
  anterior.
- Área de risco: endereço que cai dentro do mapa de risco da operação do Rio,
  ou que está na lista de ruas marcadas.
- Pacote: a última O.S. do chamado já tem material/pacote definido."""


def _ler_vocabulario():
    try:
        return io.open(_VOCABULARIO, encoding='utf-8').read().strip()
    except OSError as erro:
        logger.warning('Dossiê: não consegui ler %s (%s). Usando o glossário '
                       'embutido -- ele pode estar atrasado.',
                       _VOCABULARIO, erro)
        return _GLOSSARIO_EMBUTIDO


GLOSSARIO = _ler_vocabulario()


# Coluna do tipo de serviço na exportação do OFS. A planilha traz DUAS
# colunas chamadas "Tipo de Atividade": a primeira é o regime da atividade e
# vem inteira como "Normal"; a segunda, que o pandas nomeia com o sufixo .1, é
# a que diz Ativação, Reparo, Upgrade. Ler a errada não dá erro nenhum -- dá
# uma resposta calma e vazia, que é o pior tipo de defeito.
COLUNA_TIPO_OFS = 'Tipo de Atividade.1'

# O que NÃO é serviço de campo. Some da contagem por recurso para "quantas
# atividades o fulano tem hoje" não incluir o almoço dele.
TIPOS_NAO_PRODUTIVOS = {'Almoço', 'Consulta Médica'}


def ler_agenda_ofs(caminho):
    """A exportação do OFS de hoje, agregada. None se o arquivo não estiver lá.

    Só agregados saem daqui: contagem por recurso, por cidade, por status e
    por tipo. A exportação tem nome, endereço, telefone e e-mail do cliente em
    cada linha, e nada disso ajuda a responder pergunta de operação -- então
    nada disso entra no dossiê. Contagem responde e não vaza.

    O `Recurso` mistura duas coisas: nome de técnico e nome de cidade. Os de
    cidade são a fila da praça, não uma pessoa. O dossiê diz isso em vez de
    fingir que são todos técnicos.
    """
    import pandas as pd

    caminho = str(caminho)
    if not os.path.exists(caminho):
        return None
    try:
        quando = datetime.fromtimestamp(os.path.getmtime(caminho))
        tabela = pd.read_csv(caminho, sep=None, engine='python',
                             encoding='utf-8-sig', dtype=str)
    except Exception:
        logger.exception('Dossiê: falha ao ler a exportação do OFS em %s.',
                         caminho)
        return None

    tipos = tabela[COLUNA_TIPO_OFS] if COLUNA_TIPO_OFS in tabela.columns else None
    produtivas = tabela
    if tipos is not None:
        produtivas = tabela[~tipos.isin(TIPOS_NAO_PRODUTIVOS)]

    # TODAS as contagens saem das produtivas, não da planilha inteira. Com uma
    # base em cada contagem, a soma por cidade não fechava com a soma por
    # recurso e ninguém teria como saber por quê.
    def contar(coluna):
        if coluna not in produtivas.columns:
            return {}
        return {str(chave): int(valor) for chave, valor
                in produtivas[coluna].fillna('(vazio)').value_counts().items()}

    por_recurso = {}
    if 'Recurso' in produtivas.columns:
        agrupado = produtivas.groupby(produtivas['Recurso'].fillna('(vazio)'))
        for recurso, linhas in agrupado:
            status = {}
            if 'Status da Atividade' in linhas.columns:
                status = {str(k): int(v) for k, v in
                          linhas['Status da Atividade'].fillna('(vazio)')
                          .value_counts().items()}
            por_recurso[str(recurso).strip()] = {
                'total': int(len(linhas)),
                'status': status,
            }

    return {
        'arquivo': os.path.basename(caminho),
        'quando': quando,
        'linhas': int(len(tabela)),
        'produtivas': int(len(produtivas)),
        'por_recurso': por_recurso,
        'por_cidade': contar('Cidade'),
        'por_status': contar('Status da Atividade'),
        'por_tipo': contar(COLUNA_TIPO_OFS),
    }


def _texto_data(quando):
    return quando.strftime('%d/%m/%Y %H:%M') if quando else '?'


def _codigo_da_fila(chamado):
    fila = chamado.get('fila')
    if isinstance(fila, dict):
        return fila.get('codigo')
    if isinstance(fila, str):
        return fila
    return chamado.get('codigo')


def _dias_aberto(chamado, agora):
    ms = chamado.get('dataAbertura')
    if not ms:
        return None
    try:
        return (agora - datetime.fromtimestamp(ms / 1000)).days
    except (TypeError, ValueError, OSError):
        return None


def _rotulo_agenda(chamado, hoje):
    """D0, D+1, vencida, sem agenda -- já classificado.

    Existe por um erro observado em teste: pedindo ao modelo que olhasse as
    datas cruas da tabela, ele chamou de "vencida" uma O.S. sem agendamento e
    de "vencido" um agendamento que era de hoje. Comparar datas de cabeça é
    justamente o que modelo de linguagem faz mal. A conta é de uma linha aqui,
    e ele passa a ler o rótulo em vez de deduzi-lo -- a mesma razão de os
    totais virem apurados.
    """
    bruto = chamado.get('agendamentoData')
    if not bruto:
        return 'sem agenda'
    try:
        data = datetime.strptime(bruto, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return f'{bruto} (data ilegível)'
    diferenca = (data - hoje).days
    # Os três rótulos não compartilham palavra nenhuma de propósito. Com
    # '(D+2)' ao lado de '(VENCIDA há 2 dias)' na linha vizinha, o modelo
    # pequeno leu o número certo e a palavra errada, e devolveu uma O.S.
    # futura como atrasada. Rótulos sem parentesco tiram essa chance.
    if diferenca < 0:
        return f'{bruto} [ATRASADA, deveria ter sido feita há {-diferenca} dia(s)]'
    if diferenca == 0:
        return f'{bruto} [HOJE]'
    return f'{bruto} [MARCADA PARA O FUTURO, daqui a {diferenca} dia(s)]'


def _linha_idade(unidade, linha, categoria=None):
    um, dois, tres = _rotulos_idade(categoria)
    return (f"  {unidade}: total {linha.get('total', 0)}"
            f" | {um} {linha.get('bucket1', 0)}"
            f" | {dois} {linha.get('bucket2', 0)}"
            f" | {tres} {linha.get('bucket3', 0)}"
            f" | enviado D0 {linha.get('enviado_d0', 0)}"
            f" | conveniência {linha.get('conveniencia', 0)}"
            f" | oportunidade de injeção {linha.get('oportunidade_injecao', 0)}")


def _linha_agenda(unidade, linha):
    return (f"  {unidade}: D0 {linha.get('d0', 0)}"
            f" | D+1 {linha.get('d1', 0)}"
            f" | D+2 {linha.get('d2', 0)}"
            f" | D+3 {linha.get('d3', 0)}"
            f" | além de D+3 {linha.get('mais_d3', 0)}"
            f" | vencidas {linha.get('vencida', 0)}")


def _secao_backlog(titulo, calculado, observacao=None):
    """Uma seção de backlog a partir do par (idade, agendamento)."""
    linhas = [f"== {titulo} =="]
    if observacao:
        linhas.append(observacao)
    if not calculado:
        linhas.append('NÃO DISPONÍVEL nesta consulta.')
        linhas.append('')
        return linhas

    idade, agendamento = calculado
    for categoria, regioes in idade.items():
        linhas.append(f"- {categoria}, por idade:")
        for regiao, unidades in regioes.items():
            linhas.append(f" região {regiao}:")
            for unidade, linha in unidades.items():
                if unidade != 'TOTAL' and not linha.get('total'):
                    continue
                linhas.append(_linha_idade(unidade, linha, categoria))
        agenda_categoria = (agendamento or {}).get(categoria) or {}
        if agenda_categoria:
            linhas.append(f"- {categoria}, por agendamento:")
            for regiao, unidades in agenda_categoria.items():
                linhas.append(f" região {regiao}:")
                for unidade, linha in unidades.items():
                    if unidade != 'TOTAL' and not any(
                            linha.get(c) for c in ('d0', 'd1', 'd2', 'd3',
                                                   'mais_d3', 'vencida')):
                        continue
                    linhas.append(_linha_agenda(unidade, linha))

    linhas += _linhas_consolidado(agendamento)
    linhas.append('')
    return linhas


CAMPOS_AGENDA = ('d0', 'd1', 'd2', 'd3', 'mais_d3', 'vencida')


def _linhas_consolidado(agendamento):
    """A soma das categorias, por unidade e por região, já apurada.

    POR QUE ISTO EXISTE
    -------------------
    O bloco acima mostra o agendamento CATEGORIA POR CATEGORIA: um bloco para
    Ativação, outro para Mudança de endereço. Mas a pergunta que a operação faz
    não é por categoria -- é "quanto CAPEX tem amanhã no litoral". Para
    responder isso pelo bloco acima, o modelo teria de somar dois números por
    unidade, em dois lugares diferentes do texto, e depois somar as unidades.

    É exatamente a conta que ele erra. Já aconteceu duas vezes: uma rota de 10
    atividades virou "4 de 7", e uma prévia de CAPEX D+1 saiu com a divisão
    por unidade trocada. Nos dois casos o número estava no dossiê e a soma é
    que falhou.

    A regra do arquivo inteiro é essa: contar é de graça aqui e caro lá.
    """
    if not agendamento or len(agendamento) < 2:
        return []      # uma categoria só já é o próprio consolidado

    juntos = {}
    for unidades_por_regiao in agendamento.values():
        for regiao, unidades in (unidades_por_regiao or {}).items():
            destino = juntos.setdefault(regiao, {})
            for unidade, linha in (unidades or {}).items():
                acumulado = destino.setdefault(
                    unidade, {campo: 0 for campo in CAMPOS_AGENDA})
                for campo in CAMPOS_AGENDA:
                    acumulado[campo] += int(linha.get(campo) or 0)

    nomes = ' + '.join(agendamento.keys())
    linhas = [f"- TODAS AS CATEGORIAS SOMADAS ({nomes}), por agendamento."
              f" Use ESTAS linhas quando a pergunta não citar uma categoria;"
              f" não some as de cima."]
    for regiao, unidades in juntos.items():
        linhas.append(f" região {regiao}:")
        # TOTAL por último, sempre. Juntando dois blocos, a ordem vinha do que
        # apareceu primeiro, e o total caía no meio da lista -- lido de relance
        # ele parece o número de uma unidade.
        ordenadas = sorted(unidades.items(),
                           key=lambda par: (par[0] == 'TOTAL', par[0]))
        for unidade, linha in ordenadas:
            if unidade != 'TOTAL' and not any(linha.get(c)
                                              for c in CAMPOS_AGENDA):
                continue
            linhas.append(_linha_agenda(unidade, linha))
    return linhas


def _secao_improdutivas(dados):
    linhas = ['== IMPRODUTIVAS REINCIDENTES EM ABERTO ==']
    if not dados:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas
    linhas.append(f"Total {dados.get('total', 0)} em "
                  f"{dados.get('analisados', 0)} O.S. analisadas.")
    for chave, rotulo in (('rj', 'SUL RJ'), ('litoral', 'LITORAL NORTE')):
        itens = dados.get(chave) or []
        linhas.append(f"- {rotulo} ({len(itens)}):")
        for item in itens:
            linhas.append(
                f"  contrato {item.get('contrato')} · {item.get('unidade')}"
                f" · agenda {item.get('agendamento') or 'sem agenda'}"
                f" · improdutiva anterior {item.get('quando') or '?'}"
                f" · técnico anterior {item.get('tecnico') or 'não identificado'}"
            )
    linhas.append('')
    return linhas


def _secao_risco(dados):
    linhas = ['== ÁREA DE RISCO, O.S. EM ABERTO ==']
    if not dados:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas
    linhas.append(f"Total {dados.get('total', 0)} em "
                  f"{dados.get('analisados', 0)} O.S. analisadas. "
                  f"{dados.get('sem_coordenada', 0)} chamado(s) sem coordenada "
                  f"-- nesses, só a lista de ruas pega.")
    for chave, rotulo in (('rj', 'SUL RJ'), ('litoral', 'LITORAL NORTE')):
        itens = dados.get(chave) or []
        linhas.append(f"- {rotulo} ({len(itens)}):")
        for item in itens:
            linhas.append(
                f"  contrato {item.get('contrato')} · {item.get('unidade')}"
                f" · {item.get('bairro')}"
                f" · agenda {item.get('agendamento') or 'sem agenda'}"
                f" · casou por {item.get('casou_por')}"
                f"{' (' + str(item.get('area')) + ')' if item.get('casou_por') == 'mapa' else ''}"
            )
    linhas.append('')
    return linhas


def _secao_garantias(garantias):
    """A MESMA lista que o /garantias manda para os grupos regionais."""
    linhas = ['== GARANTIAS EM ABERTO ==']
    if not garantias:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas

    linhas.append(f"{garantias.get('total', 0)} garantia(s) em aberto, "
                  f"apuradas em {garantias.get('gerado_em', '?')}.")
    for regiao in garantias.get('regioes') or []:
        linhas.append(f"- {regiao.get('nome')} ({regiao.get('total', 0)}, "
                      f"{regiao.get('offline', 0)} offline no Autenticador):")
        for cidade in regiao.get('cidades') or []:
            for item in cidade.get('itens') or []:
                linhas.append(
                    f"  contrato {item.get('contrato')} · {cidade.get('nome')}"
                    f" · O.S. {item.get('os_id')}"
                    f" · {item.get('aging')} dia(s) desde o serviço anterior"
                    f" · serviço anterior {item.get('servico') or item.get('tipo') or '?'}"
                    f" · técnico anterior {item.get('tecnico') or 'não identificado'}"
                    f" · Autenticador {item.get('status') or '?'}"
                )
    sobrando = garantias.get('sem_regiao') or []
    if sobrando:
        linhas.append(f"[{len(sobrando)} garantia(s) com unidade fora das "
                      f"regiões conhecidas -- não estão listadas acima.]")
    linhas.append('')
    return linhas


def _secao_ofs(contratos_ofs_d0, info_ofs):
    linhas = ['== OFS GERAL, AGENDA DE HOJE ==']
    if contratos_ofs_d0 is None:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas
    linhas.append(f"{len(contratos_ofs_d0)} contrato(s) na agenda de hoje "
                  f"segundo o OFS GERAL.")
    if info_ofs:
        linhas.append(f"Origem: {info_ofs}")
    linhas.append('O OFS diz se o contrato está na agenda de hoje. Quem manda '
                  'no que está aberto é o CAMPO.')
    linhas.append('')
    return linhas


def _secao_notificacoes(estatisticas):
    linhas = ['== O QUE O BOT JÁ NOTIFICOU HOJE ==']
    if not estatisticas:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas
    linhas.append(
        f"CAPEX entrantes avisados: {estatisticas.get('capex_notificadas_hoje', 0)}"
        f" | garantias: {estatisticas.get('garantias_notificadas_hoje', 0)}"
        f" | improdutivas: {estatisticas.get('improdutivas_notificadas_hoje', 0)}"
    )
    linhas.append(
        f"O.S. analisadas hoje: {estatisticas.get('os_analisadas_hoje', 0)}"
        f" | erros no log hoje: {estatisticas.get('erros_log_hoje', 0)}"
    )
    linhas.append(
        f"CAPEX pendente na última medição: SUL RJ "
        f"{estatisticas.get('capex_pendente_sul_rj', '?')}"
        f", LITORAL NORTE {estatisticas.get('capex_pendente_litoral_sp', '?')}"
    )
    linhas.append('')
    return linhas


# Nome e logradouro do cliente na tabela de O.S.
#
# Ligado é o certo: o dado é da operação, quem vai até o cliente precisa dele,
# e a decisão de 30/08/2026 foi entregar. A chave existe porque isso tem um
# preço medido -- a tabela inteira cresce o dossiê de ~13.000 para ~20.000
# tokens, e ele viaja em TODA pergunta, inclusive nas que não falam de cliente
# nenhum. Com o motor pequeno do 9router isso já degradou a resposta: um caso
# da avaliação que passava sempre virou intermitente, e a latência triplicou.
#
# Desligar NÃO esconde nada: nome, endereço, telefone e o resto continuam
# saindo pelas buscas, que é onde telefone e CEP sempre estiveram. O que muda
# é que o assistente passa a precisar pedir, em vez de já ter na mão.
#
# Com motor grande (Claude, ou qualquer um que aguente 20k de contexto sem
# perder qualidade), deixe ligado.
DOSSIE_CLIENTE = os.environ.get('DOSSIE_CLIENTE', '1') != '0'


def _cliente_na_linha(chamado):
    if not DOSSIE_CLIENTE:
        return ''
    return (f" | {chamado.get('nomeCliente') or '?'}"
            f" | {chamado.get('enderecoLogradouro') or '?'}")


def _secao_tabela(chamados, agora, contratos_improdutivas, contratos_risco):
    linhas = ['== TABELA DAS O.S. EM ABERTO ==',
              'Uma linha por O.S. Campos, nesta ordem: O.S., contrato, '
              'unidade, categoria, dias em aberto, agendamento, '
              + ('cliente, logradouro, ' if DOSSIE_CLIENTE else '')
              + 'bairro, pacote, marcas.',
              ('Telefone, celular, e-mail e CEP NÃO estão nesta tabela: eles '
               'não existem na lista em memória, só na exportação do OFS. '
               'Para eles, use a busca por contrato ou por O.S.'
               if DOSSIE_CLIENTE else
               'Nome, endereço, telefone e CEP do cliente NÃO estão nesta '
               'tabela, e você TEM acesso a eles: use a busca por contrato ou '
               'por O.S. Não responda que não tem o dado -- procure.'),
              'O agendamento já vem classificado entre colchetes: HOJE, '
              'ATRASADA, MARCADA PARA O FUTURO ou SEM AGENDA. Use o rótulo '
              'da própria linha; não recalcule pela data nem aproveite o '
              'rótulo da linha vizinha.']
    total = 0
    escritas = 0
    for chamado in (chamados or ()):
        if not isinstance(chamado, dict):
            continue
        total += 1
        if escritas >= TETO_LINHAS_OS:
            continue
        codigo = _codigo_da_fila(chamado)
        categoria = _CATEGORIA_POR_CODIGO.get(codigo, codigo or '?')
        dias = _dias_aberto(chamado, agora)
        contrato = str(chamado.get('codigoContrato') or '?')
        marcas = []
        if contrato in contratos_improdutivas:
            marcas.append('reincidente')
        if contrato in contratos_risco:
            marcas.append('área de risco')
        linhas.append(
            f"{chamado.get('id')} | {contrato}"
            f" | {chamado.get('enderecoUnidade') or '?'}"
            f" | {categoria}"
            f" | {dias if dias is not None else '?'}d"
            f" | agenda: {_rotulo_agenda(chamado, agora.date())}"
            f"{_cliente_na_linha(chamado)}"
            f" | {chamado.get('enderecoBairro') or '?'}"
            f" | {'com pacote' if chamado.get('tem_pacote') else 'sem pacote'}"
            f"{' | ' + ', '.join(marcas) if marcas else ''}"
        )
        escritas += 1

    if total > escritas:
        linhas.append(f"[ATENÇÃO: a lista foi cortada. São {total} O.S. em "
                      f"aberto e só as {escritas} primeiras estão acima. Não "
                      f"afirme nada sobre as que faltam.]")
    linhas.append('')
    return linhas


def _secao_agenda_ofs(agenda):
    linhas = ['== OFS, EXPORTAÇÃO DO DIA (agenda de campo) ==']
    if not agenda:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas
    linhas.append(
        f"Arquivo {agenda['arquivo']}, exportado em "
        f"{_texto_data(agenda['quando'])}. {agenda['linhas']} linha(s) na "
        f"planilha; {agenda['produtivas']} atividades de campo. TODAS as "
        f"contagens abaixo são sobre as {agenda['produtivas']} de campo: "
        f"almoço e consulta médica ficaram de fora."
    )
    linhas.append('Por status: ' + ', '.join(
        f'{chave} {valor}' for chave, valor in agenda['por_status'].items()))
    linhas.append('Por tipo de serviço: ' + ', '.join(
        f'{chave} {valor}' for chave, valor in agenda['por_tipo'].items()))
    linhas.append('Por cidade: ' + ', '.join(
        f'{chave} {valor}' for chave, valor in agenda['por_cidade'].items()))
    linhas.append('Por recurso (o recurso com nome de cidade é a fila da '
                  'praça, não uma pessoa):')
    for recurso, dados in sorted(agenda['por_recurso'].items(),
                                 key=lambda par: -par[1]['total']):
        detalhe = ', '.join(f'{chave} {valor}'
                            for chave, valor in dados['status'].items())
        linhas.append(f"  {recurso}: {dados['total']}"
                      + (f" ({detalhe})" if detalhe else ''))
    linhas.append('')
    return linhas


AVISO_AUTENTICADOR = (
    'ESTA SEÇÃO TEM SÓ O STATUS, E MAIS NADA. Ela não traz a hora em que o '
    'cliente caiu, nem há quanto tempo está assim, nem quando reconectou -- '
    'esses dados NÃO existem em lugar nenhum deste dossiê. Para qualquer '
    'pergunta com "quando", "há quanto tempo" ou "que horas", use a busca '
    'consultar_autenticador, que consulta ao vivo. Nunca escreva uma data ou uma '
    'hora de queda a partir desta lista: ela não contém nenhuma.')


def _secao_autenticador(autenticador):
    linhas = ['== AUTENTICADOR, CONEXÃO DOS CONTRATOS DE REPARO ABERTOS ==',
              AVISO_AUTENTICADOR]
    if not autenticador:
        linhas += ['NÃO DISPONÍVEL nesta consulta. Isto não quer dizer que os '
                   'contratos estejam online: quer dizer que não se sabe. '
                   'Use consultar_autenticador para saber de um contrato.', '']
        return linhas
    contagem = {}
    for status in autenticador.values():
        contagem[status] = contagem.get(status, 0) + 1
    linhas.append(f"{len(autenticador)} contrato(s) consultados: " + ', '.join(
        f'{chave} {valor}' for chave, valor in sorted(contagem.items())))
    offline = sorted(contrato for contrato, status in autenticador.items()
                     if status == 'OFFLINE')
    if offline:
        linhas.append('OFFLINE: ' + ', '.join(offline))
    linhas.append('Contrato que não está na lista acima simplesmente não foi '
                  'consultado -- só entram aqui os que têm reparo aberto. '
                  'Ausência daqui não é sinal de nada.')
    linhas.append('')
    return linhas


def _secao_termometro(termometro):
    linhas = ['== TERMÔMETRO DE ENTRANTES CAPEX (hoje) ==']
    if not termometro:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas
    contagem = termometro.get('contagem') or {}
    media = termometro.get('media_geral')
    linhas.append(f"Média histórica geral por unidade: "
                  f"{media if media is not None else '?'}.")
    linhas.append('Entraram hoje, por unidade: ' + ', '.join(
        f'{unidade} {quantos}' for unidade, quantos
        in sorted(contagem.items(), key=lambda par: -par[1]) if quantos))
    linhas.append('')
    return linhas


def _secao_base_historica(estado):
    linhas = ['== BASE HISTÓRICA DO OFS (a que alimenta improdutivas e garantias) ==']
    if not estado:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas
    linhas.append(f"Cobre até {estado.get('ate', '?')}. "
                  f"Última reconstrução: {estado.get('quando', '?')}.")
    # A idade da base é o que separa "não há improdutiva reincidente" de "a
    # base não sabe ainda". Quem responde precisa poder dizer qual dos dois é.
    resumo = estado.get('resumo')
    if isinstance(resumo, dict):
        linhas.append('Resumo da última reconstrução: ' + ', '.join(
            f'{chave} {valor}' for chave, valor in resumo.items()))
    linhas.append('')
    return linhas


def _vespera_dias(categoria):
    """Com quantos dias de aberto uma O.S. está a UM dia de mudar de balde.

    Sai dos mesmos limites que fazem a classificação, e por categoria. Antes
    eram duas constantes fixas, 1 e 4, escritas quando Ativação virava balde 3
    em 5 dias; hoje ela vira em 7, e Reparo vira em 48h. As duas constantes
    estavam erradas para todo mundo, e o dossiê anunciava como "vira amanhã"
    um conjunto de O.S. que não viraria coisa nenhuma.

    É a única aritmética de calendário do dossiê, e mora aqui justamente para
    não morar na cabeça do modelo.
    """
    um, dois = LIMITES_IDADE_HORAS.get(categoria, (48, 7 * 24))
    return (um // 24 - 1, dois // 24 - 1)


def _secao_sinais(chamados, agora, risco, garantias, termometro):
    """Os cruzamentos que transformam número em diagnóstico.

    Um total sozinho não diz nada: 9 entrantes é muito ou pouco? O que informa
    é o que está ao lado dele -- a média, o que vira amanhã, o que está
    agendado para hoje sem material. Essas contas são todas triviais e todas
    perigosas de deixar para o modelo, que erra ao comparar datas e ao contar
    linhas. Ficam apuradas aqui, uma vez, e ele lê o resultado.

    Nenhum juízo é emitido nesta seção: ela não diz "VRD está mal". Ela diz o
    que está represado onde. O juízo é da resposta, e tem de vir amarrado a
    estes números.
    """
    hoje = agora.date()
    porta = {}

    def linha(unidade):
        return porta.setdefault(unidade, {
            'abertas': 0, 'sem_agenda': 0, 'atrasadas': 0,
            'hoje': 0, 'hoje_sem_pacote': 0, 'amanha': 0,
            'vira_bucket1': 0, 'vira_bucket3': 0, 'sem_pacote': 0,
        })

    for chamado in (chamados or ()):
        if not isinstance(chamado, dict):
            continue
        unidade = str(chamado.get('enderecoUnidade') or '?').upper().strip()
        dados = linha(unidade)
        dados['abertas'] += 1

        dias = _dias_aberto(chamado, agora)
        categoria = _CATEGORIA_POR_CODIGO.get(_codigo_da_fila(chamado))
        vespera_um, vespera_tres = _vespera_dias(categoria)
        if dias == vespera_um:
            dados['vira_bucket1'] += 1
        if dias == vespera_tres:
            dados['vira_bucket3'] += 1

        tem_pacote = bool(chamado.get('tem_pacote'))
        if not tem_pacote:
            dados['sem_pacote'] += 1

        bruto = chamado.get('agendamentoData')
        if not bruto:
            dados['sem_agenda'] += 1
            continue
        try:
            data = datetime.strptime(bruto, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            continue
        distancia = (data - hoje).days
        if distancia < 0:
            dados['atrasadas'] += 1
        elif distancia == 0:
            dados['hoje'] += 1
            if not tem_pacote:
                dados['hoje_sem_pacote'] += 1
        elif distancia == 1:
            dados['amanha'] += 1

    linhas = ['== SINAIS POR UNIDADE (já cruzados) ==',
              'abertas | sem agenda | atrasadas | para hoje | para hoje SEM '
              'pacote | para amanhã | passa de 48h amanhã | passa de 5 dias '
              'amanhã | sem pacote no total']
    if not porta:
        linhas += ['NÃO DISPONÍVEL nesta consulta.', '']
        return linhas

    total_abertas = sum(dados['abertas'] for dados in porta.values())
    media = total_abertas / len(porta) if porta else 0
    linhas.append(f"{total_abertas} O.S. abertas em {len(porta)} unidade(s). "
                  f"Média de {media:.1f} por unidade -- use isto para dizer se "
                  f"uma unidade está acima ou abaixo do normal da casa.")

    for unidade, dados in sorted(porta.items(),
                                 key=lambda par: -par[1]['abertas']):
        linhas.append(
            f"  {unidade}: {dados['abertas']} | {dados['sem_agenda']} | "
            f"{dados['atrasadas']} | {dados['hoje']} | "
            f"{dados['hoje_sem_pacote']} | {dados['amanha']} | "
            f"{dados['vira_bucket1']} | {dados['vira_bucket3']} | "
            f"{dados['sem_pacote']}"
        )

    # Cruzamentos que valem por si, porque cada um é uma ação diferente.
    alertas = []
    # Agrupado numa linha só. Uma linha por unidade dizendo a mesma frase vinte
    # vezes empurra o resto do dossiê para longe e não acrescenta nada.
    sem_pacote_hoje = {unidade: dados['hoje_sem_pacote']
                       for unidade, dados in sorted(porta.items())
                       if dados['hoje_sem_pacote']}
    if sem_pacote_hoje:
        alertas.append(
            f"{sum(sem_pacote_hoje.values())} O.S. na agenda de HOJE sem "
            f"pacote definido -- candidatas a improdutiva se o técnico for sem "
            f"material. Por unidade: " + ', '.join(
                f'{unidade} {quantas}'
                for unidade, quantas in sem_pacote_hoje.items()) + '.'
        )
    for chave in ('rj', 'litoral'):
        for item in ((risco or {}).get(chave) or []):
            if item.get('agendamento') in (str(hoje),
                                           str(hoje + timedelta(days=1))):
                alertas.append(
                    f"{item.get('unidade')}: contrato {item.get('contrato')} "
                    f"está em ÁREA DE RISCO e agendado para "
                    f"{item.get('agendamento')} -- decidir quem entra e a que "
                    f"hora antes de a equipe sair."
                )
    for regiao in (garantias or {}).get('regioes') or []:
        for cidade in regiao.get('cidades') or []:
            for item in cidade.get('itens') or []:
                if item.get('status') == 'OFFLINE':
                    alertas.append(
                        f"{cidade.get('sigla')}: contrato {item.get('contrato')} "
                        f"é garantia e está OFFLINE no Autenticador há "
                        f"{item.get('aging')} dia(s) -- cliente sem serviço."
                    )

    if termometro and termometro.get('media_geral'):
        media_entrantes = termometro['media_geral']
        for unidade, quantos in sorted((termometro.get('contagem') or {}).items(),
                                       key=lambda par: -par[1]):
            if quantos and media_entrantes and quantos >= media_entrantes * 1.5:
                alertas.append(
                    f"{unidade}: entraram {quantos} CAPEX hoje contra uma "
                    f"média de {media_entrantes:.1f} por unidade -- entrada "
                    f"acima do normal."
                )

    if alertas:
        linhas.append('Pontos que já saltam do cruzamento:')
        linhas += [f'  - {texto}' for texto in alertas]
    else:
        linhas.append('Nenhum cruzamento acendeu: sem O.S. de hoje sem pacote, '
                      'sem área de risco agendada, sem garantia offline.')
    linhas.append('')
    return linhas


def _secao_fontes(fontes):
    """Quais fontes entraram neste dossiê e quais faltaram.

    Vem primeiro, antes de qualquer número, e existe por uma razão só: sem
    esta lista, uma fonte que falhou vira uma resposta confiante baseada em
    menos dado do que a pessoa imagina. Com ela, dá para perguntar "e o
    Autenticador?" e o próprio dossiê responde que não veio hoje.
    """
    linhas = ['== FONTES DESTE DOSSIÊ ==']
    for nome, presente, detalhe in fontes:
        marca = 'SIM' if presente else 'NÃO VEIO'
        linhas.append(f"- {nome}: {marca}"
                      + (f" — {detalhe}" if detalhe else ''))
    linhas.append('Não afirme nada apoiado numa fonte marcada como NÃO VEIO. '
                  'Se a pergunta depender dela, diga que ela faltou.')
    linhas.append('')
    return linhas


def montar(agora=None, chamados=None, capex=None, reparo=None,
           contratos_ofs_d0=None, info_ofs=None, improdutivas=None,
           risco=None, garantias=None, estatisticas=None,
           agenda_ofs=None, autenticador=None, termometro=None,
           base_historica=None):
    """O dossiê inteiro, em texto.

    Tudo é opcional de propósito: a fonte que falhar vira "NÃO DISPONÍVEL" e o
    /bot continua respondendo com o que sobrou, em vez de virar um erro no
    grupo. É a mesma escolha do resto do bot -- responder menos vale mais do
    que não responder.
    """
    agora = agora or datetime.now()

    contratos_improdutivas = set()
    for chave in ('rj', 'litoral'):
        for item in ((improdutivas or {}).get(chave) or []):
            contratos_improdutivas.add(str(item.get('contrato')))
    contratos_risco = set()
    for chave in ('rj', 'litoral'):
        for item in ((risco or {}).get(chave) or []):
            contratos_risco.add(str(item.get('contrato')))

    partes = [
        f"DOSSIÊ DA OPERAÇÃO — {_texto_data(agora)}",
        "Retrato da última varredura do CAMPO, cruzado com OFS, planilha de "
        "conveniência e o histórico que o bot guarda. Os números abaixo são os "
        "mesmos das imagens de backlog que o grupo recebe.",
        "Regiões: SUL RJ e LITORAL NORTE. Unidades por região: "
        + '; '.join(f"{regiao} = {', '.join(unidades)}"
                    for regiao, unidades in REGIOES.items()) + '.',
        "",
        GLOSSARIO,
        "",
    ]
    fontes = [
        ('CAMPO, chamados em aberto', bool(chamados),
         f'{len(chamados or ())} chamado(s) na última varredura'),
        ('CAMPO, backlog de CAPEX apurado', bool(capex), None),
        ('CAMPO, backlog de reparo/upgrade/mudança de cômodo', bool(reparo),
         'sem o cruzamento com o Autenticador nas colunas de conexão'),
        ('OFS, exportação de campo do dia', bool(agenda_ofs),
         (f"{agenda_ofs['linhas']} atividade(s), exportada em "
          f"{_texto_data(agenda_ofs['quando'])}") if agenda_ofs else None),
        ('OFS GERAL, agenda de hoje por contrato', contratos_ofs_d0 is not None,
         f'{len(contratos_ofs_d0)} contrato(s)' if contratos_ofs_d0 is not None else None),
        ('Base histórica do OFS', bool(base_historica),
         f"cobre até {base_historica.get('ate', '?')}" if base_historica else None),
        ('Autenticador, status de conexão', bool(autenticador),
         f'{len(autenticador)} contrato(s)' if autenticador else None),
        ('Garantias em aberto', bool(garantias),
         f"{garantias.get('total', 0)} em aberto" if garantias else None),
        ('Improdutivas reincidentes', bool(improdutivas),
         f"{improdutivas.get('total', 0)} em aberto" if improdutivas else None),
        ('Área de risco', bool(risco),
         f"{risco.get('total', 0)} em aberto" if risco else None),
        ('Termômetro de entrantes CAPEX', bool(termometro), None),
        ('Contadores do dia do próprio bot', bool(estatisticas), None),
    ]
    partes += _secao_fontes(fontes)
    partes += _secao_sinais(chamados, agora, risco, garantias, termometro)
    partes += _secao_backlog('BACKLOG DE CAPEX', capex)
    partes += _secao_backlog(
        'BACKLOG DE REPARO, UPGRADE E MUDANÇA DE CÔMODO', reparo,
        observacao="Sem o cruzamento com o Autenticador nesta consulta: as colunas "
                   "de conexão e perda não entram aqui."
    )
    partes += _secao_ofs(contratos_ofs_d0, info_ofs)
    partes += _secao_agenda_ofs(agenda_ofs)
    partes += _secao_base_historica(base_historica)
    partes += _secao_autenticador(autenticador)
    partes += _secao_termometro(termometro)
    partes += _secao_improdutivas(improdutivas)
    partes += _secao_risco(risco)
    partes += _secao_garantias(garantias)
    partes += _secao_notificacoes(estatisticas)
    partes += _secao_tabela(chamados, agora, contratos_improdutivas,
                            contratos_risco)
    return '\n'.join(partes)
