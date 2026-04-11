#!/bin/bash
# Wrapper for launchd — handles working directory and log appending
cd "$(dirname "$0")"
/usr/bin/python3 engine.py "$@" >> engine.log 2>&1
