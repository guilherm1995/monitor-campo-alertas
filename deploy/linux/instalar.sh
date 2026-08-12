#!/usr/bin/env bash
# Instalador do bot CAMPO + site OPERACIONAL para Ubuntu Server 24.04 LTS.
#
# Feito para rodar UMA VEZ na maquina de producao, como root:
#     sudo ./instalar.sh
#
# Espelha o instalar.py do Windows (mesma filosofia: idempotente, pode rodar
# de novo sem desfazer o que ja esta pronto, avisa em vez de travar quando da
# para seguir em frente).
#
# NAO TESTADO CONTRA UMA UBUNTU DE VERDADE ainda -- escrito por leitura da
# documentacao (apt, systemd, Playwright) numa sessao sem maquina Linux
# disponivel. Antes de confiar em producao: rodar numa VM/maquina de teste
# primeiro e ler os avisos [ERRO] com atencao.
#
# O que faz, em ordem:
#   1. confere que esta rodando como root e numa distro Debian-based
#   2. instala pacotes de sistema (python3, node, openconnect, firmware, deps do Chromium)
#      + acesso SSH (2b), Wi-Fi robusto p/ servidor sem cabo (2c), tampa/suspensao (2d)
#   3. cria o usuario de servico dedicado 'operacional' (sem login, sem privilegio)
#   4. copia bot/ e site/ para /opt/operacional (fonte fica intocada)
#   5. cria o venv Python e instala requirements.txt
#   6. baixa o Chromium do Playwright (com as bibliotecas de sistema que ele
#      precisa -- e por isso que o passo 2 nao tenta advinhar essa lista)
#   7. npm install da ponte do WhatsApp
#   8. cria as pastas de dados e ajusta o dono para 'operacional'
#   9. ajusta os caminhos Windows->Linux dentro de config/site.json
#  10. instala as units do systemd (systemd/*.service) e habilita (nao inicia)
#  11. teste de ponta a ponta: o site importa, o venv acha o Chromium
#
# Pode rodar quantas vezes quiser -- e checagem "ja existe?" antes de cada
# passo destrutivo, igual ao instalar.py.

set -uo pipefail   # sem -e de proposito: um passo que falha deve avisar e
                   # SEGUIR para o resumo final, nao abortar no meio calado
                   # (mesmo principio do instalar.py: reunir avisos, nao
                   # travar na primeira coisa que faltou)

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="/opt/operacional"
USUARIO="operacional"
VENV="$DESTINO/venv"
PLAYWRIGHT_BROWSERS_PATH_PADRAO="$DESTINO/pw-browsers"

FALHAS=()
AVISOS=()

titulo() { echo; echo "$1"; echo "${1//?/-}"; }
ok()     { echo "  [ok]    $1"; }
aviso()  { AVISOS+=("$1"); echo "  [aviso] $1"; }
erro()   { FALHAS+=("$1"); echo "  [ERRO]  $1"; }

echo "=================================================================="
echo "  INSTALADOR - Bot CAMPO + Site OPERACIONAL (Ubuntu Server 24.04 LTS)"
echo "  fonte:  $RAIZ"
echo "  destino: $DESTINO"
echo "=================================================================="

# --------------------------------------------------------------------------
titulo "1. Pre-requisitos"

if [[ "$(id -u)" -ne 0 ]]; then
  erro "precisa rodar como root (sudo ./instalar.sh) -- apt, useradd e systemd exigem."
  echo; echo "1 problema impede a instalacao. Corrija e rode de novo."
  exit 1
fi
ok "rodando como root"

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  echo "  distro: ${PRETTY_NAME:-desconhecida}"
  if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *debian* ]]; then
    erro "esta distro nao parece ser Debian/Ubuntu -- os comandos apt abaixo vao falhar."
  elif [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" != "24.04" ]]; then
    aviso "testado pensando em Ubuntu 24.04; esta maquina e ${VERSION_ID:-?} -- deve funcionar, mas confira os avisos do apt."
  else
    ok "distro compativel"
  fi
else
  aviso "nao consegui ler /etc/os-release; seguindo mesmo assim"
fi

# --------------------------------------------------------------------------
titulo "2. Pacotes de sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

# python3-venv e essencial no Ubuntu (o python3 sozinho nao traz venv).
# openconnect e o cliente de VPN nativo -- substitui toda a automacao de
# janela do FortiClient que existia no Windows.
# python3-tk e obrigatorio: bot_campo_monitoramento.py importa tkinter (Painel
# de TV) incondicionalmente, no topo do arquivo -- sem esse pacote o bot
# nem chega a iniciar (ModuleNotFoundError logo no import).
# xserver-xorg/xinit/x11-xserver-utils sobem a sessao X minima na TV fisica
# (campo-tv-display.service) onde o Painel de TV abre janela -- confirmado em
# 09/08/2026 que a maquina de producao tem monitor/TV conectado de verdade.
# mpg123 toca o som de alerta de garantia (alerta_garantia.mp3); no Windows
# isso ia por winmm/MCI, que nao existe aqui.
# openssh-server: acesso remoto ao servidor. O operador conecta no servidor
# pelo IP que a VPN atribui (tun0) -- todas as maquinas ficam na mesma VPN
# OpenConnect, entao o SSH entra e sai pelo proprio tunel (que ja e a rota
# default). Por isso NAO precisa de policy-routing/fwmark: o caminho de volta
# ja e o tunel. Basta o sshd escutar (0.0.0.0 por padrao cobre o tun0 assim
# que ele sobe) e a porta 22 estar liberada.
# linux-firmware: blobs de firmware da GPU (o Dell Inspiron 3583 tem uma Radeon
# 520, driver amdgpu) e, principalmente, do Wi-Fi (Intel Wireless-AC 9462,
# driver iwlwifi). Sem o firmware do iwlwifi o radio pode nem subir -- e este
# servidor conecta SO por Wi-Fi. wpasupplicant: o netplan/networkd exige ele
# para conectar em rede sem fio. iw + rfkill: desbloquear o radio e desligar o
# power-save (ver passo 2c).
# mpg123: toca o alerta sonoro (alerta_garantia.mp3). alsa-utils: da o amixer
# (pra tirar o mudo do Master -- placa HDA sobe MUDA no boot) e o alsa-restore,
# que recarrega o mixer salvo a cada boot (senao volta a ficar mudo).
# unclutter: esconde o cursor do mouse na TV (hdmi-tv.sh usa).
# nodejs/npm NAO entram aqui de proposito -- ver instalacao via NodeSource
# logo abaixo (o pacote do apt do Ubuntu/Zorin 24.04/18 e Node 18, velho
# demais para o Baileys).
PACOTES=(python3 python3-venv python3-pip python3-tk openconnect openssh-server ca-certificates curl xserver-xorg xinit x11-xserver-utils mpg123 alsa-utils unclutter linux-firmware wpasupplicant iw rfkill)
if apt-get install -y -qq "${PACOTES[@]}"; then
  ok "pacotes base instalados: ${PACOTES[*]}"
else
  erro "falha instalando pacotes base -- rode 'apt-get install ${PACOTES[*]}' na mao para ver o erro completo"
fi

# Node via NodeSource, nao via apt: testado em 09/08/2026 numa Zorin 18.1
# (base Ubuntu 24.04/noble) e o 'apt-get install nodejs' traz Node 18.19.1 --
# o Baileys (@whiskeysockets/baileys 6.7.23, ponte do WhatsApp) exige Node
# 20+ e o 'npm install' falha na cara ("This package requires Node.js 20+").
# Achado rodando o instalador de verdade pela primeira vez; antes disso o
# script só avisava e torcia para dar certo.
NODE_VERSAO="$(node --version 2>/dev/null || echo '?')"
if [[ "$NODE_VERSAO" == v2[0-9]* ]]; then
  ok "node $NODE_VERSAO ja instalado e compativel (>=20)"
else
  echo "  node atual: ${NODE_VERSAO} -- instalando Node 20.x via NodeSource..."
  if curl -fsSL https://deb.nodesource.com/setup_20.x | bash - &>/dev/null \
     && apt-get install -y -qq nodejs; then
    NODE_VERSAO="$(node --version 2>/dev/null || echo '?')"
    ok "node $NODE_VERSAO instalado via NodeSource"
  else
    erro "falha instalando Node 20+ via NodeSource -- rode 'curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && sudo apt-get install -y nodejs' na mao"
  fi
fi

# --------------------------------------------------------------------------
titulo "2g. Fuso horario (Brasil)"
# O Ubuntu Server instala em UTC. O relogio UTC fica certo (NTP), mas o 'date'
# aparece 3h adiantado e -- pior -- a logica de "hoje/D0" do bot roda em UTC:
# entre 21h e meia-noite de Brasilia ela viraria o dia cedo demais. Por isso
# fixo America/Sao_Paulo (UTC-3, sem horario de verao desde 2019). O NTP ja
# vem ativo no Ubuntu; so o fuso precisa ser dito.
if timedatectl set-timezone America/Sao_Paulo 2>/dev/null; then
  ok "fuso horario = America/Sao_Paulo ($(date '+%d/%m %H:%M %Z'))"
else
  echo "  [aviso] nao consegui fixar o fuso -- rode 'sudo timedatectl set-timezone America/Sao_Paulo' na mao"
fi

# --------------------------------------------------------------------------
titulo "2f. Tunel publico (cloudflared)"
# No Windows o site subia junto um tunel Cloudflare (cloudflared) que gera uma
# URL aleatoria *.trycloudflare.com a cada reinicio -- e assim o painel fica
# acessivel de fora da rede sem abrir porta no roteador. O iniciar_site.py ja
# procura o binario 'cloudflared' no PATH e sobe o tunel sozinho; so falta ter
# o binario instalado. Baixo o oficial da Cloudflare (amd64) direto pra
# /usr/local/bin (que ja esta no PATH do usuario 'operacional' do servico do site).
if command -v cloudflared >/dev/null; then
  ok "cloudflared ja instalado ($(cloudflared --version 2>/dev/null | head -1))"
else
  echo "  baixando cloudflared oficial (amd64)..."
  if curl -fsSL -o /tmp/cloudflared \
       https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
     && install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared; then
    ok "cloudflared instalado em /usr/local/bin ($(cloudflared --version 2>/dev/null | head -1))"
    rm -f /tmp/cloudflared
  else
    # Nao e fatal: sem cloudflared o site ainda sobe na rede local/Tailscale,
    # so nao tem a URL publica trycloudflare. Por isso 'aviso', nao 'erro'.
    echo "  [aviso] nao consegui instalar o cloudflared -- o site sobe local,"
    echo "          mas sem a URL publica *.trycloudflare.com. Instale depois com:"
    echo "          curl -fsSL -o /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    echo "          sudo install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared"
  fi
fi

# --------------------------------------------------------------------------
titulo "2h. Audio (tirar o mudo do alerta sonoro)"
# A placa HDA deste Dell sobe com o Master MUDO e em 0% no boot. Resultado: o
# mpg123 do alerta "toca sem erro" mas NAO sai som nenhum. Aqui abro o Master
# (85%, unmute), garanto Speaker/Headphone ligados e salvo o estado com
# 'alsactl store' -- o alsa-restore recarrega isso a cada boot. (Descoberto
# 10/08/2026 validando o alerta: o Master estava [0%] [off].)
if command -v amixer >/dev/null; then
  amixer -c 0 sset Master unmute 85% >/dev/null 2>&1 || true
  amixer -c 0 sset Speaker unmute 100% >/dev/null 2>&1 || true
  amixer -c 0 sset Headphone unmute 100% >/dev/null 2>&1 || true
  if alsactl store >/dev/null 2>&1; then
    ok "audio: Master aberto (85%, unmute) e mixer salvo (restaura no boot via alsa-restore)"
  else
    ok "audio: Master aberto (85%, unmute) -- nao consegui 'alsactl store', pode voltar mudo no boot"
  fi
else
  echo "  [aviso] amixer ausente (alsa-utils nao instalou?) -- se o alerta ficar mudo,"
  echo "          rode 'sudo amixer -c 0 sset Master unmute 85% && sudo alsactl store'"
fi

# Saida padrao = HDMI da TV. O bot chama 'mpg123' sem indicar dispositivo,
# entao sem isto o alerta vai para a saida analogica (card 0, device 0) e sai
# numa caixa ligada no P2 -- nao no alto-falante da TV, que e onde a operacao
# escuta. 'type plug' nao e opcional: o HDMI aceita 48 kHz e o mp3 do alerta e
# 44,1 kHz; sem a camada de conversao o mpg123 nem abre o dispositivo.
#
# O numero do device de HDMI e descoberto no 'aplay -l' em vez de fixado: em
# outra maquina (ou com outra TV) ele muda, e um hw:0,3 chumbado deixaria o
# alerta mudo sem nenhum erro visivel.
# Primeiro tenta a saida que anuncia NOME de TV no EDID (ex.: "[Philips HDTV]"):
# as portas vazias aparecem com rotulo generico "[HDMI 1]", "[HDMI 2]". Se
# nenhuma anunciar nome -- caso desta placa, cujo EDID as vezes vem vazio --
# cai na primeira HDMI da lista.
hdmi_dev=$(aplay -l 2>/dev/null | grep -E 'device [0-9]+: HDMI' | grep -vE '\[HDMI [0-9]+\]$' \
           | sed -n 's/^card \([0-9]\+\).*device \([0-9]\+\):.*/\1,\2/p' | head -1)
[ -n "$hdmi_dev" ] || hdmi_dev=$(aplay -l 2>/dev/null \
           | sed -n 's/^card \([0-9]\+\).*device \([0-9]\+\): HDMI.*/\1,\2/p' | head -1)
if [ -n "$hdmi_dev" ]; then
  cat > /etc/asound.conf <<ASOUND
# Saida de audio padrao = HDMI da TV de producao.
# Gerado pelo instalar.sh; ver comentario no passo 2h.
pcm.!default {
    type plug
    slave.pcm "hw:${hdmi_dev}"
}

ctl.!default {
    type hw
    card ${hdmi_dev%%,*}
}
ASOUND
  chmod 644 /etc/asound.conf
  ok "audio: saida padrao apontada para o HDMI (hw:${hdmi_dev}) -- alerta sai na TV"
else
  echo "  [aviso] nenhuma saida HDMI no 'aplay -l' (TV desligada na instalacao?)."
  echo "          O alerta vai sair pela saida analogica. Com a TV ligada, rode"
  echo "          este passo de novo para gerar o /etc/asound.conf."
fi

# --------------------------------------------------------------------------
titulo "2i. Comando /reiniciar (sudoers)"
# O bot roda como 'operacional' (sem privilegio) mas o comando /reiniciar no grupo
# (Telegram/WhatsApp) precisa dar reboot na MAQUINA INTEIRA. Regra minima e
# PERMANENTE -- so 'systemctl reboot', nada mais -- bem diferente do NOPASSWD
# ALL temporario que fica em /etc/sudoers.d/operador (esse e so para o
# assistente remoto e deve ser removido; este aqui e por design, fica).
# 'operacional' pode nao existir ainda neste ponto do instalador (criado no passo
# 3) -- sudoers aceita o nome sem o usuario existir de verdade ainda.
SUDOERS_REINICIAR=/etc/sudoers.d/operacional-reiniciar
TMP_SUDOERS=$(mktemp)
echo "$USUARIO ALL=(root) NOPASSWD: /usr/bin/systemctl reboot" > "$TMP_SUDOERS"
if visudo -cf "$TMP_SUDOERS" >/dev/null 2>&1; then
  install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_REINICIAR"
  ok "sudoers: '$USUARIO' pode 'systemctl reboot' sem senha (comando /reiniciar do bot)"
else
  erro "regra de sudoers para /reiniciar ficou invalida -- NAO instalada (comando /reiniciar vai falhar)"
fi
rm -f "$TMP_SUDOERS"

# --------------------------------------------------------------------------
titulo "2b. Acesso remoto (SSH)"
# O operador administra o servidor pelo IP da VPN (tun0). Precisa do sshd
# escutando e da porta 22 liberada. O sshd escuta 0.0.0.0 por padrao, entao
# cobre o tun0 automaticamente quando a VPN sobe -- nao precisa fixar o IP da
# VPN em ListenAddress (que ate atrapalharia, porque esse IP so existe depois
# que a VPN conecta).
if systemctl enable --now ssh &>/dev/null || systemctl enable --now sshd &>/dev/null; then
  ok "sshd habilitado e no ar (escuta 0.0.0.0:22 -- cobre o IP da VPN quando o tunel sobe)"
else
  erro "nao consegui habilitar o sshd -- confira 'systemctl status ssh' (o pacote openssh-server instalou?)"
fi
# Libera a porta 22 SO se o ufw estiver ATIVO -- nunca ativo o ufw aqui, para
# nao correr o risco de bloquear o proprio acesso remoto por engano.
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 22/tcp >/dev/null 2>&1 && ok "ufw: porta 22/tcp liberada"
else
  ok "ufw inativo/ausente -- porta 22 ja acessivel (nada a liberar)"
fi

# --------------------------------------------------------------------------
titulo "2c. Wi-Fi do servidor (sem cabo de rede)"
# Este servidor e um notebook (Dell Inspiron 3583) que conecta SO por Wi-Fi.
# Se o Wi-Fi cair e nao voltar, perde-se o acesso remoto (SSH/VPN) e nao ha
# cabo de plano B. Por isso: firmware garantido (linux-firmware, passo 2),
# power-save DESLIGADO (o iwlwifi adormece o radio e derruba conexoes ociosas,
# a causa nº 1 de queda de Wi-Fi em servidor), rfkill liberado, e a rede
# configurada para subir sozinha no boot.

# 1) rfkill: garante que o radio nao esta bloqueado por software
if command -v rfkill >/dev/null; then rfkill unblock wifi 2>/dev/null; rfkill unblock all 2>/dev/null; fi

# 2) power-save OFF de forma persistente (modulo). 'power_save=0' e um parametro
#    antigo e estavel do iwlwifi -- NAO adiciono parametros exoticos aqui porque
#    um parametro invalido faz o modulo NAO carregar (Wi-Fi morto no boot).
echo "options iwlwifi power_save=0" > /etc/modprobe.d/iwlwifi-servidor.conf
ok "power-save do Wi-Fi desligado no modulo (evita quedas por ociosidade)"

# detecta a interface Wi-Fi REAL (nome tipo wlp2s0, nao 'wlan0')
WIFI_IF=""
for _d in /sys/class/net/*/wireless; do
  [[ -e "$_d" ]] || continue
  WIFI_IF="$(basename "$(dirname "$_d")")"; break
done

if [[ -z "$WIFI_IF" ]]; then
  aviso "nenhuma interface Wi-Fi visivel agora -- pode ser que o firmware recem-instalado so ative o radio apos um reboot. Reinicie e rode o instalador de novo para configurar a rede."
else
  # power-save OFF tambem AGORA (sem esperar reboot), se 'iw' existir
  command -v iw >/dev/null && iw dev "$WIFI_IF" set power_save off 2>/dev/null

  if ip -4 addr show "$WIFI_IF" 2>/dev/null | grep -q 'inet '; then
    ok "Wi-Fi ($WIFI_IF) ja esta conectado -- mantendo a configuracao de rede atual"
  elif [[ -f /etc/netplan/90-wifi-operacional.yaml ]]; then
    ok "Wi-Fi ja configurado em /etc/netplan/90-wifi-operacional.yaml -- nao mexo"
  else
    # credenciais: por variavel de ambiente (WIFI_SSID/WIFI_PSK), por arquivo
    # wifi.conf ao lado do instalador, ou perguntando na hora.
    [[ -z "${WIFI_SSID:-}" && -f "$RAIZ/wifi.conf" ]] && . "$RAIZ/wifi.conf"
    if [[ -z "${WIFI_SSID:-}" ]]; then
      read -r -p "  Nome da rede Wi-Fi (SSID): " WIFI_SSID
      read -r -s -p "  Senha do Wi-Fi: " WIFI_PSK; echo
    fi
    if [[ -n "${WIFI_SSID:-}" && -n "${WIFI_PSK:-}" ]]; then
      umask 077
      cat > /etc/netplan/90-wifi-operacional.yaml <<EOF
network:
  version: 2
  renderer: networkd
  wifis:
    ${WIFI_IF}:
      dhcp4: true
      optional: true
      access-points:
        "${WIFI_SSID}":
          password: "${WIFI_PSK}"
EOF
      chmod 600 /etc/netplan/90-wifi-operacional.yaml
      if netplan apply 2>/tmp/netplan.err; then
        ok "Wi-Fi '${WIFI_SSID}' configurado e aplicado (interface ${WIFI_IF})"
      else
        erro "netplan apply falhou -- veja /tmp/netplan.err e confira SSID/senha em /etc/netplan/90-wifi-operacional.yaml"
      fi
    else
      aviso "SSID/senha nao informados -- Wi-Fi nao configurado. Rode de novo com WIFI_SSID=... WIFI_PSK=... ou crie /etc/netplan/90-wifi-operacional.yaml na mao."
    fi
  fi
fi

# --------------------------------------------------------------------------
titulo "2d. Notebook como servidor: tampa e suspensao"
# O servidor e um notebook. Por padrao, fechar a tampa SUSPENDE a maquina --
# o que derrubaria o bot, o SSH e a VPN. Um drop-in do logind ignora a tampa
# em qualquer situacao (bateria, tomada, dock).
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/10-notebook-servidor.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
ok "tampa configurada para NAO suspender (drop-in do logind)"
# reload (nao restart) para nao derrubar a sessao SSH atual
systemctl reload systemd-logind 2>/dev/null || systemctl kill -s HUP systemd-logind 2>/dev/null || true
# Mascara os alvos de sono para a maquina NUNCA dormir sozinha por ociosidade.
if systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target &>/dev/null; then
  ok "suspensao/hibernacao mascaradas (a maquina nao dorme sozinha)"
fi

# --------------------------------------------------------------------------
titulo "2e. Xorg na GPU Intel (evita a Radeon)"
# O Dell tem DUAS GPUs: Intel UHD 620 (driver i915) e AMD Radeon 520 (amdgpu).
# A saida HDMI/TV desses notebooks e cabeada na Intel -- e foi justamente o
# caminho da amdgpu que deu problema no teste do porte (captura preta do Xvfb /
# instabilidade). Este OutputClass forca o Xorg a usar a Intel como GPU PRIMARIA
# (driver 'modesetting', embutido no servidor X) e deixa a Radeon fora do
# caminho de tela. NAO fixo BusID de proposito: casar por MatchDriver "i915" e
# mais robusto que chutar um endereco PCI.
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/20-intel-primary.conf <<'EOF'
Section "OutputClass"
    Identifier "intel-primary"
    MatchDriver "i915"
    Driver "modesetting"
    Option "PrimaryGPU" "yes"
EndSection
EOF
ok "Xorg configurado para usar a GPU Intel (modesetting) na saida da TV"

# --------------------------------------------------------------------------
titulo "3. Usuario de servico"
# HOME=$DESTINO de proposito: o 'operacional' nao tem home propria (--no-create-home),
# e SEM HOME o Chromium do Playwright nao abre -- o chrome_crashpad_handler
# falha com "--database is required" e o processo morre com SIGTRAP (descoberto
# testando o login visivel do CAMPO em 09/08/2026). $DESTINO ja e dono do operacional
# (passo 8), entao serve de HOME sem precisar criar /home/operacional.
if id "$USUARIO" &>/dev/null; then
  ok "usuario '$USUARIO' ja existe"
else
  useradd --system --no-create-home --home-dir "$DESTINO" --shell /usr/sbin/nologin "$USUARIO"
  ok "usuario de servico '$USUARIO' criado (sem login, sem privilegio, HOME=$DESTINO)"
fi

# Grupos video/input/tty: necessarios para o Xorg REAL na TV fisica de producao
# (campo-tv-display.service). Sem 'video' o Xorg nao abre /dev/dri/cardN (a GPU);
# sem 'input' nao le teclado/mouse; 'tty' ajuda a tomar o console. Nao faz mal
# em servidor headless (Xvfb nao usa GPU) -- so nao seria necessario la.
# (Descoberto ao ver que 'groups operacional' so retornava 'operacional'.)
# Grupo 'audio': o alerta sonoro (mpg123 tocando alerta_garantia.mp3 na caixa
# da TV) abre /dev/snd/*, que sao 'root:audio'. Com a sessao grafica ativa o
# logind ate concede ACL, mas depender so disso e fragil -- no grupo audio o
# acesso independe da sessao. (Descoberto 10/08/2026: sem isso o som falha calado.)
for grupo in video input tty audio; do
  if getent group "$grupo" >/dev/null; then
    usermod -aG "$grupo" "$USUARIO"
  fi
done
ok "usuario '$USUARIO' nos grupos: $(id -nG "$USUARIO" | tr ' ' ',')"

# --------------------------------------------------------------------------
titulo "4. Copiando bot/ e site/ para $DESTINO"
mkdir -p "$DESTINO"
for pasta in bot site; do
  if [[ ! -d "$RAIZ/$pasta" ]]; then
    erro "pasta '$pasta/' nao encontrada em $RAIZ -- instalador incompleto?"
    continue
  fi
  mkdir -p "$DESTINO/$pasta"
  # -a preserva permissao/dono na copia; --delete NAO usado de proposito --
  # um arquivo que so existe no destino (ex: node_modules ja instalado numa
  # rodada anterior) sobrevive a uma segunda execucao do instalador.
  rsync -a "$RAIZ/$pasta/" "$DESTINO/$pasta/" 2>/dev/null || cp -r "$RAIZ/$pasta/." "$DESTINO/$pasta/"
  ok "$pasta/ copiado para $DESTINO/$pasta"
done

# --------------------------------------------------------------------------
titulo "5. Ambiente Python (venv)"
if [[ -x "$VENV/bin/python3" ]]; then
  ok "venv ja existe em $VENV"
else
  python3 -m venv "$VENV"
  ok "venv criado em $VENV"
fi

if [[ -f "$RAIZ/requirements.txt" ]]; then
  "$VENV/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
  if "$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$RAIZ/requirements.txt"; then
    ok "dependencias Python instaladas"
  else
    erro "pip install falhou -- rode '$VENV/bin/pip install -r $RAIZ/requirements.txt' na mao para ver o erro completo"
  fi
else
  erro "requirements.txt nao encontrado em $RAIZ"
fi

# --------------------------------------------------------------------------
titulo "6. Chromium do Playwright"
export PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH_PADRAO"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
# --with-deps instala as bibliotecas de sistema que o Chromium headless
# precisa (libnss3, libatk, etc.) -- e por isso o passo 2 nao tenta advinhar
# essa lista na mao: ela muda entre versoes do Playwright e da Ubuntu, e o
# proprio Playwright mantem isso atualizado.
if "$VENV/bin/python3" -m playwright install --with-deps chromium; then
  ok "Chromium instalado em $PLAYWRIGHT_BROWSERS_PATH"
else
  erro "falha ao instalar o Chromium -- rode '$VENV/bin/python3 -m playwright install --with-deps chromium' na mao"
fi
# Dono do cache de browsers precisa ser o usuario que vai RODAR o bot, senao
# o systemd (rodando como 'operacional') nao consegue nem ler os binarios.
chown -R "$USUARIO:$USUARIO" "$PLAYWRIGHT_BROWSERS_PATH"

# --------------------------------------------------------------------------
titulo "7. Ponte do WhatsApp (Node)"
if [[ -f "$DESTINO/bot/package.json" ]]; then
  ( cd "$DESTINO/bot" && npm install --no-audit --no-fund --quiet )
  if [[ $? -eq 0 ]]; then
    ok "node_modules instalado"
  else
    erro "npm install falhou -- rode 'cd $DESTINO/bot && npm install' na mao"
  fi
else
  erro "package.json nao encontrado em $DESTINO/bot"
fi

# --------------------------------------------------------------------------
titulo "8. Pastas de dados"
for sub in dados logs relatorios arquivos_recebidos auth_alertas; do
  mkdir -p "$DESTINO/bot/$sub"
done
for sub in dados logs saida uploads; do
  mkdir -p "$DESTINO/site/$sub"
done
# pasta_database padrao no Linux (site/config/site.json aponta para ca). O
# instalador so CRIA a pasta vazia -- as planilhas do Operacional Database
# (chamados_abertos_field_service.xlsx, base OFS ok.xlsx, etc.) precisam ser
# copiadas para ca na migracao dos dados.
mkdir -p "$DESTINO/database"
chown -R "$USUARIO:$USUARIO" "$DESTINO"
ok "pastas de dados prontas (inclui $DESTINO/database), dono ajustado para '$USUARIO'"
if [[ -z "$(ls -A "$DESTINO/database" 2>/dev/null)" ]]; then
  aviso "$DESTINO/database esta VAZIA -- copie as planilhas do Operacional Database para ca (senao a lista de garantias fica sem dados)."
fi

if [[ ! -f "$DESTINO/bot/vpn_config.json" ]]; then
  aviso "vpn_config.json nao existe em $DESTINO/bot -- crie um com servidor/usuario/senha antes de subir campo-vpn.service"
fi

# --------------------------------------------------------------------------
titulo "9. Ajustando caminhos Windows -> Linux em config/site.json"
# O site.json veio da maquina Windows com pasta_bot apontando para
# C:\caminho\para\campo_bot_telegram -- sem corrigir isso aqui, o site nao acha
# o backlog/estatisticas do bot no caminho novo.
"$VENV/bin/python3" - "$DESTINO" <<'PYEOF'
import json, sys, pathlib
destino = pathlib.Path(sys.argv[1])
arq = destino / "site" / "config" / "site.json"
if not arq.exists():
    print("  [aviso] config/site.json nao existe ainda -- sera criado no primeiro boot do site")
    raise SystemExit(0)
dados = json.loads(arq.read_text(encoding="utf-8"))
antes = dados.get("pasta_bot", "")
dados["pasta_bot"] = str(destino / "bot")
if dados.get("pasta_database", "").startswith(("C:", "c:")):
    print(f"  [aviso] pasta_database ainda aponta para o Windows ({dados['pasta_database']!r}) -- ajuste na mao, nao sei para onde essa pasta foi")
arq.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  [ok]    pasta_bot: {antes!r} -> {dados['pasta_bot']!r}")
PYEOF

# --------------------------------------------------------------------------
titulo "10. Unidades systemd"
if [[ -d "$RAIZ/systemd" ]]; then
  for unidade in "$RAIZ"/systemd/*.service; do
    nome="$(basename "$unidade")"
    cp "$unidade" "/etc/systemd/system/$nome"
    ok "$nome copiado para /etc/systemd/system/"
  done

  if [[ -f "$RAIZ/systemd/painel-tv.xinitrc" ]]; then
    cp "$RAIZ/systemd/painel-tv.xinitrc" "$DESTINO/xinitrc"
    chmod +x "$DESTINO/xinitrc"
    chown "$USUARIO:$USUARIO" "$DESTINO/xinitrc"
    ok "xinitrc do Painel de TV copiado para $DESTINO/xinitrc"
  else
    erro "painel-tv.xinitrc nao encontrado em $RAIZ/systemd -- campo-tv-display.service vai falhar ao iniciar"
  fi

  # Auto-ligar a TV no hotplug do HDMI: o script (rodado pelo hdmi-tv.service,
  # disparado pela regra udev + pelo timer) faz o xrandr no :0. Sem window
  # manager, e ele que arranja as saidas de video quando o cabo entra/sai.
  # Dono ROOT de proposito (nao 'operacional'): o script roda como root (escreve em
  # /sys/class/drm/.../status) -- se fosse dono 'operacional', o proprio usuario
  # sem privilegio que a sessao X usa poderia reescrever um script que o root
  # executa via systemd.
  if [[ -f "$RAIZ/systemd/hdmi-tv.sh" ]]; then
    mkdir -p "$DESTINO/bin"
    install -m 0755 -o root -g root "$RAIZ/systemd/hdmi-tv.sh" "$DESTINO/bin/hdmi-tv.sh"
    ok "hdmi-tv.sh instalado em $DESTINO/bin/ (dono root)"
  else
    erro "hdmi-tv.sh nao encontrado em $RAIZ/systemd -- a TV nao ligara sozinha no HDMI"
  fi
  if [[ -f "$RAIZ/systemd/95-hdmi-tv.rules" ]]; then
    install -m 0644 "$RAIZ/systemd/95-hdmi-tv.rules" /etc/udev/rules.d/95-hdmi-tv.rules
    udevadm control --reload-rules 2>/dev/null || true
    ok "regra udev 95-hdmi-tv.rules instalada (hotplug do HDMI)"
  else
    erro "95-hdmi-tv.rules nao encontrado em $RAIZ/systemd -- o hotplug do HDMI nao sera detectado"
  fi
  # Timer do HDMI: o hotplug via udev sozinho nao e confiavel nesta porta
  # (HPD com defeito -- ver hdmi-tv.sh), entao o timer reforca a cada 2 min
  # e cobre o boot com o cabo ja plugado (xinitrc nao chama mais o script
  # direto -- perdeu o privilegio para escrever no sysfs quando o service
  # virou root-only).
  if [[ -f "$RAIZ/systemd/hdmi-tv.timer" ]]; then
    cp "$RAIZ/systemd/hdmi-tv.timer" /etc/systemd/system/hdmi-tv.timer
    ok "hdmi-tv.timer copiado (checagem a cada 2 min)"
  else
    erro "hdmi-tv.timer nao encontrado em $RAIZ/systemd -- a TV so ligaria pelo hotplug (nao confiavel nesta porta)"
  fi

  # Guardiao externo da VPN: o Restart=always do campo-vpn so cobre o processo
  # MORRER; se o tunel travar com o processo vivo (visto em 11/08: 7h30 fora),
  # nada reinicia. Este timer testa o tunel de fora e reinicia o campo-vpn se
  # estiver morto. O .service e copiado pelo laco *.service acima; falta o
  # script e o .timer (que aquele laco nao pega), e habilitar o timer.
  if [[ -f "$RAIZ/systemd/campo-vpn-watchdog.sh" ]]; then
    mkdir -p "$DESTINO/bin"
    install -m 0755 "$RAIZ/systemd/campo-vpn-watchdog.sh" "$DESTINO/bin/campo-vpn-watchdog.sh"
    ok "campo-vpn-watchdog.sh instalado em $DESTINO/bin/"
  else
    erro "campo-vpn-watchdog.sh nao encontrado em $RAIZ/systemd -- a VPN nao tera guardiao externo"
  fi
  if [[ -f "$RAIZ/systemd/campo-vpn-watchdog.timer" ]]; then
    cp "$RAIZ/systemd/campo-vpn-watchdog.timer" /etc/systemd/system/campo-vpn-watchdog.timer
    ok "campo-vpn-watchdog.timer copiado (checagem a cada 2 min)"
  fi

  systemctl daemon-reload
  for nome in campo-tv-display campo-vpn campo-whatsapp campo-bot operacional-site hdmi-tv; do
    systemctl enable "$nome" &>/dev/null && ok "$nome habilitado (sobe sozinho no boot)"
  done
  # Guardiao da VPN e checagem da TV sao TIMERS, nao services comuns -- cada
  # um habilita separado (nao entram no laco de cima, que so cobre *.service).
  if systemctl enable campo-vpn-watchdog.timer &>/dev/null; then
    ok "campo-vpn-watchdog.timer habilitado (guardiao da VPN)"
  fi
  if systemctl enable hdmi-tv.timer &>/dev/null; then
    ok "hdmi-tv.timer habilitado (checagem da TV a cada 2 min)"
  fi
  echo "  (nada foi INICIADO ainda de proposito -- ver o resumo no final)"
else
  erro "pasta systemd/ nao encontrada em $RAIZ"
fi

# --------------------------------------------------------------------------
titulo "11. Teste de ponta a ponta"
if "$VENV/bin/python3" -c "
import sys
sys.path.insert(0, '$DESTINO/site')
from web.app import app
print(f'  [ok]    site carrega ({sum(1 for r in app.routes if hasattr(r, \"methods\"))} rotas)')
" 2>/tmp/teste_site.err; then
  :
else
  erro "o site nao importou -- $(tail -3 /tmp/teste_site.err | tr '\n' ' ')"
fi

# --------------------------------------------------------------------------
echo
echo "=================================================================="
if [[ ${#FALHAS[@]} -gt 0 ]]; then
  echo "  ${#FALHAS[@]} problema(s) impedem o funcionamento completo:"
  for f in "${FALHAS[@]}"; do echo "    - $f"; done
else
  echo "  INSTALACAO BASE CONCLUIDA"
fi
if [[ ${#AVISOS[@]} -gt 0 ]]; then
  echo
  echo "  ${#AVISOS[@]} aviso(s):"
  for a in "${AVISOS[@]}"; do echo "    - $a"; done
fi
echo
echo "  Antes de iniciar, confira:"
echo "    - Wi-Fi conectado e estavel (iw dev; ping 1.1.1.1). Um servidor SO no"
echo "      Wi-Fi: se cair sem voltar, voce perde SSH/VPN e fica sem acesso."
echo "    - $DESTINO/bot/vpn_config.json existe e tem as credenciais certas"
echo "    - $DESTINO/site/config/site.json: PIN e porta"
echo
echo "  Para subir tudo (nesta ordem):"
echo "    systemctl start campo-tv-display"
echo "    journalctl -u campo-tv-display -f   # confirme que a sessao X subiu na TV antes de seguir"
echo "    systemctl start campo-vpn"
echo "    journalctl -u campo-vpn -f          # confirme que conectou antes de seguir"
echo "    systemctl start campo-whatsapp"
echo "    journalctl -u campo-whatsapp -f     # primeira vez pede QR Code (ver qr_atual.txt)"
echo "    systemctl start campo-bot"
echo "    systemctl start operacional-site"
echo
echo "  Para testar o Painel de TV: mande /exibirpaineltv no grupo e confira"
echo "  se a janela aparece na TV (journalctl -u campo-bot -f mostra erro de"
echo "  DISPLAY se a sessao X nao estiver de pe)."
echo
echo "  Para ver status geral:"
echo "    systemctl status campo-tv-display campo-vpn campo-whatsapp campo-bot operacional-site"
echo "=================================================================="

[[ ${#FALHAS[@]} -eq 0 ]]
