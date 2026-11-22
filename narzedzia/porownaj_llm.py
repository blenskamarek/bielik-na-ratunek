"""Porównanie modeli językowych: jakość (F1) i czas, gdy model wypełnia pola SAM.

Reguły na danych syntetycznych dają F1 = 1, więc model prawie nie jest pytany. Tu celowo
czyścimy wszystkie pola z typy/<typ>/llm.yaml i każemy modelowi wypełnić je od zera
(z tą samą weryfikacją w tekście co w produkcji), a potem liczymy F1 tych pól względem wzorca.

Użycie:
  python narzedzia/porownaj_llm.py --model SpeakLeash/bielik-11b-v3.1-instruct:Q4_K_M --zestawy 51-60
  python narzedzia/porownaj_llm.py --model SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0 --typ pelnomocnictwo --zestawy 51-55
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ocena  # noqa: E402
from silnik import llm  # noqa: E402
from silnik.rdzen import czytaj_tekst, wczytaj_typy  # noqa: E402

PLIKI = {"odwolanie": ("odwolanie.pdf", "odwolanie.json"),
         "pelnomocnictwo": ("pelnomocnictwo.pdf", "pelnomocnictwo.json"),
         "przelew": ("potwierdzenie_przelewu.pdf", "przelew.json")}


def zakres(s: str) -> list[int]:
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--typ", default="odwolanie", choices=PLIKI)
    ap.add_argument("--zestawy", default="51-60")
    ap.add_argument("--dane", type=Path, default=ROOT.parent / "dane_uzp" / "hacknation-2026-main" / "dane")
    a = ap.parse_args()
    os.environ["LLM_MODEL"] = a.model
    # porównanie modeli ma sens tylko bezpośrednio na lokalnej Ollamie (w kolejce model wybiera pracownik)
    os.environ.setdefault("LLM_TRYB", "api")
    os.environ.setdefault("LLM_ADRES", "http://localhost:11434/v1")
    llm._stan["wylaczony_po_bledzie"] = False

    typ = wczytaj_typy()[a.typ]
    pola = typ.konf_llm["pola"]
    pdf_n, gt_n = PLIKI[a.typ]
    licz = defaultdict(lambda: [0, 0, 0])
    czasy, odrzucone = [], 0
    for nr in zakres(a.zestawy):
        z = a.dane / f"przyklad_{nr:02d}"
        tekst = czytaj_tekst(z / pdf_n)
        gt = json.loads((z / gt_n).read_text(encoding="utf-8"))
        w = typ.wynik(tekst)
        for n, s in pola.items():                     # czyścimy pola dla modelu
            llm._ustaw(w, n, [] if s.get("typ") == "lista" else None)
        t0 = time.time()
        raport = llm.uzupelnij(tekst, w, typ.konf_llm)
        czasy.append(time.time() - t0)
        odrzucone += sum(not r["przyjete"] for r in raport)
        for n in pola:
            ocena._porownaj(copy.deepcopy(llm._pobierz(gt, n)), llm._pobierz(w, n), n, False, licz)
        print(f"{z.name}: {czasy[-1]:5.1f} s, odrzuconych wartości: {sum(not r['przyjete'] for r in raport)}", flush=True)

    s = [sum(c[i] for c in licz.values()) for i in range(3)]
    print(f"\nmodel: {a.model}   typ: {a.typ}   zestawy: {a.zestawy}")
    print(f"F1 pól wypełnianych przez model: {ocena.f1(*s)[2]:.3f}   (tp/fp/fn {s[0]}/{s[1]}/{s[2]})")
    print(f"czas na dokument: średnio {sum(czasy) / len(czasy):.1f} s, max {max(czasy):.1f} s   odrzuconych: {odrzucone}")
    for pole, c in sorted(licz.items()):
        print(f"  {pole:45} {ocena.f1(*c)[2]:.3f}  ({c[0]}/{c[1]}/{c[2]})")


if __name__ == "__main__":
    main()
