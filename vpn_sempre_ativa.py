"""Mantém a VPN (OpenConnect / FortiGate SSL VPN) sempre conectada. Porte Linux.

Sobe o openconnect como subprocesso, autentica com usuário/senha lidos de
vpn_config.json (nunca versionar esse arquivo) e fica de olho na saída.
Quando o processo cai por qualquer motivo (queda de rede, sessão expirada,
"Cookie is no longer valid" etc.) ele detecta o encerramento e reconecta
sozinho, com espera crescente entre tentativas.

Portado de vpn_sempre_ativa.py (Windows) em 09/08/2026. A lógica de negócio
--- ler config, testar saúde do túnel, reconectar com backoff --- é a MESMA;
só o que dependia do Windows mudou:

  - lock de instância única: msvcrt -> fcntl (o bot principal já usava esse
    mesmo padrão dos dois lados, então este arquivo só copia o ramo Unix dele)
  - encerrar o openconnect: CTRL_BREAK_EVENT (só existe no Windows) -> SIGTERM
  - encoding da saída do processo: "mbcs" (codepage do Windows) -> "utf-8"
    (locale padrão do Linux; o openconnect escreve UTF-8 lá, então isso fica
    mais simples, não mais difícil)
  - binário: nada de pasta embutida com .exe + DLLs -- é "apt install
    openconnect" e resolver pelo PATH com shutil.which
  - REMOVIDA a dança de elevação (IsUserAnAdmin + ShellExecuteW "runas"):
    no Windows o script se reabria elevado sozinho porque o openconnect
    precisa criar o adaptador de rede. No Linux quem decide o privilégio é
    a unit do systemd (roda como root, ou com AmbientCapabilities=CAP_NET_ADMIN
    se quisermos evitar rodar como root de verdade) -- o processo já nasce
    com o que precisa, não tem "elevar depois".

NÃO TESTADO CONTRA UM OPENCONNECT REAL ainda -- foi portado por leitura, sem
uma máquina Linux disponível esta noite para validar contra o VPN de verdade.
Antes de confiar nisto em produção: rodar manual uma vez (`sudo python3
vpn_sempre_ativa.py`) e confirmar que conecta, que o teste de saúde detecta
queda, e que Ctrl+C desconecta limpo.

Uso:
    sudo python3 vpn_sempre_ativa.py
    (pensado para rodar como serviço systemd -- ver bot-vpn.service)

Ctrl+C (ou SIGTERM, que é o que o systemd manda) encerra a VPN de forma
limpa e sai.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import requests

DIRETORIO_SCRIPT = os.path.dirname(os.path.abspath(__file__))

CAMINHO_CONFIG = os.path.join(DIRETORIO_SCRIPT, "vpn_config.json")
PASTA_LOGS = os.path.join(DIRETORIO_SCRIPT, "logs")
os.makedirs(PASTA_LOGS, exist_ok=True)
CAMINHO_LOG = os.path.join(PASTA_LOGS, "vpn_sempre_ativa.log")
CAMINHO_LOCK = os.path.join(PASTA_LOGS, "vpn_sempre_ativa.lock")
# Onde gravar o IP que a VPN atribuiu, para o operador saber onde fazer SSH.
# Todas as maquinas ficam na mesma VPN OpenConnect e alcancam o servidor por
# esse IP (tun0). Fica num arquivo simples para dar 'cat' rapido.
CAMINHO_IP_VPN = os.path.join(DIRETORIO_SCRIPT, "vpn_ip_atual.txt")

# Porta do SSH -- so para a mensagem de log dizer onde conectar (nao abre nada;
# quem abre/escuta e o sshd do sistema, instalado pelo instalar.sh).
PORTA_SSH = int(os.environ.get("PORTA_SSH", "22"))

# openconnect avisa o IP atribuido em PT ("Configurado como X") ou EN
# ("Configured as X"); tambem loga "Obteve/Got ... IP ... X". Pegamos o
# primeiro IPv4 dessas linhas.
_RE_IP_VPN = re.compile(
    r"(?:Configurado como|Configured as|IP address of|endereços de IP legados|legacy IP address)\D*"
    r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
)

# No Windows havia uma cópia embutida do openconnect.exe + DLLs na pasta do
# projeto, para não depender de instalar nada em cada máquina. No Linux isso
# é ao contrário: `apt install openconnect` é o caminho normal, e resolver
# pelo PATH evita carregar binário no repositório à toa.
OPENCONNECT_PADRAO = shutil.which("openconnect") or "/usr/sbin/openconnect"

ESPERA_MINIMA_SEG = 5
ESPERA_MAXIMA_SEG = 120

# Checagem ativa de saúde do túnel: o processo do openconnect pode ficar
# "vivo" mas com o túnel travado por horas sem cair sozinho (já vimos isso
# acontecer no Windows -- horas de "Unexpected pre-PPP packet header" sem o
# processo morrer). Por isso testamos tráfego real periodicamente e, se não
# responder várias vezes seguidas, matamos o processo à força pra forçar
# reconexão. Mantido idêntico ao original -- é lógica de rede, não de SO.
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


_arquivo_lock = None


def _adquirir_lock():
    """Impede duas instâncias rodando ao mesmo tempo -- sem isso, dois
    openconnect brigariam pelo mesmo dispositivo tun (causa erro de rede,
    igual ao access violation que via no Wintun do Windows)."""
    global _arquivo_lock
    try:
        arquivo = open(CAMINHO_LOCK, "a+")
        fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        fcntl.flock(_arquivo_lock.fileno(), fcntl.LOCK_UN)
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


def _anunciar_ip_vpn(ip):
    """Registra o IP que a VPN atribuiu -- e por ele que o operador faz SSH no
    servidor (todas as maquinas estao na mesma VPN OpenConnect). Grava num
    arquivo para 'cat rapido' e deixa um log bem visivel."""
    try:
        with open(CAMINHO_IP_VPN, "w", encoding="utf-8") as f:
            f.write(ip + "\n")
    except Exception as e:
        logger.warning(f"Nao consegui gravar o IP da VPN em {CAMINHO_IP_VPN}: {e}")
    logger.info("=" * 60)
    logger.info(f"  VPN conectada. IP deste servidor na VPN: {ip}")
    logger.info(f"  Acesso remoto:  ssh <usuario>@{ip} -p {PORTA_SSH}")
    logger.info(f"  (IP tambem salvo em {CAMINHO_IP_VPN})")
    logger.info("=" * 60)


def _conectar_uma_vez(config):
    """Sobe o openconnect e bloqueia até o processo cair. Retorna o código de saída.

    As flags de linha de comando são as MESMAS do Windows -- é a CLI do
    próprio openconnect, que é idêntica nas duas plataformas. Não precisou
    mudar nada aqui além do encoding da saída.
    """
    exe = config["openconnect_exe"]
    if not exe or not os.path.exists(exe):
        raise SystemExit(
            f"openconnect não encontrado ({exe!r}). Instale com: "
            "sudo apt install openconnect"
        )

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
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        # Processo no seu próprio grupo: SIGTERM mandado só nele (via
        # send_signal) não se espalha para o processo pai por engano, e dá
        # para mandar sinal pro GRUPO inteiro se o openconnect abrir
        # sub-helpers (o vpnc-script roda como filho dele).
        start_new_session=True,
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

    ip_ja_anunciado = None
    try:
        for linha in processo.stdout:
            linha = linha.rstrip("\n")
            if linha:
                logger.info(f"[openconnect] {linha}")
                m = _RE_IP_VPN.search(linha)
                if m and m.group(1) != ip_ja_anunciado:
                    ip_ja_anunciado = m.group(1)
                    _anunciar_ip_vpn(ip_ja_anunciado)
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
        # SIGTERM no grupo (pid negativo) pega o openconnect E o vpnc-script
        # que ele às vezes deixa rodando como filho.
        os.killpg(processo.pid, signal.SIGTERM)
        processo.wait(timeout=10)
    except Exception:
        try:
            processo.kill()
        except Exception:
            pass


def main():
    if os.geteuid() != 0:
        raise SystemExit(
            "Precisa rodar como root (o openconnect cria interface de rede e "
            "mexe nas rotas). No systemd isso é a unit rodando como root, ou "
            "com AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW -- não dê "
            "'sudo' na mão em produção, deixe a unit cuidar disso."
        )

    if not _adquirir_lock():
        logger.info(
            "Já tem uma instância da VPN sempre-ativa rodando (lock ativo). "
            "Encerrando esta tentativa para não brigar pelo mesmo adaptador."
        )
        return

    # SIGTERM é o que o systemd manda ao parar/reiniciar o serviço. Sem este
    # handler, o processo morre sem desconectar limpo e sem soltar o lock --
    # o próximo start ficaria preso em "lock ativo" até o arquivo expirar
    # sozinho (nunca expira: fcntl solta só quando o processo morre).
    def _ao_receber_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _ao_receber_sigterm)

    try:
        config = _carregar_config()
        espera_atual = ESPERA_MINIMA_SEG

        logger.info("=== VPN sempre-ativa iniciada (Ctrl+C ou SIGTERM para sair) ===")
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
                logger.info("Encerrando...")
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
