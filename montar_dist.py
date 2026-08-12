"""Depois de compilar (PyInstaller), este script monta a pasta dist/ com
tudo que os .exe esperam encontrar do LADO deles (não embutido no .exe --
PyInstaller onefile não empacota pastas inteiras direito, e o próprio
código já assume esse layout via `os.path.dirname(sys.executable)`,
igual ao chromium/ que já funcionava assim).

Rodar depois de: pyinstaller bot_campo_monitoramento.spec
              e: pyinstaller --onefile --name vpn_sempre_ativa vpn_sempre_ativa.py

Uso:
    python montar_dist.py
"""
import os
import shutil

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(RAIZ, "dist")

PASTAS_INTEIRAS = [
    "chromium",              # portátil, se existir (opcional -- fallback automático se faltar)
    "openconnect",           # binário + DLLs da VPN
    "assets",                # logo + som de alerta
    "node_modules",          # dependências do serviço WhatsApp (rodar "npm install" antes se faltar)
]

ARQUIVOS_SOLTOS = [
    "index.js",
    "config.json",
    "package.json",
    "package-lock.json",
    "vpn_config.json",
    # Registra a tarefa do Agendador que mantém o bot no ar (roda uma vez, como
    # administrador, na máquina de produção). Substituiu o reiniciar_bot.bat,
    # aposentado em 07/08/2026: o laço 'goto' dele falhou durante um travamento
    # de I/O da máquina e o CMD encerra o .bat quando um goto falha, então o
    # supervisor morreu calado e o bot ficou horas fora do ar.
    "instalar_tarefa.ps1",
    # Amostra o estado da máquina a cada 30s em logs/telemetria.csv. A PRODUCAO
    # congela a cada ~7h sem deixar rastro; sem isso só dá para reagir.
    "telemetria.ps1",
]

# Só os arquivos ESTÁTICOS de dados/ (entrada, não estado gerado em runtime
# -- os_notificadas.json etc nascem vazios numa instalação nova, de propósito).
ARQUIVOS_DADOS_ESTATICOS = [
    os.path.join("dados", "base OFS ok.xlsx"),
    os.path.join("dados", "google_credentials.json"),
]


def copiar_pasta(nome):
    origem = os.path.join(RAIZ, nome)
    destino = os.path.join(DIST, nome)
    if not os.path.isdir(origem):
        print(f"  [pulado] {nome}/ não existe na raiz do projeto.")
        return
    shutil.copytree(origem, destino, dirs_exist_ok=True)
    print(f"  [ok] {nome}/ copiada.")


def copiar_arquivo(caminho_relativo):
    origem = os.path.join(RAIZ, caminho_relativo)
    destino = os.path.join(DIST, caminho_relativo)
    if not os.path.exists(origem):
        print(f"  [pulado] {caminho_relativo} não existe na raiz do projeto.")
        return
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.copy2(origem, destino)
    print(f"  [ok] {caminho_relativo} copiado.")


def main():
    if not os.path.isdir(DIST):
        raise SystemExit(f"Pasta dist/ não encontrada em {DIST}. Rode o PyInstaller antes.")

    print("Copiando pastas inteiras:")
    for pasta in PASTAS_INTEIRAS:
        copiar_pasta(pasta)

    print("\nCopiando arquivos soltos:")
    for arquivo in ARQUIVOS_SOLTOS:
        copiar_arquivo(arquivo)

    print("\nCopiando dados estáticos (planilha, credencial Google):")
    for arquivo in ARQUIVOS_DADOS_ESTATICOS:
        copiar_arquivo(arquivo)

    print(f"\nPronto. dist/ montada em: {DIST}")


if __name__ == "__main__":
    main()
