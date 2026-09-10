# -*- coding: utf-8 -*-
"""A prévia da carga desenhada: a capa e a lista detalhada.

São DUAS imagens, e é assim de propósito -- é assim que a operação sempre fez
no Excel, e é assim que o material chega no grupo hoje:

  CAPA           a tabela dinâmica. Cidade e tipo nas linhas, turno nas
                 colunas, total geral na ponta. Cabe numa tela de celular e é
                 o que a coordenação olha primeiro.
  LISTA          uma linha por atividade, com nome, endereço, cidade, turno e
                 tipo. É o que vai para quem monta a rota.

Reaproveita o motor (HTML/CSS -> PNG por Playwright, em subprocesso) e a paleta
do backlog_render, pela mesma razão de sempre: o grupo já recebe backlog,
termômetro e garantias com essa cara, e uma quarta identidade visual faria a
prévia parecer de outro sistema.
"""

import html
import os
from datetime import datetime

from backlog_render import (
    renderizar_backlog_png,
    COR_FUNDO, COR_CARD, COR_CARD_ESCURO, COR_LINHA, COR_DESTAQUE,
    COR_TEXTO, COR_TEXTO_MUTED, COR_AMARELO, COR_VERDE,
    FONTE, FONTE_NUM,
)
from carga_litoral import TOTAL, total_da_cidade, total_do_turno


def _e(valor):
    """Escapa para HTML. Nome e endereço são texto livre digitado por gente --
    um '&' perdido ali quebraria a tabela inteira em silêncio."""
    return html.escape(str(valor if valor is not None else ''))


# As medidas da capa, num lugar só. O bloco precisa saber exatamente a soma
# das colunas: com `table-layout: fixed`, uma tabela mais larga que o cartão que
# a segura transborda por cima da borda arredondada -- e o "Total Geral", que é
# a última coluna, é justamente o que fica pendurado para fora.
LARGURA_ROTULO = 560
LARGURA_TURNO = 160
LARGURA_TOTAL = 200
RESPIRO_BLOCO = 52       # o padding lateral do .bloco, 26 de cada lado
RESPIRO_PAGINA = 76      # o padding lateral do body, 38 de cada lado


def largura_da_capa(colunas_turno):
    return (LARGURA_ROTULO + LARGURA_TURNO * colunas_turno + LARGURA_TOTAL
            + RESPIRO_BLOCO)


def _estilo(colunas_turno):
    largura_capa = largura_da_capa(colunas_turno)
    return f"""
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: {COR_FUNDO};
    font-family: {FONTE};
    padding: 34px 38px;
    width: fit-content;
  }}
  .cabecalho {{ margin-bottom: 22px; }}
  .titulo-principal {{
    color: {COR_TEXTO}; font-size: 44px; font-weight: 700; letter-spacing: 0.5px;
  }}
  .titulo-principal span {{ color: {COR_DESTAQUE}; }}
  .subtitulo {{
    color: {COR_TEXTO_MUTED}; font-size: 24px; margin-top: 8px;
  }}
  .cartoes {{ display: flex; gap: 18px; margin-bottom: 22px; }}
  .cartao {{
    background: {COR_CARD}; border-autenticador: 12px; padding: 16px 26px;
    /* 260 e nao 210: "Já com técnico" quebrava em duas linhas e desalinhava a
       fileira inteira de cartoes. */
    min-width: 260px; white-space: nowrap;
  }}
  .cartao .rotulo {{
    color: {COR_TEXTO_MUTED}; font-size: 21px; text-transform: uppercase;
    letter-spacing: 1px;
  }}
  .cartao .valor {{
    color: {COR_TEXTO}; font-size: 46px; font-weight: 700;
    font-family: {FONTE_NUM}; margin-top: 4px;
  }}
  .bloco {{
    background: {COR_CARD}; border-autenticador: 14px; padding: 24px 26px 26px;
    margin-bottom: 20px; box-shadow: 0 4px 18px rgba(0,0,0,0.35);
    width: {largura_capa}px;
  }}
  table {{
    border-collapse: collapse; width: 100%;
    font-family: {FONTE_NUM}; table-layout: fixed;
  }}
  th {{
    color: {COR_TEXTO}; font-size: 24px; font-weight: 600; text-align: right;
    padding: 12px 14px; border-bottom: 2px solid {COR_LINHA}; white-space: nowrap;
  }}
  th.rotulo {{ text-align: left; }}
  td {{
    font-size: 27px; color: {COR_TEXTO}; padding: 11px 14px;
    border-bottom: 1px solid {COR_CARD_ESCURO};
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    text-align: right;
  }}
  td.rotulo {{ text-align: left; }}
  /* A hierarquia da tabela dinâmica: cidade em destaque, tipo recuado abaixo
     dela. Sem o recuo as duas linhas têm o mesmo peso e o leitor precisa
     decorar quais nomes são cidade -- que é exatamente o que a planilha do
     Excel resolve com o mesmo recuo. */
  tr.cidade td {{
    font-weight: 700; font-size: 29px; background: {COR_CARD_ESCURO}88;
    color: {COR_DESTAQUE};
  }}
  tr.tipo td.rotulo {{ padding-left: 46px; color: {COR_TEXTO_MUTED}; }}
  tr.tipo td {{ font-size: 25px; }}
  tr.total td {{
    font-weight: 700; font-size: 30px; border-top: 2px solid {COR_LINHA};
    border-bottom: none; background: {COR_DESTAQUE}18;
  }}
  td.zero {{ color: {COR_TEXTO_MUTED}44; }}
  td.geral {{ color: {COR_AMARELO}; font-weight: 700; }}
  .vazio {{
    background: {COR_CARD}; border-autenticador: 14px; padding: 40px;
    color: {COR_VERDE}; font-size: 34px; font-weight: 600;
  }}
"""


# As mesmas medidas para a lista. A soma tem de bater com o colgroup pela razão
# do bloco da capa -- e aqui o que ficava pendurado para fora era a coluna do
# tipo, com "Mudança de Endereço" cortado no "ç".
COLUNAS_LISTA = (
    ('Nome', 520, 'nome'),
    ('Endereço', 1240, ''),
    ('Cidade', 300, 'cidade'),
    ('Intervalo', 190, ''),
    ('Tipo de Atividade', 340, ''),
)
LARGURA_LISTA = sum(l for _, l, _ in COLUNAS_LISTA) + RESPIRO_BLOCO


def _estilo_lista():
    return f"""
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: {COR_FUNDO}; font-family: {FONTE};
    padding: 34px 38px; width: fit-content;
  }}
  .cabecalho {{ margin-bottom: 22px; }}
  .titulo-principal {{
    color: {COR_TEXTO}; font-size: 44px; font-weight: 700;
  }}
  .titulo-principal span {{ color: {COR_DESTAQUE}; }}
  .subtitulo {{ color: {COR_TEXTO_MUTED}; font-size: 24px; margin-top: 8px; }}
  .bloco {{
    background: {COR_CARD}; border-autenticador: 14px; padding: 24px 26px 26px;
    box-shadow: 0 4px 18px rgba(0,0,0,0.35); width: {LARGURA_LISTA}px;
  }}
  table {{
    border-collapse: collapse; width: 100%; font-family: {FONTE_NUM};
    table-layout: fixed;
  }}
  th {{
    color: {COR_TEXTO}; font-size: 23px; font-weight: 600; text-align: left;
    padding: 12px 14px; border-bottom: 2px solid {COR_LINHA}; white-space: nowrap;
  }}
  td {{
    font-size: 24px; color: {COR_TEXTO}; padding: 11px 14px;
    border-bottom: 1px solid {COR_CARD_ESCURO};
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  tr.linha:nth-child(odd) td {{ background: {COR_CARD_ESCURO}55; }}
  td.nome {{ font-weight: 700; }}
  td.cidade {{ color: {COR_DESTAQUE}; font-weight: 700; }}
  /* Uma faixa por cidade separando os blocos: a lista chega a passar de cem
     linhas, e sem a faixa quem procura "as de Ilhabela" percorre tudo. */
  tr.faixa td {{
    background: {COR_DESTAQUE}18; color: {COR_DESTAQUE};
    font-size: 26px; font-weight: 700; letter-spacing: 1px;
    border-bottom: 2px solid {COR_LINHA};
  }}
"""


def _celula(valor):
    if not valor:
        return '<td class="zero">&mdash;</td>'
    return f'<td>{valor}</td>'


def gerar_html_capa(carga):
    turnos = carga['turnos']
    colunas = ''.join(f'<th>{_e(t)}</th>' for t in turnos)
    largura_col = ''.join(f'<col style="width:{LARGURA_TURNO}px">'
                          for _ in turnos)

    linhas = []
    for cidade in sorted(carga['capa']):
        celulas = ''.join(_celula(total_da_cidade(carga, cidade, t))
                          for t in turnos)
        linhas.append(
            f'<tr class="cidade"><td class="rotulo">{_e(cidade)}</td>{celulas}'
            f'<td class="geral">{total_da_cidade(carga, cidade)}</td></tr>')
        for tipo in sorted(carga['capa'][cidade]):
            contagens = carga['capa'][cidade][tipo]
            celulas = ''.join(_celula(contagens.get(t, 0)) for t in turnos)
            linhas.append(
                f'<tr class="tipo"><td class="rotulo">{_e(tipo)}</td>{celulas}'
                f'<td class="geral">{sum(contagens.values())}</td></tr>')

    celulas_total = ''.join(f'<td>{total_do_turno(carga, t)}</td>' for t in turnos)
    linhas.append(
        f'<tr class="total"><td class="rotulo">{TOTAL}</td>{celulas_total}'
        f'<td class="geral">{carga["total"]}</td></tr>')

    corpo = ''.join(linhas) if carga['total'] else (
        '<tr><td class="rotulo" colspan="99">Nenhuma atividade no dia.</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
{_estilo(len(turnos))}
</style>
</head>
<body>
  <div class="cabecalho">
    <div class="titulo-principal">Prévia da carga &middot; <span>{_e(carga['data'].strftime('%d/%m/%Y'))}</span></div>
    <div class="subtitulo">
      Litoral Norte, balde e rotas &middot; Ativação, Mudança de Endereço e Reparo &middot;
      tudo menos cancelado &middot; apurado em {_e(datetime.now().strftime('%d/%m %H:%M'))}
    </div>
  </div>
  <div class="cartoes">
    <div class="cartao">
      <div class="rotulo">Total do dia</div>
      <div class="valor">{carga['total']}</div>
    </div>
    {''.join(f'''<div class="cartao">
      <div class="rotulo">{_e(t)}</div>
      <div class="valor">{total_do_turno(carga, t)}</div>
    </div>''' for t in turnos)}
  </div>
  <div class="bloco">
    <table>
      <colgroup><col style="width:{LARGURA_ROTULO}px">{largura_col}<col style="width:{LARGURA_TOTAL}px"></colgroup>
      <tr>
        <th class="rotulo">Contagem de Cidade</th>
        {colunas}
        <th>{TOTAL}</th>
      </tr>
      {corpo}
    </table>
  </div>
</body>
</html>"""


def gerar_html_lista(carga):
    linhas = []
    cidade_atual = None
    for item in carga['linhas']:
        if item['cidade'] != cidade_atual:
            cidade_atual = item['cidade']
            quantos = total_da_cidade(carga, cidade_atual)
            linhas.append(
                f'<tr class="faixa"><td colspan="{len(COLUNAS_LISTA)}">'
                f'{_e(cidade_atual)} &middot; {quantos} O.S.</td></tr>')
        linhas.append(f"""
        <tr class="linha">
          <td class="nome">{_e(item['nome'])}</td>
          <td>{_e(item['endereco'])}</td>
          <td class="cidade">{_e(item['cidade'])}</td>
          <td>{_e(item['turno'])}</td>
          <td>{_e(item['tipo'])}</td>
        </tr>""")

    corpo = ''.join(linhas) or (
        f'<tr class="linha"><td colspan="{len(COLUNAS_LISTA)}">'
        'Nenhuma atividade no dia.</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
{_estilo_lista()}
</style>
</head>
<body>
  <div class="cabecalho">
    <div class="titulo-principal">Lista detalhada &middot; <span>{_e(carga['data'].strftime('%d/%m/%Y'))}</span></div>
    <div class="subtitulo">
      {carga['total']} atividades no Litoral Norte &middot;
      apurado em {_e(datetime.now().strftime('%d/%m %H:%M'))}
    </div>
  </div>
  <div class="bloco">
    <table>
      <colgroup>{''.join(f'<col style="width:{l}px">' for _, l, _ in COLUNAS_LISTA)}</colgroup>
      <tr>{''.join(f'<th>{_e(t)}</th>' for t, _, _ in COLUNAS_LISTA)}</tr>
      {corpo}
    </table>
  </div>
</body>
</html>"""


def gerar_imagens_carga(carga, pasta_saida=None):
    """Gera as duas imagens e devolve (caminho_capa, caminho_lista).

    A lista sai mesmo quando é longa: cortar a lista da prévia seria entregar
    meia rota para quem vai montar a rota inteira. A imagem cresce em altura, e
    o WhatsApp aguenta -- a de garantias já passa disso todo dia.
    """
    pasta_saida = pasta_saida or os.path.join(os.getcwd(), 'relatorios')
    os.makedirs(pasta_saida, exist_ok=True)
    dia = carga['data'].strftime('%Y-%m-%d')

    capa = os.path.join(pasta_saida, f'carga_capa_{dia}.png')
    renderizar_backlog_png(
        gerar_html_capa(carga), capa,
        largura=largura_da_capa(len(carga['turnos'])) + RESPIRO_PAGINA)

    lista = os.path.join(pasta_saida, f'carga_lista_{dia}.png')
    renderizar_backlog_png(gerar_html_lista(carga), lista,
                           largura=LARGURA_LISTA + RESPIRO_PAGINA)

    return capa, lista
