#!/bin/bash
# Weekly optimizer wrapper — needs full PATH for claude CLI
export PATH="/Users/marc/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd "$(dirname "$0")"
/usr/bin/python3 optimizer.py >> engine.log 2>&1
