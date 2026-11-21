#!/usr/bin/env bash
# Jednorazowa instalacja na maszynie oceny (Ubuntu 24.04, Python 3.12, bez GPU).
# Uruchom z katalogu repozytorium:  cd /opt/solution/repo && bash vm/instaluj.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOL=/opt/solution

echo "== Tesseract OCR z językiem polskim"
sudo apt-get update -y
sudo apt-get install -y tesseract-ocr tesseract-ocr-pol python3-venv

echo "== Środowisko Python (.venv) i zależności"
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install --upgrade pip
"$REPO/.venv/bin/pip" install -r "$REPO/requirements.txt"

echo "== RUN.md i .env w $SOL"
cp "$REPO/vm/RUN.md" "$SOL/RUN.md"
if [ ! -f "$SOL/.env" ]; then
  printf 'LLM_API_KEY=tutaj-wpisz-klucz\n' > "$SOL/.env"
  echo "   Utworzono $SOL/.env - wpisz w nim klucz klienta kolejki (LLM_API_KEY=...)"
fi
mkdir -p "$SOL/input" "$SOL/output"

echo "== Test: przykładowe PDF-y z repozytorium"
TMP=$(mktemp -d)
mkdir -p "$TMP/in/sprawa"
cp "$REPO"/typy/_pokaz_ogloszenie_ted/przyklady/*.pdf "$TMP/in/sprawa/" 2>/dev/null || true
LLM_WYLACZ=1 "$REPO/.venv/bin/python" "$REPO/run.py" --wejscie "$TMP/in" --wyjscie "$TMP/out"
ls -R "$TMP/out"
"$REPO/.venv/bin/python" -m unittest discover -s "$REPO/tests" 2>&1 | tail -3
tesseract --list-langs | grep -q pol && echo "Tesseract: język pol OK"
echo "== Gotowe. Sprawdź jak oceniający: wrzuć PDF-y do $SOL/input, potem: cd $SOL && sudo bash repo/vm/uruchom.sh"
