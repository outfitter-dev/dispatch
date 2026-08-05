#!/bin/sh
set -eu

command -v zmx >/dev/null
command -v jq >/dev/null
command -v rg >/dev/null

case "$(zmx version)" in
  *0.6.0*) ;;
  *) printf '%s\n' 'zmx 0.6.0 required' >&2; exit 2 ;;
esac

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
probe_root=$(mktemp -d /tmp/dispatch-zmx-snapshot.XXXXXX)
export ZMX_DIR="$probe_root/zmx"
export ZMX_DIR_MODE=0700
export ZMX_LOG_MODE=0600
name="dispatch-snapshot-$$"
client_pid=

cleanup() {
  zmx kill "$name" >/dev/null 2>&1 || true
  if [ -n "$client_pid" ]; then
    wait "$client_pid" 2>/dev/null || true
  fi
  rm -rf "$probe_root"
}
trap cleanup EXIT INT TERM

zmx attach "$name" "$root/fake_repl.sh" </dev/null >/dev/null 2>&1 &
client_pid=$!

i=0
while [ "$i" -lt 50 ]; do
  zmx list --short 2>/dev/null | rg -qx "$name" && break
  i=$((i + 1))
  sleep 0.05
done
zmx list --short | rg -qx "$name"

printf 'snapshot\r' | zmx send "$name"
i=0
while [ "$i" -lt 50 ]; do
  zmx history "$name" 2>/dev/null | rg -q 'completed:snapshot' && break
  i=$((i + 1))
  sleep 0.05
done

plain=$(zmx history "$name")
vt=$(zmx history "$name" --vt)
html=$(zmx history "$name" --html)
for rendered in "$plain" "$vt" "$html"; do
  printf '%s' "$rendered" | rg -q 'accepted:snapshot'
  printf '%s' "$rendered" | rg -q 'completed:snapshot'
done

zmx kill "$name" >/dev/null
wait "$client_pid" 2>/dev/null || true
client_pid=
if zmx list --short | rg -q "^$name$"; then
  printf '%s\n' "isolated zmx session remained after cleanup: $name" >&2
  exit 1
fi
rm -rf "$probe_root"
trap - EXIT INT TERM

jq -cn '{version:"0.6.0",target:"synthetic",plain:true,vt:true,html:true,cleanup:true}'
