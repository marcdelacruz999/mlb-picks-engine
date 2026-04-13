#!/bin/bash
# Wrapper for launchd — export DB snapshot and push to repo before remote CEO fires
cd "$(dirname "$0")"
/usr/bin/python3 export_db_snapshot.py >> engine.log 2>&1
