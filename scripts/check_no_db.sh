#!/bin/bash
if git diff --cached --name-only | grep -qE '\.db$|\.db-shm$|\.db-wal$'; then
    echo "ERROR: intentando commitear una base de datos"
    exit 1
fi
exit 0