"""Silnik ekstrakcji sterowany konfiguracją YAML (nowy typ dokumentu bez kodu).

Plik typy/<nazwa>/typ.yaml (pełny opis z przykładami: docs/NOWY_TYP.md):

    klasyfikacja: {slowa_kluczowe: [..], wymagane_na_poczatku: [..], ...}
    sekcje:                          # nazwane fragmenty tekstu (pomocnicze, nie trafiają do wyniku)
      naglowek: {regex: '\\A([\\s\\S]*?)(?=^dotyczy|\\Z)'}
    pola:                            # ścieżka z kropkami -> reguła
      a.numer:
        regex: ['wzorzec1', 'wzorzec2']   # pierwszy pasujący wygrywa
        w: [sekcja1, dokument]            # gdzie szukać (kolejno); domyślnie cały dokument
        grupa: 1 | format: '{1}/BZP{2}'   # wartość = grupa albo złożenie grup
        typ: tekst|data|kwota|liczba|cyfry|rachunek|bool
        szukaj_w: plaski                  # białe znaki -> spacja przed szukaniem
        pomin: ['regex']                  # fragmenty wymazywane przed szukaniem
        flagi: [dotall]                   # ignorecase i multiline są domyślne; (?-i:...) wyłącza lokalnie
        wszystkie: true                   # lista wszystkich trafień
        mapa: [[wartość, 'regex'], ...]   # wartość wg pierwszej pasującej reguły
        z_pola: a.id + slownik: {1: opis} # wartość wyliczona z innego pola
        walidacja: {dlugosc: 10, wzorzec: '..', wartosci: [..], zakres: [od, do], suma_kontrolna: nip}
                                          # wartość niespełniająca warunków jest pomijana (szukanie trwa dalej)
        domyslnie: null
    listy:                           # listy obiektów
      strony:
        regex: ['alternatywa1', ...]      # pierwsza alternatywa z trafieniami wygrywa; 1 trafienie = 1 element
        w / szukaj_w / pomin / flagi      # jak w polach
        pola:
          nazwa: nazwa_grupy              # wartość z grupy nazwanej
          krs: {regex: 'KRS (\\d{10})', w: segment}   # reguła w obrębie elementu:
              # w: dopasowanie (tekst trafienia) | segment (do następnego trafienia) | <grupa> | <sekcja>
          rola: {z_grupy: etykieta, mapa: [[1, 'lider']], tylko_gdy_wiele: true}
          osoby: {lista: {regex: ..., w: podpisy, od: 'Za {nazwa}', do: '...', pola: {...}}}
        typy: {pole: typ}, unikalne_po: [pole], max: 5

Makra w regexach: {DATA}, {KWOTA}, {RACHUNEK}. Każda wartość pochodzi z tekstu dokumentu.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from silnik.narzedzia import SUMY_KONTROLNE, WZ_DATA, data_iso, kwota, rachunek

MAKRA = {
    "DATA": WZ_DATA.pattern,
    # NRB (26 cyfr, z "PL" albo bez, ze spacjami albo bez) albo IBAN innego kraju (kod + grupy po 4 znaki)
    # (kod kraju nie może stać w środku dłuższego ciągu, np. numeru transakcji "CEN2603...")
    "RACHUNEK": r"(?:(?:PL)?\s?(?:\d\s?){26}|(?<![A-Za-z0-9])(?-i:(?!PL)[A-Z]{2}\d{2})(?: ?(?-i:[A-Z0-9]{4})){2,7}"
                r"(?: ?(?-i:[A-Z0-9]{1,3}))?(?![A-Za-z0-9]))",
    "KWOTA": r"\d[\d ]*[.,]\d{2}",
}
FLAGI = {"ignorecase": re.IGNORECASE, "multiline": re.MULTILINE, "dotall": re.DOTALL}
DOMYSLNE_FLAGI = re.IGNORECASE | re.MULTILINE
ZRODLA_ELEMENTU = {"dopasowanie", "segment"}


class BladKonfiguracji(ValueError):
    pass


@lru_cache(maxsize=4096)
def _kompiluj_cache(wzorzec: str, flagi: tuple) -> re.Pattern:
    for nazwa, tekst in MAKRA.items():
        wzorzec = wzorzec.replace("{" + nazwa + "}", f"(?:{tekst})")
    f = DOMYSLNE_FLAGI
    for nazwa in flagi:
        if nazwa not in FLAGI:
            raise BladKonfiguracji(f"nieznana flaga: {nazwa}")
        f |= FLAGI[nazwa]
    try:
        return re.compile(wzorzec, f)
    except re.error as e:
        raise BladKonfiguracji(f"błędny regex {wzorzec!r}: {e}") from e


def _kompiluj(wzorzec: str, flagi: list[str] | None) -> re.Pattern:
    return _kompiluj_cache(wzorzec, tuple(flagi or ()))


def normalizuj(wartosc: str | None, typ: str | None) -> Any:
    """Zamienia znaleziony tekst na wartość o wskazanym typie (None, gdy się nie da)."""
    if wartosc is None:
        return None
    w = re.sub(r"\s+", " ", wartosc).strip()
    if not typ or typ == "tekst":
        return w or None
    if typ == "data":
        m = WZ_DATA.search(w)
        return data_iso(m) if m else None
    if typ == "kwota":
        return kwota(w)
    if typ == "liczba":
        try:
            return int(w)
        except ValueError:
            return None
    if typ == "cyfry":
        c = re.sub(r"\D", "", w)
        return c or None
    if typ == "rachunek":                       # bez spacji; kod kraju (PL) tylko, gdy jest w dokumencie
        return rachunek(w)
    if typ == "bool":
        return w.casefold() in {"tak", "1", "true"}
    raise BladKonfiguracji(f"nieznany typ wartości: {typ}")


# ----------------------------------------------------------------------------- walidacja
KLUCZE_WALIDACJI = {"dlugosc", "wzorzec", "wartosci", "zakres", "suma_kontrolna"}


def _nie_wieksze(a, b) -> bool:
    """a <= b: liczbowo, a gdy się nie da - jako tekst (daty ISO porównują się poprawnie)."""
    try:
        return float(a) <= float(b)
    except (TypeError, ValueError):
        return str(a) <= str(b)


def waliduj(wartosc: Any, warunki: dict | None) -> str | None:
    """Powód odrzucenia wartości wg klucza `walidacja` (None = wartość poprawna albo brak warunków).

    dlugosc: 10 | [9, 14]   długość napisu wartości (po normalizacji, np. same cyfry)
    wzorzec: 'regex'        cała wartość musi pasować (makra {DATA}, {KWOTA} działają)
    wartosci: [..]          dozwolone wartości (bez rozróżniania wielkości liter)
    zakres: [od, do]        liczby albo daty ISO; null = bez ograniczenia z tej strony
    suma_kontrolna: nip | regon | pesel | rachunek   (rachunek: IBAN / NRB, mod 97)
    """
    if not warunki or wartosc is None:
        return None
    s = str(wartosc)
    if warunki.get("dlugosc") is not None:
        dozwolone = warunki["dlugosc"] if isinstance(warunki["dlugosc"], list) else [warunki["dlugosc"]]
        if len(s) not in dozwolone:
            return f"zła długość {len(s)} (oczekiwano {'/'.join(map(str, dozwolone))})"
    if warunki.get("wzorzec") and not _kompiluj(warunki["wzorzec"], None).fullmatch(s):
        return f"nie pasuje do wzorca {warunki['wzorzec']}"
    if warunki.get("wartosci") is not None and s.casefold() not in {str(x).casefold() for x in warunki["wartosci"]}:
        return "spoza dozwolonych wartości"
    if warunki.get("zakres") is not None:
        od, do = warunki["zakres"]
        if not ((od is None or _nie_wieksze(od, wartosc)) and (do is None or _nie_wieksze(wartosc, do))):
            return f"poza zakresem [{od}, {do}]"
    suma = warunki.get("suma_kontrolna")
    if suma and not SUMY_KONTROLNE[suma](wartosc):
        return f"błędna suma kontrolna ({suma})"
    return None


def sprawdz_walidacje(warunki: Any, miejsce: str) -> None:
    """Błąd w kluczu `walidacja` zgłaszany przy wczytaniu typu, z nazwą pola i problemu."""
    m = f"{miejsce}.walidacja"
    if not isinstance(warunki, dict) or not warunki:
        raise BladKonfiguracji(f"{m}: oczekiwano słownika warunków ({', '.join(sorted(KLUCZE_WALIDACJI))})")
    nieznane = set(warunki) - KLUCZE_WALIDACJI
    if nieznane:
        raise BladKonfiguracji(f"{m}: nieznany warunek {', '.join(sorted(nieznane))} "
                               f"(dozwolone: {', '.join(sorted(KLUCZE_WALIDACJI))})")
    dl = warunki.get("dlugosc")
    if dl is not None and not (isinstance(dl, int) or (isinstance(dl, list) and dl and all(isinstance(x, int) for x in dl))):
        raise BladKonfiguracji(f"{m}.dlugosc: liczba albo lista liczb, np. 10 lub [9, 14]")
    if warunki.get("wzorzec") is not None:
        _kompiluj(str(warunki["wzorzec"]), None)
    if warunki.get("wartosci") is not None and not isinstance(warunki["wartosci"], list):
        raise BladKonfiguracji(f"{m}.wartosci: lista dozwolonych wartości, np. [1, 2, 3]")
    if warunki.get("zakres") is not None and not (isinstance(warunki["zakres"], list) and len(warunki["zakres"]) == 2):
        raise BladKonfiguracji(f"{m}.zakres: dwie granice [od, do], np. [1, 12] albo ['2020-01-01', null]")
    if warunki.get("suma_kontrolna") is not None and warunki["suma_kontrolna"] not in SUMY_KONTROLNE:
        raise BladKonfiguracji(f"{m}.suma_kontrolna: jedna z {', '.join(sorted(SUMY_KONTROLNE))}")


def _ustaw(wynik: dict, sciezka: str, wartosc: Any) -> None:
    cz = sciezka.split(".")
    d = wynik
    for k in cz[:-1]:
        d = d.setdefault(k, {})
    d[cz[-1]] = wartosc


def _pobierz(d: dict, sciezka: str):
    for k in sciezka.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _lista_wzorcow(w) -> list[str]:
    return [] if w is None else ([w] if isinstance(w, str) else list(w))


# ----------------------------------------------------------------------------- dopasowanie
def _przygotuj(tekst: str | None, regula: dict) -> str | None:
    """Spłaszczenie (szukaj_w: plaski) i wymazanie fragmentów 'pomin' (pozycje zachowane)."""
    if tekst is None:
        return None
    if regula.get("szukaj_w") == "plaski":
        tekst = re.sub(r"\s+", " ", tekst)
    for wz in _lista_wzorcow(regula.get("pomin")):
        tekst = _kompiluj(wz, regula.get("flagi")).sub(lambda m: " " * len(m.group()), tekst)
    return tekst


def _zamien(wartosc: str | None, regula: dict) -> str | None:
    """Przekształcenia wartości: zamien: [[regex, zastąpienie], ...] (np. usunięcie dopisku, spacji)."""
    if wartosc is None:
        return None
    for wz, na in regula.get("zamien") or []:
        wartosc = _kompiluj(wz, regula.get("flagi")).sub(na, wartosc)
    return wartosc


def _surowa_wartosc(m: re.Match, regula: dict) -> str | None:
    """Tekst wartości z trafienia: grupa (domyślnie 1 albo całość) lub złożenie grup (format)."""
    if regula.get("format"):
        grupy = [m.group(0)] + [g or "" for g in m.groups()]
        return _zamien(regula["format"].format(*grupy, **{k: v or "" for k, v in m.groupdict().items()}), regula)
    grupa = regula.get("grupa")
    if grupa is None:
        grupa = 1 if m.re.groups else 0
    try:
        return _zamien(m.group(grupa), regula)
    except IndexError:
        raise BladKonfiguracji(f"brak grupy {grupa!r} w regexie {m.re.pattern!r}") from None


class Kontekst:
    """Tekst dokumentu, nazwane sekcje i już wyliczone pola (dla z_pola)."""

    def __init__(self, tekst: str):
        self.sekcje: dict[str, str | None] = {"dokument": tekst}
        self.wynik: dict = {}
        self.odrzucone: list[dict] = []           # wartości odrzucone przez `walidacja` (do raportu)

    def poprawna(self, wartosc: Any, regula: dict, pole: str) -> bool:
        """Czy wartość spełnia `walidacja` reguły; odrzuconą zapisuje z powodem."""
        powod = waliduj(wartosc, regula.get("walidacja"))
        if powod:
            self.odrzucone.append({"pole": pole, "wartosc": wartosc, "powod_odrzucenia": powod})
        return powod is None

    def zrodla(self, w, dodatkowe: dict | None = None) -> list[str | None]:
        nazwy = _lista_wzorcow(w) or ["dokument"]
        dostepne = {**self.sekcje, **(dodatkowe or {})}
        return [dostepne.get(n) for n in nazwy]


def _wartosc(ctx: Kontekst, regula: dict, dodatkowe: dict | None = None, pole: str = "") -> Any:
    """Wartość pola wg reguły (regex / mapa / z_pola) w kolejnych źródłach.
    Z `walidacja` wygrywa pierwsza POPRAWNA wartość (także kolejne trafienia tego samego regexu)."""
    if "z_pola" in regula:
        v = _pobierz(ctx.wynik, regula["z_pola"])
        slownik = regula.get("slownik") or {}
        v = slownik.get(v, slownik.get(str(v), regula.get("domyslnie")))
        return v if v is None or ctx.poprawna(v, regula, pole) else None
    zrodla = [_przygotuj(z, regula) for z in ctx.zrodla(regula.get("w"), dodatkowe)]
    if "mapa" in regula:
        for zrodlo in zrodla:
            if zrodlo is None:
                continue
            for wartosc, wz in regula["mapa"]:
                if _kompiluj(wz, regula.get("flagi")).search(zrodlo) and ctx.poprawna(wartosc, regula, pole):
                    return wartosc
        return regula.get("domyslnie")
    typ = regula.get("typ")
    for zrodlo in zrodla:
        if zrodlo is None:
            continue
        for wz in _lista_wzorcow(regula.get("regex")):
            rx = _kompiluj(wz, regula.get("flagi"))
            if regula.get("wszystkie"):
                wyniki = []
                for m in rx.finditer(zrodlo):
                    v = normalizuj(_surowa_wartosc(m, regula), typ)
                    if v is not None and v not in wyniki and ctx.poprawna(v, regula, pole):
                        wyniki.append(v)
                if wyniki:
                    return wyniki
            else:
                # bez walidacji tylko pierwsze trafienie (jak dotąd); z walidacją - pierwsze poprawne
                trafienia = rx.finditer(zrodlo) if regula.get("walidacja") else filter(None, [rx.search(zrodlo)])
                for m in trafienia:
                    v = normalizuj(_surowa_wartosc(m, regula), typ)
                    if v is not None and ctx.poprawna(v, regula, pole):
                        return v
    return regula.get("domyslnie")


def _sekcja(ctx: Kontekst, regula: dict) -> str | None:
    """Surowy tekst sekcji (bez normalizacji: zachowuje nowe linie)."""
    for zrodlo in (_przygotuj(z, regula) for z in ctx.zrodla(regula.get("w"))):
        if zrodlo is None:
            continue
        for wz in _lista_wzorcow(regula.get("regex")):
            m = _kompiluj(wz, regula.get("flagi")).search(zrodlo)
            if m:
                return _surowa_wartosc(m, regula)
    return None


# ----------------------------------------------------------------------------- listy
def _podfragment(tekst: str | None, regula: dict, wartosci: dict) -> str | None:
    """Fragment od 'od' do 'do' (wzorce mogą zawierać {pole} elementu nadrzędnego)."""
    if tekst is None:
        return None
    zamien = {k: re.escape(str(v)) for k, v in wartosci.items() if v is not None}

    def wzorzec(w: str) -> str | None:
        try:
            return w.format(**zamien) if "{" in w else w
        except (KeyError, IndexError):
            return None                             # brak wartości pola nadrzędnego

    if regula.get("od"):
        wz = wzorzec(regula["od"])
        m = _kompiluj(wz, regula.get("flagi")).search(tekst) if wz else None
        if m:
            tekst = tekst[m.end():]
    if regula.get("do"):
        wz = wzorzec(regula["do"])
        m = _kompiluj(wz, regula.get("flagi")).search(tekst) if wz else None
        if m:
            tekst = tekst[: m.start()]
    return tekst


def _alternatywy(regula: dict) -> list[dict]:
    """Warianty regexu listy; wariant może być słownikiem z własnym w / szukaj_w / od / do / flagi."""
    wynik = []
    for a in ([regula["regex"]] if isinstance(regula.get("regex"), (str, dict)) else regula.get("regex") or []):
        wynik.append({**regula, **a} if isinstance(a, dict) else {**regula, "regex": a})
    return wynik


def _lista(ctx: Kontekst, regula: dict, dodatkowe: dict | None = None, wartosci_nadrz: dict | None = None,
           sciezka: str = "") -> list[dict]:
    typy = regula.get("typy") or {}
    # warianty po kolei (pierwszy, który coś znalazł, wygrywa); w każdym wariancie źródła po kolei
    for alt in _alternatywy(regula):
        zrodla = ctx.zrodla(alt.get("w"), dodatkowe)
        if alt.get("od") or alt.get("do"):
            zrodla = [_podfragment(z, alt, wartosci_nadrz or {}) for z in zrodla]
        for zrodlo in (_przygotuj(z, alt) for z in zrodla):
            if zrodlo is None:
                continue
            trafienia = list(_kompiluj(alt["regex"], alt.get("flagi")).finditer(zrodlo))
            if not trafienia:
                continue
            wynik: list[dict] = []
            for i, m in enumerate(trafienia):
                koniec = trafienia[i + 1].start() if i + 1 < len(trafienia) else len(zrodlo)
                lokalne = {**(dodatkowe or {}), "dopasowanie": m.group(0), "segment": zrodlo[m.start():koniec],
                           **{k: v for k, v in m.groupdict().items()}}
                obiekt: dict = {}
                plaskie: dict = {}                    # wartości do wzorców {pole} w listach zagnieżdżonych
                for pole, spec in regula["pola"].items():
                    sciezka_pola = f"{sciezka}[{i}].{pole}"
                    if isinstance(spec, str):          # nazwa grupy
                        v = normalizuj(m.groupdict().get(spec), typy.get(pole))
                    elif "lista" in spec:
                        v = _lista(ctx, spec["lista"], lokalne, plaskie, sciezka_pola)
                    elif "z_grupy" in spec:
                        tekst_grupy = m.groupdict().get(spec["z_grupy"])
                        if "mapa" in spec:
                            v = _wartosc(ctx, {**spec, "w": "_grupa"}, {"_grupa": tekst_grupy}, sciezka_pola) \
                                if tekst_grupy is not None else spec.get("domyslnie")
                        else:
                            v = normalizuj(_zamien(tekst_grupy, spec), spec.get("typ") or typy.get(pole))
                            if v is not None and not ctx.poprawna(v, spec, sciezka_pola):
                                v = spec.get("domyslnie")
                    else:
                        v = _wartosc(ctx, {"w": "dopasowanie", **spec}, lokalne, sciezka_pola)
                    _ustaw(obiekt, pole, v)
                    plaskie[pole.replace(".", "_")] = v
                    plaskie[pole.split(".")[-1]] = v
                klucz = tuple(str(_pobierz(obiekt, p)) for p in regula.get("unikalne_po") or [])
                if regula.get("unikalne_po") and any(
                        tuple(str(_pobierz(o, p)) for p in regula["unikalne_po"]) == klucz for o in wynik):
                    continue
                if regula.get("unikalne") and obiekt in wynik:
                    continue
                wynik.append(obiekt)
            wynik = wynik[: regula.get("max", len(wynik))]
            if len(wynik) == 1:                       # pola sensowne tylko przy kilku elementach
                for pole, spec in regula["pola"].items():
                    if isinstance(spec, dict) and spec.get("tylko_gdy_wiele"):
                        _ustaw(wynik[0], pole, None)
            return wynik
    return []


# ----------------------------------------------------------------------------- całość
def ekstrahuj(tekst: str, konf: dict, odrzucone: list | None = None) -> dict:
    """Buduje wynik JSON dla dokumentu wg konfiguracji typu.
    `odrzucone` (opcjonalnie): lista, do której trafiają wartości odrzucone przez `walidacja`."""
    ctx = Kontekst(tekst)
    for nazwa, regula in (konf.get("sekcje") or {}).items():
        ctx.sekcje[nazwa] = _sekcja(ctx, regula)
    for sciezka, regula in (konf.get("pola") or {}).items():
        _ustaw(ctx.wynik, sciezka, _wartosc(ctx, regula, pole=sciezka))
    for sciezka, regula in (konf.get("listy") or {}).items():
        _ustaw(ctx.wynik, sciezka, _lista(ctx, regula, sciezka=sciezka))
    if odrzucone is not None:
        odrzucone.extend(ctx.odrzucone)
    return ctx.wynik


def _sprawdz_regule(regula: dict, miejsce: str, sekcje: set[str], zrodla_extra: set[str]) -> None:
    if not isinstance(regula, dict):
        raise BladKonfiguracji(f"{miejsce}: oczekiwano słownika reguły")
    for w in _lista_wzorcow(regula.get("w")):
        if w not in sekcje | zrodla_extra:
            raise BladKonfiguracji(f"{miejsce}: nieznane źródło w: '{w}' (dostępne: {', '.join(sorted(sekcje | zrodla_extra))})")
    for w in _lista_wzorcow(regula.get("regex")) + _lista_wzorcow(regula.get("pomin")):
        _kompiluj(w, regula.get("flagi"))
    for _, w in regula.get("mapa") or []:
        _kompiluj(w, regula.get("flagi"))
    for w, _ in regula.get("zamien") or []:
        _kompiluj(w, regula.get("flagi"))
    normalizuj("1", regula.get("typ"))
    if "walidacja" in regula:
        sprawdz_walidacje(regula["walidacja"], miejsce)
    if not ({"regex", "mapa", "z_pola", "z_grupy", "lista"} & set(regula)):
        raise BladKonfiguracji(f"{miejsce}: reguła wymaga 'regex', 'mapa', 'z_pola' lub 'z_grupy'")


def sprawdz_konfiguracje(konf: dict) -> None:
    """Kompiluje wzorce i sprawdza odwołania, by błąd wyszedł od razu przy wczytaniu typu."""
    sekcje = {"dokument"}
    for nazwa, regula in (konf.get("sekcje") or {}).items():
        _sprawdz_regule(regula, f"sekcje.{nazwa}", sekcje, set())
        sekcje.add(nazwa)
    for sciezka, regula in (konf.get("pola") or {}).items():
        _sprawdz_regule(regula, f"pola.{sciezka}", sekcje, set())

    def lista(regula, miejsce, extra):
        if not isinstance(regula.get("pola"), dict) or not regula["pola"]:
            raise BladKonfiguracji(f"{miejsce}: lista wymaga słownika 'pola'")
        grupy = set()
        for i, alt in enumerate(_alternatywy(regula)):
            _sprawdz_regule({k: v for k, v in alt.items() if k != "pola"}, f"{miejsce}.regex[{i}]", sekcje, extra)
            grupy |= set(_kompiluj(alt["regex"], alt.get("flagi")).groupindex)
        lokalne = extra | ZRODLA_ELEMENTU | grupy
        for pole, spec in regula["pola"].items():
            if isinstance(spec, str):
                if spec not in grupy:
                    raise BladKonfiguracji(f"{miejsce}.pola.{pole}: brak grupy '{spec}' w regex listy")
            elif "lista" in spec:
                lista(spec["lista"], f"{miejsce}.pola.{pole}.lista", lokalne)
            else:
                _sprawdz_regule(spec, f"{miejsce}.pola.{pole}", sekcje, lokalne)

    for sciezka, regula in (konf.get("listy") or {}).items():
        lista(regula, f"listy.{sciezka}", set())
