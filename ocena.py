"""Ocena jakości ekstrakcji: F1 względem plików referencyjnych.

Użycie:
    python ocena.py --wzorzec ../dane_uzp/hacknation-2026-main/dane --wynik output
    python ocena.py --wzorzec ... --wynik output --scisle      # bez normalizacji
    python ocena.py --wzorzec ... --wynik output --zestawy przyklad_01 przyklad_02

Układ katalogów (zgodny z README organizatora): <wynik>/<zestaw>/<plik>.json,
gdzie nazwa JSON-a = nazwa PDF-a z wejścia. Wzorce: <wzorzec>/<zestaw>/<typ>.json.
Dla potwierdzenia przelewu wzorzec to przelew.json, a wynik
potwierdzenie_przelewu.json (mapowanie w NAZWY_WZORCOW).

ZAŁOŻENIA (organizator nie opisał liczenia F1 — do potwierdzenia):
 1. Każdy liść JSON-a to jedno pole. Wzorzec z wartością (nie null/"") to
    pozycja dodatnia: trafienie = TP, brak/inna wartość = FN.
 2. Wartość przewidziana (nie null/""), która nie trafiła we wzorzec = FP.
    (Błędna wartość daje więc jednocześnie FN i FP.)
 3. null po obu stronach nie liczy się wcale.
 4. Listy obiektów (Odwolujacy, Zamawiajacy, mocodawca, podpisujacy,
    pelnomocnicy) są dopasowywane bez względu na kolejność (zachłannie,
    po największej liczbie zgodnych pól). Nadmiarowy element = FP, brakujący = FN.
 5. Normalizacja (domyślnie): wielkość liter, białe znaki, liczby jako float,
    cyfry w KRS/NIP/PESEL/REGON/rachunkach bez spacji. --scisle ją wyłącza.
 6. F1 mikro (sumowane TP/FP/FN) — ogółem, per pole (ścieżka bez indeksów)
    i per typ dokumentu. Podawane też F1 makro po zestawach.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TYPY = ("odwolanie", "pelnomocnictwo", "przelew")
# nazwa pliku wyjściowego (= nazwa PDF-a) -> typ dokumentu
NAZWY_WYNIKU = {
    "odwolanie": "odwolanie",
    "pelnomocnictwo": "pelnomocnictwo",
    "potwierdzenie_przelewu": "przelew",
    "przelew": "przelew",
}


NAZWA_WZORCA = {"potwierdzenie_przelewu": "przelew"}   # stary układ: nazwa PDF -> nazwa JSON-a wzorca


def typ_wzorca(wz) -> str | None:
    """Typ dokumentu z kluczy wzorca JSON."""
    if not isinstance(wz, dict):
        return None
    if "OdwolanieKIO" in wz:
        return "odwolanie"
    if "mocodawca" in wz or "pelnomocnicy" in wz:
        return "pelnomocnictwo"
    if "przelew_bankowy" in wz:
        return "przelew"
    return None


def _pusty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def normalizuj(v, scisle: bool):
    if _pusty(v):
        return None
    if scisle:
        return v
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and abs(v) >= 10**8:   # długi int = identyfikator (PESEL), nie ilość
        return str(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"\s+", " ", str(v)).strip().casefold()
    if re.fullmatch(r"[\d\s]+", s):          # identyfikatory liczbowe (PESEL, KRS...)
        return s.replace(" ", "")
    if re.fullmatch(r"[a-z]{0,2}[\d ]{10,}", s):  # rachunek z prefiksem PL/spacjami
        return s.replace(" ", "")
    return s


def _lisc_rowny(a, b, scisle):
    return normalizuj(a, scisle) == normalizuj(b, scisle)


def _liscie(o, pre=""):
    """Liście obiektu jako (ścieżka, wartość); listy -> ścieżka z '[]'."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _liscie(v, f"{pre}{k}.")
    elif isinstance(o, list):
        for v in o:
            yield from _liscie(v, f"{pre[:-1]}[].")
    else:
        yield pre[:-1], o


def _porownaj(wz, pr, sciezka, scisle, licz):
    """Rekurencyjne porównanie; licz[(sciezka)] -> [tp, fp, fn]."""
    if isinstance(wz, list) or isinstance(pr, list):
        wz = wz if isinstance(wz, list) else ([] if wz is None else [wz])
        pr = pr if isinstance(pr, list) else ([] if pr is None else [pr])
        _porownaj_listy(wz, pr, sciezka + "[]", scisle, licz)
        return
    if isinstance(wz, dict) or isinstance(pr, dict):
        wz = wz if isinstance(wz, dict) else {}
        pr = pr if isinstance(pr, dict) else {}
        for k in list(wz) + [k for k in pr if k not in wz]:
            _porownaj(wz.get(k), pr.get(k), f"{sciezka}.{k}" if sciezka else k, scisle, licz)
        return
    w, p = normalizuj(wz, scisle), normalizuj(pr, scisle)
    c = licz[sciezka]
    if w is None and p is None:
        return
    if w is not None and p is not None and w == p:
        c[0] += 1
    else:
        if p is not None:
            c[1] += 1
        if w is not None:
            c[2] += 1


def _wynik_pary(a, b, scisle):
    """Liczba zgodnych liści dwóch elementów listy (do dopasowania)."""
    la = {k: v for k, v in _liscie(a)} if isinstance(a, (dict, list)) else {"": a}
    lb = {k: v for k, v in _liscie(b)} if isinstance(b, (dict, list)) else {"": b}
    return sum(1 for k, v in la.items() if not _pusty(v) and k in lb and _lisc_rowny(v, lb[k], scisle))


def _porownaj_listy(wz, pr, sciezka, scisle, licz):
    pary = sorted(
        ((_wynik_pary(w, p, scisle), i, j) for i, w in enumerate(wz) for j, p in enumerate(pr)),
        key=lambda x: (-x[0], x[1], x[2]),
    )
    uzyte_w, uzyte_p, dopasowane = set(), set(), []
    for sc, i, j in pary:
        if i in uzyte_w or j in uzyte_p:
            continue
        uzyte_w.add(i); uzyte_p.add(j); dopasowane.append((i, j))
    for i, j in dopasowane:
        _porownaj(wz[i], pr[j], sciezka, scisle, licz)
    for i, w in enumerate(wz):            # brakujące elementy -> FN
        if i not in uzyte_w:
            _porownaj(w, None, sciezka, scisle, licz)
    for j, p in enumerate(pr):            # nadmiarowe elementy -> FP
        if j not in uzyte_p:
            _porownaj(None, p, sciezka, scisle, licz)


def f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def wczytaj(sciezka: Path):
    try:
        return json.loads(sciezka.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wzorzec", required=True, type=Path, help="katalog z zestawami referencyjnymi")
    ap.add_argument("--wynik", required=True, type=Path, help="katalog wyjściowy rozwiązania")
    ap.add_argument("--scisle", action="store_true", help="porównanie dokładne, bez normalizacji")
    ap.add_argument("--zestawy", nargs="*", help="ogranicz do podanych zestawów")
    ap.add_argument("--pola", action="store_true", help="wypisz F1 per pole")
    a = ap.parse_args(argv)

    # Każdy PDF we wzorcu (rekurencyjnie): wzorzec = JSON o tej samej nazwie (skany: 0001.pdf/0001.json,
    # sprawy: odwolanie.pdf/odwolanie.json); stary układ: potwierdzenie_przelewu.pdf -> przelew.json.
    # PDF bez wzorca = dokument negatywny. Wynik = <wynik>/<ta sama ścieżka>.json. Typ - z kluczy wzorca.
    pdfy = sorted(a.wzorzec.rglob("*.pdf"))
    if a.zestawy:
        pdfy = [p for p in pdfy if set(a.zestawy) & set(p.relative_to(a.wzorzec).parts)]
    if not pdfy:
        sys.exit("Brak dokumentów PDF we wzorcu.")

    per_pole = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # typ -> pole -> [tp,fp,fn]
    per_zestaw = defaultdict(lambda: [0, 0, 0])
    zestawy_ze_wzorcem = set()
    brakujace = dokumentow = 0
    neg = {"razem": 0, "puste": 0, "brak_pliku": 0, "fp": 0, "bledne": []}
    for pdf in pdfy:
        rel = pdf.relative_to(a.wzorzec)
        zestaw = str(rel.parent)
        wz_plik = pdf.with_suffix(".json")
        if not wz_plik.exists() and pdf.stem in NAZWA_WZORCA:
            wz_plik = pdf.parent / f"{NAZWA_WZORCA[pdf.stem]}.json"
        plik = a.wynik / rel.with_suffix(".json")
        if not wz_plik.exists():
            # dokument negatywny (README UZP: wynik = pusty plik .json); wypełnione pola -> FP
            neg["razem"] += 1
            if not plik.exists():
                neg["brak_pliku"] += 1
                neg["bledne"].append(f"{rel}: brak pliku wyniku")
                continue
            tresc = plik.read_text(encoding="utf-8-sig").strip()
            if tresc in ("", "{}"):
                neg["puste"] += 1
                continue
            licz = defaultdict(lambda: [0, 0, 0])
            try:
                _porownaj(None, json.loads(tresc), "", a.scisle, licz)
            except ValueError:
                pass
            fp = sum(c[1] for c in licz.values()) or 1
            neg["fp"] += fp
            per_zestaw[zestaw][1] += fp
            neg["bledne"].append(f"{rel}: {fp} wypełnionych pól")
            continue
        wz = wczytaj(wz_plik)
        typ = typ_wzorca(wz)
        if typ is None:
            continue
        dokumentow += 1
        zestawy_ze_wzorcem.add(zestaw)
        pr = wczytaj(plik) if plik.exists() else None
        if pr is None:
            brakujace += 1
        licz = defaultdict(lambda: [0, 0, 0])
        _porownaj(wz, pr, "", a.scisle, licz)
        for pole, c in licz.items():
            for i in range(3):
                per_pole[typ][pole][i] += c[i]
                per_zestaw[zestaw][i] += c[i]

    print(f"Dokumentów ze wzorcem: {dokumentow}   negatywnych: {neg['razem']}   "
          f"brakujące pliki wyniku: {brakujace}   tryb: {'ścisły' if a.scisle else 'z normalizacją'}\n")
    ogol = [0, 0, neg["fp"] * 0]
    ogol[1] += neg["fp"]                                        # pola wypełnione w negatywach = FP
    print(f"{'typ dokumentu':18}{'P':>8}{'R':>8}{'F1':>8}   (tp/fp/fn)")
    for typ in TYPY:
        s = [sum(c[i] for c in per_pole[typ].values()) for i in range(3)]
        for i in range(3):
            ogol[i] += s[i]
        p, r, f = f1(*s)
        print(f"{typ:18}{p:8.3f}{r:8.3f}{f:8.3f}   ({s[0]}/{s[1]}/{s[2]})")
    if neg["razem"]:
        print(f"{'negatywne':18}  poprawnie puste {neg['puste']}/{neg['razem']}, brak pliku {neg['brak_pliku']}, "
              f"FP pól {neg['fp']}")
    p, r, f = f1(*ogol)
    print(f"{'OGÓŁEM (mikro)':18}{p:8.3f}{r:8.3f}{f:8.3f}   ({ogol[0]}/{ogol[1]}/{ogol[2]})")
    if zestawy_ze_wzorcem:
        sr = sum(f1(*per_zestaw[z])[2] for z in zestawy_ze_wzorcem) / len(zestawy_ze_wzorcem)
        print(f"{'średnia po zest.':18}{'':16}{sr:8.3f}")
    for b in neg["bledne"][:20]:
        print(f"  negatyw: {b}")
    if a.pola:
        print("\nPer pole:")
        for typ in TYPY:
            for pole, c in sorted(per_pole[typ].items()):
                p, r, f = f1(*c)
                print(f"  {typ}.{pole:48}{f:6.3f}  ({c[0]}/{c[1]}/{c[2]})")
    return f


if __name__ == "__main__":
    main()
