# Bielik na ratunek – uruchomienie

Z katalogu `/opt/solution`:

```bash
sudo bash repo/vm/uruchom.sh
```

Przetwarza wszystkie pliki PDF z `/opt/solution/input` (także w podkatalogach) i zapisuje JSON-y do
`/opt/solution/output` (ta sama ścieżka i nazwa pliku, rozszerzenie `.json`). Dokument spoza trzech typów
(odwołanie, pełnomocnictwo, potwierdzenie przelewu) daje plik z `{}`. Komenda kończy się sama, bez interakcji.

## Czego się spodziewać

- **Czas:** na tej maszynie ok. 5 minut na 228 PDF-ów (dane treningowe UZP). Skany trwają dłużej (OCR,
  kilka sekund na stronę), a dokumenty, przy których reguły zostawiły puste pole, dodatkowo kilka–kilkanaście
  sekund na zapytanie do modelu. Brak nowych linii przez minutę lub dwie przy skanach to normalne.
- **Postęp:** dla każdego pliku linia `<plik>.pdf -> <typ>` (`None` = dokument spoza trzech typów).
- **Koniec:** linia `Przetworzono plików PDF: N`.
- Każdy plik dostaje wynik, nawet gdy model jest niedostępny: program wykrywa to w ≤ 20 s i kończy
  na regułach i OCR. Błąd w jednym pliku nie przerywa przetwarzania pozostałych.

Kod: `/opt/solution/repo` (Python 3.12 w `repo/.venv`, Tesseract OCR z językiem polskim z apt).

## Zewnętrzne usługi

- **Model językowy Bielik 11B v3.1** (otwarte wagi, SpeakLeash) przez kolejkę HTTPS na serwerze w Polsce
  (`https://bielik.kowale.edu.pl/kolejka.php`, hosting cal.pl). Klucz: `LLM_API_KEY` w `/opt/solution/.env`.
  Model uzupełnia tylko pola, których nie znalazły reguły; każdą jego wartość program sprawdza w tekście.
- Gdy model jest niedostępny, program wykrywa to w ≤ 20 s i kończy pracę na samych regułach i OCR.
