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


# Sábado e domingo não são dias úteis, mas entram no histórico com o mesmo
# peso desde sempre -- e como entram bem menos chamados, puxavam a média para
# baixo (medido em 27/08/2026: 5,5 com fim de semana, 6,2 só com dia útil, uma
# diferença de ~11%). Agora cada dia vale uma fração de DIA ÚTIL, e a média é
# ponderada: soma dos entrantes dividida pela soma dos pesos. O resultado é
# "quantos entrantes se espera num dia útil cheio".
#
# A mesma tabela serve para o outro lado da conta: no sábado a referência do
# dia é a média D.U. multiplicada por 0,7, senão o termômetro pintaria todo
# fim de semana de ▼ sem que nada estivesse errado.
# Largura de TODO o conteúdo da imagem. Antes cada peça tinha a sua (card de
# região 820px, faixas do topo 884px), e o resultado era um degrau: as réguas
# e a legenda passavam da borda direita dos cards e a imagem parecia torta.
# Um número só, usado por todos os blocos, resolve. Precisa caber dentro da
# `largura` passada ao renderizar_backlog_png() somada ao padding do body
# (980 + 2x32 = 1044, contra 1200 de viewport).
LARGURA_CONTEUDO = 980
PADDING_IMAGEM = 32

PESO_DIA_UTIL = {5: 0.7, 6: 0.3}  # segunda=0 ... sábado=5, domingo=6
NOME_DIA_PESO = {5: "sábado", 6: "domingo"}


def peso_do_dia(data):
    """Quanto o dia `data` vale em dias úteis (1,0 de segunda a sexta)."""
    return PESO_DIA_UTIL.get(data.weekday(), 1.0)


def _numero(valor, casas=1):
    """Número no formato daqui: vírgula decimal."""
    return f"{valor:.{casas}f}".replace(".", ",")


def _classificar(contagem, referencia):
    """Classifica comparando com a REFERÊNCIA do dia (a média histórica já
    ajustada pelo peso de hoje -- ver PESO_DIA_UTIL):
      - 0 entrantes -> "SEM ENTRANTE" (onde não teve)
      - acima da referência -> ▲ (maior índice de entrante)
      - abaixo da referência -> ▼ (menor índice de entrante)
      - na referência -> neutro
    """
    if contagem == 0:
        return "vermelho", "— SEM ENTRANTE"
    if contagem > referencia:
        return "verde", "▲"
    if contagem < referencia:
        return "amarelo", "▼"
    return "azul", "•"


def _linha_termometro(unidade, contagem, referencia):
    classe, indicador = _classificar(contagem, referencia)
    return f"""
    <tr class="linha">
      <td class="col-unidade">{unidade}</td>
      <td class="cel {classe}">{contagem}</td>
      <td class="cel {classe} indicador">{indicador}</td>
    </tr>"""


def _linha_total(total_regiao, referencia_cluster):
    """A linha TOTAL do cluster, agora com seta própria: compara o total da
    região com a média histórica DELA (não com a média por unidade), que é o
    número que diz se a região como um todo está acima ou abaixo do normal.

    Sem histórico do cluster ainda, mostra "—" como antes."""
    if not referencia_cluster:
        return f"""
    <tr class="linha linha-total">
      <td class="col-unidade">TOTAL</td>
      <td class="cel total-col">{total_regiao}</td>
      <td class="cel total-col">—</td>
    </tr>"""

    classe, indicador = _classificar(total_regiao, referencia_cluster)
    # O indicador vai dentro de um <span> porque a linha TOTAL pinta o fundo e
    # a cor da célula com !important -- a cor do span é de outro elemento e
    # sobrevive a isso.
    return f"""
    <tr class="linha linha-total">
      <td class="col-unidade">TOTAL</td>
      <td class="cel total-col">{total_regiao}</td>
      <td class="cel total-col indicador"><span class="ind-{classe}">{indicador}</span></td>
    </tr>"""


def _tabela_regiao_termometro(nome_regiao, unidades, contagem_por_unidade,
                             referencia_unidade, media_cluster=None, peso_hoje=1.0):
    unidades_ordenadas = sorted(unidades, key=lambda u: contagem_por_unidade.get(u, 0), reverse=True)
    total_regiao = sum(contagem_por_unidade.get(u, 0) for u in unidades)

    linhas_html = "".join(
        _linha_termometro(u, contagem_por_unidade.get(u, 0), referencia_unidade)
        for u in unidades_ordenadas
    )
    referencia_cluster = media_cluster * peso_hoje if media_cluster else None
    linha_total = _linha_total(total_regiao, referencia_cluster)

    if media_cluster:
        selo = f"""
        <div class="media-regiao">
          média histórica <b>{_numero(media_cluster)}</b> por dia útil
          <span class="media-regiao-nota">soma de todas as unidades do cluster</span>
        </div>"""
    else:
        selo = ""

    return f"""
    <div class="bloco-regiao">
      <div class="cabecalho-regiao">
        <div class="titulo-regiao">{nome_regiao}</div>{selo}
      </div>
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
    padding: {PADDING_IMAGEM}px;
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
  .reguas {{
    display: flex;
    gap: 18px;
    margin-bottom: 24px;
    width: {LARGURA_CONTEUDO}px;
  }}
  .regua {{
    flex: 1 1 0;
    background: {COR_CARD};
    border: 1px solid {COR_LINHA};
    border-autenticador: 12px;
    padding: 18px 22px;
    font-family: {FONTE_NUM};
  }}
  .regua-cluster {{ border-color: {COR_DESTAQUE}55; background: {COR_DESTAQUE}14; }}
  .regua-rotulo {{
    color: {COR_TEXTO_MUTED};
    font-size: 21px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .regua-cluster .regua-rotulo {{ color: {COR_DESTAQUE}; }}
  .regua-valor {{
    color: {COR_TEXTO};
    font-size: 46px;
    font-weight: 700;
    line-height: 1.15;
  }}
  .regua-nota {{ color: {COR_TEXTO_MUTED}; font-size: 18px; opacity: 0.8; white-space: nowrap; }}
  .aviso-peso {{
    background: {COR_DESTAQUE}1a;
    border-left: 6px solid {COR_DESTAQUE};
    border-autenticador: 8px;
    padding: 16px 22px;
    margin-bottom: 22px;
    font-family: {FONTE_NUM};
    font-size: 24px;
    line-height: 1.4;
    color: {COR_TEXTO_MUTED};
    width: {LARGURA_CONTEUDO}px;
  }}
  .aviso-peso b {{ color: {COR_TEXTO}; font-weight: 700; }}
  .legenda {{
    display: flex;
    justify-content: space-between;
    flex-wrap: nowrap;
    gap: 24px;
    margin-bottom: 24px;
    width: {LARGURA_CONTEUDO}px;
    font-family: {FONTE_NUM};
    font-size: 22px;
    color: {COR_TEXTO_MUTED};
  }}
  .legenda > div {{ white-space: nowrap; }}
  .legenda span.verde {{ color: {COR_VERDE}; font-weight: 700; }}
  .legenda span.amarelo {{ color: {COR_AMARELO}; font-weight: 700; }}
  .legenda span.vermelho {{ color: {COR_VERMELHO}; font-weight: 700; }}
  .bloco-regiao {{
    background: {COR_CARD};
    border-autenticador: 14px;
    padding: 28px 30px 30px;
    margin-bottom: 24px;
    box-shadow: 0 4px 18px rgba(0,0,0,0.35);
    width: {LARGURA_CONTEUDO}px;
  }}
  .cabecalho-regiao {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 20px;
  }}
  .media-regiao {{
    font-family: {FONTE_NUM};
    white-space: nowrap;
    font-size: 24px;
    color: {COR_TEXTO_MUTED};
    text-align: right;
    line-height: 1.35;
  }}
  .media-regiao b {{ color: {COR_TEXTO}; font-weight: 700; }}
  .media-regiao-nota {{ display: block; font-size: 19px; opacity: 0.72; }}
  .ind-verde {{ color: {COR_VERDE}; }}
  .ind-amarelo {{ color: {COR_AMARELO}; }}
  .ind-vermelho {{ color: {COR_VERMELHO}; }}
  .ind-azul {{ color: {COR_DESTAQUE}; }}
  .titulo-regiao {{
    color: {COR_DESTAQUE};
    font-size: 32px;
    font-weight: 700;
    letter-spacing: 1.2px;
    display: inline-block;
    white-space: nowrap;
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


def gerar_html_termometro(contagem_por_unidade, media_geral, regioes=None, gerado_em=None,
                          medias_por_cluster=None):
    """Gera o HTML completo do termômetro (todas as regiões numa imagem só,
    diferente do backlog que gera uma imagem separada por região).

    media_geral: média histórica POR UNIDADE, em entrantes por dia útil --
    calculada por quem chama esta função (o bot mantém o histórico
    persistido em disco). Cada unidade é comparada contra esse valor, não
    contra a média das próprias unidades de hoje.

    medias_por_cluster: {nome da região: média histórica da região inteira,
    por dia útil}. É a soma de todas as unidades do cluster, e é contra ela
    que a linha TOTAL de cada bloco ganha a seta. Pode vir vazio (histórico
    ainda curto) -- aí a linha TOTAL volta a mostrar "—".

    As duas médias estão em DIA ÚTIL equivalente. Se hoje for sábado ou
    domingo, as referências do dia são multiplicadas pelo peso do dia antes
    da comparação -- ver PESO_DIA_UTIL.
    """
    regioes = regioes or REGIOES_TERMOMETRO
    medias_por_cluster = medias_por_cluster or {}
    gerado_em = gerado_em or datetime.now()
    timestamp_str = gerado_em.strftime("%d/%m/%Y às %H:%M")

    peso_hoje = peso_do_dia(gerado_em.date())
    referencia_unidade = media_geral * peso_hoje

    blocos = "".join(
        _tabela_regiao_termometro(
            nome_regiao, unidades, contagem_por_unidade, referencia_unidade,
            media_cluster=medias_por_cluster.get(nome_regiao), peso_hoje=peso_hoje,
        )
        for nome_regiao, unidades in regioes.items()
    )

    # A régua de hoje só é dita em voz alta quando ela muda: de segunda a
    # sexta o peso é 1,0 e repetir isso em toda imagem seria ruído.
    if peso_hoje != 1.0:
        nome_dia = NOME_DIA_PESO.get(gerado_em.date().weekday(), "hoje")
        aviso_peso = f"""
    <div class="aviso-peso">
      Hoje é <b>{nome_dia}</b>: vale <b>{_numero(peso_hoje, 1)} dia útil</b>, e as
      referências desta imagem já estão ajustadas por isso
      (unidade: {_numero(referencia_unidade)} &middot; cluster: a média &times; {_numero(peso_hoje, 1)}).
    </div>"""
    else:
        aviso_peso = ""

    # As médias ficam numa faixa de cartões, e não numa frase: a imagem tem
    # largura fixa (LARGURA_CONTEUDO) e a linha corrida quebrava no meio da
    # palavra.
    reguas = [f"""
      <div class="regua">
        <div class="regua-rotulo">por unidade</div>
        <div class="regua-valor">{_numero(media_geral)}</div>
        <div class="regua-nota">média histórica &middot; dia útil</div>
      </div>"""]
    reguas += [f"""
      <div class="regua regua-cluster">
        <div class="regua-rotulo">{nome}</div>
        <div class="regua-valor">{_numero(medias_por_cluster[nome])}</div>
        <div class="regua-nota">cluster inteiro &middot; dia útil</div>
      </div>"""
        for nome in regioes
        if medias_por_cluster.get(nome)
    ]
    faixa_reguas = f"""
    <div class="reguas">{"".join(reguas)}
    </div>"""

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
    <div class="subtitulo">Hoje, atualizado em {timestamp_str}</div>
  </div>
  {faixa_reguas}{aviso_peso}
  <div class="legenda">
    <div><span class="verde">▲</span> acima da média</div>
    <div><span class="amarelo">▼</span> abaixo da média</div>
    <div><span class="vermelho">— SEM ENTRANTE</span> nenhum entrante hoje</div>
  </div>
  {blocos}
</body>
</html>"""


def gerar_imagem_termometro(contagem_por_unidade, media_geral, pasta_saida=None, gerado_em=None,
                            regioes=None, medias_por_cluster=None):
    """Gera o PNG do termômetro e devolve o caminho do arquivo."""
    pasta_saida = pasta_saida or os.path.join(os.getcwd(), "relatorios")
    os.makedirs(pasta_saida, exist_ok=True)
    html = gerar_html_termometro(contagem_por_unidade, media_geral, regioes=regioes,
                                 gerado_em=gerado_em, medias_por_cluster=medias_por_cluster)
    caminho = os.path.join(pasta_saida, "termometro_capex.png")
    # A viewport é exatamente o conteúdo mais as margens: sobrando largura,
    # o PNG sai com uma faixa vazia à direita dos cards.
    renderizar_backlog_png(html, caminho, largura=LARGURA_CONTEUDO + 2 * PADDING_IMAGEM)
    return caminho
