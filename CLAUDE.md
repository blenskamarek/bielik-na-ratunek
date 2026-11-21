# ODWOŁANIA2JSON – wskazówki dla asystenta AI

Program zamienia PDF-y sprawy odwoławczej KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu)
na JSON. Python 3.13+, uruchomienie: `python run.py --wejscie input --wyjscie output` (lub `run.bat`).

## Układ
- `silnik/` – rdzeń (odczyt PDF + OCR, klasyfikacja, silnik reguł YAML, warstwa LLM z weryfikacją). **Nie zmieniaj przy dodawaniu typów.**
- `typy/<typ>/typ.yaml` – konfiguracja typu dokumentu (klasyfikacja, `sekcje`, reguły `pola` i `listy`,
  sekcja `llm` lub plik `llm.yaml`). **Wszystkie typy, także konkursowe, są samą konfiguracją** –
  najbardziej rozbudowane wzory: `typy/odwolanie/typ.yaml`, `typy/pelnomocnictwo/typ.yaml`.
  (`ekstraktor.py` w katalogu typu jest nadal obsługiwany jako furtka awaryjna, ale żaden typ go nie używa.)
- `typy/_szablon/`, `typy/_pokaz_ogloszenie_ted/` – wzory (katalog z `_` = nieaktywny).
- `narzedzia/nowy_typ.py` – szkielet i sprawdzanie typu; `ocena.py` – F1 względem wzorców.
- `docs/NOWY_TYP.md` – pełna składnia YAML i procedura.

## Dodawanie nowego typu dokumentu (najczęstsze zadanie administratora)
Postępuj według `docs/NOWY_TYP.md` (w Claude Code: `/nowy-typ`). Zasady:
1. Struktura i nazwy pól **tylko** z oczekiwanego JSON-a podanego przez użytkownika – nie zgaduj.
2. Edytuj wyłącznie `typy/<nowy_typ>/`. Zero zmian w `silnik/`, `run.py`, istniejących typach.
3. Wartości wycinamy z tekstu (reguły) albo model je wskazuje (sekcja `llm`, weryfikowana w tekście) – nigdy nie wpisuj wartości na sztywno.
4. Po każdej zmianie: `py narzedzia\nowy_typ.py sprawdz typy\<typ>`; przed zakończeniem także
   `--bez-llm --dane <katalog z PDF innych typów>` (musi być 0 zmian klasyfikacji) i `py -m unittest discover -s tests`.
5. Raportuj użytkownikowi F1 per pole i to, czego nie udało się uzyskać – bez upiększania.

## Model językowy
`llm.yaml` jest ustawiony na ocenę: `tryb: kolejka` przez `https://bielik.kowale.edu.pl/kolejka.php`
(pracownik na komputerze z GPU: `docs/ZDALNY_BIELIK.md`). Do pracy lokalnej z Ollamą bez edycji pliku:
`$env:LLM_TRYB="api"; $env:LLM_ADRES="http://localhost:11434/v1"`. Bez modelu: `--bez-llm`.
Pamięć podręczna odpowiedzi to `.cache_llm/` – czyść ją przed pomiarami czasu.

## Polecenia
- Testy: `py -m unittest discover -s tests`
- F1 na danych UZP: `py run.py --wejscie <dane> --wyjscie output --bez-llm` i `py ocena.py --wzorzec <dane> --wynik output --pola`
- Pliki `.ps1` zapisuj w UTF-8 **z BOM** (Windows PowerShell 5.1).
