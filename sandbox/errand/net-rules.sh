#!/bin/sh
# OpenMousse errand sandbox: host firewall rules for the Docker network mousse-errand (bridge br-mousse-err, 172.30.99.0/24).
# Installed as /usr/local/sbin/openmousse-errand-net and run by openmousse-errand-net.service (root, after docker).
#   up    nothing on the bridge is forwarded anywhere (DOCKER-USER); towards this host only Doorman's port is open (INPUT),
#         plus replies to connections the host opened (OpenClaw reaching the browser's CDP port through Docker's port mapping)
#   down  removes them
# The network itself also has no NAT and no outside DNS: these rules are the second lock, not the only one.
set -eu
BR="${MOUSSE_ERRAND_BRIDGE:-br-mousse-err}"
GW="${MOUSSE_ERRAND_GATEWAY:-172.30.99.1}"
PORT="${MOUSSE_SENTINEL_PORT:-3128}"

rule() {  # rule <chain> <position> <args…>: add once
  chain="$1"; pos="$2"; shift 2
  iptables -C "$chain" "$@" 2>/dev/null || iptables -I "$chain" "$pos" "$@"
}
drop() {  # drop <chain> <args…>: remove every copy
  chain="$1"; shift
  while iptables -C "$chain" "$@" 2>/dev/null; do iptables -D "$chain" "$@"; done
}

case "${1:-up}" in
  up)
    iptables -L DOCKER-USER -n >/dev/null 2>&1 || iptables -N DOCKER-USER
    rule DOCKER-USER 1 -i "$BR" -j DROP
    rule DOCKER-USER 1 -o "$BR" -m conntrack --ctstate NEW -j DROP
    # INPUT: order matters, so (re)insert the three at the top in reverse
    rule INPUT 1 -i "$BR" -j DROP
    rule INPUT 1 -i "$BR" -p tcp -d "$GW" --dport "$PORT" -j ACCEPT
    rule INPUT 1 -i "$BR" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    ;;
  down)
    drop DOCKER-USER -i "$BR" -j DROP
    drop DOCKER-USER -o "$BR" -m conntrack --ctstate NEW -j DROP
    drop INPUT -i "$BR" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    drop INPUT -i "$BR" -p tcp -d "$GW" --dport "$PORT" -j ACCEPT
    drop INPUT -i "$BR" -j DROP
    ;;
  status)
    iptables -S DOCKER-USER 2>/dev/null | grep -- "$BR" || true
    iptables -S INPUT | grep -- "$BR" || true
    ;;
  *) echo "usage: $0 up|down|status" >&2; exit 2 ;;
esac
