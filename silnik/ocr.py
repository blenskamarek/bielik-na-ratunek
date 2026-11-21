"""OCR dla stron PDF bez warstwy tekstowej (skanów).

Strategia: obraz strony bierzemy bezpośrednio z PDF-a (skan = osadzony obraz, zwykle JPG),
bez renderowania, więc nie trzeba poppler/pymupdf. Silniki OCR, w kolejności:
  1. Tesseract (najlepsza jakość dla języka polskiego; osobny program, nie pakiet pip):
     ścieżka z TESSERACT_CMD, z PATH albo ze standardowej instalacji Windows.
  2. RapidOCR (pakiet pip rapidocr_onnxruntime), jeśli zainstalowany. Uwaga: jego domyślny
     model nie zna polskich znaków diakrytycznych - to tylko awaryjny zapas.
Brak silnika = ostrzeżenie w logu i pusty tekst dla skanu (program działa dalej).

Zmienne środowiskowe: TESSERACT_CMD, TESSDATA_DIR, OCR_JEZYK (domyślnie "pol"),
OCR_SILNIK=tesseract|rapidocr (wymusza silnik), OCR_WYLACZ=1 (wyłącza OCR).
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("odwolania.ocr")

STANDARDOWE_SCIEZKI = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
]
MIN_ZNAKOW_TEKSTU = 50       # strona z mniejszą liczbą znaków jest traktowana jako skan


@lru_cache(maxsize=1)
def _tesseract() -> tuple[str, str | None] | None:
    """(ścieżka do tesseract, katalog tessdata albo None) lub None, gdy brak programu."""
    kandydaci = [os.environ.get("TESSERACT_CMD"), shutil.which("tesseract"), *STANDARDOWE_SCIEZKI]
    for k in kandydaci:
        if k and Path(k).is_file():
            # jawny katalog tessdata: chroni przed błędnie ustawionym TESSDATA_PREFIX
            tessdata = os.environ.get("TESSDATA_DIR")
            if not tessdata and (Path(k).parent / "tessdata").is_dir():
                tessdata = str(Path(k).parent / "tessdata")
            return k, tessdata
    return None


@lru_cache(maxsize=1)
def _rapidocr():
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except ImportError:
        return None
    return RapidOCR()


def dostepny_silnik() -> str | None:
    """Wybrany silnik OCR; OCR_SILNIK=tesseract|rapidocr wymusza konkretny (do testów)."""
    if os.environ.get("OCR_WYLACZ") == "1":
        return None
    wymuszony = os.environ.get("OCR_SILNIK")
    if wymuszony == "rapidocr":
        return "rapidocr" if _rapidocr() else None
    if wymuszony == "tesseract":
        return "tesseract" if _tesseract() else None
    if _tesseract():
        return "tesseract"
    if _rapidocr():
        return "rapidocr"
    return None


def _ocr_tesseract(obraz: bytes, rozszerzenie: str) -> str:
    exe, tessdata = _tesseract()
    jezyk = os.environ.get("OCR_JEZYK", "pol")
    with tempfile.TemporaryDirectory() as tmp:
        plik = Path(tmp) / f"strona{rozszerzenie}"
        plik.write_bytes(obraz)
        # składnia: tesseract <obraz> <wyjście> [opcje]; opcje muszą być PO "stdout"
        cmd = [exe, str(plik), "stdout", "-l", jezyk, "--psm", "3"]
        if tessdata:
            cmd += ["--tessdata-dir", tessdata]
        wynik = subprocess.run(cmd, capture_output=True, timeout=120)
    if wynik.returncode != 0:
        raise RuntimeError(f"tesseract: {wynik.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return wynik.stdout.decode("utf-8", "replace")


def _ocr_rapidocr(obraz: bytes) -> str:
    wynik, _ = _rapidocr()(obraz)
    return "\n".join(linia[1] for linia in (wynik or []))


def popraw_ocr(tekst: str) -> str:
    """Poprawki typowych, jednoznacznych błędów OCR w polskich pismach (tylko strony z OCR)."""
    # forma prawna: "Sp. z 0.0." / "sp. Z o.0." -> "Sp. z o.o."
    tekst = re.sub(r"\b([Ss][Pp])\.\s?[zZ]\s?[0oO]\.\s?[0oO]\.", r"\1. z o.o.", tekst)
    # "@" odczytane jako Q, ©, (OQ, (O - tylko w liniach z adresem e-mail
    def maile(m: re.Match) -> str:
        return re.sub(r"([\w.+-]+)\s?(?:\(OQ|\(O|\(Q|Q|©)\s?([a-z0-9-]+(?:\.[a-z0-9-]+)+)", r"\1@\2", m.group())
    tekst = re.sub(r"^[^\n]*mail[^\n]*$", maile, tekst, flags=re.IGNORECASE | re.MULTILINE)
    # "&" odczytane jako "$" między słowami (np. "Olszewski $ Partnerzy")
    tekst = re.sub(r"(?<=\w) \$ (?=\w)", " & ", tekst)
    # Tesseract rozdziela wiersze pustymi liniami - ujednolicenie z warstwą tekstową PDF
    tekst = re.sub(r"\n[ \t]*(?:\n[ \t]*)+", "\n", tekst.replace("\r\n", "\n"))
    return tekst


def ocr_strony(strona) -> str:
    """OCR największego obrazu osadzonego na stronie pypdf (pusty tekst, gdy brak obrazów)."""
    silnik = dostepny_silnik()
    if not silnik:
        return ""
    try:
        obrazy = list(strona.images)
    except Exception as e:  # noqa: BLE001 - nietypowe kodowanie obrazu nie może przerwać pracy
        log.warning("Nie udało się odczytać obrazów strony: %s", e)
        return ""
    if not obrazy:
        return ""
    najwiekszy = max(obrazy, key=lambda im: len(im.data))
    rozszerzenie = Path(najwiekszy.name).suffix.lower() or ".png"
    dane = najwiekszy.data
    if rozszerzenie not in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}:
        bufor = io.BytesIO()                       # np. JPEG2000/JBIG2 -> PNG (wymaga Pillow)
        najwiekszy.image.save(bufor, format="PNG")
        dane, rozszerzenie = bufor.getvalue(), ".png"
    try:
        if silnik == "tesseract":
            return popraw_ocr(_ocr_tesseract(dane, rozszerzenie))
        return popraw_ocr(_ocr_rapidocr(dane))
    except Exception as e:  # noqa: BLE001 - błąd OCR jednej strony nie przerywa dokumentu
        log.warning("OCR (%s) nie powiódł się: %s", silnik, str(e)[:200])
        return ""
