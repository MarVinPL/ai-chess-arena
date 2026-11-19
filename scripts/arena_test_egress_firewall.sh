#!/bin/sh
# Owns only uniquely named arena-test interface firewall chains.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
ACTION=${1:-}
IFACE=at-egress
CONTAINER_IP=10.203.181.2
HOST_IP=10.203.181.1
PROXY_PORT=18765
IN_CHAIN=ARENA_TEST_EGRESS_IN
FWD_CHAIN=ARENA_TEST_EGRESS_FWD

if [ "$(id -u)" -ne 0 ]; then echo "must run as root" >&2; exit 1; fi
for tool in ip iptables ip6tables flock; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 1; }
done
# Serialize apply/remove so concurrent invocations cannot race chain creation.
exec 9>/run/lock/arena-test-egress-firewall.lock
flock -n 9 || { echo "another firewall operation is in progress" >&2; exit 1; }

chain_exists() { "$1" -w -S "$2" >/dev/null 2>&1; }
remove_family() {
  cmd=$1
  while "$cmd" -w -C INPUT -i "$IFACE" -j "$IN_CHAIN" 2>/dev/null; do "$cmd" -w -D INPUT -i "$IFACE" -j "$IN_CHAIN"; done
  while "$cmd" -w -C FORWARD -i "$IFACE" -j "$FWD_CHAIN" 2>/dev/null; do "$cmd" -w -D FORWARD -i "$IFACE" -j "$FWD_CHAIN"; done
  while "$cmd" -w -C FORWARD -o "$IFACE" -j "$FWD_CHAIN" 2>/dev/null; do "$cmd" -w -D FORWARD -o "$IFACE" -j "$FWD_CHAIN"; done
  if chain_exists "$cmd" "$IN_CHAIN"; then "$cmd" -w -F "$IN_CHAIN"; "$cmd" -w -X "$IN_CHAIN"; fi
  if chain_exists "$cmd" "$FWD_CHAIN"; then "$cmd" -w -F "$FWD_CHAIN"; "$cmd" -w -X "$FWD_CHAIN"; fi
}

case "$ACTION" in
  apply)
    # Install interface-name rules before the veth is created when possible,
    # so there is no unfiltered window during container startup. If it already
    # exists, refuse an unexpected address rather than protecting the wrong link.
    if ip link show dev "$IFACE" >/dev/null 2>&1; then
      ip addr show dev "$IFACE" | grep -Fq "$HOST_IP/30" || { echo "$IFACE lacks $HOST_IP/30; refusing" >&2; exit 1; }
    else
      echo "$IFACE is absent; preparing interface-specific rules before creation"
    fi
    for cmd in iptables ip6tables; do
      for chain in "$IN_CHAIN" "$FWD_CHAIN"; do
        if chain_exists "$cmd" "$chain"; then echo "$cmd $chain already exists; inspect/remove first" >&2; exit 1; fi
      done
      if "$cmd" -w -C INPUT -i "$IFACE" -j "$IN_CHAIN" 2>/dev/null; then
        echo "$cmd has a pre-existing INPUT jump; refusing" >&2; exit 1
      fi
      if "$cmd" -w -C FORWARD -i "$IFACE" -j "$FWD_CHAIN" 2>/dev/null; then
        echo "$cmd has a pre-existing FORWARD-in jump; refusing" >&2; exit 1
      fi
      if "$cmd" -w -C FORWARD -o "$IFACE" -j "$FWD_CHAIN" 2>/dev/null; then
        echo "$cmd has a pre-existing FORWARD-out jump; refusing" >&2; exit 1
      fi
    done
    # Any failure after the first mutation rolls back only these uniquely
    # named chains and their interface-specific jumps in both address families.
    rollback() { remove_family iptables || true; remove_family ip6tables || true; }
    rollback_exit() {
      rc=$?
      trap - EXIT HUP INT TERM
      if [ "$rc" -ne 0 ]; then rollback; fi
      exit "$rc"
    }
    trap rollback_exit EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    iptables -w -N "$IN_CHAIN"
    iptables -w -N "$FWD_CHAIN"
    iptables -w -A "$IN_CHAIN" -s "$CONTAINER_IP" -d "$HOST_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT
    iptables -w -A "$IN_CHAIN" -j DROP
    iptables -w -A "$FWD_CHAIN" -j DROP
    iptables -w -I INPUT 1 -i "$IFACE" -j "$IN_CHAIN"
    iptables -w -I FORWARD 1 -i "$IFACE" -j "$FWD_CHAIN"
    iptables -w -I FORWARD 1 -o "$IFACE" -j "$FWD_CHAIN"

    ip6tables -w -N "$IN_CHAIN"
    ip6tables -w -N "$FWD_CHAIN"
    ip6tables -w -A "$IN_CHAIN" -j DROP
    ip6tables -w -A "$FWD_CHAIN" -j DROP
    ip6tables -w -I INPUT 1 -i "$IFACE" -j "$IN_CHAIN"
    ip6tables -w -I FORWARD 1 -i "$IFACE" -j "$FWD_CHAIN"
    ip6tables -w -I FORWARD 1 -o "$IFACE" -j "$FWD_CHAIN"
    trap - HUP INT TERM EXIT
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
