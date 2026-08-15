# ================= LISTA DE GARANTIAS: a imagem =================
#
# Uma imagem POR REGIÃO -- diferente do termômetro, que junta tudo numa só.
# Aqui é assim porque cada imagem vai para um grupo diferente: o do litoral e
# o do Rio. Mandar a lista inteira para os dois faria cada grupo ler metade do
# que não é dele.
#
# Reaproveita o motor (HTML/CSS -> PNG via Playwright) e a paleta do
# backlog_render, pelo mesmo motivo de sempre: o grupo já recebe backlog e
# termômetro com essa cara, e uma terceira identidade visual só faria a lista
# parecer de outro sistema.
#
# As colunas são as da tela de Garantias do site, e nessa ordem de propósito:
# quem confere a mensagem contra o site não deveria precisar procurar nada.

import html
import os
from datetime import datetime

from backlog_render import (
    renderizar_backlog_png,
    COR_FUNDO, COR_CARD, COR_CARD_ESCURO, COR_LINHA, COR_DESTAQUE,
    COR_TEXTO, COR_TEXTO_MUTED,
    COR_VERDE, COR_VERDE_BG, COR_VERMELHO, COR_VERMELHO_BG,
    FONTE, FONTE_NUM,
)


def _e(valor):
    """Escapa para HTML. Nome de cliente e bairro vêm do CAMPO, texto livre --
    um '&' ou um '<' perdido ali quebraria a tabela inteira em silêncio."""
    return html.escape(str(valor if valor is not None else ''))


def _classe_status(status):
    texto = str(status or '').upper()
    if texto == 'ONLINE':
        return 'verde'
    if texto == 'OFFLINE':
        return 'vermelho'
    return 'neutro'


def _linha(item):
    aging = item.get('aging')
    aging_txt = f"{aging}d" if aging is not None else '—'
    return f"""
      <tr class="linha">
        <td class="cel contrato">{_e(item.get('contrato'))}</td>
        <td class="cel contato">{_e(item.get('telefones'))}</td>
        <td class="cel"><span class="etiqueta {_classe_status(item.get('status'))}">{_e(item.get('status'))}</span></td>
        <td class="cel num">{aging_txt}</td>
        <td class="cel">{_e(item.get('servico'))}</td>
        <td class="cel tecnico">{_e(item.get('tecnico'))}</td>
        <td class="cel bairro">{_e(item.get('bairro'))}</td>
      </tr>"""


# Larguras fixas, iguais em toda cidade. Sem isso cada tabela calcula a sua
# própria pela maior célula: o nome comprido de um técnico de Ilhabela empurra
# a coluna e as tabelas param de se alinhar entre si -- numa lista que se lê de
# relance, coluna que dança de bloco em bloco faz o olho reprocurar tudo.
#
# As medidas saíram de render conferido, não de estimativa. SERVIÇO precisa dos
# 200px porque "IFI de MDE" é o rótulo mais longo e é justamente o que
# distingue uma mudança de endereço de uma ativação -- cortado em "IFI de …"
# ele vira ruído. CONTATO leva 250px para caber um telefone inteiro; o segundo,
# quando há dois, pode cortar sem prejuízo.
#
# CONTRATO é a única coluna que NÃO pode cortar nunca: é o dado que a pessoa
# usa para procurar o cliente no Autenticador ou no OFS, e meio número não serve para
# nada -- pior, "6890…" parece um número. Os 130px de 13/08/2026 davam 102px
# úteis contra os ~116px que 7 dígitos ocupam em Consolas 30px bold: TODO
# contrato saía truncado. Os 200px de agora cabem 10 dígitos, folga para o dia
# em que aparecer contrato mais longo.
_COLGROUP = """
        <colgroup>
          <col style="width:200px">
          <col style="width:250px">
          <col style="width:150px">
          <col style="width:100px">
          <col style="width:200px">
          <col style="width:400px">
          <col>
        </colgroup>"""


def _bloco_cidade(cidade):
    linhas = "".join(_linha(i) for i in cidade['itens'])
    return f"""
    <div class="bloco-cidade">
      <div class="titulo-cidade">{_e(cidade['nome'])} <span class="contagem">{len(cidade['itens'])}</span></div>
      <table>{_COLGROUP}
        <thead>
          <tr class="linha-cabecalho">
            <th>CONTRATO</th>
            <th>CONTATO</th>
            <th>STATUS</th>
            <th>AGING</th>
            <th>SERVIÇO</th>
            <th>TÉCNICO (OFS)</th>
            <th>BAIRRO</th>
          </tr>
        </thead>
        <tbody>{linhas}
        </tbody>
      </table>
    </div>"""


def _estilo():
    return f"""
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: {COR_FUNDO};
    font-family: {FONTE};
    padding: 32px;
    width: fit-content;
  }}
  .cabecalho {{
    margin-bottom: 24px;
    border-bottom: 2px solid {COR_LINHA};
    padding-bottom: 18px;
  }}
  .titulo-principal {{
    color: {COR_TEXTO};
    font-size: 56px;
    font-weight: 700;
  }}
  .titulo-principal span {{ color: {COR_DESTAQUE}; }}
  .subtitulo {{
    color: {COR_TEXTO_MUTED};
    font-size: 25px;
    font-family: {FONTE_NUM};
    margin-top: 8px;
  }}
  .cartoes {{ display: flex; gap: 18px; margin-bottom: 26px; }}
  .cartao {{
    background: {COR_CARD};
    border-autenticador: 12px;
    padding: 18px 26px;
    min-width: 210px;
    border-left: 5px solid {COR_DESTAQUE};
  }}
  .cartao.vermelho {{ border-left-color: {COR_VERMELHO}; }}
  .cartao .rotulo {{ color: {COR_TEXTO_MUTED}; font-size: 21px; }}
  .cartao .valor {{
    color: {COR_TEXTO}; font-size: 46px; font-weight: 700;
    font-family: {FONTE_NUM}; margin-top: 4px;
  }}
  .cartao.vermelho .valor {{ color: {COR_VERMELHO}; }}
  .bloco-cidade {{
    background: {COR_CARD};
    border-autenticador: 14px;
    padding: 24px 26px 26px;
    margin-bottom: 20px;
    box-shadow: 0 4px 18px rgba(0,0,0,0.35);
    /* Largura fixa: o body é fit-content, e sem uma medida aqui cada bloco
       encolheria até o seu próprio conteúdo -- blocos de larguras diferentes
       empilhados um sobre o outro.
       1600 -> 1680 quando CONTRATO ganhou 70px: crescer o bloco é o que
       impede a conta de ser paga por BAIRRO, que é a coluna sem largura fixa
       e portanto quem absorve qualquer aumento das outras. */
    width: 1680px;
  }}
  .titulo-cidade {{
    color: {COR_DESTAQUE};
    font-size: 30px;
    font-weight: 700;
    letter-spacing: 1px;
    margin-bottom: 16px;
    display: inline-block;
    background: {COR_DESTAQUE}22;
    border: 1px solid {COR_DESTAQUE}55;
    padding: 7px 18px;
    border-autenticador: 6px;
  }}
  .titulo-cidade .contagem {{ color: {COR_TEXTO_MUTED}; font-family: {FONTE_NUM}; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    font-family: {FONTE_NUM};
    table-layout: fixed;   /* manda o colgroup acima valer de verdade */
  }}
  .linha-cabecalho th {{
    color: {COR_TEXTO};
    font-size: 23px;
    font-weight: 600;
    text-align: left;
    padding: 12px 14px;
    border-bottom: 2px solid {COR_LINHA};
    white-space: nowrap;
  }}
  td.cel {{
    text-align: left;
    padding: 13px 14px;
    font-size: 27px;
    color: {COR_TEXTO};
    border-bottom: 1px solid {COR_CARD_ESCURO};
    white-space: nowrap;
  }}
  td.contrato {{ font-weight: 700; font-size: 30px; }}
  td.num {{ text-align: center; }}
  /* Com table-layout fixo toda célula tem de saber o que fazer ao transbordar;
     nome de técnico e bairro são os campos de tamanho imprevisível, e quebrar
     a linha estouraria a altura da tabela. Cortam com reticências. */
  td.cel {{ overflow: hidden; text-overflow: ellipsis; }}
  tr.linha:nth-child(odd) td {{ background: {COR_CARD_ESCURO}55; }}
  .etiqueta {{
    display: inline-block;
    padding: 5px 14px;
    border-autenticador: 6px;
    font-size: 23px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }}
  .etiqueta.verde {{ background: {COR_VERDE_BG}; color: {COR_VERDE}; }}
  .etiqueta.vermelho {{ background: {COR_VERMELHO_BG}; color: {COR_VERMELHO}; }}
  .etiqueta.neutro {{ background: {COR_LINHA}; color: {COR_TEXTO_MUTED}; }}
  .vazio {{
    background: {COR_CARD};
    border-autenticador: 14px;
    padding: 40px;
    color: {COR_VERDE};
    font-size: 34px;
    font-weight: 600;
    width: 1680px;
  }}
"""


def gerar_html_garantias(dados, chave_regiao):
    regiao = next((r for r in dados.get('regioes', []) if r['chave'] == chave_regiao), None)
    if regiao is None:
        raise ValueError(f"Região desconhecida na lista de garantias: {chave_regiao!r}")

    if regiao['cidades']:
        corpo = "".join(_bloco_cidade(c) for c in regiao['cidades'])
    else:
        # O envio NÃO passa por aqui quando a região está vazia: manda um texto
        # com o horário da conferência, porque tabela sem linha é imagem de
        # nada (ver garantias_envio.gerar_e_enviar). Este ramo existe só para a
        # função ser total -- gerar o HTML de uma região vazia à mão, num
        # ensaio ou numa conferência, não deve estourar.
        corpo = '<div class="vazio">✓ Nenhuma garantia em aberto nesta região agora.</div>'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
{_estilo()}
</style>
</head>
<body>
  <div class="cabecalho">
    <div class="titulo-principal">Garantias &middot; <span>{_e(regiao['nome'])}</span></div>
    <div class="subtitulo">Reparos em garantia com chamado ABERTO no CAMPO &middot; apurado em {_e(dados.get('gerado_em'))}</div>
  </div>
  <div class="cartoes">
    <div class="cartao">
      <div class="rotulo">Garantias em aberto</div>
      <div class="valor">{regiao['total']}</div>
    </div>
    <div class="cartao vermelho">
      <div class="rotulo">Offline</div>
      <div class="valor">{regiao['offline']}</div>
    </div>
  </div>
  {corpo}
</body>
</html>"""


def gerar_imagem_garantias(dados, chave_regiao, pasta_saida=None, largura=1780):
    """Gera o PNG da região e devolve o caminho do arquivo."""
    pasta_saida = pasta_saida or os.path.join(os.getcwd(), "relatorios")
    os.makedirs(pasta_saida, exist_ok=True)
    html_pagina = gerar_html_garantias(dados, chave_regiao)
    caminho = os.path.join(pasta_saida, f"garantias_{chave_regiao}.png")
    renderizar_backlog_png(html_pagina, caminho, largura=largura)
    return caminho
