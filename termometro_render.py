# ================= TERMÔMETRO DE ENTRANTES CAPEX: geração da imagem =================
# Mostra, por unidade, quantos chamados de CAPEX (Ativação + Mudança de
# Endereço) entraram HOJE -- com base no histórico de OS já notificadas
# pelo próprio bot (mesmo evento que dispara o alerta individual no grupo).
#
# Reaproveita a mesma paleta de cores e o mesmo motor de renderização
# (HTML/CSS -> PNG via Playwright) já usados no backlog_render.py -- só
# muda o layout/conteúdo da tabela, que aqui é mais simples (não tem
# idade de chamado nem agendamento, só a contagem de entrantes do dia).
import os
from datetime import datetime

from backlog_capex import REGIOES as REGIOES_BACKLOG
from backlog_render import (
    renderizar_backlog_png,
    COR_FUNDO, COR_CARD, COR_CARD_ESCURO, COR_LINHA, COR_DESTAQUE,
    COR_TEXTO, COR_TEXTO_MUTED,
    COR_VERDE, COR_VERDE_BG, COR_AMARELO, COR_AMARELO_BG, COR_VERMELHO, COR_VERMELHO_BG,
    FONTE, FONTE_NUM,
)

# ============ Região usada só pelo termômetro (não mexe em
# backlog_capex.REGIOES, que outras imagens -- backlog CAPEX/Reparo/
# Upgrade/Mudança -- continuam usando do jeito que está). Aqui
# adicionamos BERT e BERTN em "LITORAL NORTE": essas duas unidades
# fazem parte do LITORAL_SP monitorado pela notificação individual de
# CAPEX (bot_campo_monitoramento.py), mas não estavam na lista mais
# restrita do backlog_capex.py -- sem isso, entrantes dessas unidades
# eram contados só que nunca apareciam na imagem do termômetro. ============
REGIOES_TERMOMETRO = {nome: list(unidades) for nome, unidades in REGIOES_BACKLOG.items()}
REGIOES_TERMOMETRO["LITORAL NORTE"] = REGIOES_TERMOMETRO["LITORAL NORTE"] + ["BERT", "BERTN"]


def _classificar(contagem, media):
    """Classifica uma unidade comparando com a média das unidades que
    tiveram pelo menos 1 entrante hoje:
      - 0 entrantes -> "SEM ENTRANTE" (onde não teve)
      - acima da média -> ▲ (maior índice de entrante)
      - abaixo da média -> ▼ (menor índice de entrante)
      - na média -> neutro
    """
    if contagem == 0:
        return "vermelho", "— SEM ENTRANTE"
    if contagem > media:
        return "verde", "▲"
    if contagem < media:
        return "amarelo", "▼"
    return "azul", "•"


def _linha_termometro(unidade, contagem, media, destaque=False):
    if destaque:
        return f"""
    <tr class="linha linha-total">
      <td class="col-unidade">TOTAL</td>
      <td class="cel total-col">{contagem}</td>
      <td class="cel total-col">—</td>
    </tr>"""

    classe, indicador = _classificar(contagem, media)
    return f"""
    <tr class="linha">
      <td class="col-unidade">{unidade}</td>
      <td class="cel {classe}">{contagem}</td>
      <td class="cel {classe} indicador">{indicador}</td>
    </tr>"""


def _tabela_regiao_termometro(nome_regiao, unidades, contagem_por_unidade, media_geral):
    unidades_ordenadas = sorted(unidades, key=lambda u: contagem_por_unidade.get(u, 0), reverse=True)
    total_regiao = sum(contagem_por_unidade.get(u, 0) for u in unidades)

    linhas_html = "".join(
        _linha_termometro(u, contagem_por_unidade.get(u, 0), media_geral)
        for u in unidades_ordenadas
    )
    linha_total = _linha_termometro(None, total_regiao, media_geral, destaque=True)

    return f"""
    <div class="bloco-regiao">
      <div class="titulo-regiao">{nome_regiao}</div>
      <table>
        <thead>
          <tr class="linha-cabecalho">
            <th class="col-unidade">UNIDADE</th>
            <th>Entrantes hoje</th>
            <th>Indicador</th>
          </tr>
        </thead>
        <tbody>
          {linhas_html}
          {linha_total}
        </tbody>
      </table>
    </div>"""


def _estilo_base_termometro():
    return f"""
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: {COR_FUNDO};
    font-family: {FONTE};
    padding: 32px;
    width: fit-content;
  }}
  .cabecalho {{
    margin-bottom: 26px;
    border-bottom: 2px solid {COR_LINHA};
    padding-bottom: 20px;
  }}
  .titulo-principal {{
    color: {COR_TEXTO};
    font-size: 64px;
    font-weight: 700;
    letter-spacing: 0.3px;
  }}
  .titulo-principal span {{ color: {COR_DESTAQUE}; }}
  .subtitulo {{
    color: {COR_TEXTO_MUTED};
    font-size: 28px;
    font-family: {FONTE_NUM};
    margin-top: 8px;
  }}
  .legenda {{
    display: flex;
    gap: 34px;
    margin-bottom: 24px;
    font-family: {FONTE_NUM};
    font-size: 24px;
    color: {COR_TEXTO_MUTED};
  }}
  .legenda span.verde {{ color: {COR_VERDE}; font-weight: 700; }}
  .legenda span.amarelo {{ color: {COR_AMARELO}; font-weight: 700; }}
  .legenda span.vermelho {{ color: {COR_VERMELHO}; font-weight: 700; }}
  .bloco-regiao {{
    background: {COR_CARD};
    border-autenticador: 14px;
    padding: 28px 30px 30px;
    margin-bottom: 24px;
    box-shadow: 0 4px 18px rgba(0,0,0,0.35);
    width: 820px;
  }}
  .titulo-regiao {{
    color: {COR_DESTAQUE};
    font-size: 32px;
    font-weight: 700;
    letter-spacing: 1.2px;
    margin-bottom: 20px;
    display: inline-block;
    background: {COR_DESTAQUE}22;
    border: 1px solid {COR_DESTAQUE}55;
    padding: 8px 20px;
    border-autenticador: 6px;
  }}
  table {{ border-collapse: collapse; width: 100%; font-family: {FONTE_NUM}; }}
  .linha-cabecalho th {{
    color: {COR_TEXTO};
    font-size: 26px;
    font-weight: 600;
    text-align: center;
    padding: 14px 16px;
    border-bottom: 2px solid {COR_LINHA};
    white-space: nowrap;
  }}
  .col-unidade {{
    text-align: left !important;
    color: {COR_TEXTO};
    font-weight: 700;
    padding-left: 8px !important;
  }}
  td.cel {{
    text-align: center;
    padding: 16px 16px;
    font-size: 34px;
    font-weight: 600;
    color: {COR_TEXTO};
    border-bottom: 1px solid {COR_CARD_ESCURO};
    white-space: nowrap;
  }}
  td.cel.indicador {{ font-size: 38px; }}
  tr.linha:nth-child(odd) td {{ background: {COR_CARD_ESCURO}55; }}
  td.verde {{ background: {COR_VERDE_BG}; color: {COR_VERDE}; }}
  td.amarelo {{ background: {COR_AMARELO_BG}; color: {COR_AMARELO}; }}
  td.vermelho {{ background: {COR_VERMELHO_BG}; color: {COR_VERMELHO}; }}
  td.azul {{ background: {COR_DESTAQUE}22; color: {COR_DESTAQUE}; }}
  td.total-col {{ background: {COR_DESTAQUE}22; color: {COR_DESTAQUE}; font-weight: 700; }}
  tr.linha-total td {{
    background: {COR_LINHA} !important;
    color: {COR_TEXTO} !important;
    font-weight: 700;
    border-top: 2px solid {COR_DESTAQUE};
  }}
"""


def gerar_html_termometro(contagem_por_unidade, media_geral, regioes=None, gerado_em=None):
    """Gera o HTML completo do termômetro (todas as regiões numa imagem só,
    diferente do backlog que gera uma imagem separada por região).

    media_geral: média histórica geral de entrantes (todas as unidades,
    dias anteriores) -- calculada por quem chama esta função (o bot mantém
    o histórico persistido em disco). Cada unidade é comparada contra esse
    valor, não contra a média das próprias unidades de hoje.
    """
    regioes = regioes or REGIOES_TERMOMETRO
    gerado_em = gerado_em or datetime.now()
    timestamp_str = gerado_em.strftime("%d/%m/%Y às %H:%M")

    blocos = "".join(
        _tabela_regiao_termometro(nome_regiao, unidades, contagem_por_unidade, media_geral)
        for nome_regiao, unidades in regioes.items()
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
{_estilo_base_termometro()}
</style>
</head>
<body>
  <div class="cabecalho">
    <div class="titulo-principal">Termômetro &middot; <span>Entrantes CAPEX</span></div>
    <div class="subtitulo">Hoje, atualizado em {timestamp_str} &middot; média histórica geral: {media_geral:.1f}</div>
  </div>
  <div class="legenda">
    <div><span class="verde">▲</span> acima da média histórica geral</div>
    <div><span class="amarelo">▼</span> abaixo da média histórica geral</div>
    <div><span class="vermelho">— SEM ENTRANTE</span> nenhum entrante hoje</div>
  </div>
  {blocos}
</body>
</html>"""


def gerar_imagem_termometro(contagem_por_unidade, media_geral, pasta_saida=None, gerado_em=None, regioes=None):
    """Gera o PNG do termômetro e devolve o caminho do arquivo."""
    pasta_saida = pasta_saida or os.path.join(os.getcwd(), "relatorios")
    os.makedirs(pasta_saida, exist_ok=True)
    html = gerar_html_termometro(contagem_por_unidade, media_geral, regioes=regioes, gerado_em=gerado_em)
    caminho = os.path.join(pasta_saida, "termometro_capex.png")
    renderizar_backlog_png(html, caminho, largura=1200)
    return caminho
