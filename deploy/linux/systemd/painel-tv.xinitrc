#!/bin/sh
# Sessao X minima que fica sempre ligada na TV/monitor fisico conectado na
# maquina de producao. Nao roda window manager nem abre nada sozinha -- so
# desliga blank/DPMS/screensaver (senao a TV apagaria a tela sozinha depois
# de uns minutos sem input de mouse/teclado, coisa que nunca existiu nesta
# maquina) e fica esperando.
#
# Quem abre a janela de verdade e o campo-bot.service, rodando como processo
# SEPARADO no MESMO :0 (ve DISPLAY/XAUTHORITY em campo-bot.service) quando
# alguem pede o painel pelo grupo (/exibirpaineltv). Dois processos, um
# display -- por isso este script so segura a sessao viva, ele nao e o dono
# da janela.
xset s off
xset s noblank
xset -dpms

# A TV (HDMI) e ligada/espelhada pelo hdmi-tv.service, que precisa rodar como
# ROOT (escreve em /sys/class/drm/.../status -- a porta tem HPD defeituoso,
# ver hdmi-tv.sh). Este xinitrc roda como 'operacional' (sem privilegio para isso),
# entao NAO chama o script direto -- quem aciona e o hdmi-tv.timer (10s apos
# o boot) e a regra udev 95-hdmi-tv.rules (hotplug), os dois via systemd.

# startx encerra o :0 assim que o processo do xinitrc termina -- sleep
# infinity mantem a sessao no ar indefinidamente sem gastar nada.
exec sleep infinity
