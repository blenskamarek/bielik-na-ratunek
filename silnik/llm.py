"""Warstwa LLM: uzupełnia pola, których reguły nie znalazły (null / pusta lista).

Zasady (transparentność i brak zmyśleń):
 * reguły mają pierwszeństwo: model wypełnia WYŁĄCZNIE pola puste po regułach,
 * każda wartość od modelu jest sprawdzana w tekście dokumentu (tekst, cyfry, data, e-mail,
   kwota) albo na zamkniętej liście (wybór); niepotwierdzona wartość -> null + wpis w raporcie,
 * temperatura 0, stałe ziarno, odpowiedzi w pamięci podręcznej (.cache_llm): ten sam
   dokument = ta sama odpowiedź i brak ponownego zużycia tokenów,
 * błąd połączenia wyłącza LLM do końca przebiegu (program działa dalej na samych regułach).

API zgodne z OpenAI (/chat/completions): Ollama / llama.cpp lokalnie albo dostawca w UE.
Konfiguracja: llm.yaml w katalogu głównym; pola dla modelu: typy/<typ>/llm.yaml.
Zmienne środowiskowe: LLM_WYLACZ=1, LLM_ADRES, LLM_MODEL, klucz w zmiennej z llm.yaml.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from silnik.konfig import MAKRA, BladKonfiguracji, sprawdz_walidacje, waliduj
from silnik.narzedzia import WZ_DATA, data_iso, kwota

TYPY_POL = {"tekst", "data", "cyfry", "email", "kwota", "wybor", "osoba", "lista"}


def sprawdz_konfiguracje_llm(konf: dict, sciezka: str = "llm.pola") -> None:
    """Waliduje sekcję llm typu dokumentu; błąd nazywa pole i problem (pomoc dla administratora/AI)."""
    for f in konf.get("fragmenty") or []:
        try:
            re.compile(f["od"])
        except (KeyError, re.error, TypeError) as e:
            raise BladKonfiguracji(f"llm.fragmenty: zły wpis {f!r} ({e})") from None
    pola = konf.get("pola") if "pola" in konf else konf.get("element")
    if not isinstance(pola, dict) or not pola:
        raise BladKonfiguracji(f"{sciezka}: oczekiwano słownika pól")
    for nazwa, spec in pola.items():
        miejsce = f"{sciezka}.{nazwa}"
        if not isinstance(spec, dict):
            raise BladKonfiguracji(f"{miejsce}: oczekiwano {{typ: ..., opis: ...}}")
        typ = spec.get("typ", "tekst")
        if typ not in TYPY_POL:
            raise BladKonfiguracji(f"{miejsce}: nieznany typ '{typ}' (dozwolone: {', '.join(sorted(TYPY_POL))})")
        if typ == "lista":
            sprawdz_konfiguracje_llm({"element": spec.get("element")}, f"{miejsce}.element")
        if typ == "wybor" and not spec.get("wartosci"):
            raise BladKonfiguracji(f"{miejsce}: typ 'wybor' wymaga listy/słownika 'wartosci'")
        if spec.get("opis_do") and not isinstance(spec.get("wartosci"), dict):
            raise BladKonfiguracji(f"{miejsce}: 'opis_do' wymaga 'wartosci' jako słownika numer: opis")
        if "walidacja" in spec:
            sprawdz_walidacje(spec["walidacja"], miejsce)

log = logging.getLogger("odwolania.llm")
KATALOG = Path(__file__).resolve().parent.parent
_stan = {"wylaczony_po_bledzie": False}


# ----------------------------------------------------------------------------- konfiguracja
def konfiguracja() -> dict | None:
    """Globalna konfiguracja LLM albo None, gdy LLM wyłączony."""
    if os.environ.get("LLM_WYLACZ") == "1" or _stan["wylaczony_po_bledzie"]:
        return None
    plik = KATALOG / "llm.yaml"
    if not plik.exists():
        return None
    k = yaml.safe_load(plik.read_text(encoding="utf-8-sig")) or {}
    if not k.get("wlaczony", False):
        return None
    k["adres"] = os.environ.get("LLM_ADRES", k.get("adres", "")).rstrip("/")
    k["tryb"] = os.environ.get("LLM_TRYB", k.get("tryb", "api"))
    k["model"] = os.environ.get("LLM_MODEL", k.get("model", ""))
    if k["tryb"] == "kolejka" and not _klucz(k):
        # bez klucza kolejka i tak odrzuci zapytania (401) - nie łączymy się wcale
        if not _stan.get("brak_klucza_zgloszony"):
            _stan["brak_klucza_zgloszony"] = True
            log.warning("LLM: brak klucza - wpisz klucz z formularza zgłoszeniowego do pliku llm.klucz "
                        "(obok run.bat); dalej same reguły i OCR")
        return None
    return k


# ----------------------------------------------------------------------------- weryfikacja
def _zloz(t: str) -> str:
    """Postać do porównań: małe litery, bez diakrytyków, cudzysłowów i nadmiarowych spacji."""
    t = unicodedata.normalize("NFKD", str(t).casefold().replace("ł", "l"))
    t = "".join(z for z in t if not unicodedata.combining(z))
    t = re.sub(r"[„”\"“'`]", "", t)
    return re.sub(r"\s+", " ", t).strip()


class Weryfikator:
    """Sprawdza, czy wartość od modelu ma pokrycie w tekście dokumentu."""

    def __init__(self, tekst: str):
        self.tekst = tekst
        self.zlozony = _zloz(tekst)
        self.daty = {data_iso(m) for m in WZ_DATA.finditer(tekst)} - {None}
        self.kwoty = {kwota(m.group()) for m in re.finditer(MAKRA["KWOTA"], tekst)} - {None}

    def _w_odmianie(self, slowa: list[str]) -> bool:
        """Imię i nazwisko w mianowniku (wymóg UZP) może stać w tekście w innym przypadku:
        "Mateusz Szymański" <- "Mateusza Szymańskiego". Każde słowo musi mieć w tekście sąsiadujący
        odpowiednik o wspólnym rdzeniu (różnica tylko w końcówce, maks. 4 znaki)."""
        tokeny = re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)?", self.zlozony)

        def pasuje(slowo: str, token: str) -> bool:
            s = _zloz(slowo)
            rdzen = max(3, len(s) - 3)
            return token[:rdzen] == s[:rdzen] and abs(len(token) - len(s)) <= 4

        for i in range(len(tokeny) - len(slowa) + 1):
            if all(pasuje(s, tokeny[i + j]) for j, s in enumerate(slowa)):
                return True
        return False

    def sprawdz(self, wartosc: Any, spec: dict) -> tuple[Any, str | None]:
        """(wartość po normalizacji, None) albo (None, powód odrzucenia): pokrycie w tekście,
        a potem warunki `walidacja` (te same co w regułach typ.yaml)."""
        v, powod = self._w_tekscie(wartosc, spec)
        if v is not None and spec.get("walidacja"):
            powod = waliduj(v, spec["walidacja"])
            if powod:
                return None, powod
        return v, powod

    def _w_tekscie(self, wartosc: Any, spec: dict) -> tuple[Any, str | None]:
        if wartosc is None or (isinstance(wartosc, str) and not wartosc.strip()):
            return None, None
        typ = spec.get("typ", "tekst")
        if typ == "wybor":
            dozwolone = spec.get("wartosci", {})
            klucze = list(dozwolone) if isinstance(dozwolone, dict) else list(dozwolone)
            for k in klucze:
                if str(k) == str(wartosc).strip():
                    return k, None
            return None, f"spoza listy {klucze}"
        w = str(wartosc).strip()
        if typ == "data":
            m = WZ_DATA.search(w)
            iso = data_iso(m) if m else None
            return (iso, None) if iso in self.daty else (None, "brak tej daty w tekście")
        if typ == "cyfry":
            c = re.sub(r"\D", "", w)
            dl = spec.get("dlugosc")
            if len(c) < 5 or (dl and len(c) != dl):
                return None, "zła długość"
            wz = r"[\s\-]?".join(c)                # dopuszcza spacje/myślniki między cyframi
            return (c, None) if re.search(rf"(?<!\d){wz}(?!\d)", self.tekst) else (None, "brak w tekście")
        if typ == "kwota":
            k = kwota(w)
            return (k, None) if k in self.kwoty else (None, "brak tej kwoty w tekście")
        if typ == "email":
            return (w, None) if w.casefold() in self.tekst.casefold() else (None, "brak w tekście")
        if typ == "osoba":
            # pole na jedną osobę: model bywa podaje kilka ("A B, C D") - bierzemy pierwszą
            w = re.split(r",|;|\s+i\s+|\s+oraz\s+", w)[0].strip()
            w = re.sub(r"^(?:adw\.|r\.\s?pr\.|radca prawny|adwokat|mec\.)\s+", "", w, flags=re.IGNORECASE)
            # imię i nazwisko: 2-4 słowa z wielkiej litery, każde min. 2 litery (odrzuca śmieci OCR)
            slowa = re.sub(r"\s+", " ", w).strip().split(" ")
            if not (2 <= len(slowa) <= 4 and all(re.fullmatch(r"[A-ZĄĆĘŁŃÓŚŹŻ][^\W\d_]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][^\W\d_]+)?", s) for s in slowa)):
                return None, "nie wygląda na imię i nazwisko"
            if _zloz(w) in self.zlozony or self._w_odmianie(slowa):
                return " ".join(slowa), None
            return None, "brak w tekście (także w innym przypadku)"
        # tekst: dosłowny cytat (po normalizacji spacji/wielkości liter/diakrytyków)
        if len(_zloz(w)) < 2:
            return None, "za krótki"
        # końcowej kropki nie obcinamy: należy do form prawnych ("sp. z o.o.", "S.A.")
        return (re.sub(r"\s+", " ", w).strip(" ,;:"), None) if _zloz(w) in self.zlozony \
            else (None, "brak dosłownie w tekście")


# ----------------------------------------------------------------------------- ścieżki w JSON
def _pobierz(d: dict, sciezka: str):
    for k in sciezka.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _ustaw(d: dict, sciezka: str, wartosc) -> None:
    cz = sciezka.split(".")
    for k in cz[:-1]:
        d = d.setdefault(k, {})
    d[cz[-1]] = wartosc


def _pusty(v) -> bool:
    return v is None or v == [] or (isinstance(v, str) and not v.strip())


# ----------------------------------------------------------------------------- prompt
def _fragment(tekst: str, konf_typu: dict, limit: int) -> str:
    """Wybrane fragmenty dokumentu (początek + okna po kotwicach), w sumie <= limit znaków."""
    okna = []
    for f in konf_typu.get("fragmenty") or [{"od": r"\A", "znakow": limit}]:
        m = re.search(f["od"], tekst, re.IGNORECASE | re.MULTILINE)
        if m:
            okna.append((m.start(), min(len(tekst), m.start() + int(f.get("znakow", 2000)))))
    okna.sort()
    scalone = []
    for a, b in okna:                              # łączenie nakładających się okien
        if scalone and a <= scalone[-1][1]:
            scalone[-1] = (scalone[-1][0], max(b, scalone[-1][1]))
        else:
            scalone.append((a, b))
    czesci = [tekst[a:b] for a, b in scalone]
    return "\n[...]\n".join(czesci)[:limit]


def _opis_pola(nazwa: str, spec: dict, wciecie: str = "") -> list[str]:
    linie = []
    if spec.get("typ") == "lista":
        linie.append(f'{wciecie}- "{nazwa}": lista obiektów. {spec.get("opis", "")}'.rstrip())
        for n, s in spec["element"].items():
            linie += _opis_pola(n, s, wciecie + "    ")
        return linie
    format_ = {"data": " Format RRRR-MM-DD.", "cyfry": " Same cyfry.", "kwota": " Liczba."}.get(spec.get("typ"), "")
    opis = f'{wciecie}- "{nazwa}": {spec.get("opis", "")}{format_}'
    if spec.get("typ") == "wybor":
        w = spec.get("wartosci", {})
        opcje = "; ".join(f"{k} = {v}" for k, v in w.items()) if isinstance(w, dict) else ", ".join(map(str, w))
        opis += f" Dozwolone wartości: {opcje}."
    return [opis]


def _szablon(spec: dict):
    if spec.get("typ") == "lista":
        return [{n: _szablon(s) for n, s in spec["element"].items()}]
    return None


def _prompt(konf_typu: dict, pola: dict, fragment: str) -> list[dict]:
    opis = [l for n, s in pola.items() for l in _opis_pola(n, s)]
    szablon = json.dumps({n: _szablon(s) for n, s in pola.items()}, ensure_ascii=False)
    system = ("Jesteś asystentem ekstrakcji danych z polskich dokumentów urzędowych. "
              "Odpowiadasz wyłącznie jednym obiektem JSON. Wartości przepisujesz dosłownie z tekstu dokumentu. "
              "Jeśli informacji nie ma w tekście, wpisujesz null. Niczego nie zgadujesz.")
    user = (f"Dokument: {konf_typu.get('opis_dokumentu', '')}\n\n"
            f"Pola do odczytania:\n" + "\n".join(opis) +
            f"\n\nZwróć JSON dokładnie o tej strukturze:\n{szablon}\n\n"
            f"TEKST DOKUMENTU:\n<<<\n{fragment}\n>>>")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ----------------------------------------------------------------------------- wywołanie API
def _klucz(k: dict) -> str:
    """Klucz API, kolejno: zmienna środowiskowa, plik lokalny (llm.klucz.lokalny, poza gitem),
    plik llm.klucz (w repozytorium jest w nim tylko tekst-wzór, np. "tutaj wpisz klucz").
    Wzór albo tekst ze spacjami nie jest kluczem -> zwracamy pusty napis."""
    klucz = os.environ.get(k.get("klucz_zmienna", "LLM_API_KEY"), "").strip()
    for nazwa in (k.get("klucz_plik_lokalny", "llm.klucz.lokalny"), k.get("klucz_plik", "llm.klucz")):
        if klucz:
            break
        plik = KATALOG / nazwa
        tekst = plik.read_text(encoding="utf-8-sig").strip() if plik.exists() else ""
        klucz = tekst.splitlines()[0].strip() if tekst else ""
    return "" if _to_wzor_klucza(klucz) else klucz


def _to_wzor_klucza(klucz: str) -> bool:
    return bool(klucz) and (bool(re.search(r"\s", klucz)) or klucz.casefold().startswith(("tutaj", "wpisz")))


class _BladModelu(Exception):
    """Model nie policzył odpowiedzi dla tego dokumentu (np. za długi prompt)."""


class _PracownikNieaktywny(Exception):
    """Kolejka działa, ale nikt nie pobiera zadań (komputer z modelem wyłączony)."""


def _json_z(url: str, naglowki: dict, metoda: str = "GET", dane: dict | None = None, limit: float = 30):
    req = urllib.request.Request(url, data=json.dumps(dane).encode() if dane is not None else None,
                                 headers=naglowki, method=metoda)
    with urllib.request.urlopen(req, timeout=limit) as r:
        return json.loads(r.read().decode("utf-8"))


def _wyslij_bezposrednio(k: dict, tresc: dict, naglowki: dict) -> dict:
    """API zgodne z OpenAI: jedno zapytanie, odpowiedź od razu."""
    return _json_z(f"{k['adres']}/chat/completions", naglowki, "POST", tresc, k.get("limit_czasu_s", 120))


def _wyslij_kolejka(k: dict, tresc: dict, naglowki: dict) -> dict:
    """Kolejka na hostingu (serwer_kolejki/kolejka.php): zadanie -> odpytywanie o wynik.

    Komputer z modelem sam pobiera zadania (tylko połączenia wychodzące). Szybkie wykrycie, że nikt
    ich nie pobiera: test ?a=zdrowie przy pierwszym zapytaniu i limit czekania w kolejce.
    """
    adres = k["adres"]
    if "zdrowie_sprawdzone" not in _stan:
        _stan["zdrowie_sprawdzone"] = True
        zdrowie = _json_z(f"{adres}?a=zdrowie", {"User-Agent": naglowki["User-Agent"]}, limit=15)
        if not zdrowie.get("ok"):
            raise _PracownikNieaktywny(f"pracownik kolejki nieaktywny ({zdrowie})")
    id_zadania = _json_z(f"{adres}?a=zadanie", naglowki, "POST", tresc)["id"]
    start = time.monotonic()
    odstep = float(k.get("odstep_odpytywania_s", 0.5))
    while True:
        time.sleep(odstep)
        st = _json_z(f"{adres}?a=wynik&id={id_zadania}", naglowki)
        status = st.get("status")
        if status == "gotowe":
            return st["odpowiedz"]
        if status == "blad":
            raise _BladModelu(st.get("blad"))
        uplynelo = time.monotonic() - start
        if status == "czeka" and uplynelo > float(k.get("limit_czekania_s", 20)):
            raise _PracownikNieaktywny(f"zadanie nie zostało podjęte w {uplynelo:.0f} s")
        if uplynelo > float(k.get("limit_czasu_s", 180)):
            raise TimeoutError(f"brak wyniku z kolejki po {uplynelo:.0f} s")


def _zapytaj(k: dict, wiadomosci: list[dict]) -> dict | None:
    tresc = {"model": k["model"], "messages": wiadomosci, "temperature": k.get("temperatura", 0),
             "seed": k.get("ziarno", 0), "max_tokens": k.get("max_tokenow", 1200)}
    if k.get("tryb_json", True):
        tresc["response_format"] = {"type": "json_object"}
    klucz_cache = hashlib.sha256(json.dumps([k["adres"], tresc], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache = KATALOG / k.get("cache", ".cache_llm") / f"{klucz_cache}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    naglowki = {"Content-Type": "application/json", "User-Agent": "odwolania2json"}
    klucz = _klucz(k)
    if klucz:
        naglowki["Authorization"] = f"Bearer {klucz}"
        naglowki["X-Api-Key"] = klucz                # hostingi współdzielone bywa gubią Authorization
    try:
        if k.get("tryb") == "kolejka":
            odp = _wyslij_kolejka(k, tresc, naglowki)
        else:
            odp = _wyslij_bezposrednio(k, tresc, naglowki)
    except _PracownikNieaktywny as e:
        log.warning("LLM: %s — wyłączam LLM do końca przebiegu", e)
        _stan["wylaczony_po_bledzie"] = True
        return None
    except _BladModelu as e:
        log.warning("LLM: błąd modelu dla tego dokumentu (%s) — same reguły", e)
        return None
    except urllib.error.HTTPError as e:
        tresc_bledu = e.read().decode("utf-8", "replace")[:300]
        e.close()
        if e.code in (401, 403, 404):              # zły klucz / adres / model: dotyczy wszystkich zapytań
            log.warning("LLM: HTTP %s (%s) — wyłączam LLM do końca przebiegu", e.code, tresc_bledu)
            _stan["wylaczony_po_bledzie"] = True
        else:                                      # np. za długie zapytanie: pomijamy tylko ten dokument
            log.warning("LLM: HTTP %s dla tego dokumentu (%s) — same reguły", e.code, tresc_bledu)
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        # zdalny model: pojedyncza czkawka sieci nie może wyłączyć LLM dla całego przebiegu
        _stan["bledy_z_rzedu"] = _stan.get("bledy_z_rzedu", 0) + 1
        if _stan["bledy_z_rzedu"] >= int(k.get("prog_wylaczenia", 2)):
            _stan["wylaczony_po_bledzie"] = True
            log.warning("LLM niedostępny (%s): %s — %d błędy z rzędu, dalej same reguły",
                        k["adres"], e, _stan["bledy_z_rzedu"])
        else:
            log.warning("LLM niedostępny (%s): %s — ten dokument bez modelu", k["adres"], e)
        return None
    _stan["bledy_z_rzedu"] = 0
    tekst = odp["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", tekst, re.DOTALL)
    try:
        wynik = json.loads(m.group() if m else tekst)
    except ValueError:
        log.warning("LLM zwrócił niepoprawny JSON: %s", tekst[:200])
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(wynik, ensure_ascii=False), encoding="utf-8")
    return wynik


# ----------------------------------------------------------------------------- uzupełnianie
def _waliduj_liste(wartosc, spec: dict, wer: Weryfikator, sciezka: str, raport: list) -> list:
    if not isinstance(wartosc, list):
        return []
    wynik = []
    for i, el in enumerate(wartosc[: spec.get("max", 20)]):
        if not isinstance(el, dict):
            continue
        obiekt, potwierdzone = {}, 0
        for n, s in spec["element"].items():
            v = el.get(n)
            if s.get("typ") == "lista":
                v = _waliduj_liste(v, s, wer, f"{sciezka}[{i}].{n}", raport)
                potwierdzone += bool(v)
            else:
                v_ok, powod = wer.sprawdz(v, s)
                _wpis(raport, f"{sciezka}[{i}].{n}", v, v_ok, powod)
                if v_ok is not None and s.get("typ") != "wybor":
                    potwierdzone += 1
                v = v_ok
            _ustaw(obiekt, n, v)
        if potwierdzone:                           # element bez żadnej potwierdzonej wartości odrzucamy
            wynik.append(obiekt)
    if len(wynik) == 1:                            # pola sensowne tylko przy kilku elementach (np. lider)
        for n, s in spec["element"].items():
            if s.get("tylko_gdy_wiele"):
                _ustaw(wynik[0], n, None)
    return wynik


def _wpis(raport: list, pole: str, z_modelu, przyjete, powod) -> None:
    if z_modelu is None or z_modelu == "":
        return
    raport.append({"zrodlo": "model", "pole": pole, "z_modelu": z_modelu, "przyjete": przyjete is not None,
                   **({"powod_odrzucenia": powod} if powod else {})})


def uzupelnij(tekst: str, wynik: dict, konf_typu: dict) -> list[dict]:
    """Uzupełnia puste pola wyniku (w miejscu). Zwraca raport: co podał model i czy przyjęto."""
    k = konfiguracja()
    if not k or not konf_typu.get("pola"):
        return []
    brakujace = {n: s for n, s in konf_typu["pola"].items() if _pusty(_pobierz(wynik, n))}
    if not brakujace:
        return []
    # budżet: kontekst modelu musi pomieścić prompt (instrukcje + fragment) i odpowiedź
    narzut = sum(len(m["content"]) for m in _prompt(konf_typu, brakujace, ""))
    budzet_tokenow = int(k.get("kontekst_tokenow", 4096)) - int(k.get("max_tokenow", 800)) - 100
    budzet = int(budzet_tokenow * float(k.get("znakow_na_token", 1.5))) - narzut
    limit = min(int(k.get("max_znakow_wejscia", 6000)), budzet)
    if limit < 500:
        log.warning("LLM: za mały kontekst na %d pól (budżet %d znaków) — pomijam", len(brakujace), budzet)
        return []
    fragment = _fragment(tekst, konf_typu, limit)
    odp = _zapytaj(k, _prompt(konf_typu, brakujace, fragment))
    if not odp:
        return []
    wer, raport = Weryfikator(tekst), []
    for n, s in brakujace.items():
        v = odp.get(n, _pobierz(odp, n))
        if s.get("typ") == "lista":
            lista = _waliduj_liste(v, s, wer, n, raport)
            if lista:
                _ustaw(wynik, n, lista)
            continue
        v_ok, powod = wer.sprawdz(v, s)
        _wpis(raport, n, v, v_ok, powod)
        if v_ok is not None:
            _ustaw(wynik, n, v_ok)
            if s.get("opis_do") and isinstance(s.get("wartosci"), dict):   # np. id powodu -> opis
                _ustaw(wynik, s["opis_do"], s["wartosci"][v_ok])
    log.info("LLM: %d pól od modelu, przyjęto %d", len(raport), sum(r["przyjete"] for r in raport))
    return raport
