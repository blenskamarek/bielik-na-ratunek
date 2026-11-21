---
name: nowy-typ
description: Dodaje nowy typ dokumentu do ODWOŁANIA2JSON wyłącznie konfiguracją YAML (bez zmian w kodzie) - od przykładowego PDF-a i oczekiwanego JSON-a do sprawdzonego typu z F1 i testem regresji. Użyj, gdy administrator chce, żeby program rozpoznawał i wyciągał dane z nowego rodzaju dokumentu.
---

# Nowy typ dokumentu (tylko YAML)

Cel: katalog `typy/<nazwa>/typ.yaml`, który klasyfikuje i wyciąga pola nowego dokumentu, bez żadnej
zmiany w `silnik/`, `run.py` ani w istniejących typach. Pełna składnia: `docs/NOWY_TYP.md`.

## Potrzebne od administratora
- 1+ przykładowy PDF nowego typu (lepiej 2–3 różne),
- oczekiwany JSON dla przykładu (struktura i nazwy pól). Jeśli go nie ma: poproś o listę pól
  i ich znaczenie, **nie wymyślaj nazw pól**,
- nazwa typu (małe litery, cyfry, `_`).

## Kroki
1. Szkielet:
   `py narzedzia\nowy_typ.py nowy <nazwa> --pdf <pdf> [--pdf ...] --wzorzec <json> [--wzorzec ...]`
2. Przeczytaj `typy/<nazwa>/przyklady/*.txt` (tekst dokumentu, także z OCR) i wzorzec.
3. Uzupełnij w `typ.yaml`:
   - `klasyfikacja.slowa_kluczowe` – 4–6 zwrotów charakterystycznych dla tego dokumentu, rzadkich w innych,
   - `klasyfikacja.wymagane_na_poczatku` – tytuł lub nagłówek z pierwszych linii,
   - `llm.opis_dokumentu` i `opis` każdego pola (z nazwą etykiety z dokumentu), `llm.fragmenty`
     obejmujące sekcje z potrzebnymi wartościami.
4. `py narzedzia\nowy_typ.py sprawdz typy\<nazwa>` – typ działa przez model. Zapisz F1 jako punkt wyjścia.
5. Dla pól o stałej etykiecie dopisz reguły w `pola:` (regex w pojedynczych cudzysłowach; `{DATA}`,
   `{KWOTA}`; `flagi: [dotall]` dla wartości wielowierszowych). Iteruj z
   `sprawdz typy\<nazwa> --bez-llm`, aż pola z reguł mają F1 = 1 na wszystkich przykładach.
6. Regresja: `sprawdz typy\<nazwa> --bez-llm --dane <katalog z PDF-ami innych typów>` → **0 zmian**.
   Jeśli są zmiany: zaostrz `wymagane_na_poczatku` / słowa kluczowe, nie ruszaj innych typów.
7. `py -m unittest discover -s tests` – wszystko musi przejść.
8. Raport dla administratora: F1 per pole (z regułami i z samym modelem), pola nieosiągnięte i dlaczego,
   wynik regresji. Przypomnij, że `przyklady/` zawiera treść dokumentu (dane osobowe → nie do publicznego repo).

## Zakazy
- Żadnych zmian poza `typy/<nazwa>/`.
- Żadnych wartości wpisanych na sztywno (każda wartość z tekstu dokumentu).
- Nie ogłaszaj sukcesu bez wyniku `sprawdz` i testów.
