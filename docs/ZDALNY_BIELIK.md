# Zdalny Bielik na czas oceny

Sędzia uruchamia program u siebie (komputer bez GPU). Reguły i OCR działają lokalnie u sędziego,
a pola, których reguły nie znalazły, program uzupełnia modelem **Bielik 11B v3.1** działającym na
naszym komputerze z GPU. Połączenie idzie przez **kolejkę na naszym hostingu** (cal.pl, domena
kowale.edu.pl; polska firma, serwer w centrum danych w Niemczech, czyli w UE).

```
komputer sędziego                hosting cal.pl (UE/DE)               nasz komputer (RTX 5060)
┌─────────────────────┐ HTTPS ┌──────────────────────────┐ HTTPS ┌──────────────────────────┐
│ run.py: reguły+OCR  │──────▶│ kolejka.php              │◀──────│ pracownik_kolejki.py     │
│ LLM: wrzuć zadanie, │ klucz │ zadania = pliki JSON     │ klucz │ co 1 s: "jest zadanie?"  │
│ odpytuj o wynik     │◀──────│ (usuwane po odbiorze)    │──────▶│ → Ollama (127.0.0.1)     │
└─────────────────────┘       └──────────────────────────┘       └──────────────────────────┘
```

Dlaczego tak:
- **Nasz komputer nie przyjmuje żadnych połączeń.** Pracownik sam łączy się na zewnątrz, więc nie
  trzeba otwierać portów ani konfigurować routera. Ollama nasłuchuje tylko lokalnie.
- **Dane idą tylko przez nasz hosting w UE** (polska firma, serwer w Niemczech; bez Cloudflare/Tailscale), więc wymóg „serwer w UE” jest spełniony. Model liczy w Polsce.
- **Hosting współdzielony wystarcza.** `kolejka.php` działa na PHP 7.0+, bez bazy danych i rozszerzeń.
  Przejęcie zadania to atomowe `rename()`, więc dwa pobrania nigdy nie wezmą tego samego zadania.
- **Program nie traci czasu, gdy model jest niedostępny.** Przy pierwszym zapytaniu sprawdza
  `?a=zdrowie` (czy pracownik odpytywał kolejkę w ciągu 30 s). Zadanie niepodjęte przez 20 s
  wyłącza model do końca przebiegu. Dalej działają reguły i OCR, a każdy plik dostaje pełny JSON.

Sprawdzone lokalnie (PHP 8.3 + pracownik + Bielik + program na skanie UZP): wynik identyczny jak przy
połączeniu bezpośrednim, ~23–29 s na dokument wraz z OCR. Testy automatyczne obejmują: pełny obieg,
brak pracownika (wyłączenie w < 5 s) i zły klucz (`tests/test_podstawy.py`, klasa `TestKolejkaPHP`).

## Wdrożenie (jednorazowo)

1. **Klucze** (na naszym komputerze):
   ```powershell
   py narzedzia\pracownik_kolejki.py --nowe-klucze
   ```
   Zapisuje `llm.klucz.lokalny` (klucz klienta – podawany w formularzu zgłoszeniowym) i `narzedzia\pracownik.klucz`
   (zostaje u nas) oraz wypisuje dwie linie do konfiguracji PHP. Oba pliki są poza gitem.
2. **Panel cal.pl:** subdomena `bielik.kowale.edu.pl`, certyfikat SSL (Let's Encrypt), PHP 7.0+ (najlepiej 8.x).
3. **Wgranie plików** (FTP / menedżer plików) do katalogu subdomeny:
   - `serwer_kolejki/kolejka.php`
   - `serwer_kolejki/.htaccess`
   - `kolejka_konfig.php`: kopia `serwer_kolejki/kolejka_konfig.php.wzor` z wpisanymi kluczami z kroku 1.
4. **Kontrola po wgraniu:**
   - `https://bielik.kowale.edu.pl/kolejka.php?a=zdrowie` → `{"ok":false,...}` (pracownik jeszcze nie działa – to poprawne),
   - `https://bielik.kowale.edu.pl/kolejka_konfig.php` → pusta strona albo 403 (nie może pokazać kluczy).

   Dane kolejki skrypt trzyma w katalogu `bielik_kolejka_dane` piętro wyżej (jeśli może tam pisać),
   inaczej w `dane_kolejki` obok siebie. W obu przypadkach z blokadą dostępu z WWW.
5. **Wariant modelu** z kontekstem 6144 (Ollama przez API zgodne z OpenAI nie ustawia kontekstu):
   ```powershell
   ollama create bielik-11b-6k -f narzedzia\modelfile\bielik-11b-6k.Modelfile
   ```
6. **Program dla sędziego** – `llm.yaml` jest już tak ustawiony (domyślnie):
   ```yaml
   tryb: kolejka
   adres: https://bielik.kowale.edu.pl/kolejka.php
   kontekst_tokenow: 6144      # musi odpowiadać modelowi pracownika
   ```
   W repozytorium `llm.klucz` zawiera tylko `tutaj wpisz klucz`; sędzia wpisuje w nim klucz z formularza
   zgłoszeniowego (albo ustawia zmienną `LLM_API_KEY`). U nas klucz leży w `llm.klucz.lokalny` (poza gitem,
   ma pierwszeństwo), więc repozytorium nigdy go nie zawiera.
   Do pracy lokalnej bez kolejki: `$env:LLM_TRYB="api"; $env:LLM_ADRES="http://localhost:11434/v1"`.

   Sprawdzone na domyślnej konfiguracji przez prawdziwy hosting (skan UZP, bez pamięci podręcznej):
   pracownik włączony → 26,9 s, model uzupełnił czynność, odwołującego i zamawiającego;
   pracownik wyłączony → 6,5 s, same reguły + OCR (wykrycie po jednym zapytaniu o zdrowie).

## Dzień oceny

Start (sprawdza Ollamę i model, wczytuje model na stałe do pamięci, uruchamia pracownika, czeka,
aż kolejka go zobaczy):
```powershell
powershell -ExecutionPolicy Bypass -File narzedzia\tunel\start_ocena.ps1 -Kolejka https://bielik.kowale.edu.pl/kolejka.php
```
Sprawdzenie tak, jak zobaczy to sędzia (najlepiej z **innej sieci**, np. telefon jako hotspot):
```powershell
powershell -ExecutionPolicy Bypass -File narzedzia\tunel\sprawdz_zdalny.ps1 -Adres https://bielik.kowale.edu.pl/kolejka.php
```
`WYNIK: OK` = zdrowie OK, bez klucza 401, pełny przebieg na skanie UZP dostał wartości od modelu.

- [ ] `start_ocena.ps1 -Kolejka …` uruchomiony (po restarcie komputera ponownie).
- [ ] Pracownik blokuje uśpienie komputera, dopóki działa. Restartów do aktualizacji Windows nie
      blokuje, więc trzeba ustawić „godziny aktywne” na okno oceny.
- [ ] `sprawdz_zdalny.ps1` z innej sieci → `WYNIK: OK`.
- [ ] Log: `narzedzia\tunel\logi\pracownik.log`: każde zadanie to linia `<id> gotowe <czas> tokeny …`.
- [ ] Po ocenie: zatrzymać pracownika, zmienić klucze (`--nowe-klucze` + nowy `kolejka_konfig.php`).

## Budżet czasu (ważne)

Mentorzy: 150 plików, 4 h na 30–40 rozwiązań, czyli **ok. 6–8 min na aplikację**, jeśli testy idą
po kolei. Model to 15–30 s na dokument, więc w tym czasie zdąży pomóc w kilkunastu dokumentach,
a nie we wszystkich. Do rozważenia: najpierw zapis wyników z reguł i OCR dla wszystkich plików,
potem uzupełnianie modelem w ramach budżetu czasu (najpierw odwołania). Wtedy przerwanie przebiegu
nie zostawi plików bez wyniku.

## Przekazanie klucza – do uzgodnienia z organizatorem

FAQ: rozwiązanie „powinno zawierać klucz API”, a repozytorium na GitHubie jest publiczne.
- **Wybrane:** klucz w formularzu zgłoszeniowym; w repozytorium plik `llm.klucz` z tekstem `tutaj wpisz klucz`.
  Program rozpoznaje ten tekst i bez prawdziwego klucza od razu działa na samych regułach (bez łączenia się z hostingiem).
- **Jeśli wymagają klucza w repozytorium:** osobny klucz tylko na czas oceny, wymieniany zaraz po niej.
  Klucz klienta pozwala tylko wrzucać zadania i odbierać wyniki (nie pobierze cudzych zadań).

## Ryzyka

| Ryzyko | Skutek | Ograniczenie |
|---|---|---|
| Nasz komputer / internet niedostępny | brak pól od modelu | program wykrywa to w ≤ 20 s i działa na regułach + OCR |
| Limity lub zapora hostingu (WAF, liczba zapytań) | odrzucone zapytania | pracownik po minucie bez zadań pyta co 5 s; test `sprawdz_zdalny.ps1` przed oceną |
| Kilka zadań naraz (sędzia równolegle) | kolejka, dłuższe czekanie | zadania liczone po kolei; limit 180 s na wynik |
| Wyciek klucza klienta | obce zadania dla naszego GPU | klucz tylko na ocenę; model wymuszony przez pracownika; log zadań |
| Treść dokumentów na hostingu | dane osobowe na serwerze w UE (DE) | usuwane zaraz po odbiorze wyniku, najpóźniej po 15 min; katalog zablokowany dla WWW |
