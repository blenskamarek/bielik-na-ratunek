"""Dodawanie nowego typu dokumentu bez zmian w kodzie - narzędzie dla administratora (i jego asystenta AI).

1) Szkielet z przykładu:
     python narzedzia/nowy_typ.py nowy faktura --pdf przyklad1.pdf --pdf przyklad2.pdf --wzorzec oczekiwany1.json
   Tworzy typy/faktura/ z:
     typ.yaml                      - klasyfikacja (do uzupełnienia) + sekcja llm: ze wszystkimi polami wzorca
                                     (typ pola odgadnięty z wartości) => typ działa od razu przez model językowy,
     przyklady/<nazwa>.pdf / .txt  - przykład i jego tekst (także z OCR) do wglądu dla administratora i AI,
     przyklady/<nazwa>.oczekiwany.json - wzorzec do pomiaru jakości.
   Wzorce przypisywane są do PDF-ów w kolejności podania.

2) Sprawdzenie (po każdej zmianie typ.yaml):
     python narzedzia/nowy_typ.py sprawdz typy/faktura [--bez-llm] [--dane KATALOG_Z_PDF]
   - walidacja konfiguracji (czytelny komunikat, co i gdzie jest źle),
   - klasyfikacja każdego przykładu (czy wygrywa nowy typ i z jaką przewagą),
   - wynik JSON + F1 per pole względem *.oczekiwany.json,
   - z --dane: czy nowy typ nie "przejmuje" dokumentów innych typów (porównanie klasyfikacji z nim i bez niego).
Żadnego kroku nie trzeba wykonywać w kodzie silnika.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

import ocena  # noqa: E402
from silnik import konfig, llm  # noqa: E402
from silnik.rdzen import KATALOG_TYPOW, Typ, czytaj_tekst, klasyfikuj, wczytaj_typy  # noqa: E402


# ----------------------------------------------------------------------------- szkielet
def _typ_wartosci(v) -> dict:
    """Odgadnięcie typu pola z przykładowej wartości wzorca."""
    if isinstance(v, bool) or (isinstance(v, int) and v in (0, 1)):
        return {"typ": "wybor", "wartosci": [0, 1]}
    if isinstance(v, float) or (isinstance(v, int)):
        return {"typ": "kwota"}
    if isinstance(v, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return {"typ": "data"}
        if re.fullmatch(r"\d{5,}", v):
            return {"typ": "cyfry", "dlugosc": len(v)}
        if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", v):
            return {"typ": "email"}
    return {"typ": "tekst"}


def _pola_z_wzorca(o, przedrostek: str = "") -> dict:
    """Spłaszcza wzorzec JSON do {ścieżka: spec} (listy obiektów -> typ lista z elementem)."""
    pola = {}
    for k, v in o.items():
        sciezka = f"{przedrostek}{k}"
        if isinstance(v, dict):
            pola.update(_pola_z_wzorca(v, sciezka + "."))
        elif isinstance(v, list):
            el = next((x for x in v if isinstance(x, dict)), None)
            if el is not None:
                pola[sciezka] = {"typ": "lista", "opis": "UZUPEŁNIJ opis listy", "element": _pola_z_wzorca(el)}
            else:
                pola[sciezka] = {"typ": "tekst", "opis": "UZUPEŁNIJ (lista wartości prostych - opisz jako tekst)"}
        else:
            pola[sciezka] = {**_typ_wartosci(v), "opis": "UZUPEŁNIJ: czego szukać w dokumencie"}
    return pola


def nowy(a) -> int:
    katalog = KATALOG_TYPOW / a.nazwa
    if katalog.exists():
        print(f"BŁĄD: {katalog} już istnieje")
        return 1
    if not re.fullmatch(r"_?[a-z0-9_]+", a.nazwa):
        print("BŁĄD: nazwa typu tylko małymi literami, cyframi i '_' (np. faktura, ogloszenie_ted)")
        return 1
    (katalog / "przyklady").mkdir(parents=True)
    wzorce = [json.loads(Path(w).read_text(encoding="utf-8-sig")) for w in a.wzorzec or []]
    for i, pdf in enumerate(a.pdf or []):
        pdf = Path(pdf)
        nazwa = re.sub(r"[^\w-]+", "_", pdf.stem).strip("_").lower()
        shutil.copy(pdf, katalog / "przyklady" / f"{nazwa}.pdf")
        (katalog / "przyklady" / f"{nazwa}.txt").write_text(czytaj_tekst(pdf), encoding="utf-8")
        if i < len(wzorce):
            (katalog / "przyklady" / f"{nazwa}.oczekiwany.json").write_text(
                json.dumps(wzorce[i], ensure_ascii=False, indent=2), encoding="utf-8")
    konf = {
        "klasyfikacja": {
            "slowa_kluczowe": [],
            "wymagane_na_poczatku": [],
            "okno_poczatku": 2000,
            "ogranicz_do_znakow": 100000,
        },
        "pola": {},
        "llm": {
            "opis_dokumentu": "UZUPEŁNIJ: jednym zdaniem, czym jest ten dokument",
            "fragmenty": [{"od": r"\A", "znakow": 6000}],
            "pola": _pola_z_wzorca(wzorce[0]) if wzorce else {},
        },
    }
    naglowek = (f"# Typ dokumentu: {a.nazwa}\n"
                "# Wygenerowane przez narzedzia/nowy_typ.py - uzupełnij miejsca 'UZUPEŁNIJ'.\n"
                "# Składnia i przykłady: docs/NOWY_TYP.md. Sprawdzenie: python narzedzia/nowy_typ.py sprawdz "
                f"typy/{a.nazwa}\n#\n"
                "# klasyfikacja.slowa_kluczowe: słowa typowe dla tego dokumentu (puste = typ nieaktywny)\n"
                "# pola: reguły (regex) dla pól o stałej formie - mają pierwszeństwo przed modelem\n"
                "# llm: pola, które model językowy uzupełnia, gdy reguły nic nie znalazły (wartość\n"
                "#      musi wystąpić w tekście dokumentu - inaczej jest odrzucana)\n")
    (katalog / "typ.yaml").write_text(naglowek + yaml.safe_dump(konf, allow_unicode=True, sort_keys=False, width=110),
                                      encoding="utf-8")
    print(f"Utworzono {katalog}")
    print("Dalej: uzupełnij klasyfikacja.slowa_kluczowe i opisy w typ.yaml (tekst przykładów: przyklady/*.txt),")
    print(f"potem: python narzedzia/nowy_typ.py sprawdz typy/{a.nazwa}")
    return 0


# ----------------------------------------------------------------------------- sprawdzanie
def sprawdz(a) -> int:
    katalog = Path(a.katalog).resolve()
    if a.bez_llm:
        import os
        os.environ["LLM_WYLACZ"] = "1"
    try:
        typ = Typ(katalog)
    except Exception as e:  # noqa: BLE001 - komunikat dla administratora
        print(f"BŁĄD KONFIGURACJI: {e}")
        return 1
    print(f"Konfiguracja OK: {typ.nazwa} (reguł: {len((typ.konf_pola.get('pola') or {})) + len(typ.konf_pola.get('listy') or {})}, "
          f"pól dla LLM: {len(typ.konf_llm.get('pola') or {})})")
    inne = {n: t for n, t in wczytaj_typy().items() if n != typ.nazwa}
    wszystkie = {**inne, typ.nazwa: typ}
    licz = defaultdict(lambda: [0, 0, 0])
    przyklady = sorted((katalog / "przyklady").glob("*.pdf"))
    if not przyklady:
        print("Brak przykładów w przyklady/*.pdf")
    for pdf in przyklady:
        tekst = czytaj_tekst(pdf)
        punkty = {n: t.punkty(tekst) for n, t in wszystkie.items()}
        wygrany = klasyfikuj(tekst, wszystkie)
        znak = "OK " if wygrany == typ.nazwa else "BŁĄD"
        print(f"\n[{znak}] {pdf.name}: klasyfikacja -> {wygrany}   punkty: {punkty}")
        wynik, raport = typ.wynik_z_raportem(tekst)
        print(json.dumps(wynik, ensure_ascii=False, indent=2))
        for r in raport:
            print(f"   model: {r['pole']} = {r['z_modelu']!r} -> {'przyjęte' if r['przyjete'] else 'odrzucone: ' + r.get('powod_odrzucenia', '')}")
        oczek = pdf.parent / f"{pdf.stem}.oczekiwany.json"
        if oczek.exists():
            wz = json.loads(oczek.read_text(encoding="utf-8-sig"))
            ocena._porownaj(copy.deepcopy(wz), wynik, "", False, licz)
    if licz:
        s = [sum(c[i] for c in licz.values()) for i in range(3)]
        print(f"\nF1 względem wzorców: {ocena.f1(*s)[2]:.3f}  (tp/fp/fn {s[0]}/{s[1]}/{s[2]})")
        for pole, c in sorted(licz.items()):
            if c != [0, 0, 0]:
                print(f"  {ocena.f1(*c)[2]:.3f}  {pole}  ({c[0]}/{c[1]}/{c[2]})")
    if a.dane:
        zmiany = []
        for pdf in sorted(Path(a.dane).rglob("*.pdf")):
            tekst = czytaj_tekst(pdf)
            if klasyfikuj(tekst, inne) != klasyfikuj(tekst, wszystkie):
                zmiany.append(pdf)
        print(f"\nRegresja klasyfikacji na {a.dane}: zmienionych dokumentów: {len(zmiany)}")
        for p in zmiany[:20]:
            print("   ", p)
        if zmiany:
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="komenda", required=True)
    n = sub.add_parser("nowy", help="utwórz szkielet typu z przykładowych PDF-ów i wzorca JSON")
    n.add_argument("nazwa")
    n.add_argument("--pdf", action="append")
    n.add_argument("--wzorzec", action="append", help="oczekiwany JSON (kolejność jak --pdf)")
    s = sub.add_parser("sprawdz", help="waliduj typ, pokaż wyniki i F1 na przykładach")
    s.add_argument("katalog")
    s.add_argument("--bez-llm", action="store_true")
    s.add_argument("--dane", help="katalog z PDF-ami innych typów do sprawdzenia regresji klasyfikacji")
    a = ap.parse_args()
    return nowy(a) if a.komenda == "nowy" else sprawdz(a)


if __name__ == "__main__":
    sys.exit(main())
