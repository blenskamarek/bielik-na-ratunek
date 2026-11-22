"""Bramka z kluczem API przed lokalną Ollamą (biblioteka standardowa Pythona).

Po co: Ollama nie ma uwierzytelniania, więc NIE wystawiamy jej portu do internetu.
Na zewnątrz wystawiamy tę bramkę (przez HTTPS, np. reverse proxy w sieci urzędu), która:
  * wymaga nagłówka Authorization: Bearer <klucz> (porównanie w stałym czasie),
  * przepuszcza tylko POST /v1/chat/completions i GET /v1/models (+ GET /zdrowie bez klucza),
  * podmienia pole "model" na jeden skonfigurowany model (klient nie wybierze innego),
  * ogranicza rozmiar zapytania i kolejkuje zapytania (GPU obsługuje jedno naraz),
  * loguje tylko metadane (czas, IP, status, liczba tokenów) - bez treści dokumentów (RODO).

Użycie:
  python narzedzia/bramka_llm.py --nowy-klucz                 # tworzy plik z kluczem (bramka.klucz)
  python narzedzia/bramka_llm.py --port 8080 --model bielik-11b-6k
Po stronie programu: llm.yaml -> tryb: api, adres: https://<adres-bramki>/v1, klucz w pliku llm.klucz.
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("bramka")
KATALOG = Path(__file__).resolve().parent


class Bramka(BaseHTTPRequestHandler):
    konf: dict = {}
    kolejka = threading.Semaphore(1)
    server_version = "bramka-llm"

    # --- pomocnicze
    def _odpowiedz(self, kod: int, tresc: dict | bytes, typ: str = "application/json") -> None:
        dane = tresc if isinstance(tresc, bytes) else json.dumps(tresc, ensure_ascii=False).encode()
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(dane)))
        self.end_headers()
        self.wfile.write(dane)

    def _autoryzacja(self) -> bool:
        naglowek = self.headers.get("Authorization", "")
        podany = naglowek[7:].strip() if naglowek.startswith("Bearer ") else ""
        return any(hmac.compare_digest(podany, k) for k in self.konf["klucze"]) if podany else False

    def _ip(self) -> str:
        # za pośrednikiem HTTPS prawdziwy adres jest w nagłówku (tylko do logów)
        return self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For") or self.client_address[0]

    def log_message(self, fmt, *args):            # wyłącza domyślne logi z pełnymi ścieżkami
        pass

    # --- obsługa
    def do_GET(self):
        if self.path == "/zdrowie":
            try:
                with urllib.request.urlopen(f"{self.konf['cel']}/api/tags", timeout=5) as r:
                    modele = [m["name"] for m in json.loads(r.read())["models"]]
                ok = any(m.split(":")[0] == self.konf["model"].split(":")[0] for m in modele)
                return self._odpowiedz(200 if ok else 503, {"ok": ok, "model": self.konf["model"]})
            except (urllib.error.URLError, OSError, ValueError):
                return self._odpowiedz(503, {"ok": False, "powod": "Ollama nie odpowiada"})
        if not self._autoryzacja():
            log.warning("401 GET %s z %s", self.path, self._ip())
            return self._odpowiedz(401, {"error": {"message": "brak lub zły klucz API"}})
        if self.path == "/v1/models":
            return self._odpowiedz(200, {"object": "list", "data": [{"id": self.konf["model"], "object": "model"}]})
        return self._odpowiedz(404, {"error": {"message": "nieobsługiwana ścieżka"}})

    def do_POST(self):
        t0 = time.time()
        if self.path != "/v1/chat/completions":
            return self._odpowiedz(404, {"error": {"message": "nieobsługiwana ścieżka"}})
        if not self._autoryzacja():
            log.warning("401 POST z %s", self._ip())
            return self._odpowiedz(401, {"error": {"message": "brak lub zły klucz API"}})
        dlugosc = int(self.headers.get("Content-Length") or 0)
        if dlugosc <= 0 or dlugosc > self.konf["max_bajtow"]:
            return self._odpowiedz(413, {"error": {"message": "zapytanie puste lub za duże"}})
        try:
            tresc = json.loads(self.rfile.read(dlugosc))
            if not isinstance(tresc.get("messages"), list):
                raise ValueError
        except ValueError:
            return self._odpowiedz(400, {"error": {"message": "niepoprawny JSON"}})
        tresc["model"] = self.konf["model"]          # klient nie wybiera modelu
        tresc["stream"] = False
        req = urllib.request.Request(f"{self.konf['cel']}/v1/chat/completions", data=json.dumps(tresc).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        if not self.kolejka.acquire(timeout=self.konf["czekanie_s"]):
            return self._odpowiedz(429, {"error": {"message": "kolejka pełna, spróbuj później"}})
        try:
            with urllib.request.urlopen(req, timeout=self.konf["limit_s"]) as r:
                dane, kod = r.read(), r.status
        except urllib.error.HTTPError as e:
            dane, kod = e.read(), e.code
        except (urllib.error.URLError, OSError) as e:
            log.error("Ollama niedostępna: %s", e)
            return self._odpowiedz(502, {"error": {"message": "model niedostępny"}})
        finally:
            self.kolejka.release()
        try:
            uzycie = json.loads(dane).get("usage", {})
        except ValueError:
            uzycie = {}
        log.info("%s POST %s %.1fs tokeny wej/wyj %s/%s", kod, self._ip(), time.time() - t0,
                 uzycie.get("prompt_tokens"), uzycie.get("completion_tokens"))
        self._odpowiedz(kod, dane)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 gdy przed bramką jest pośrednik HTTPS (zalecane)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--cel", default="http://127.0.0.1:11434", help="adres lokalnej Ollamy")
    ap.add_argument("--model", default="bielik-11b-6k")
    ap.add_argument("--klucz-plik", type=Path, default=KATALOG / "bramka.klucz",
                    help="plik z kluczami API (jeden na linię)")
    ap.add_argument("--nowy-klucz", action="store_true", help="dopisz nowy losowy klucz do pliku i zakończ")
    ap.add_argument("--max-bajtow", type=int, default=400_000)
    # Pośrednicy HTTPS często zrywają zapytanie bez odpowiedzi po ~100 s (np. HTTP 524).
    # Dlatego domyślnie: czekanie w kolejce + generowanie < 100 s; zajęta kolejka -> szybkie 429.
    ap.add_argument("--limit-s", type=int, default=90, help="maks. czas jednego zapytania do modelu")
    ap.add_argument("--czekanie-s", type=int, default=45, help="maks. czas czekania w kolejce")
    ap.add_argument("--pozwol-usypiac", action="store_true",
                    help="nie blokuj uśpienia komputera (domyślnie bramka blokuje je, dopóki działa)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if a.nowy_klucz:
        klucz = secrets.token_urlsafe(32)
        with a.klucz_plik.open("a", encoding="utf-8") as f:
            f.write(klucz + "\n")
        print(f"Dopisano nowy klucz do {a.klucz_plik}")
        return
    klucze = [l.strip() for l in a.klucz_plik.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if a.klucz_plik.exists() else []
    if not klucze:
        raise SystemExit(f"Brak kluczy w {a.klucz_plik}. Utwórz: python narzedzia/bramka_llm.py --nowy-klucz")
    Bramka.konf = {"cel": a.cel.rstrip("/"), "model": a.model, "klucze": klucze, "max_bajtow": a.max_bajtow,
                   "limit_s": a.limit_s, "czekanie_s": a.czekanie_s}
    if sys.platform == "win32" and not a.pozwol_usypiac:
        # jak odtwarzacz wideo: system nie zaśnie, dopóki bramka działa (bez zmiany ustawień zasilania)
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log.info("Uśpienie systemu zablokowane na czas działania bramki")
    log.info("Bramka na %s:%d -> %s (model %s), kluczy: %d", a.host, a.port, a.cel, a.model, len(klucze))
    ThreadingHTTPServer((a.host, a.port), Bramka).serve_forever()


if __name__ == "__main__":
    main()
