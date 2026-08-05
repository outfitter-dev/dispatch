#!/bin/sh
set -eu

trap 'printf "interrupted\r\n"' INT
printf 'ready\r\n'

while IFS= read -r line; do
  printf 'accepted:%s\r\n' "$line"
  sleep 1
  printf 'completed:%s\r\n' "$line"
done
