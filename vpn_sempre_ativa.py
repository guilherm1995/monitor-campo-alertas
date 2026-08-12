"""Mantém a VPN (OpenConnect / FortiGate SSL VPN) sempre conectada.

Sobe o openconnect.exe como subprocesso, autentica com usuário/senha lidos
de vpn_config.json (nunca versionar esse arquivo) e fica de olho na saída.
Quando o processo cai por qualquer motivo (queda de rede, sessão expirada,
"Cookie is no longer valid" etc.) ele detecta o encerramento e reconecta
sozinho, com espera crescente entre tentativas.

Precisa rodar como Administrador (o openconnect precisa criar o adaptador
Wintun e mexer nas rotas do Windows) -- se não estiver, o script se
reinicia sozinho elevado.

Uso:
    python vpn_sempre_ativa.py
    (ou dois-cliques, ele se eleva sozinho)

Ctrl+C encerra a VPN de forma limpa e sai.
"""
import ctypes
import json
import logging
import msvcrt
import os
import signal
import subprocess
import sys
import threading
import time

import requests

# Quando compilado com PyInstaller (frozen), __file__ aponta pra dentro da
# pasta temporária de extração (_MEI...), não pra onde o .exe realmente
# está -- por isso usamos sys.executable nesse caso (mesmo padrão do
# bot_campo_monitoramento.py com o chromium/).
if getattr(sys, 'frozen', False):
    DIRETORIO_SCRIPT = os.path.dirname(sys.executable)
else:
    DIRETORIO_SCRIPT = os.path.dirname(os.path.abspath(__file__))

CAMINHO_CONFIG = os.path.join(DIRETORIO_SCRIPT, "vpn_config.json")
PASTA_LOGS = os.path.join(DIRETORIO_SCRIPT, "logs")
os.makedirs(PASTA_LOGS, exist_ok=True)
CAMINHO_LOG = os.path.join(PASTA_LOGS, "vpn_sempre_ativa.log")
CAMINHO_LOCK = os.path.join(PASTA_LOGS, "vpn_sempre_ativa.lock")

# Cópia embutida no projeto (pasta openconnect/, com todas as DLLs que ele
# precisa) -- assim o projeto não depende de instalar o OpenConnect-GUI em
# cada máquina. Se por algum motivo essa pasta não existir, cai pro
# executável instalado no sistema.
OPENCONNECT_EMBUTIDO = os.path.join(DIRETORIO_SCRIPT, "openconnect", "openconnect.exe")
OPENCONNECT_INSTALADO = r"C:\Program Files\OpenConnect-GUI\openconnect.exe"
OPENCONNECT_PADRAO = (
    OPENCONNECT_EMBUTIDO if os.path.exists(OPENCONNECT_EMBUTIDO) else OPENCONNECT_INSTALADO
)

ESPERA_MINIMA_SEG = 5
ESPERA_MAXIMA_SEG = 120

# Checagem ativa de saúde do túnel: o processo do openconnect pode ficar
# "vivo" mas com o túnel travado por horas sem cair sozinho (já vimos isso
# acontecer -- horas de "Unexpected pre-PPP packet header" sem o processo
# morrer). Por isso testamos tráfego real periodicamente e, se não responder
# várias vezes seguidas, matamos o processo à força pra forçar reconexão.
URL_TESTE_SAUDE = "https://campo.provedor.example/login/"
SAUDE_ESPERA_INICIAL_SEG = 30
SAUDE_INTERVALO_SEG = 20
SAUDE_FALHAS_MAX = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(CAMINHO_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("vpn_sempre_ativa")


def _rodando_como_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _reiniciar_elevado():
    logger.info("Não estou rodando como Administrador. Reabrindo elevado...")
    parametros = " ".join(f'"{a}"' for a in sys.argv[1:])
    if getattr(sys, 'frozen', False):
        # O .exe já é o "interpretador" -- reabrir ele mesmo, sem passar
        # __file__ (que dentro do frozen aponta pra pasta temporária de
        # extração, não pro .exe de verdade).
        alvo = sys.executable
        argumentos = parametros
    else:
        alvo = sys.executable
        argumentos = f'"{os.path.abspath(__file__)}" {parametros}'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", alvo, argumentos, DIRETORIO_SCRIPT, 1)


_arquivo_lock = None


def _adquirir_lock():
    """Impede duas instâncias elevadas rodando ao mesmo tempo -- sem isso,
    reiniciar o bot (ex: testando no VS Code) deixa a instância antiga da
    VPN órfã rodando junto com a nova, e os dois openconnect.exe brigam
    pelo mesmo adaptador Wintun (causa crashes tipo access violation)."""
    global _arquivo_lock
    try:
        arquivo = open(CAMINHO_LOCK, 'a+')
        msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
        arquivo.seek(0)
        arquivo.truncate()
        arquivo.write(str(os.getpid()))
        arquivo.flush()
        _arquivo_lock = arquivo
        return True
    except (OSError, IOError):
        return False


def _liberar_lock():
    global _arquivo_lock
    if _arquivo_lock is None:
        return
    try:
        _arquivo_lock.seek(0)
        msvcrt.locking(_arquivo_lock.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    finally:
        try:
            _arquivo_lock.close()
        except Exception:
            pass
        _arquivo_lock = None


def _carregar_config():
    if not os.path.exists(CAMINHO_CONFIG):
        raise SystemExit(
            f"Arquivo de configuração não encontrado: {CAMINHO_CONFIG}\n"
            "Crie um vpn_config.json com: servidor, protocolo, usuario, senha."
        )
    with open(CAMINHO_CONFIG, "r", encoding="utf-8") as f:
        config = json.load(f)

    faltando = [c for c in ("servidor", "usuario", "senha") if not config.get(c)]
    if faltando:
        raise SystemExit(f"Faltam campos no vpn_config.json: {', '.join(faltando)}")

    config.setdefault("protocolo", "fortinet")
    config.setdefault("openconnect_exe", OPENCONNECT_PADRAO)
    return config


def _vpn_respondendo():
    """Testa tráfego real através do túnel (não só se o processo está vivo)."""
    try:
        r = requests.head(URL_TESTE_SAUDE, timeout=8)
        return r.status_code < 500
    except Exception:
        return False


def _monitorar_saude(processo, parar_evento):
    """Roda em paralelo à leitura de log; mata o processo se o túnel travar."""
    if parar_evento.wait(SAUDE_ESPERA_INICIAL_SEG):
        return

    falhas_seguidas = 0
    while not parar_evento.is_set():
        if _vpn_respondendo():
            if falhas_seguidas:
                logger.info("VPN voltou a responder no teste de saúde.")
            falhas_seguidas = 0
        else:
            falhas_seguidas += 1
            logger.warning(
                f"Teste de saúde da VPN falhou ({falhas_seguidas}/{SAUDE_FALHAS_MAX})."
            )
            if falhas_seguidas >= SAUDE_FALHAS_MAX:
                logger.warning(
                    "VPN parece travada (processo vivo, mas sem tráfego real). "
                    "Forçando reconexão..."
                )
                _desconectar(processo)
                return

        if parar_evento.wait(SAUDE_INTERVALO_SEG):
            return


def _conectar_uma_vez(config):
    """Sobe o openconnect e bloqueia até o processo cair. Retorna o código de saída."""
    exe = config["openconnect_exe"]
    if not os.path.exists(exe):
        raise SystemExit(f"openconnect.exe não encontrado em: {exe}")

    comando = [
        exe,
        f"--protocol={config['protocolo']}",
        "-u", config["usuario"],
        "--passwd-on-stdin",
    ]
    if config.get("servercert_sha256"):
        comando.append(f"--servercert=pin-sha256:{config['servercert_sha256']}")
    comando.append(config["servidor"])

    logger.info(f"Conectando na VPN {config['servidor']} (usuário {config['usuario']})...")
    processo = subprocess.Popen(
        comando,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # openconnect.exe (e o cscript do vpnc-script) escrevem no codepage
        # ANSI do Windows, não em UTF-8 -- decodificar como UTF-8 corrompe
        # acentos (ex: "Versão" virava "Vers?o"). "mbcs" usa o codepage
        # ativo do sistema automaticamente.
        encoding="mbcs",
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    try:
        processo.stdin.write(config["senha"] + "\n")
        processo.stdin.close()
    except Exception as e:
        logger.warning(f"Falha ao enviar a senha para o openconnect: {e}")

    parar_evento = threading.Event()
    thread_saude = threading.Thread(
        target=_monitorar_saude, args=(processo, parar_evento), daemon=True
    )
    thread_saude.start()

    try:
        for linha in processo.stdout:
            linha = linha.rstrip("\n")
            if linha:
                logger.info(f"[openconnect] {linha}")
    except KeyboardInterrupt:
        parar_evento.set()
        _desconectar(processo)
        raise
    except Exception as e:
        logger.warning(f"Leitura da saída do openconnect interrompida: {e}")
    finally:
        parar_evento.set()

    codigo_saida = processo.wait()
    return codigo_saida


def _desconectar(processo):
    logger.info("Encerrando a VPN de forma limpa...")
    try:
        processo.send_signal(signal.CTRL_BREAK_EVENT)
        processo.wait(timeout=10)
    except Exception:
        try:
            processo.kill()
        except Exception:
            pass


def main():
    if not _rodando_como_admin():
        _reiniciar_elevado()
        return

    if not _adquirir_lock():
        logger.info(
            "Já tem uma instância da VPN sempre-ativa rodando (lock ativo). "
            "Encerrando esta tentativa para não brigar pelo mesmo adaptador."
        )
        return

    try:
        config = _carregar_config()
        espera_atual = ESPERA_MINIMA_SEG

        logger.info("=== VPN sempre-ativa iniciada (Ctrl+C para sair) ===")
        while True:
            try:
                inicio = time.time()
                codigo_saida = _conectar_uma_vez(config)
                duracao = time.time() - inicio

                if duracao > 60:
                    # Ficou conectada por um bom tempo -> reseta o backoff.
                    espera_atual = ESPERA_MINIMA_SEG

                logger.warning(
                    f"openconnect encerrou (código {codigo_saida}) após {duracao:.0f}s conectado. "
                    f"Reconectando em {espera_atual}s..."
                )
            except KeyboardInterrupt:
                logger.info("Encerrando por Ctrl+C...")
                break
            except SystemExit:
                raise
            except Exception:
                logger.exception("Erro inesperado no loop da VPN.")

            try:
                time.sleep(espera_atual)
            except KeyboardInterrupt:
                break

            espera_atual = min(espera_atual * 2, ESPERA_MAXIMA_SEG)

        logger.info("=== VPN sempre-ativa finalizada ===")
    finally:
        _liberar_lock()


if __name__ == "__main__":
    main()
