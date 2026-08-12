# ================= BACKLOG: geração da imagem (HTML/CSS -> PNG via Playwright) =================
# Suporta tanto CAPEX (Ativação/MDE) quanto REPARO/UPGRADE/MUDANÇA DE CÔMODO
import os
import sys
import time
from datetime import datetime

from backlog_capex import REGIOES

# ---- Paleta: reaproveita as cores já usadas no Painel de TV do bot, pra manter
# a mesma identidade visual em vez de inventar uma paleta nova ----
COR_FUNDO = "#081B3A"
COR_CARD = "#102B57"
COR_CARD_ESCURO = "#0A1931"
COR_LINHA = "#16376B"
COR_DESTAQUE = "#00BFFF"
COR_TEXTO = "#FFFFFF"
COR_TEXTO_MUTED = "#9FB3D1"

COR_VERDE = "#1FCB6B"      # bucket saudável (até 48h / até 2 dias, no prazo)
COR_VERDE_BG = "#123A2C"
COR_AMARELO = "#F5B942"    # bucket atenção (até 7 dias)
COR_AMARELO_BG = "#3A3212"
COR_VERMELHO = "#FF4D6A"   # bucket crítico (acima de 7 dias, vencida)
COR_VERMELHO_BG = "#3A1220"

FONTE = "'Segoe UI', 'Inter', system-ui, -apple-system, sans-serif"
FONTE_NUM = "'Consolas', 'SF Mono', 'Cascadia Code', monospace"

# Mapeamento de slugs para nomes de arquivo (para qualquer categoria)
_SLUG_CATEGORIA = {
    "Ativação": "ativacao",
    "Mudança de endereço": "mudanca_endereco",
    "Reparo": "reparo",
    "Upgrade": "upgrade",
    "Mudança de cômodo": "mudanca_comodo",
}


def _fmt_pct(valor):
    return f"{valor:.1f}".rstrip("0").rstrip(".") + "%" if valor else "0%"


def _linha_tabela(unidade, linha_idade, linha_agenda, categoria, destaque=False):
    classe_total = " linha-total" if destaque else ""
    nome_exibido = "TOTAL" if destaque else unidade

    # Para Reparo/Upgrade/Mudança de Cômodo, mostramos conectados/loss
    mostra_autenticador = "conectados" in linha_idade and "loss" in linha_idade
    if mostra_autenticador:
        return f"""
    <tr class="linha{classe_total}">
      <td class="col-unidade">{nome_exibido}</td>
      <td class="cel verde">{linha_idade['bucket1']}</td>
      <td class="cel verde pct">{_fmt_pct(linha_idade['pct_bucket1'])}</td>
      <td class="cel amarelo">{linha_idade['bucket2']}</td>
      <td class="cel amarelo pct">{_fmt_pct(linha_idade['pct_bucket2'])}</td>
      <td class="cel vermelho">{linha_idade['bucket3']}</td>
      <td class="cel vermelho pct">{_fmt_pct(linha_idade['pct_bucket3'])}</td>
      <td class="cel total-col">{linha_idade['total']}</td>
      <td class="cel">{linha_idade['enviado_d0']}</td>
      <td class="cel">{linha_idade['conveniencia']}</td>
      <td class="cel destaque-col">{linha_idade['oportunidade_injecao']}</td>
      <td class="cel verde">{linha_idade['conectados']}</td>
      <td class="cel vermelho">{linha_idade['loss']}</td>
      <td class="cel verde pct">{_fmt_pct(linha_idade['pct_conectado'])}</td>
      <td class="cel vermelho pct">{_fmt_pct(linha_idade['pct_loss'])}</td>
      <td class="cel sep-esq">{linha_agenda['d1']}</td>
      <td class="cel">{linha_agenda['d2']}</td>
      <td class="cel">{linha_agenda['d3']}</td>
      <td class="cel">{linha_agenda['mais_d3']}</td>
      <td class="cel vermelho sep-esq">{linha_agenda['vencida']}</td>
      <td class="cel verde">{linha_agenda['no_prazo']}</td>
    </tr>"""
    else:
        # CAPEX (sem Autenticador)
        return f"""
    <tr class="linha{classe_total}">
      <td class="col-unidade">{nome_exibido}</td>
      <td class="cel verde">{linha_idade['bucket1']}</td>
      <td class="cel verde pct">{_fmt_pct(linha_idade['pct_bucket1'])}</td>
      <td class="cel amarelo">{linha_idade['bucket2']}</td>
      <td class="cel amarelo pct">{_fmt_pct(linha_idade['pct_bucket2'])}</td>
      <td class="cel vermelho">{linha_idade['bucket3']}</td>
      <td class="cel vermelho pct">{_fmt_pct(linha_idade['pct_bucket3'])}</td>
      <td class="cel total-col">{linha_idade['total']}</td>
      <td class="cel">{linha_idade['enviado_d0']}</td>
      <td class="cel">{linha_idade['conveniencia']}</td>
      <td class="cel destaque-col">{linha_idade['oportunidade_injecao']}</td>
      <td class="cel sep-esq">{linha_agenda['d1']}</td>
      <td class="cel">{linha_agenda['d2']}</td>
      <td class="cel">{linha_agenda['d3']}</td>
      <td class="cel">{linha_agenda['mais_d3']}</td>
      <td class="cel vermelho sep-esq">{linha_agenda['vencida']}</td>
      <td class="cel verde">{linha_agenda['no_prazo']}</td>
    </tr>"""


def _tabela_regiao(nome_regiao, unidades_ordenadas, idade_regiao, agenda_regiao, categoria):
    linhas_html = "".join(
        _linha_tabela(u, idade_regiao[u], agenda_regiao[u], categoria)
        for u in unidades_ordenadas
        if idade_regiao[u]["total"] > 0
    )
    linha_total = _linha_tabela(
        "TOTAL", idade_regiao["TOTAL"], agenda_regiao["TOTAL"], categoria, destaque=True
    )

    # Define o label do bucket1 conforme a categoria
    if categoria == "Ativação":
        label_bucket1 = "Até 48hrs"
    elif categoria == "Mudança de endereço":
        label_bucket1 = "Até 2 dias"
    elif categoria == "Reparo":
        label_bucket1 = "Até 24hrs"
    elif categoria in ("Upgrade", "Mudança de cômodo"):
        label_bucket1 = "Até 4 dias"
    else:
        label_bucket1 = "Bucket 1"

    # Cabeçalho da tabela varia se tem conectados/loss
    mostra_autenticador = "conectados" in idade_regiao["TOTAL"] and "loss" in idade_regiao["TOTAL"]
    if mostra_autenticador:
        return f"""
    <div class="bloco-regiao">
      <div class="titulo-regiao">{nome_regiao}</div>
      <table>
        <thead>
          <tr class="linha-grupo">
            <td></td>
            <td colspan="7" class="grupo idade">IDADE DO CHAMADO</td>
            <td colspan="3" class="grupo classificacao">CLASSIFICAÇÃO</td>
            <td colspan="4" class="grupo autenticador">STATUS AUTENTICADOR</td>
            <td colspan="4" class="grupo agenda">PRÓXIMO AGENDAMENTO</td>
            <td colspan="2" class="grupo situacao">SITUAÇÃO</td>
          </tr>
          <tr class="linha-cabecalho">
            <th class="col-unidade">UNIDADE</th>
            <th>{label_bucket1}</th><th>%</th>
            <th>Até 7 dias</th><th>%</th>
            <th>Acima 7 dias</th><th>%</th>
            <th class="total-col">Total</th>
            <th>Enviado D0</th>
            <th>Conveniência</th>
            <th class="destaque-col">Oport. Injeção</th>
            <th class="verde">Conectado</th><th class="vermelho">Loss</th>
            <th class="verde pct">% Conect.</th><th class="vermelho pct">% Loss</th>
            <th class="sep-esq">D+1</th><th>D+2</th><th>D+3</th><th>&gt;D+3</th>
            <th class="sep-esq">Vencida</th><th>No prazo</th>
          </tr>
        </thead>
        <tbody>
          {linhas_html}
          {linha_total}
        </tbody>
      </table>
    </div>"""
    else:
        # CAPEX
        return f"""
    <div class="bloco-regiao">
      <div class="titulo-regiao">{nome_regiao}</div>
      <table>
        <thead>
          <tr class="linha-grupo">
            <td></td>
            <td colspan="7" class="grupo idade">IDADE DO CHAMADO</td>
            <td colspan="3" class="grupo classificacao">CLASSIFICAÇÃO</td>
            <td colspan="4" class="grupo agenda">PRÓXIMO AGENDAMENTO</td>
            <td colspan="2" class="grupo situacao">SITUAÇÃO</td>
          </tr>
          <tr class="linha-cabecalho">
            <th class="col-unidade">UNIDADE</th>
            <th>{label_bucket1}</th><th>%</th>
            <th>Até 7 dias</th><th>%</th>
            <th>Acima 7 dias</th><th>%</th>
            <th class="total-col">Total</th>
            <th>Enviado D0</th>
            <th>Conveniência</th>
            <th class="destaque-col">Oport. Injeção</th>
            <th class="sep-esq">D+1</th><th>D+2</th><th>D+3</th><th>&gt;D+3</th>
            <th class="sep-esq">Vencida</th><th>No prazo</th>
          </tr>
        </thead>
        <tbody>
          {linhas_html}
          {linha_total}
        </tbody>
      </table>
    </div>"""


def _estilo_base():
    # O estilo precisa ser adaptado para acomodar colunas extras do Autenticador
    return f"""
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: {COR_FUNDO};
    font-family: {FONTE};
    padding: 32px;
    width: fit-content;
  }}
  .cabecalho {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 20px;
    border-bottom: 2px solid {COR_LINHA};
    padding-bottom: 16px;
  }}
  .titulo-principal {{
    color: {COR_TEXTO};
    font-size: 42px;
    font-weight: 700;
    letter-spacing: 0.3px;
  }}
  .titulo-principal span {{
    color: {COR_DESTAQUE};
  }}
  .subtitulo {{
    color: {COR_TEXTO_MUTED};
    font-size: 20px;
    font-family: {FONTE_NUM};
  }}
  .bloco-regiao {{
    background: {COR_CARD};
    border-autenticador: 14px;
    padding: 22px 24px 24px;
    margin-bottom: 18px;
    box-shadow: 0 4px 18px rgba(0,0,0,0.35);
  }}
  .titulo-regiao {{
    color: {COR_TEXTO};
    font-size: 25px;
    font-weight: 700;
    letter-spacing: 1.2px;
    margin-bottom: 16px;
    display: inline-block;
    background: {COR_DESTAQUE}22;
    border: 1px solid {COR_DESTAQUE}55;
    color: {COR_DESTAQUE};
    padding: 6px 16px;
    border-autenticador: 6px;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    font-family: {FONTE_NUM};
  }}
  .linha-grupo td {{
    color: {COR_TEXTO_MUTED};
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-align: center;
    padding: 9px 4px 4px;
    border-bottom: 1px solid {COR_LINHA};
  }}
  .grupo.idade {{ color: #7FE0AE; }}
  .grupo.classificacao {{ color: #8FC6FF; }}
  .grupo.autenticador {{ color: #FFB347; }}
  .grupo.agenda {{ color: #FFD98F; }}
  .grupo.situacao {{ color: #FF9FB0; }}
  .linha-cabecalho th {{
    color: {COR_TEXTO};
    font-size: 20px;
    font-weight: 600;
    text-align: center;
    padding: 12px 14px;
    border-bottom: 2px solid {COR_LINHA};
    white-space: nowrap;
  }}
  .col-unidade {{
    text-align: left !important;
    color: {COR_TEXTO};
    font-weight: 700;
    padding-left: 6px !important;
  }}
  td.cel {{
    text-align: center;
    padding: 14px 14px;
    font-size: 23px;
    font-weight: 600;
    color: {COR_TEXTO};
    border-bottom: 1px solid {COR_CARD_ESCURO};
  }}
  td.cel.pct {{
    font-size: 18px;
    font-weight: 400;
    color: {COR_TEXTO_MUTED};
  }}
  tr.linha:nth-child(odd) td {{ background: {COR_CARD_ESCURO}55; }}
  td.verde {{ background: {COR_VERDE_BG}; color: {COR_VERDE}; }}
  td.amarelo {{ background: {COR_AMARELO_BG}; color: {COR_AMARELO}; }}
  td.vermelho {{ background: {COR_VERMELHO_BG}; color: {COR_VERMELHO}; }}
  td.total-col {{ background: {COR_DESTAQUE}22; color: {COR_DESTAQUE}; font-weight: 700; }}
  td.destaque-col {{ background: {COR_DESTAQUE}22; color: {COR_DESTAQUE}; font-weight: 700; }}
  td.sep-esq {{ border-left: 2px solid {COR_LINHA}; }}
  tr.linha-total td {{
    background: {COR_LINHA} !important;
    color: {COR_TEXTO} !important;
    font-weight: 700;
    border-top: 2px solid {COR_DESTAQUE};
  }}
"""


def _encontrar_base_dir():
    """Detecta o diretório base, funcionando tanto como script Python
    quanto como executável compilado pelo PyInstaller."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def _encontrar_chromium_executavel():
    """Procura o executável do Chromium em vários locais possíveis,
    retornando o primeiro que existir ou None se não encontrar."""
    base = _encontrar_base_dir()

    candidatos = [
        os.path.join(base, 'chromium', 'chrome-win64', 'chrome.exe'),
        os.path.join(base, 'chromium_headless_shell-1228', 'chrome-headless-shell-win64', 'chrome-headless-shell.exe'),
        os.path.join(base, 'chromium_headless_shell-1228', 'chrome-headless-shell-win64', 'chrome.exe'),
    ]

    for caminho in candidatos:
        if os.path.exists(caminho):
            return caminho

    return None


def gerar_html_categoria_regiao(categoria, nome_regiao, idade, agendamento, gerado_em=None, regioes=None):
    """Gera HTML para UMA categoria + UMA região."""
    from backlog_capex import REGIOES as REGIOES_DEFAULT
    regioes = regioes or REGIOES_DEFAULT

    gerado_em = gerado_em or datetime.now()
    timestamp_str = gerado_em.strftime("%d/%m/%Y às %H:%M")

    unidades = regioes[nome_regiao]
    bloco = _tabela_regiao(
        nome_regiao,
        unidades,
        idade[categoria][nome_regiao],
        agendamento[categoria][nome_regiao],
        categoria,
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
{_estilo_base()}
</style>
</head>
<body>
  <div class="cabecalho">
    <div class="titulo-principal">Backlog &middot; <span>{categoria}</span></div>
    <div class="subtitulo">Atualizado em {timestamp_str}</div>
  </div>
  {bloco}
</body>
</html>"""


# Tempo máximo de um render. Estourou, o subprocesso é morto: melhor perder
# uma imagem do que deixar um Chromium pendurado segurando recurso.
TIMEOUT_RENDER_SEG = 120

# Flag interna: faz o executável se comportar só como renderizador e sair.
FLAG_RENDER = "--render-backlog"


def _render_nesta_thread(html, caminho_saida, largura):
    """O render de fato. Roda SEMPRE em processo separado -- ver
    renderizar_backlog_png() logo abaixo para o porquê."""
    from playwright.sync_api import sync_playwright

    chromium_exe = (_encontrar_chromium_executavel()
                    or os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'))

    ultimo_erro = None
    for tentativa in range(1, 3):
        try:
            # O `with` fica DENTRO do retry de propósito. A falha que derrubou
            # o termômetro em 02/08 acontecia justamente aqui, ao subir o
            # driver ("Connection closed while reading from the driver") -- e
            # com o `with` fora do laço o retry nunca chegava a rodar.
            with sync_playwright() as p:
                if chromium_exe:
                    navegador = p.chromium.launch(executable_path=chromium_exe, headless=True)
                else:
                    navegador = p.chromium.launch(headless=True)
                try:
                    pagina = navegador.new_page(
                        viewport={"width": largura, "height": 800},
                        device_scale_factor=2,
                    )
                    pagina.set_content(html, wait_until="networkidle")
                    pagina.screenshot(path=caminho_saida, full_page=True)
                finally:
                    navegador.close()
            return
        except Exception as erro:
            ultimo_erro = erro
            if tentativa < 2:
                time.sleep(5)

    raise ultimo_erro


def executar_render_cli(argv):
    """Entrada do subprocesso: <exe> --render-backlog <html> <png> <largura>.

    Chamada no topo de bot_campo_monitoramento.py, antes de qualquer
    inicialização -- assim o filho não abre o log do bot nem mexe nas
    estatísticas dele.
    """
    try:
        i = argv.index(FLAG_RENDER)
        arq_html, caminho_saida, largura = argv[i + 1], argv[i + 2], int(argv[i + 3])
    except (ValueError, IndexError):
        print(f"uso: {FLAG_RENDER} <arquivo_html> <arquivo_png> <largura>", file=sys.stderr)
        return 2

    try:
        with open(arq_html, encoding="utf-8") as f:
            html = f.read()
        _render_nesta_thread(html, caminho_saida, largura)
        return 0
    except Exception as erro:
        print(f"falha no render: {erro}", file=sys.stderr)
        return 1


def _comando_subprocesso(arq_html, caminho_saida, largura):
    if getattr(sys, 'frozen', False):
        # Executável PyInstaller: ele mesmo sabe se comportar como renderizador.
        return [sys.executable, FLAG_RENDER, arq_html, caminho_saida, str(largura)]
    return [sys.executable, os.path.abspath(__file__), FLAG_RENDER,
            arq_html, caminho_saida, str(largura)]


def renderizar_backlog_png(html, caminho_saida, largura=1950):
    """Recebe o HTML pronto e gera um PNG usando Playwright, em SUBPROCESSO.

    Por que subprocesso: este render é chamado de threads (agendador do
    termômetro, comandos /backlog e /termometro, botão do site) e abria uma
    segunda instância do Playwright dentro do processo do bot. Quando esse
    driver morria, ele não voltava mais -- em 02/08 o termômetro falhou 18
    ciclos seguidos, ~27h, até alguém reiniciar o bot na mão. Num processo
    separado, um driver quebrado morre junto com o filho e o próximo render
    começa limpo.
    """
    import subprocess
    import tempfile

    os.makedirs(os.path.dirname(os.path.abspath(caminho_saida)), exist_ok=True)

    arq_html = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8",
                                         delete=False) as tmp:
            tmp.write(html)
            arq_html = tmp.name

        try:
            concluido = subprocess.run(
                _comando_subprocesso(arq_html, caminho_saida, largura),
                capture_output=True, text=True, timeout=TIMEOUT_RENDER_SEG,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"render não terminou em {TIMEOUT_RENDER_SEG}s (subprocesso encerrado)"
            )

        if concluido.returncode != 0 or not os.path.exists(caminho_saida):
            detalhe = (concluido.stderr or concluido.stdout or "").strip()[-400:]
            raise RuntimeError(
                f"subprocesso de render falhou (código {concluido.returncode}): {detalhe}"
            )
    finally:
        if arq_html:
            try:
                os.unlink(arq_html)
            except OSError:
                pass

    return caminho_saida


def gerar_imagens_backlog_generico(idade, agendamento, categorias, regioes, pasta_saida=None, gerado_em=None, prefixo="backlog"):
    """Gera imagens para qualquer conjunto de categorias + regiões."""
    pasta_saida = pasta_saida or os.path.join(os.getcwd(), "relatorios")
    os.makedirs(pasta_saida, exist_ok=True)
    gerado_em = gerado_em or datetime.now()
    caminhos = {}

    slugs = _SLUG_CATEGORIA

    for nome_categoria in categorias:
        for nome_regiao in regioes:
            slug_cat = slugs.get(nome_categoria, nome_categoria.lower().replace(" ", "_"))
            slug_reg = nome_regiao.lower().replace(" ", "_").replace("ç", "c").replace("ã", "a")
            html = gerar_html_categoria_regiao(
                nome_categoria,
                nome_regiao,
                idade,
                agendamento,
                gerado_em,
                regioes=regioes,
            )
            nome_arquivo = f"{prefixo}_{slug_cat}_{slug_reg}.png"
            caminho = os.path.join(pasta_saida, nome_arquivo)
            renderizar_backlog_png(html, caminho)
            caminhos[(nome_categoria, nome_regiao)] = caminho

    return caminhos


# Mantido para compatibilidade com chamadas antigas (CAPEX)
def gerar_imagens_backlog(idade, agendamento, pasta_saida=None, gerado_em=None):
    """Versão específica para CAPEX (Ativação/MDE) - usa as categorias do backlog_capex."""
    from backlog_capex import CATEGORIAS as CATEGORIAS_CAPEX
    return gerar_imagens_backlog_generico(
        idade, agendamento, CATEGORIAS_CAPEX, REGIOES, pasta_saida, gerado_em, prefixo="backlog_capex"
    )

if __name__ == "__main__":
    sys.exit(executar_render_cli(sys.argv))
