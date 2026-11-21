"""Wspólne narzędzia do normalizacji wartości (daty, kwoty, rachunki)."""
from __future__ import annotations

import re

MIESIACE = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "wrzesnia": 9, "października": 10,
    "pazdziernika": 10, "listopada": 11, "grudnia": 12,
}

# Wzorzec daty: 2026-03-21 | 21.03.2026 | 21-03-2026 | 21/03/2026 | 21 marca 2026
WZ_DATA = re.compile(
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<kr>(?<![\d/])\d{1,2}[./\-]\d{1,2}[./\-]\d{4}(?![\d/]))"
    r"|(?P<sl>\d{1,2}\s+(?:" + "|".join(MIESIACE) + r")\s+\d{4})",
    re.IGNORECASE,
)


def data_iso(m: re.Match) -> str | None:
    """Zamienia dopasowanie WZ_DATA na YYYY-MM-DD (None, gdy data niepoprawna)."""
    try:
        if m.group("iso"):
            r, mi, d = m.group("iso").split("-")
        elif m.group("kr"):
            d, mi, r = re.split(r"[./\-]", m.group("kr"))
        else:
            d, mi, r = m.group("sl").split()
            mi = MIESIACE[mi.lower()]
        d, mi, r = int(d), int(mi), int(r)
        if not (1 <= mi <= 12 and 1 <= d <= 31):
            return None
        return f"{r:04d}-{mi:02d}-{d:02d}"
    except (ValueError, KeyError):
        return None


def kwota(tekst: str) -> float | None:
    """'1 200,50' / '17.00' / '200,00' -> float."""
    s = tekst.replace(" ", " ").strip()
    s = re.sub(r"(?<=\d)[ ](?=\d{3}\b)", "", s)
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def tylko_cyfry_rachunku(tekst: str) -> str:
    """'PL66 2111 8080 ...' -> '66211180...' (26 cyfr NRB, bez prefiksu PL)."""
    return re.sub(r"\D", "", re.sub(r"^\s*PL", "", tekst.strip(), flags=re.IGNORECASE))
