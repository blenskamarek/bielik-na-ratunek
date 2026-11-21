#!/usr/bin/env bash
# Komenda oceny (z RUN.md): przetwarza wszystkie PDF-y z /opt/solution/input do /opt/solution/output.
set -uo pipefail
SOL=/opt/solution
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# klucz do kolejki z modelem Bielik (wymóg organizatora: klucze w /opt/solution/.env)
if [ -f "$SOL/.env" ]; then set -a; . "$SOL/.env"; set +a; fi
mkdir -p "$SOL/output"
cd "$REPO"
exec "$REPO/.venv/bin/python" run.py --wejscie "$SOL/input" --wyjscie "$SOL/output"
