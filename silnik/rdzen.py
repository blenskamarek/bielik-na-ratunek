"""Rdzeń: odczyt PDF, rejestr typów dokumentów, klasyfikacja, przetwarzanie katalogów.

Nowy typ dokumentu = nowy katalog typy/<nazwa>/ z plikiem typ.yaml (klasyfikacja + reguły pól,
patrz silnik/konfig.py i typy/_szablon/typ.yaml). Bez zmian w kodzie.
Dla trudnych przypadków można dodać ekstraktor.py z funkcją ekstrakcja(tekst) -> dict
(ma wtedy pierwszeństwo). Starszy układ typ.json + schemat.json nadal działa.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import logging
import os
import re
import traceback
import unicodedata
from pathlib import Path

import yaml
from pypdf import PdfReader

from silnik import konfig, llm, ocr

log = logging.getLogger("odwolania")
KATALOG_TYPOW = Path(__file__).resolve().parent.parent / "typy"


def czytaj_tekst(sciezka: Path) -> str:
    """Tekst PDF: warstwa tekstowa, a dla stron-skanów (prawie bez tekstu) OCR.

    CACHE_TEKSTU=<katalog> (opcjonalnie, do pracy nad regułami): tekst zapisywany wg skrótu pliku.
    """
    cache = os.environ.get("CACHE_TEKSTU")
    if cache:
        import hashlib
        klucz = hashlib.sha256(sciezka.read_bytes()).hexdigest()[:32] + f"_{ocr.dostepny_silnik()}"
        plik = Path(cache) / f"{klucz}.txt"
        if plik.exists():
            return plik.read_text(encoding="utf-8")
        tekst = _czytaj_tekst(sciezka)
        plik.parent.mkdir(parents=True, exist_ok=True)
        plik.write_text(tekst, encoding="utf-8")
        return tekst
    return _czytaj_tekst(sciezka)


def _czytaj_tekst(sciezka: Path) -> str:
    r = PdfReader(str(sciezka))
    strony, ocr_uzyty = [], 0
    for s in r.pages:
        t = s.extract_text() or ""
        if len(t.strip()) < ocr.MIN_ZNAKOW_TEKSTU:
            t_ocr = ocr.ocr_strony(s)
            if len(t_ocr.strip()) > len(t.strip()):
                t, ocr_uzyty = t_ocr, ocr_uzyty + 1
        strony.append(t)
    if ocr_uzyty:
        log.info("%s: OCR %d z %d stron (%s)", sciezka.name, ocr_uzyty, len(r.pages), ocr.dostepny_silnik())
    elif not "".join(strony).strip() and not ocr.dostepny_silnik():
        log.warning("%s: brak tekstu i brak silnika OCR (zainstaluj Tesseract z językiem pol)", sciezka.name)
    tekst = "\n".join(strony)
    # jednolite końce linii (Tesseract na Windows zwraca \r\n, strony skanu kończy \f)
    tekst = tekst.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    for znak in ("\u00a0", "\u202f"):
        tekst = tekst.replace(znak, " ")
    tekst = tekst.replace("\u200b", "")
    return tekst


def _zloz(tekst: str) -> str:
    """Postać do porównań: małe litery, bez białych znaków i bez polskich znaków diakrytycznych."""
    t = unicodedata.normalize("NFKD", tekst.casefold().replace("ł", "l"))
    t = "".join(z for z in t if not unicodedata.combining(z))
    return re.sub(r"\s+", "", t)


def _szkielet(konf_pola: dict, konf_llm: dict) -> dict:
    """Pusta struktura wyniku ze ścieżek pól (reguły + llm): null dla wartości, [] dla list."""
    w: dict = {}
    for sciezka, regula in (konf_pola.get("pola") or {}).items():
        konfig._ustaw(w, sciezka, [] if regula.get("wszystkie") else None)
    for sciezka in (konf_pola.get("listy") or {}):
        konfig._ustaw(w, sciezka, [])
    for sciezka, spec in (konf_llm.get("pola") or {}).items():
        konfig._ustaw(w, sciezka, [] if spec.get("typ") == "lista" else None)
    return w


def _scal(cel: dict, zrodlo: dict) -> None:
    """Wpisuje wartości ze źródła do celu (rekurencyjnie dla słowników)."""
    for k, v in zrodlo.items():
        if isinstance(v, dict) and isinstance(cel.get(k), dict):
            _scal(cel[k], v)
        else:
            cel[k] = v


class Typ:
    """Typ dokumentu wczytany z katalogu: typ.yaml (zalecany) albo typ.json + schemat.json."""

    def __init__(self, katalog: Path):
        self.nazwa = katalog.name
        self.konf_pola: dict = {}
        self.schemat: dict = {}
        if (katalog / "typ.yaml").exists():
            konf = yaml.safe_load((katalog / "typ.yaml").read_text(encoding="utf-8-sig")) or {}
            self.konf = konf.get("klasyfikacja") or {}
            self.konf_pola = {k: konf[k] for k in ("sekcje", "pola", "listy") if k in konf}
            konfig.sprawdz_konfiguracje(self.konf_pola)
        else:
            self.konf = json.loads((katalog / "typ.json").read_text(encoding="utf-8-sig"))
            self.schemat = json.loads((katalog / "schemat.json").read_text(encoding="utf-8-sig"))
        if not self.konf.get("slowa_kluczowe"):
            raise konfig.BladKonfiguracji(f"{katalog.name}: brak klasyfikacja.slowa_kluczowe")
        self.ekstraktor = None
        plik = katalog / "ekstraktor.py"                  # opcjonalny moduł dla trudnych przypadków
        if plik.exists():
            spec = importlib.util.spec_from_file_location(f"typy_{self.nazwa}", plik)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.ekstraktor = mod.ekstrakcja
        # opcjonalnie: pola, które model językowy może uzupełnić - sekcja "llm:" w typ.yaml
        # albo osobny plik typy/<nazwa>/llm.yaml
        self.konf_llm: dict = {}
        if (katalog / "typ.yaml").exists() and konf.get("llm"):
            self.konf_llm = konf["llm"]
        elif (katalog / "llm.yaml").exists():
            self.konf_llm = yaml.safe_load((katalog / "llm.yaml").read_text(encoding="utf-8-sig")) or {}
        if self.konf_llm:
            llm.sprawdz_konfiguracje_llm(self.konf_llm)
        # pełna struktura wyniku: każde pole z reguł i z llm ma klucz (null / pusta lista)
        self.szkielet = _szkielet(self.konf_pola, self.konf_llm)

    def punkty(self, tekst: str) -> int:
        # porównanie bez białych znaków (tytuły rozstrzelone "P E Ł N O M O C N I C T W O")
        # i bez polskich znaków (OCR bywa ich pozbawiony: "Odwolujacy")
        zwarty = _zloz(tekst[: self.konf.get("ogranicz_do_znakow", 600)])
        # warunek konieczny: sygnał typu (np. tytuł) na początku dokumentu; odrzuca dokumenty obce,
        # które tylko wspominają słowa kluczowe gdzieś w treści (np. ogłoszenie TED)
        wymagane = self.konf.get("wymagane_na_poczatku")
        if wymagane:
            poczatek = zwarty[: self.konf.get("okno_poczatku", 2000)]
            if not any(_zloz(s) in poczatek for s in wymagane):
                return 0
        # budowa dokumentu (regex na surowym tekście): wszystkie wymagane wzorce muszą wystąpić
        # w oknie początku, a żaden wykluczający (np. tytuł innego rodzaju pisma: WYROK, DECYZJA)
        okno = tekst[: self.konf.get("okno_wzorcow", 4000)]
        for wz in self.konf.get("wymagane_wzorce") or []:
            if not konfig._kompiluj(wz, None).search(okno):
                return 0
        okno_wykl = tekst[: self.konf.get("okno_wykluczen", 1000)]
        for wz in self.konf.get("wykluczajace_wzorce") or []:
            if konfig._kompiluj(wz, None).search(okno_wykl):
                return 0
        return sum(zwarty.count(_zloz(s)) for s in self.konf["slowa_kluczowe"])

    def wynik(self, tekst: str, odrzucone: list | None = None) -> dict:
        if self.ekstraktor:                               # kod ma pierwszeństwo przed konfiguracją
            return self.ekstraktor(tekst) or copy.deepcopy(self.schemat)
        w = copy.deepcopy(self.szkielet) if self.szkielet else copy.deepcopy(self.schemat)
        if self.konf_pola:
            _scal(w, konfig.ekstrahuj(tekst, self.konf_pola, odrzucone))
        return w

    def wynik_z_raportem(self, tekst: str) -> tuple[dict, list[dict]]:
        """Reguły, potem model językowy dla pól pustych. Raport: wartości odrzucone przez walidację
        reguł (zrodlo: reguly) oraz wartości od modelu i ich weryfikacja (zrodlo: model)."""
        odrzucone: list[dict] = []
        w = self.wynik(tekst, odrzucone)
        raport = [{"zrodlo": "reguly", **o, "przyjete": False} for o in odrzucone]
        raport += llm.uzupelnij(tekst, w, self.konf_llm) if self.konf_llm else []
        return w, raport


def wczytaj_typy(katalog: Path | None = None) -> dict[str, Typ]:
    """Rejestr typów: każdy podkatalog z typ.yaml/typ.json (katalogi zaczynające się od _ pomijamy)."""
    typy = {}
    for k in sorted((katalog or KATALOG_TYPOW).iterdir()):
        if not k.is_dir() or k.name.startswith("_"):
            continue
        if not ((k / "typ.yaml").exists() or (k / "typ.json").exists()):
            continue
        try:
            typy[k.name] = Typ(k)
        except Exception as e:  # noqa: BLE001 - zły typ nie może zablokować pozostałych
            log.error("Pominięto typ %s: %s", k.name, e)
    return typy


def klasyfikuj(tekst: str, typy: dict[str, Typ]) -> str | None:
    punkty = {n: t.punkty(tekst) for n, t in typy.items()}
    najlepszy = max(punkty, key=punkty.get, default=None)
    return najlepszy if najlepszy and punkty[najlepszy] > 0 else None


def przetworz_plik(pdf: Path, wyjscie: Path, typy: dict[str, Typ], raport: Path | None = None) -> None:
    """Jeden PDF -> jeden JSON o tej samej nazwie. Błąd nie przerywa przetwarzania reszty.

    Opcjonalny raport (osobny katalog, by nie wpływać na plik wynikowy): typ dokumentu
    i wartości zaproponowane przez model językowy wraz z wynikiem ich weryfikacji.
    """
    info: dict = {"plik": pdf.name}
    try:
        tekst = czytaj_tekst(pdf)
        typ = klasyfikuj(tekst, typy)
        info["typ"] = typ
        log.info("%s -> %s", pdf.name, typ)
        if not typ:
            # dokument spoza znanych typów: plik .json z pustym obiektem "{}" (odpowiedź organizatora
            # na Discordzie: "pusty z {}"); PUSTY_WYNIK=zero zapisuje plik o długości 0
            info["uwaga"] = "nierozpoznany typ dokumentu - pusty wynik {}"
            wyjscie.parent.mkdir(parents=True, exist_ok=True)
            wyjscie.write_text("" if os.environ.get("PUSTY_WYNIK") == "zero" else "{}", encoding="utf-8")
            if raport is not None:
                raport.parent.mkdir(parents=True, exist_ok=True)
                raport.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        wynik, raport_typu = typy[typ].wynik_z_raportem(tekst)
        info["llm"] = [r for r in raport_typu if r.get("zrodlo") != "reguly"]
        odrzucone = [r for r in raport_typu if r.get("zrodlo") == "reguly"]
        if odrzucone:
            info["walidacja"] = odrzucone             # wartości z reguł odrzucone przez `walidacja`
    except Exception as e:  # noqa: BLE001 - celowo szeroko: ocena automatyczna nie może się wysypać
        log.error("Błąd w %s: %s", pdf, e)
        wynik = {"blad": f"{type(e).__name__}: {e}", "slad": traceback.format_exc(limit=3)}
        info["blad"] = wynik["blad"]
    wyjscie.parent.mkdir(parents=True, exist_ok=True)
    wyjscie.write_text(json.dumps(wynik, ensure_ascii=False, indent=2), encoding="utf-8")
    if raport is not None:
        raport.parent.mkdir(parents=True, exist_ok=True)
        raport.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def przetworz_katalog(wejscie: Path, wyjscie: Path, raport: Path | None = None) -> int:
    typy = wczytaj_typy()
    pdfy = sorted(p for p in wejscie.rglob("*") if p.suffix.lower() == ".pdf")
    for pdf in pdfy:
        rel = pdf.relative_to(wejscie).with_suffix(".json")
        przetworz_plik(pdf, wyjscie / rel, typy, raport / rel if raport else None)
    return len(pdfy)

