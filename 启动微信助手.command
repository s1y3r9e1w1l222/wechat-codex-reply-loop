#!/bin/bash
set -euo pipefail
umask 077
TASK_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$TASK_ROOT"
if [ ! -f config.json ]; then
  cp config.example.json config.json
fi
export PYTHONPATH="$TASK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
# Finder-launched processes may omit the existing Homebrew / Node / Codex paths.
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
python3 -m wechat_autoreply --config "$TASK_ROOT/config.json" run
