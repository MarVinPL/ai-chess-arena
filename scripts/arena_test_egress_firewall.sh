#!/bin/sh
# Owns only uniquely named arena-test interface firewall chains.
set -eu
ACTION="$1"
IFACE=at-egress
CONTAINER_IP=10.203.181.2
HOST_IP=10.203.181.1
PROXY_PORT=18765
IN_CHAIN=ARENA_TEST_EGRESS_IN
FWD_CHAIN=ARENA_TEST_EGRESS_FWD
if [ "$(id -u)" -ne 0 ]; then echo "must run as root" >&2; exit 1; fi
for tool in ip iptables ip6tables; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 1; }
done
remove_family() {
  cmd=$1
  while "$cmd" -C INPUT -i "$IFACE" -j "$IN_CHAIN" 2>/dev/null; do "$cmd" -D INPUT -i "$IFACE" -j "$IN_CHAIN"; done
  while "$cmd" -C FORWARD -i "$IFACE" -j "$FWD_CHAIN" 2>/dev/null; do "$cmd" -D FORWARD -i "$IFACE" -j "$FWD_CHAIN"; done
  while "$cmd" -C FORWARD -o "$IFACE" -j "$FWD_CHAIN" 2>/dev/null; do "$cmd" -D FORWARD -o "$IFACE" -j "$FWD_CHAIN"; done
  "$cmd" -F "$IN_CHAIN" 2>/dev/null || true
  "$cmd" -X "$IN_CHAIN" 2>/dev/null || true
  "$cmd" -F "$FWD_CHAIN" 2>/dev/null || true
  "$cmd" -X "$FWD_CHAIN" 2>/dev/null || true
}
case "$ACTION" in
  apply)
    ip link show dev "$IFACE" >/dev/null 2>&1 || { echo "$IFACE is not present; refusing" >&2; exit 1; }
    ip addr show dev "$IFACE" | grep -Fq "$HOST_IP/30" || { echo "$IFACE lacks $HOST_IP/30; refusing" >&2; exit 1; }
    for cmd in iptables ip6tables; do
      "$cmd" -S "$IN_CHAIN" >/dev/null 2>&1 && { echo "$cmd $IN_CHAIN already exists; inspect/remove first" >&2; exit 1; }
      "$cmd" -S "$FWD_CHAIN" >/dev/null 2>&1 && { echo "$cmd $FWD_CHAIN already exists; inspect/remove first" >&2; exit 1; }
    done
    iptables -w -N "$IN_CHAIN"
    iptables -w -N "$FWD_CHAIN"
    iptables -A "$IN_CHAIN" -s "$CONTAINER_IP" -d "$HOST_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT
    iptables -A "$IN_CHAIN" -j DROP
    iptables -A "$FWD_CHAIN" -j DROP
    iptables -C INPUT -i "$IFACE" -j "$IN_CHAIN" 2>/dev/null || iptables -I INPUT 1 -i "$IFACE" -j "$IN_CHAIN"
    iptables -C FORWARD -i "$IFACE" -j "$FWD_CHAIN" 2>/dev/null || iptables -I FORWARD 1 -i "$IFACE" -j "$FWD_CHAIN"
    iptables -C FORWARD -o "$IFACE" -j "$FWD_CHAIN" 2>/dev/null || iptables -I FORWARD 1 -o "$IFACE" -j "$FWD_CHAIN"
    ip6tables -w -N "$IN_CHAIN"
    ip6tables -w -N "$FWD_CHAIN"
    ip6tables -A "$IN_CHAIN" -j DROP
    ip6tables -A "$FWD_CHAIN" -j DROP
    ip6tables -C INPUT -i "$IFACE" -j "$IN_CHAIN" 2>/dev/null || ip6tables -I INPUT 1 -i "$IFACE" -j "$IN_CHAIN"
    ip6tables -C FORWARD -i "$IFACE" -j "$FWD_CHAIN" 2>/dev/null || ip6tables -I FORWARD 1 -i "$IFACE" -j "$FWD_CHAIN"
    ip6tables -C FORWARD -o "$IFACE" -j "$FWD_CHAIN" 2>/dev/null || ip6tables -I FORWARD 1 -o "$IFACE" -j "$FWD_CHAIN"
    ;;
  remove)
    remove_family iptables
    remove_family ip6tables
    ;;
  *)
    echo "usage: $0 apply|remove" >&2
    exit 2
    ;;
esac
