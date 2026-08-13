"""Traz o site de 'auto relaótio' (a fonte) para migracao_linux/site (o porte).

A fonte do site e sempre a pasta de desenvolvimento; esta aqui e uma copia
preparada para o Linux. Copiar na mao sempre esquece um arquivo, entao a
regra de o que vai junto e uma so -- a do preparar_envio.py, que este script
importa em vez de repetir.

O que NAO vem junto:

  - segredos (senha do SMTP, segredo do Google): vao vazios, e sao preenchidos
    direto no servidor. Esta pasta viaja em pendrive.
  - o cadastro de usuarios e a chave de sessao: cada instalacao tem os seus.
  - os caminhos desta maquina: pasta_bot e pasta_database aqui apontam para
    /opt/operacional/..., e e isso que tem de continuar valendo.

    python sincronizar_site.py
    python sincronizar_site.py --conferir    # so mostra, nao grava
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FONTE = AQUI.parent / "auto relaótio"
DESTINO = AQUI / "site"

# Chaves que pertencem ao servidor, nao a maquina de desenvolvimento. Se ja
# estiverem preenchidas no destino, o valor de la vence.
DO_SERVIDOR = ("pasta_bot", "pasta_database", "pastas_dados_extra",
               "smtp_servidor", "smtp_porta", "smtp_usuario", "smtp_senha",
               "smtp_remetente", "google_client_id", "google_client_secret",
               "endereco_publico", "cookie_seguro")

# So fazem sentido no Windows. O preparar_envio.py leva porque o .zip dele
# tambem serve para outra maquina Windows; o porte para Linux, nao.
SO_NO_WINDOWS = (".bat", ".ps1")


def e_do_windows(relativo: Path) -> bool:
    return relativo.suffix.lower() in SO_NO_WINDOWS


def carregar_regras():
    """Usa as listas do preparar_envio.py em vez de manter uma copia delas."""
    if str(FONTE) not in sys.path:
        sys.path.insert(0, str(FONTE))
    import preparar_envio
    return preparar_envio


def config_para_o_porte(regras) -> str:
    """A configuracao da fonte, sem segredos, com os caminhos do servidor."""
    limpa = json.loads(regras._config_para_envio() or "{}")
    limpa.pop("pin", None)

    arquivo = DESTINO / "config" / "site.json"
    if arquivo.exists():
        atual = json.loads(arquivo.read_text(encoding="utf-8"))
        for chave in DO_SERVIDOR:
            if atual.get(chave) not in (None, "", []):
                limpa[chave] = atual[chave]
    return json.dumps(limpa, ensure_ascii=False, indent=2)


def main() -> int:
    p = argparse.ArgumentParser(description="Sincroniza o site com o porte Linux.")
    p.add_argument("--conferir", action="store_true",
                   help="mostra o que mudaria, sem gravar nada")
    args = p.parse_args()

    if not FONTE.is_dir():
        print(f"Não achei a fonte em {FONTE}")
        return 1

    regras = carregar_regras()
    itens = [c for c in regras.reunir()
             if not e_do_windows(c.relative_to(regras.RAIZ))]
    relativo_config = Path("config") / "site.json"

    novos, alterados, iguais = [], [], 0
    for arquivo in itens:
        relativo = arquivo.relative_to(regras.RAIZ)
        copia = DESTINO / relativo
        if not copia.exists():
            novos.append(relativo)
        elif copia.read_bytes() != arquivo.read_bytes():
            alterados.append(relativo)
        else:
            iguais += 1

    print(f"Fonte  : {FONTE}")
    print(f"Destino: {DESTINO}\n")
    print(f"{len(novos)} novo(s), {len(alterados)} alterado(s), {iguais} igual(is)")
    for relativo in novos:
        print(f"  novo      {relativo}")
    for relativo in alterados:
        print(f"  alterado  {relativo}")

    # arquivos que so existem no destino: aviso, nao apago. Podem ser dados do
    # servidor que nunca estiveram na fonte.
    de_la = {c.relative_to(DESTINO) for c in DESTINO.rglob("*") if c.is_file()}
    de_ca = {a.relative_to(regras.RAIZ) for a in itens}
    sobrando = sorted(r for r in de_la - de_ca
                      if not (regras.IGNORAR & set(r.parts))
                      and r.suffix in (".py", ".html", ".css", ".js"))
    for relativo in sobrando:
        print(f"  [só no destino] {relativo}")

    if args.conferir:
        print("\n--conferir: nada foi gravado.")
        return 0

    for arquivo in itens:
        relativo = arquivo.relative_to(regras.RAIZ)
        copia = DESTINO / relativo
        copia.parent.mkdir(parents=True, exist_ok=True)
        if relativo == relativo_config:
            continue          # a configuracao tem tratamento proprio, abaixo
        shutil.copy2(arquivo, copia)

    alvo = DESTINO / relativo_config
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(config_para_o_porte(regras), encoding="utf-8")

    print("\nSincronizado.")
    print("A configuração foi para lá SEM os segredos — preencha smtp_senha e")
    print("google_client_secret direto no servidor, em /opt/operacional/site/config/site.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
