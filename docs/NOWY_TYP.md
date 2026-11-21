# Nowy typ dokumentu bez zmian w kodzie

Administrator (sam albo z asystentem AI, np. Claude) dodaje typ dokumentu **wyłącznie plikiem
`typy/<nazwa>/typ.yaml`**. Silnik (`silnik/`) się nie zmienia, a program po restarcie sam wczytuje nowy katalog.

Wszystkie typy w projekcie są samą konfiguracją. Przykłady od prostego do złożonego:
- [`typy/_pokaz_ogloszenie_ted/typ.yaml`](../typy/_pokaz_ogloszenie_ted/typ.yaml): ogłoszenie TED z repozytorium UZP.
  Same pola z etykietami. F1 1,000 na przykładzie, 0 zmian klasyfikacji 180 innych dokumentów.
- [`typy/przelew/typ.yaml`](../typy/przelew/typ.yaml): 8 różnych układów banków.
- [`typy/odwolanie/typ.yaml`](../typy/odwolanie/typ.yaml): sekcje dokumentu, bloki stron (lider konsorcjum,
  kilku zamawiających), klasyfikacja czynności do zamkniętej listy, złożenie numerów BZP/TED.
- [`typy/pelnomocnictwo/typ.yaml`](../typy/pelnomocnictwo/typ.yaml): firmy po formie prawnej z pominięciem
  zamawiającego, KRS/NIP z bloku firmy, podpisujący przypisani do firmy po nagłówkach „W imieniu …”.

Oba typy konkursowe zostały przepisane z Pythona na YAML z wynikiem identycznym pole w pole
(60 + 60 dokumentów i skan UZP).

## Procedura (4 kroki)

1. **Szkielet z przykładu.** Potrzebny jest 1+ przykładowy PDF i najlepiej oczekiwany JSON dla niego:
   ```powershell
   py narzedzia\nowy_typ.py nowy faktura --pdf faktura1.pdf --wzorzec faktura1.json
   ```
   Narzędzie tworzy `typy/faktura/`:
   - `typ.yaml` z sekcją `llm:` (wszystkie pola wzorca, z odgadniętym typem),
   - `przyklady/*.pdf`, `przyklady/*.txt` (tekst dokumentu, także z OCR), `przyklady/*.oczekiwany.json`.
2. **Uzupełnienie `typ.yaml`:** `klasyfikacja.slowa_kluczowe` (bez nich typ jest nieaktywny), opis
   dokumentu i opisy pól (`UZUPEŁNIJ`). W tym momencie typ już działa przez model językowy.
3. **Sprawdzenie** (po każdej zmianie):
   ```powershell
   py narzedzia\nowy_typ.py sprawdz typy\faktura
   py narzedzia\nowy_typ.py sprawdz typy\faktura --bez-llm --dane ..\dane_uzp\hacknation-2026-main\dane
   ```
   Wynik pokazuje:
   - czy konfiguracja jest poprawna (błąd wskazuje pole i problem),
   - czy przykłady klasyfikują się jako nowy typ (wraz z punktami wszystkich typów),
   - wynik JSON, wartości od modelu (przyjęte lub odrzucone z powodem) i F1 per pole względem wzorca,
   - z `--dane`: liczbę dokumentów innych typów, które zmieniłyby klasyfikację. **Musi być 0.**
4. **Reguły dla pól o stałej formie** (sekcja `pola:`): numery, daty, kwoty z etykietami. Są szybkie
   i deterministyczne. Pętla: dopisać regułę → `sprawdz --bez-llm` → poprawić, aż F1 pól z reguł = 1.
   Model zostaje jako zapas dla pól, których reguła nie znajdzie.

Typ nieaktywny = katalog zaczynający się od `_` (np. `_pokaz_ogloszenie_ted`). Aktywacja to zmiana nazwy katalogu.

## Składnia `typ.yaml`

### `klasyfikacja` – rozpoznanie typu

| Klucz | Znaczenie |
|---|---|
| `slowa_kluczowe` | lista zwrotów; typ z największą liczbą trafień wygrywa. Porównanie bez wielkości liter, spacji i polskich znaków (łapie „P E Ł N O M O C N I C T W O” i tekst z OCR bez ogonków). |
| `wymagane_na_poczatku` | warunek konieczny: któryś zwrot musi wystąpić w pierwszych `okno_poczatku` znakach (np. tytuł). Chroni przed dokumentami, które tylko wspominają słowa kluczowe. |
| `okno_poczatku` | domyślnie 2000 |
| `ogranicz_do_znakow` | ile tekstu przeszukiwać (domyślnie 600; zwykle 100000) |

### `pola` – reguły (regex)

Klucz = ścieżka w wynikowym JSON (kropka = zagnieżdżenie), np. `faktura.numer`.

| Klucz reguły | Znaczenie |
|---|---|
| `regex` | wzorzec albo lista wzorców (pierwszy pasujący wygrywa). Pisać w `'pojedynczych cudzysłowach'`, bo w YAML wtedy `\d` działa bez podwajania. |
| `grupa` | numer lub nazwa grupy z wartością (domyślnie 1, a bez grup całe dopasowanie) |
| `typ` | `tekst` (domyślny) \| `data` (→ RRRR-MM-DD) \| `kwota` (→ liczba) \| `liczba` \| `cyfry` \| `rachunek` (26 cyfr) \| `bool` |
| `flagi` | `[dotall]`, gdy wartość ciągnie się przez kilka linii; `ignorecase` i `multiline` są włączone zawsze |
| `wszystkie: true` | lista wszystkich trafień (bez powtórzeń) |
| `szukaj_w: plaski` | szukanie w tekście z liniami sklejonymi spacją |
| `mapa` | zamiast wartości z tekstu: `[[wartość, regex], ...]` – pierwsza pasująca (np. rodzaj dokumentu → kod) |
| `domyslnie` | wartość, gdy nic nie pasuje (domyślnie `null`) |
| `w` | gdzie szukać: nazwa sekcji albo lista nazw (próbowane kolejno); domyślnie `dokument` |
| `format` | wartość złożona z grup, np. `'{1}/BZP{2}'` albo z nazwanych `'{nr}-{rok}'` |
| `pomin` | wzorce fragmentów wymazywanych przed szukaniem (np. dane zamawiającego, żeby nie wziąć ich za stronę) |
| `z_pola` + `slownik` | wartość wyliczona z innego pola, np. opis z numeru: `z_pola: a.id`, `slownik: {1: Opis 1}` |
| `walidacja` | warunki dla wartości (patrz niżej); działa też w polach elementów list i w `llm` |

#### `walidacja` – warunki poprawności wartości

```yaml
  faktura.nip_sprzedawcy:
    regex: 'NIP:?\s*([\d -]{10,13})'
    typ: cyfry
    walidacja: {dlugosc: 10, suma_kontrolna: nip}
```

| Warunek | Znaczenie |
|---|---|
| `dlugosc` | długość wartości (po `typ`, np. same cyfry): `10` albo lista, np. `[9, 14]` dla REGON |
| `wzorzec` | cała wartość musi pasować do regexu, np. `'\d{4}/BZP\d{8}'` |
| `wartosci` | lista dozwolonych wartości, np. `[1, 2, 3]` |
| `zakres` | `[od, do]` dla liczb i dat ISO; `null` = bez ograniczenia, np. `['2021-01-01', null]` |
| `suma_kontrolna` | `nip` \| `regon` (9 i 14 cyfr) \| `pesel` \| `rachunek` (IBAN / NRB, mod 97) |

Wartość, która nie spełnia warunków, **nie trafia do wyniku**. Program szuka dalej (kolejne trafienie
regexu, kolejna alternatywa, kolejne źródło), a gdy nic nie przejdzie, pole jest puste albo dostaje
`domyslnie`. Puste pole może jeszcze uzupełnić model (sekcja `llm`), a jego wartość przechodzi te same
warunki, jeśli dopisać `walidacja` przy polu w `llm`. Każde odrzucenie trafia do raportu (`run.py --raport`,
klucz `walidacja`) i do wydruku `nowy_typ.py sprawdz`, z powodem, np. „błędna suma kontrolna (nip)”.

Sumę kontrolną włączaj tylko wtedy, gdy dokumenty zawierają prawdziwe numery. W danych treningowych UZP
numery są fikcyjne: sumę przechodzi ok. 2/3 NIP-ów i REGON-ów i mniej niż połowa rachunków. Dlatego typy
konkursowe sprawdzają tylko długość i format, a sumę kontrolną – wyłącznie PESEL (w danych wszystkie poprawne).

Makra w regexach: `{DATA}` (2026-03-21, 21.03.2026, 21/03/2026, 21 marca 2026), `{KWOTA}` (1 200,50),
`{RACHUNEK}` (NRB z PL i spacjami lub bez). Wielkość liter jest ignorowana; `(?-i:...)` włącza ją lokalnie
(np. `(?-i:[A-ZŁ])` = tylko wielka litera).

Uwaga: `{DATA}` ma własne grupy. Jeśli makro stoi w regexie **przed** grupą z wartością (np. w warunku
`(?=...)`), „grupa 1” wskaże grupę daty. Wtedy nazwij swoją grupę i podaj ją w `grupa`:
`regex: '\A(?=...{DATA}...)(?P<tresc>[\s\S]*)'` + `grupa: tresc`.

### `sekcje` – nazwane fragmenty dokumentu

Pomocnicze fragmenty tekstu (nie trafiają do wyniku). Reguły pól i list wskazują je przez `w:`.
Sekcja może korzystać z innej sekcji zdefiniowanej wyżej.

```yaml
sekcje:
  naglowek: {regex: '\A([\s\S]*?)(?=^[ \t]*dotyczy\b|\Z)'}          # wszystko przed "dotyczy"
  odwolujacy: {w: naglowek, regex: '^[ \t]*Odwołujący\b[^\n]*\n([\s\S]*?)(?=^[ \t]*Zamawiający|\Z)'}
```

**Sekcja warunkowa = inny układ dokumentu bez psucia dotychczasowego.** Sekcja, której regex nie pasuje,
nie istnieje, a reguły z `w:` na nią po prostu nic nie znajdują. Tak `typy/odwolanie/typ.yaml` obsługuje
układ „etykieta POD danymi strony” (dwie kolumny w prawdziwych odwołaniach): `naglowek_po` istnieje tylko,
gdy nagłówek kończy się samotną etykietą. Alternatywa listy z `w: odwolujacy_po` stoi na pierwszym miejscu,
a w zwykłym układzie wygrywa dawna reguła:

```yaml
listy:
  Odwolujacy:
    regex:
      - {w: odwolujacy_po, regex: *blok_luzny}    # tylko w układzie "pod blokiem"
      - {w: odwolujacy, regex: *blok_strony}      # zwykły układ
      - {w: odwolujacy, regex: *blok_luzny}       # zwykły układ, ale adres bez "ul." / wszystko w jednej linii
```

Przed dodaniem takiej reguły sprawdź, że nie zmienia wyników na znanych dokumentach. Najprościej
wygenerować warianty układu (np. przenieść etykiety, usunąć „ul.”) z dokumentów, które mają wzorce.

### `listy` – listy obiektów z reguł

Jedno trafienie regexu to jeden element. Jeśli `regex` jest listą alternatyw, wygrywa pierwsza,
która coś znalazła. Pola elementu:

```yaml
listy:
  faktura.pozycje:
    regex: '^\d+\.\s+(?P<nazwa>.+?)\s+(?P<ilosc>\d+)\s+szt\.\s+(?P<cena>{KWOTA})$'
    pola: {nazwa: nazwa, ilosc: ilosc, cena: cena}     # pole -> nazwa grupy
    typy: {ilosc: liczba, cena: kwota}
    unikalne: true

  strony:                                               # bloki: nazwa / adres / linia z KRS-NIP
    w: odwolujacy
    regex: '(?P<nazwa>[^\n]+)\n^ul\.[^\n]*\n^(?P<ids>[^\n]*NIP[^\n]*)'
    max: 1
    unikalne_po: [nazwa]
    pola:
      nazwa: {z_grupy: nazwa}                           # wartość z grupy (+ opcjonalnie typ / mapa)
      nip: {w: ids, regex: 'NIP[:\s]*([\d -]{10,13})', typ: cyfry}   # reguła w obrębie elementu
      krs: {w: segment, regex: 'KRS[:\s]*(\d{10})'}     # segment = od tego trafienia do następnego
      lider: {z_grupy: etykieta, mapa: [[1, 'lider']], tylko_gdy_wiele: true}
      osoby:                                            # lista zagnieżdżona
        lista:
          w: podpisy                                    # sekcja dokumentu
          od: '^Za {nazwa}'                             # {pole} = wartość pola tego elementu
          do: '^Za (?!{nazwa})'
          regex: '^(?P<imie>[A-ZŁ]\w+ [A-ZŁ]\w+)$'
          pola: {imie_nazwisko: imie}
```

Źródła `w:` w polu elementu: `dopasowanie` (tekst trafienia, domyślnie), `segment`, nazwa grupy z regexu
listy, nazwa sekcji albo `dokument`.

### `llm` – pola uzupełniane przez model językowy

Model wypełnia **tylko pola puste po regułach**. Każdą jego wartość program sprawdza w tekście
dokumentu, a niepotwierdzona wartość jest odrzucana (widać to w `sprawdz` i w raporcie `--raport`).

| Klucz | Znaczenie |
|---|---|
| `opis_dokumentu` | jedno zdanie dla modelu: czym jest dokument |
| `fragmenty` | które części tekstu dostaje model: `[{od: '<regex>', znakow: N}, ...]` (kontekst jest ograniczony, więc warto podać okna wokół potrzebnych sekcji) |
| `pola.<ścieżka>.typ` | `tekst` (dosłowny cytat) \| `data` \| `cyfry` (+ `dlugosc`) \| `email` \| `kwota` \| `osoba` (imię i nazwisko, pierwsza osoba z listy) \| `wybor` (+ `wartosci`) \| `lista` (+ `element`, `max`) |
| `pola.<ścieżka>.opis` | czego szukać – konkretnie, z nazwą etykiety z dokumentu |
| `wartosci` | dla `wybor`: lista albo słownik `numer: opis`; `opis_do: ścieżka` wpisze opis wybranego numeru |
| `tylko_gdy_wiele` | w elemencie listy: pole zerowane, gdy lista ma 1 element (np. „lider”) |
| `walidacja` | te same warunki co w regułach (`dlugosc`, `wzorzec`, `wartosci`, `zakres`, `suma_kontrolna`), sprawdzane po potwierdzeniu wartości w tekście |

## Dobre praktyki (także dla asystenta AI)

- **Nazwy pól i struktura JSON tylko ze wzorca** (oczekiwanego JSON-a od zamawiającego), nie wymyślane.
- **Wartości zawsze z tekstu dokumentu.** Reguła ma wycinać wartość z tekstu, a model tylko ją
  wskazuje, bo program i tak ją weryfikuje.
- **Pole, którego reguła jest pewna, nie musi być w `llm`.** Gdy model psuje pole puste we wzorcu
  (np. wstawia datę z innego miejsca), usuń to pole z `llm`.
- **Zanim typ trafi do użytku:** `sprawdz --dane` = 0 zmian klasyfikacji, testy `py -m unittest discover -s tests`.
- **Dane osobowe:** `przyklady/` zawiera treść dokumentów. Do publicznego repozytorium tylko
  dokumenty jawne lub zanonimizowane.
