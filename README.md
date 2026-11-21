# Bielik na ratunek

**Odwołania2JSON – sprawa odwoławcza KIO zamieniona na JSON** (HackNation 2026, wyzwanie Urzędu Zamówień Publicznych)

Program czyta pliki PDF sprawy odwoławczej (odwołanie, pełnomocnictwo, potwierdzenie przelewu), rozpoznaje
rodzaj każdego dokumentu po treści, wyciąga dane i zapisuje je jako JSON zgodny ze schematem UZP.
Najpierw działają szybkie, przewidywalne reguły; polski model językowy **Bielik 11B** uzupełnia tylko to, czego
reguły nie znalazły, a każda jego odpowiedź jest sprawdzana w tekście dokumentu.

## Szybki start

Wymagania: Python 3.13+ (sprawdzone na 3.14, Windows), bez GPU.
Do skanów (PDF bez warstwy tekstowej) potrzebny jest **Tesseract OCR z językiem polskim**
(Windows: instalator UB Mannheim; Linux: `apt install tesseract-ocr tesseract-ocr-pol`). Bez niego program działa,
ale skanów nie odczyta.

```powershell
pip install -r requirements.txt
run.bat                       # albo: start.bat, albo: python run.py --wejscie input --wyjscie output
```

Model językowy jest używany przez API (patrz niżej). **Klucz API: wpisz klucz podany w formularzu zgłoszeniowym
do pliku `llm.klucz`** (obok `run.bat`; w repozytorium plik zawiera tylko tekst `tutaj wpisz klucz`).
Bez klucza lub bez połączenia program działa na samych regułach i OCR (w logu pojawi się informacja o braku klucza).

Opcje: `--raport KATALOG` (raport przejrzystości), `--bez-llm`, `--bez-ocr`.

### Wejście i wyjście

```
input/                            output/
├── case1/                        ├── case1/
│   ├── odwolanie_nr_1.pdf        │   ├── odwolanie_nr_1.json
│   ├── pelnomocnictwo_nr_1.pdf   │   ├── pelnomocnictwo_nr_1.json
│   ├── przelew_nr_1.pdf          │   ├── przelew_nr_1.json
│   └── inny_dokument.pdf         │   └── inny_dokument.json      <- pusty plik: dokument spoza trzech typów
```

Nazwa JSON-a = nazwa PDF-a. Typ dokumentu jest rozpoznawany po treści, nie po nazwie pliku. Dokumenty spoza
trzech typów (wyroki i postanowienia KIO, decyzje, ogłoszenia TED, inne pisma) dają pusty plik `.json`.

## Jak to działa

```
PDF ─▶ tekst (pypdf) ─▶ OCR stron bez tekstu (Tesseract, pol)
    ─▶ klasyfikacja po treści i budowie pisma (typy/*/typ.yaml)  ── obcy dokument ──▶ pusty .json
    ─▶ reguły z konfiguracji YAML (sekcje, regex, bloki stron, słowniki)
    ─▶ Bielik 11B tylko dla pól pustych po regułach ─▶ weryfikacja każdej wartości w tekście
    ─▶ JSON (+ opcjonalny raport: co podał model, co przyjęto, dlaczego odrzucono)
```

- **Reguły przed modelem.** Daty, kwoty, numery, NIP/KRS/REGON i rachunki wycina program z tekstu; model nigdy
  ich nie „przepisuje”. Na danych treningowych reguły same dają F1 = 1,000.
- **Kontrola źródła.** Wartość od modelu jest przyjmowana tylko, gdy występuje w dokumencie (tekst, data,
  cyfry, e-mail, kwota) albo na zamkniętej liście (np. kod czynności 1–12). Niepotwierdzona – odrzucana.
- **Wszystkie typy dokumentów to konfiguracja YAML**, bez kodu w `typy/` (patrz „Nowy typ dokumentu”).

## Model językowy (LLM)

| | |
|---|---|
| Model | **Bielik 11B v3.1 Instruct** (SpeakLeash, otwarte wagi), GGUF Q4_K_M |
| Uruchomienie | Ollama na komputerze z GPU (RTX 5060 8 GB), kontekst 6144 tokenów |
| Dostęp dla programu | HTTPS przez kolejkę na serwerze w Polsce (hosting cal.pl): program wrzuca zadanie, komputer z modelem sam je pobiera (tylko połączenia wychodzące) i odsyła wynik |
| Ustawienia | temperatura 0, stałe ziarno, odpowiedzi w pamięci podręcznej (powtórny przebieg nie zużywa tokenów) |
| Rola | uzupełnienie pól pustych po regułach (np. strony i czynność w realnych skanach), z weryfikacją w tekście |
| Odporność | pracownik nieaktywny → wykrycie w ≤ 20 s i dalej same reguły; zły klucz → natychmiast same reguły |

Pomiar (model wypełnia **wszystkie** pola odwołania sam, zestawy 51–60, `narzedzia/porownaj_llm.py`):

| Model | F1 pól od modelu | Czas / dokument (GPU) | Czas / dokument (samo CPU, Ryzen 5 9600X) |
|---|---|---|---|
| Bielik 11B v3.1 (Q4_K_M) | **1,000** | ~28 s | ~105–120 s |
| Bielik 4.5B v3.0 (Q8_0) | 0,946 (kod czynności 0,40) | ~10 s | ~72–110 s |

Wybraliśmy 11B, bo mniejszy model myli się w najważniejszym polu (rodzaj zaskarżonej czynności), a reguły i tak
wypełniają większość pól bez modelu. Szczegóły i konfiguracja: [docs/ZDALNY_BIELIK.md](docs/ZDALNY_BIELIK.md),
[llm.yaml](llm.yaml).

## Jakość

`python ocena.py --wzorzec <dane UZP> --wynik output --pola` (F1 per pole i per typ; negatywy: wypełnione pole = FP).

| | Wynik |
|---|---|
| F1 na danych treningowych UZP z warstwą tekstową (60 spraw, 180 dokumentów) | **1,000** |
| F1 na skanach UZP (30 dokumentów, OCR), same reguły – wszystkie / odłożone 0008–0010 | **0,917** / **0,808** |
| F1 na skanach odłożonych z modelem Bielik 11B | 0,810 |
| Dokumenty negatywne UZP (16) | 16/16 pusty plik |
| Klasyfikacja (180 tekstowych + 30 skanów + realny skan UZP; 16 negatywów) | 211/211, 16/16 |
| Czas: 196 PDF-ów tekstowych, same reguły | ~9 s |

Skany: reguły rozwijane na zestawach 0001–0007 każdego typu, 0008–0010 odłożone do uczciwego pomiaru
(przed rozwinięciem reguł pod skany F1 na wszystkich 30 skanach wynosiło 0,385). Typowe błędy OCR
(„Sp. z 0.0.”, `@` odczytane jako `Q`, puste linie) są poprawiane przed regułami; błędnie odczytanych cyfr
i liter nie da się odtworzyć.

## Nowy typ dokumentu – bez zmian w kodzie

Typ dokumentu = plik `typy/<nazwa>/typ.yaml` (klasyfikacja, reguły, opisy pól dla modelu). Administrator
(sam lub z asystentem AI, np. Claude) dodaje go tak:

```powershell
py narzedzia\nowy_typ.py nowy faktura --pdf przyklad.pdf --wzorzec oczekiwany.json   # szkielet
py narzedzia\nowy_typ.py sprawdz typy\faktura                                       # F1, klasyfikacja
py narzedzia\nowy_typ.py sprawdz typy\faktura --bez-llm --dane <inne PDF-y>         # 0 zmian u innych typów
```

Pokaz: [typy/_pokaz_ogloszenie_ted](typy/_pokaz_ogloszenie_ted/typ.yaml) – ogłoszenie TED dodane samą konfiguracją
(sam model: F1 0,952; z regułami: 1,000; 0 zmian klasyfikacji 180 innych dokumentów). Katalog z `_` jest nieaktywny;
aktywacja = zmiana nazwy. Pełny opis składni: [docs/NOWY_TYP.md](docs/NOWY_TYP.md). Dla asystentów AI:
[CLAUDE.md](CLAUDE.md) i polecenie `/nowy-typ` w Claude Code.

## Struktura

```
run.py, run.bat, start.bat   uruchomienie
silnik/                      odczyt PDF + OCR, klasyfikacja, silnik reguł YAML, warstwa LLM z weryfikacją
typy/                        typy dokumentów (YAML): odwolanie, pelnomocnictwo, przelew, _szablon, _pokaz_ogloszenie_ted
llm.yaml                     konfiguracja modelu (tryb kolejki / API)
serwer_kolejki/              kolejka.php dla hostingu (PHP 7.0+, bez bazy)
narzedzia/                   pracownik kolejki, bramka API, nowy_typ.py, porownaj_llm.py, skrypty dnia oceny
ocena.py                     F1 względem wzorców
tests/                       testy (python -m unittest discover -s tests)
docs/                        NOWY_TYP, ZDALNY_BIELIK, WDROZENIE_UZP (plan wdrożenia w urzędzie)
deploy/                      Dockerfile i Docker Compose (aplikacja + lokalny model)
```

## Wdrożenie w urzędzie

Plan wdrożenia na serwerach UZP (architektura, sprzęt, RODO, etapy pilotażu): [docs/WDROZENIE_UZP.md](docs/WDROZENIE_UZP.md).
Pliki Docker Compose są w `deploy/` (nie były jeszcze uruchamiane – brak Dockera na komputerze deweloperskim).

## Ograniczenia

- Sposób liczenia F1 przez organizatora nie jest opisany – `ocena.py` przyjmuje założenia opisane na początku pliku.
- Dane treningowe są syntetyczne; realne dokumenty mogą mieć inne układy (od tego jest warstwa LLM z weryfikacją).
- Tesseract nie instaluje się przez pip; zapasowy OCR przez pip (`requirements-ocr.txt`, RapidOCR) nie zna polskich znaków.
- Sprawdzone na Pythonie 3.14 (Windows); wersja 3.13 nie była osobno testowana.
