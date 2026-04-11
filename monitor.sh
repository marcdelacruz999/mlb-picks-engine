#!/bin/bash
cd "$(dirname "$0")"
/usr/bin/python3 monitor.py >> engine.log 2>&1
