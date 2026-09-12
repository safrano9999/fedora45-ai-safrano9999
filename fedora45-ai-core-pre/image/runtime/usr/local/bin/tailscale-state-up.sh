#!/usr/bin/env bash
set -euo pipefail

log() { printf '[tailscale] %s\n' "$*"; }

state_dir="${TS_STATE_DIR:-/var/lib/tailscale}"
state_file="${state_dir}/tailscaled.state"
up_timeout="${TS_UP_TIMEOUT:-30}"
up_args=(up)
extra_args=()
has_accept_routes=false
has_accept_dns=false

if [ -n "${TS_EXTRA_ARGS:-}" ]; then
    read -r -a extra_args <<< "$TS_EXTRA_ARGS"
    for arg in "${extra_args[@]}"; do
        case "$arg" in
            --accept-routes|--accept-routes=*) has_accept_routes=true ;;
            --accept-dns|--accept-dns=*) has_accept_dns=true ;;
        esac
    done
fi
$has_accept_routes || up_args+=(--accept-routes)
$has_accept_dns || up_args+=(--accept-dns)
up_args+=("${extra_args[@]}")
[ -z "${TS_HOSTNAME:-}" ] || up_args+=(--hostname="$TS_HOSTNAME")

backend_state=""
if [ -s "$state_file" ]; then
    for _ in {1..50}; do
        backend_state="$(tailscale status --json 2>/dev/null \
            | python3 -c 'import json, sys; print(json.load(sys.stdin).get("BackendState", ""))' \
            2>/dev/null || true)"
        [ -n "$backend_state" ] && break
        sleep 0.1
    done
fi

case "$backend_state" in
    Running|Stopped)
        if timeout "$up_timeout" tailscale "${up_args[@]}"; then
            log "reused persistent state${TS_HOSTNAME:+ for $TS_HOSTNAME}"
            exit 0
        fi
        log "persistent state failed; trying auth key fallback"
        ;;
esac

if [ -z "${TS_AUTHKEY:-}" ]; then
    log "no reusable state and TS_AUTHKEY is empty; skipping"
    exit 0
fi

if timeout "$up_timeout" tailscale "${up_args[@]}" --authkey="$TS_AUTHKEY"; then
    log "authenticated and stored state in $state_file"
    exit 0
fi

log "authentication failed"
exit 1
