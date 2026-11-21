"""Punkt wejścia: python run.py --wejscie input --wyjscie output

Struktura: wejscie/<sprawa>/<plik>.pdf  ->  wyjscie/<sprawa>/<plik>.json
Opcje:
  --raport KATALOG  zapisuje raport przejrzystości (typ dokumentu, wartości od modelu i ich weryfikacja)
  --bez-llm         same reguły, bez modelu językowego (to samo co LLM_WYLACZ=1)
  --bez-ocr         bez OCR skanów (to samo co OCR_WYLACZ=1)
"""
import argparse
import logging
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF (odwołanie / pełnomocnictwo / przelew) -> JSON")
    ap.add_argument("--wejscie", type=Path, default=Path("input"))
    ap.add_argument("--wyjscie", type=Path, default=Path("output"))
    ap.add_argument("--raport", type=Path, default=None)
    ap.add_argument("--bez-llm", action="store_true")
    ap.add_argument("--bez-ocr", action="store_true")
    a = ap.parse_args()
    if a.bez_llm:
        os.environ["LLM_WYLACZ"] = "1"
    if a.bez_ocr:
        os.environ["OCR_WYLACZ"] = "1"
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from silnik.rdzen import przetworz_katalog     # po ustawieniu zmiennych środowiskowych

    if not a.wejscie.is_dir():
        print(f"Brak katalogu wejściowego: {a.wejscie}", file=sys.stderr)
        return 2
    n = przetworz_katalog(a.wejscie, a.wyjscie, a.raport)
    print(f"Przetworzono plików PDF: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
