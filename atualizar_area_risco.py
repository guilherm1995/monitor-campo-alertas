# ============ Rebaixa o mapa de área de risco e reescreve area_risco.py ============
#
# Rode isto QUANDO A OPERAÇÃO REDESENHAR O MAPA, não de rotina. O bot não lê o
# Google: os polígonos ficam copiados dentro de area_risco.py justamente para
# que o alerta não dependa da rede (ver o cabeçalho de lá). O preço dessa
# escolha é que o arquivo e o mapa podem divergir em silêncio, e este script é
# o que fecha essa distância — de propósito, com alguém olhando.
#
#     python atualizar_area_risco.py            # mostra o que mudaria
#     python atualizar_area_risco.py --gravar   # reescreve area_risco.py
#
# Depois de gravar, o arquivo tem de ser publicado como qualquer outra
# mudança: nada aqui fala com o servidor.
#
# O que entra e o que fica de fora: o mapa tem uma camada de polígonos
# "ÁREA n NOME" que é divisão de equipe (Denny, Bruno, Paulo...), não área de
# risco. Só entram as pastas cujo nome fala em BLOQUEIO ou RISCO. Se um dia a
# operação criar uma pasta de risco com outro nome, ela NÃO entra e o script
# avisa quais pastas ignorou — leia essa linha, é a única defesa contra o mapa
# crescer e o bot não saber.
import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

MID = "1gJ5vP1_-25WX19JI6ETKlsq23aDBrJE"
URL_KML = f"https://www.google.com/maps/d/kml?mid={MID}&forcekml=1"

KML = "{http://www.opengis.net/kml/2.2}"
ARQUIVO = Path(__file__).with_name("area_risco.py")

ABERTURA = "POLIGONOS_DE_RISCO = [\n"
FECHAMENTO = "\n]\n"


def baixar_kml(url=URL_KML):
    with urllib.request.urlopen(url, timeout=60) as resposta:
        return resposta.read()


def pastas_de_risco(raiz):
    """(polígonos, nomes das pastas ignoradas)."""
    poligonos = []
    ignoradas = []
    for pasta in raiz.iter(KML + "Folder"):
        nome_pasta = pasta.find(KML + "name")
        nome_pasta = (nome_pasta.text or "") if nome_pasta is not None else ""
        alvo = nome_pasta.upper()
        if "BLOQUEIO" not in alvo and "RISCO" not in alvo:
            ignoradas.append(nome_pasta)
            continue
        for marca in pasta.findall(KML + "Placemark"):
            nome = marca.find(KML + "name")
            nome = (nome.text or "") if nome is not None else ""
            for coordenadas in marca.iter(KML + "coordinates"):
                if not (coordenadas.text or "").strip():
                    continue
                pontos = []
                for item in coordenadas.text.split():
                    partes = item.split(",")
                    # O KML escreve lng,lat[,altura] -- invertido em relação ao
                    # resto do mundo. Trocar aqui, uma vez, é o que impede o
                    # erro de aparecer lá na frente como "o polígono está no
                    # meio do oceano".
                    pontos.append((float(partes[1]), float(partes[0])))
                if len(pontos) >= 3:
                    poligonos.append({"nome": nome, "pontos": pontos})
    return poligonos, ignoradas


def montar_bloco(poligonos):
    linhas = []
    for area in poligonos:
        linhas.append("    {")
        linhas.append(f"        'nome': {area['nome']!r},")
        linhas.append("        'pontos': [")
        formatados = [f"({lat:.6f}, {lng:.6f})" for lat, lng in area["pontos"]]
        for inicio in range(0, len(formatados), 3):
            linhas.append("            " + ", ".join(formatados[inicio:inicio + 3]) + ",")
        linhas.append("        ],")
        linhas.append("    },")
    return "\n".join(linhas)


def trocar_bloco(fonte, bloco):
    inicio = fonte.index(ABERTURA) + len(ABERTURA)
    fim = fonte.index(FECHAMENTO, inicio)
    return fonte[:inicio] + bloco + fonte[fim:]


def main():
    argumentos = argparse.ArgumentParser(description=__doc__)
    argumentos.add_argument("--gravar", action="store_true",
                            help="reescreve area_risco.py (sem isto, só mostra)")
    argumentos.add_argument("--kml", help="ler de um arquivo .kml local em vez do Google")
    opcoes = argumentos.parse_args()

    bruto = Path(opcoes.kml).read_bytes() if opcoes.kml else baixar_kml()
    raiz = ET.fromstring(bruto)

    poligonos, ignoradas = pastas_de_risco(raiz)
    if not poligonos:
        print("NENHUM polígono de risco no mapa. Nada foi gravado.", file=sys.stderr)
        print(f"Pastas vistas e ignoradas: {ignoradas}", file=sys.stderr)
        return 1

    print(f"{len(poligonos)} polígono(s) de risco:")
    for area in poligonos:
        lats = [p[0] for p in area["pontos"]]
        lngs = [p[1] for p in area["pontos"]]
        print(f"  {area['nome']} — {len(area['pontos'])} pontos, "
              f"lat {min(lats):.5f}..{max(lats):.5f}, lng {min(lngs):.5f}..{max(lngs):.5f}")
    if ignoradas:
        print(f"\nPastas ignoradas (não falam em BLOQUEIO nem RISCO): {ignoradas}")
        print("Se alguma delas for área de risco, o bot NÃO vai enxergá-la.")

    fonte = ARQUIVO.read_text(encoding="utf-8")
    novo = trocar_bloco(fonte, montar_bloco(poligonos))

    if novo == fonte:
        print("\nO mapa não mudou desde a última vez. Nada a fazer.")
        return 0

    if not opcoes.gravar:
        print("\nO arquivo MUDARIA. Rode de novo com --gravar para aplicar.")
        return 0

    ARQUIVO.write_text(novo, encoding="utf-8", newline="\n")
    print(f"\n{ARQUIVO.name} reescrito. Confira, compile e publique.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
