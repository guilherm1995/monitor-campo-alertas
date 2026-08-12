#!/bin/sh
# Liga a TV (HDMI) na sessao X do Painel (:0) e a deixa preenchendo a tela
# inteira, com a tela do notebook desligada. Chamado pela regra udev
# (95-hdmi-tv.rules, no hotplug) e pelo timer hdmi-tv.timer (a cada 2 min --
# ver o motivo do timer abaixo). Roda como ROOT (hdmi-tv.service sem User=):
# precisa escrever em /sys/class/drm/.../status, algo que so root pode fazer;
# os comandos que tocam o DISPLAY (xrandr, unclutter) sao delegados ao
# usuario 'operacional', dono da sessao X, via runuser.
x() {
    runuser -u operacional -- env DISPLAY=:0 XAUTHORITY=/opt/operacional/.Xauthority HOME=/opt/operacional "$@"
}

sleep 1

MODE_1080="1920x1080_60"
MODELINE_1080="173.00 1920 2048 2248 2576 1080 1083 1088 1120 -hsync +vsync"

hdmi=$(x xrandr --query 2>/dev/null | grep -Eo '^HDMI[^ ]*' | head -1)
[ -z "$hdmi" ] && { echo "hdmi-tv: sem saida HDMI no :0 (X no ar?)"; exit 0; }

estado=$(x xrandr --query 2>/dev/null | awk -v o="$hdmi" '$1==o{print $2}')

# Esta porta (Intel i915) tem o hotplug detect (HPD) com defeito -- visto em
# 11/08/2026: "HPD interrupt storm on connector HDMI-A-1: switching from
# hotplug detection to polling" no dmesg, e o EDID sempre le 0 bytes (a TV
# nunca "aperta a mao" direito com o kernel). Resultado: com o cabo
# fisicamente plugado, o xrandr pode continuar reportando "disconnected"
# para sempre -- e como o polling tambem falha (sem EDID), nem o timer sozinho
# resolveria sem isto. Por isso, se a deteccao normal nao achar nada, FORCAMOS
# via sysfs. Seguro mesmo com o cabo de fato desconectado: nesse caso a TV so
# nao mostra nada (sem sinal fisico chegando), e o bloco de baixo religa o
# notebook se o xrandr não confirmar a saida.
if [ "$estado" != "connected" ]; then
    sysfs=$(ls -d /sys/class/drm/card*-HDMI-A-* 2>/dev/null | head -1)
    if [ -n "$sysfs" ]; then
        echo on > "$sysfs/status" 2>/dev/null
        sleep 1
        estado=$(x xrandr --query 2>/dev/null | awk -v o="$hdmi" '$1==o{print $2}')
    fi
fi

if [ "$estado" = "connected" ]; then
    # Sem EDID real a TV nunca anuncia um modo 1080p pronto -- registra o CVT
    # padrao sempre (idempotente: se ja existe, xrandr so reclama no stderr,
    # que suprimimos). --scale-from mantem a area logica em 1366x768 (o
    # tamanho que o Painel de TV/tkinter usa ao abrir) e a HDMI da upscale
    # para 1920x1080 -- assim o painel preenche a TV inteira em vez de ficar
    # so no canto superior esquerdo. (Validado ao vivo com o usuario em
    # 11/08/2026.)
    x xrandr --newmode "$MODE_1080" $MODELINE_1080 >/dev/null 2>&1
    x xrandr --addmode "$hdmi" "$MODE_1080" >/dev/null 2>&1

    edp_ligada=$(x xrandr --query 2>/dev/null | awk '/^eDP-1/ && /[0-9]+x[0-9]+\+[0-9]+\+[0-9]+/{print "sim"}')
    hdmi_ok=$(x xrandr --query 2>/dev/null | awk -v o="$hdmi" '$0 ~ "^"o" connected primary"{print "sim"}')

    if [ "$edp_ligada" = "sim" ] || [ "$hdmi_ok" != "sim" ]; then
        if x xrandr --output "$hdmi" --mode "$MODE_1080" --scale-from 1366x768 --primary --pos 0x0 \
                    --output eDP-1 --off; then
            echo "hdmi-tv: $hdmi ligada -> TV em 1080p (upscale de 1366x768), tela do notebook desligada"
        else
            echo "hdmi-tv: falha ao configurar $hdmi -- religando a tela do notebook por seguranca"
            x xrandr --output eDP-1 --auto --primary --output "$hdmi" --off
        fi
    fi
else
    # Sem HDMI de jeito nenhum (nem forcando) -- garante que o notebook NAO
    # fique as escuras (eDP desligado + HDMI sem sinal = nada visivel).
    edp_ligada=$(x xrandr --query 2>/dev/null | awk '/^eDP-1/ && /[0-9]+x[0-9]+\+[0-9]+\+[0-9]+/{print "sim"}')
    if [ "$edp_ligada" != "sim" ]; then
        x xrandr --output eDP-1 --auto --primary && echo "hdmi-tv: sem HDMI -> voltando pra tela do notebook"
    fi
fi

# Esconde o cursor do mouse na TV (idle 0 = some assim que parar de mexer).
# Idempotente: so sobe uma instancia.
if ! pgrep -u operacional -x unclutter >/dev/null 2>&1; then
    x unclutter -idle 0 -root >/dev/null 2>&1 &
fi
