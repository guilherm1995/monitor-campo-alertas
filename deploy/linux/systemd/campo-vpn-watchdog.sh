#!/bin/sh
# Guardiao EXTERNO da VPN. Roda a cada 2 min (campo-vpn-watchdog.timer).
#
# Por que existe: em 11/08/2026 a VPN ficou 7h30 fora sem reconectar -- o
# processo do openconnect ficou VIVO mas com o tunel travado (rota default via
# tun0 vira buraco negro no modo tunel-cheio). Nesse estado o systemd nao
# reinicia (o processo nao morreu) e o teste de saude interno do wrapper nao
# resolveu. Este guardiao testa o tunel de FORA e, se estiver morto, faz um
# 'systemctl restart campo-vpn' -- que derruba o tun0 (a rota default volta pro
# enp2s0) e reconecta limpo. Independe de qualquer thread do processo da VPN.
ALVO="https://campo.provedor.example/login/"
MARK=/run/campo-vpn-watchdog.last

# Anti-rajada: se ja reiniciou nos ultimos 3 min, da tempo de assentar antes
# de considerar reiniciar de novo (reconexao leva ~5-10s).
if [ -f "$MARK" ]; then
    agora=$(date +%s)
    ult=$(cat "$MARK" 2>/dev/null || echo 0)
    [ $((agora - ult)) -lt 180 ] && exit 0
fi

# 3 checagens (com 5s entre elas). Basta UMA responder para considerar o tunel
# vivo -- so reinicia se as tres falharem, para nao reagir a um soluco isolado.
i=1
while [ "$i" -le 3 ]; do
    if curl -s -o /dev/null --max-time 8 -A "campo-vpn-watchdog" "$ALVO"; then
        exit 0
    fi
    [ "$i" -lt 3 ] && sleep 5
    i=$((i + 1))
done

logger -t campo-vpn-watchdog "tunel sem resposta em 3 checagens -> systemctl restart campo-vpn"
date +%s > "$MARK"
systemctl restart campo-vpn
