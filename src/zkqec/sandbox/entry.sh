#!/bin/sh
# zkqec sandbox entry (contracts J0/J1). Runs as the first process inside bwrap.
# If ZKQEC_PROXY_PORT is set, start the in-sandbox half of the egress bridge: socat listening on
# 127.0.0.1:$ZKQEC_PROXY_PORT (the sandbox's private loopback) forwarding to the read-only-mounted
# Unix socket /run/zkqec/broker.sock, whose host side injects the job token and reaches the broker.
# Then exec the job command. Nothing else is reachable: the sandbox has no other network route.
set -eu
if [ -n "${ZKQEC_PROXY_PORT:-}" ]; then
  port="$ZKQEC_PROXY_PORT"
  unset ZKQEC_PROXY_PORT
  /usr/bin/socat "TCP-LISTEN:${port},bind=127.0.0.1,fork,reuseaddr" "UNIX-CONNECT:/run/zkqec/broker.sock" \
    </dev/null >/dev/null 2>&1 &
  hex=$(printf '%04X' "$port")
  n=0
  until grep -q ":${hex} 00000000:0000 0A" /proc/net/tcp 2>/dev/null; do
    n=$((n + 1))
    if [ "$n" -gt 500 ]; then
      echo "zkqec-entry: egress bridge did not start" >&2
      exit 97
    fi
    sleep 0.01
  done
fi
exec "$@"
