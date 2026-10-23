#!/bin/bash
set -euo pipefail
umask 077
TASK_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$TASK_ROOT"
if [ ! -f config.json ]; then
  cp config.example.json config.json
fi
export PYTHONPATH="$TASK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m wechat_autoreply --config "$TASK_ROOT/config.json" run
