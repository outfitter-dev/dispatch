#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  printf 'usage: %s OUTPUT receipt|block-prompt|continue-stop|fail|timeout|preflight\n' "$0" >&2
  exit 2
fi

output=$1
mode=$2
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
capture=$root/hook_capture.sh
tmp=$output.tmp.$$
trap 'rm -f "$tmp"' EXIT INT TERM
umask 077

base_hooks=$(jq -cn --arg command "$capture" '
  def command($value; $timeout):
    {type:"command", command:$value, timeout:$timeout};
  {
    SessionStart:[{hooks:[command($command; 5)]}],
    UserPromptSubmit:[{hooks:[command($command; 5)]}],
    Stop:[{hooks:[command($command; 5)]}],
    StopFailure:[{hooks:[command($command; 5)]}],
    SessionEnd:[{hooks:[command($command; 5)]}]
  }
')

case $mode in
  receipt)
    jq -cn --argjson hooks "$base_hooks" '{hooks:$hooks}' > "$tmp"
    ;;
  block-prompt)
    jq -cn --argjson hooks "$base_hooks" \
      --arg command "$root/hook_block_prompt.sh" '
      ($hooks | .UserPromptSubmit[0].hooks +=
        [{type:"command",command:$command,timeout:5}])
      | {hooks:.}
    ' > "$tmp"
    ;;
  continue-stop)
    jq -cn --argjson hooks "$base_hooks" \
      --arg command "$root/hook_continue_once.sh" '
      ($hooks | .Stop[0].hooks +=
        [{type:"command",command:$command,timeout:5}])
      | {hooks:.}
    ' > "$tmp"
    ;;
  fail)
    jq -cn --argjson hooks "$base_hooks" --arg command "$root/hook_fail.sh" '
      ($hooks | .UserPromptSubmit[0].hooks +=
        [{type:"command",command:$command,timeout:5}])
      | {hooks:.}
    ' > "$tmp"
    ;;
  timeout)
    jq -cn --argjson hooks "$base_hooks" \
      --arg command "$root/hook_timeout.sh" '
      ($hooks | .UserPromptSubmit[0].hooks +=
        [{type:"command",command:$command,timeout:1}])
      | {hooks:.}
    ' > "$tmp"
    ;;
  preflight)
    : "${DISPATCH_CLAUDE_PREFLIGHT_NONCE:?set DISPATCH_CLAUDE_PREFLIGHT_NONCE}"
    command="DISPATCH_CLAUDE_PREFLIGHT_NONCE=$DISPATCH_CLAUDE_PREFLIGHT_NONCE $root/hook_preflight.sh"
    jq -cn --arg command "$command" '
      {hooks:{SessionStart:[{hooks:[
        {type:"command",command:$command,timeout:5}
      ]}]}}
    ' > "$tmp"
    ;;
  *)
    printf 'unknown mode: %s\n' "$mode" >&2
    exit 2
    ;;
esac

chmod 600 "$tmp"
mv "$tmp" "$output"
trap - EXIT INT TERM
