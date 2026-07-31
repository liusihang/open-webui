#!/usr/bin/env bash
set -euo pipefail

rm -f \
    /tmp/v011-tool-dropdown-auth-state.json \
    /tmp/v011-tool-dropdown-create-auth.py \
    /tmp/v011-tool-dropdown-mint-auth-state.sh

test ! -e /tmp/v011-tool-dropdown-auth-state.json
test ! -e /tmp/v011-tool-dropdown-create-auth.py
test ! -e /tmp/v011-tool-dropdown-mint-auth-state.sh
printf 'temporary browser authentication files removed\n'
