r"""Pracownik kolejki: komputer z Bielikiem odpytuje kolejkę na hostingu i liczy odpowiedzi.

Tylko połączenia WYCHODZĄCE (HTTPS do hostingu) - komputer z modelem nie przyjmuje żadnych połączeń.
Pętla: GET ?a=nastepne -> zapytanie do lokalnej Ollamy (model wymuszony) -> POST ?a=odpowiedz.

Użycie:
  py narzedzia\pracownik_kolejki.py --nowe-klucze          # klucze do kolejka_konfig.php i llm.klucz
  py narzedzia\pracownik_kolejki.py --adres https://bielik.kowale.edu.pl/kolejka.php
Logi zawierają tylko metadane (id, czas, tokeny) - bez treści dokumentów.
"""
from __future__ import annotations

import argparse
import json
import logging
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("pracownik")
KATALOG = Path(__file__).resolve().parent
ROOT = KATALOG.parent


def _http(metoda: str, url: str, klucz: str | None = None, dane: dict | None = None, limit: float = 30):
    """(status, json|None). Błędy HTTP zwracane jako status; błędy sieci jako wyjątek."""
    naglowki = {"Content-Type": "application/json", "User-Agent": "bielik-pracownik"}
    if klucz:
        naglowki["X-Api-Key"] = klucz
        naglowki["Authorization"] = f"Bearer {klucz}"
    req = urllib.request.Request(url, data=json.dumps(dane).encode() if dane is not None else None,
                                 headers=naglowki, method=metoda)
    try:
        with urllib.request.urlopen(req, timeout=limit) as r:
            tresc = r.read()
            return r.status, (json.loads(tresc) if tresc else None)
    except urllib.error.HTTPError as e:
        tresc = e.read()
        try:
            return e.code, json.loads(tresc) if tresc else None
        except ValueError:
            return e.code, None


def nowe_klucze() -> None:
    kk, kp = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    (ROOT / "llm.klucz").write_text(kk + "\n", encoding="utf-8")
    (KATALOG / "pracownik.klucz").write_text(kp + "\n", encoding="utf-8")
    print("Zapisano: llm.klucz (klucz klienta - dla programu sędziego) i narzedzia/pracownik.klucz.")
    print("Do serwer_kolejki/kolejka_konfig.php na hostingu wpisz:")
    print(f"    'klucz_klienta' => '{kk}',\n    'klucz_pracownika' => '{kp}',")


def licz(cel: str, model: str, tresc: dict, limit: float, atrapa: bool) -> dict:
    """Odpowiedź modelu w formacie /chat/completions."""
    if atrapa:                                       # do testów kolejki bez modelu
        return {"choices": [{"message": {"role": "assistant", "content": '{"atrapa": true}'}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    tresc = {**tresc, "model": model, "stream": False}
    status, odp = _http("POST", f"{cel}/v1/chat/completions", None, tresc, limit)
    if status != 200 or not odp:
        raise RuntimeError(f"model: HTTP {status} {str(odp)[:200]}")
    return odp


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adres", help="adres kolejka.php na hostingu")
    ap.add_argument("--klucz-plik", type=Path, default=KATALOG / "pracownik.klucz")
    ap.add_argument("--cel", default="http://127.0.0.1:11434", help="lokalna Ollama")
    ap.add_argument("--model", default="bielik-11b-6k")
    ap.add_argument("--odstep", type=float, default=1.0, help="odstęp odpytywania, gdy kolejka pusta [s]")
    ap.add_argument("--odstep-max", type=float, default=5.0,
                    help="odstęp po minucie bez zadań (mniej zapytań do hostingu) [s]")
    ap.add_argument("--limit-s", type=float, default=170, help="maks. czas liczenia jednej odpowiedzi")
    ap.add_argument("--atrapa", action="store_true", help="bez modelu (test kolejki)")
    ap.add_argument("--nowe-klucze", action="store_true")
    ap.add_argument("--pozwol-usypiac", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if a.nowe_klucze:
        nowe_klucze()
        return 0
    if not a.adres:
        ap.error("podaj --adres")
    klucz = a.klucz_plik.read_text(encoding="utf-8").strip() if a.klucz_plik.exists() else ""
    if not klucz:
        raise SystemExit(f"Brak klucza w {a.klucz_plik} (utwórz: --nowe-klucze)")
    if sys.platform == "win32" and not a.pozwol_usypiac:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)   # nie usypiaj w czasie pracy

    log.info("Pracownik: %s -> %s (model %s%s)", a.adres, a.cel, a.model, ", ATRAPA" if a.atrapa else "")
    przerwa = a.odstep
    ostatnie_zadanie = time.monotonic()
    while True:
        try:
            status, zad = _http("GET", f"{a.adres}?a=nastepne", klucz, limit=20)
            if status == 401:
                log.error("401: zły klucz pracownika - przerywam")
                return 1
            if status == 204 or not zad:
                # oszczędnie dla hostingu: po minucie bez zadań odpytujemy rzadziej (maks. co --odstep-max s);
                # serce dla ?a=zdrowie i tak odświeża się przy każdym odpytaniu
                bezczynnosc = time.monotonic() - ostatnie_zadanie
                time.sleep(a.odstep if bezczynnosc < 60 else a.odstep_max)
                przerwa = a.odstep
                continue
            ostatnie_zadanie = time.monotonic()
            if status != 200:
                raise RuntimeError(f"kolejka: HTTP {status}")
            t0 = time.time()
            try:
                odp = licz(a.cel, a.model, zad["tresc"], a.limit_s, a.atrapa)
                wynik = {"odpowiedz": odp}
                u = odp.get("usage") or {}
                log.info("%s gotowe %.1fs tokeny %s/%s", zad["id"], time.time() - t0,
                         u.get("prompt_tokens"), u.get("completion_tokens"))
            except Exception as e:  # noqa: BLE001 - błąd modelu zgłaszamy klientowi, pracujemy dalej
                wynik = {"blad": str(e)[:300]}
                log.warning("%s błąd: %s", zad["id"], e)
            status, _ = _http("POST", f"{a.adres}?a=odpowiedz&id={zad['id']}", klucz, wynik, limit=30)
            if status != 200:
                log.warning("%s: odesłanie wyniku HTTP %s", zad["id"], status)
            przerwa = a.odstep
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
            log.warning("Sieć/kolejka: %s - ponowię za %.0f s", e, przerwa)
            time.sleep(przerwa)
            przerwa = min(przerwa * 2, 30)            # rosnąca przerwa przy awarii
        except KeyboardInterrupt:
            log.info("Zatrzymano")
            return 0


if __name__ == "__main__":
    sys.exit(main())
