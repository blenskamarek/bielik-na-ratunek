# Plan wdrożenia ODWOŁANIA2JSON na serwerze Urzędu Zamówień Publicznych

Cel: uruchomić przetwarzanie spraw odwoławczych (PDF → JSON) w całości na infrastrukturze UZP,
bez wysyłania dokumentów poza urząd, i przejść od prototypu do pilotażu z mierzoną jakością.

## 1. Architektura docelowa

```
            sieć wewnętrzna UZP
┌──────────────┐     ┌──────────────────────────────────┐      ┌──────────────────────┐
│ użytkownik / │────▶│ serwer aplikacji (bez GPU)        │─────▶│ serwer LLM (GPU)      │
│ system UZP   │     │ ODWOŁANIA2JSON: reguły + OCR      │ HTTP │ Bielik 11B v3.1       │
│ (PDF sprawy) │◀────│ (Tesseract pol) + konfiguracja    │ API  │ Ollama / vLLM         │
└──────────────┘JSON │ typów (typy/*.yaml)               │ OpenAI│ dostęp tylko z sieci  │
                     └──────────────────────────────────┘      │ wewnętrznej           │
                              │ raporty przejrzystości           └──────────────────────┘
                              ▼
                     magazyn: input / output / raport (retencja wg polityki RODO UZP)
```

- **Aplikacja** działa bez GPU: odczyt PDF, OCR skanów, klasyfikacja, reguły, weryfikacja wartości.
- **Model językowy** jest osobną usługą z API zgodnym z OpenAI. Uzupełnia tylko pola puste po regułach,
  a aplikacja sprawdza każdą jego wartość w tekście dokumentu (brak zmyśleń, pełny raport).
- **Rozbudowa o nowe typy dokumentów** odbywa się przez pliki `typy/<typ>/typ.yaml` i `llm.yaml`,
  bez zmian w kodzie i bez przebudowy obrazu (katalog `typy/` montowany w kontenerze).

## 2. Wymagania sprzętowe

| Element | Minimum | Zalecane | Uzasadnienie / pomiar |
|---|---|---|---|
| Serwer aplikacji | 4 vCPU, 8 GB RAM | 8 vCPU, 16 GB RAM | OCR ~0,7 s/strona (Tesseract), reguły: 180 PDF w 2,7 s |
| Serwer LLM – GPU | 16 GB VRAM (Bielik 11B Q4_K_M + kontekst 8k ≈ 9 GB) | 24–48 GB VRAM (Q8 / FP16, kilka zapytań naraz) | na RTX 5060 8 GB: 28 s/dokument przy częściowym odciążeniu na CPU; na karcie 16 GB+ model mieści się w całości – czas do zmierzenia (szacunek: kilka–kilkanaście s) |
| Serwer LLM – bez GPU (awaryjnie) | 16 rdzeni, 32 GB RAM | – | zmierzone na Ryzen 5 9600X: ~2 min/dokument – tylko dla małego wolumenu |
| Dysk | 50 GB | 200 GB | modele (~7–25 GB), obrazy, dane robocze |

## 3. Instalacja (Docker Compose)

Pliki: `deploy/Dockerfile`, `deploy/docker-compose.yml`, `deploy/docker-compose.gpu.yml`
(**nieprzetestowane** – na komputerze deweloperskim nie było Dockera; pierwszy krok fazy 0).

```bash
cd deploy
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d llm
docker compose run --rm llm-init        # pobranie Bielika 11B v3.1 + wariant z kontekstem 6144/8192
docker compose run --rm app             # ../input -> ../output (+ ../raport)
```

- **Instalacja bez internetu (air-gap):** obrazy przenoszone przez `docker save/load`, model przez
  skopiowanie wolumenu `modele`, pakiety Python przez `pip download` na nośniku.
- **Bezpieczeństwo:** usługa `llm` bez wystawionych portów (dostęp tylko z sieci compose). Do serwera LLM
  używanego przez kilka aplikacji: reverse proxy z uwierzytelnieniem (wzorzec: `narzedzia/bramka_llm.py`).
- **Przepustowość:** przy większym wolumenie zamiast Ollamy vLLM (wiele zapytań naraz na jednym GPU).
  API pozostaje zgodne z OpenAI, więc zmienia się tylko `LLM_ADRES` i `LLM_MODEL`.

## 4. Ochrona danych (RODO)

- **Dane osobowe w dokumentach:** imiona i nazwiska, PESEL pełnomocników, adresy, dane kontaktowe.
- **Przetwarzanie wyłącznie na serwerach UZP.** Model jest lokalny, nic nie trafia do zewnętrznych API.
- **Logi:** aplikacja i bramka logują metadane (plik, typ, czas, liczba tokenów), bez treści dokumentów.
  Pamięć podręczna odpowiedzi modelu (`.cache_llm`) zawiera wartości z dokumentów, więc albo wyłączyć ją
  w produkcji, albo objąć tą samą retencją co dane wejściowe.
- **Do uzgodnienia z IOD UZP:** okres retencji input/output/raport, uprawnienia do katalogów,
  ocena skutków (DPIA) przed pilotażem na realnych sprawach.

## 5. Etapy wdrożenia

| Faza | Czas | Zakres | Kryterium przejścia |
|---|---|---|---|
| 0. Przygotowanie | 2 tyg. | serwery, instalacja z compose, test na zbiorze treningowym (`ocena.py`), test skanów | F1 na zbiorze treningowym = wynik referencyjny; czas/dokument zmierzony na sprzęcie UZP |
| 1. Zbiór referencyjny | 2–4 tyg. | eksperci UZP opisują 50–100 realnych spraw (wzorcowe JSON) | zbiór podzielony: rozwój / walidacja (nieoglądana przy pracy nad regułami) |
| 2. Tryb cienia | 4–6 tyg. | system działa równolegle z obsługą ręczną; porównanie per pole | F1 per pole na zbiorze walidacyjnym; lista pól wymagających poprawy |
| 3. Tryb wspomagany | 2–3 mies. | pracownik zatwierdza dane wstępnie wypełnione (widok PDF obok JSON + raport źródeł) | % pól zatwierdzonych bez poprawek; czas obsługi sprawy vs ręcznie |
| 4. Integracja | wg potrzeb | API do systemów UZP, kolejne typy dokumentów przez konfigurację | decyzja o produkcji |

## 6. Utrzymanie

- **Wersjonowanie:** kod i konfiguracja typów w repozytorium. Każda zmiana reguł lub `typy/*.yaml`
  przechodzi testy (`python -m unittest`) i ocenę F1 na zbiorze referencyjnym przed wdrożeniem.
- **Model:** przypięta wersja (nazwa + kwantyzacja). Zmiana modelu tylko po porównaniu
  `narzedzia/porownaj_llm.py` (F1 i czas) na zbiorze walidacyjnym.
- **Monitoring:** odsetek dokumentów nierozpoznanych, liczba wartości od modelu odrzuconych przez
  weryfikację (raporty), czas na dokument, dostępność usługi `llm` (healthcheck).
- **Nowy typ dokumentu:** kopia `typy/_szablon/`, uzupełnienie YAML, kilka przykładów do testu,
  ocena F1, wdrożenie bez przebudowy.

## 7. Ryzyka

| Ryzyko | Ograniczenie |
|---|---|
| Realne dokumenty różnią się od syntetycznych (układ, skany, anonimizacja) | faza 1–2: zbiór referencyjny z realnych spraw; LLM jako uzupełnienie reguł; mierzenie per pole |
| Słaba jakość skanów | Tesseract z językiem polskim; raport stron z OCR; w razie potrzeby wstępna obróbka obrazu |
| Brak GPU na serwerze | tryb CPU (~2 min/dokument) albo same reguły dla pilnych spraw; przetwarzanie wsadowe nocą |
| Błędna wartość od modelu | weryfikacja w tekście, zamknięte listy, raport przejrzystości, zatwierdzanie przez pracownika (faza 3) |

## 8. Pytania do działu IT UZP

1. Jaki sprzęt jest dostępny dla serwera LLM (GPU: model i VRAM)? Czy jest Docker i NVIDIA Container Toolkit?
2. Czy serwery mają dostęp do internetu (pobranie modelu), czy instalacja w trybie air-gap?
3. Jaki wolumen dokumentów na dzień i wymagany czas odpowiedzi?
4. Gdzie mają trafiać wyniki (katalog, baza, API istniejącego systemu)?
5. Wymogi bezpieczeństwa: uwierzytelnianie (AD/LDAP?), logowanie, retencja.
