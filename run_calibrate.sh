#!/bin/bash
# Weekly calibration wrapper
export PATH="/Users/marc/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd "$(dirname "$0")"
/usr/bin/python3 calibrate.py >> engine.log 2>&1
