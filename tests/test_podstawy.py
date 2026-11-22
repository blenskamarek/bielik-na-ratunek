"""Testy (unittest, bez dodatkowych zależności): python -m unittest discover -s tests -v

Katalog z danymi UZP: zmienna środowiskowa DANE_UZP lub domyślnie
../dane_uzp/hacknation-2026-main/dane. Testy na danych są pomijane, gdy go brak.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ocena  # noqa: E402
from silnik.narzedzia import WZ_DATA, data_iso, kwota, tylko_cyfry_rachunku  # noqa: E402
from silnik.rdzen import klasyfikuj, przetworz_katalog, wczytaj_typy  # noqa: E402

DANE = Path(os.environ.get("DANE_UZP", ROOT.parent / "dane_uzp" / "hacknation-2026-main" / "dane"))
MA_DANE = DANE.is_dir()


def _porownanie(wz, pr, scisle=False):
    from collections import defaultdict
    licz = defaultdict(lambda: [0, 0, 0])
    ocena._porownaj(wz, pr, "", scisle, licz)
    return [sum(c[i] for c in licz.values()) for i in range(3)]


class TestNarzedzia(unittest.TestCase):
    def test_daty(self):
        for tekst, oczek in [("2026-03-21", "2026-03-21"), ("21.03.2026", "2026-03-21"),
                             ("04-07-2026", "2026-07-04"), ("1 września 2026", "2026-09-01"),
                             ("26 października 2026", "2026-10-26")]:
            self.assertEqual(data_iso(WZ_DATA.search(tekst)), oczek, tekst)

    def test_kwoty(self):
        self.assertEqual(kwota("200,00"), 200.0)
        self.assertEqual(kwota("17.00"), 17.0)
        self.assertEqual(kwota("1 200,50"), 1200.5)
        self.assertIsNone(kwota("abc"))

    def test_rachunek(self):
        self.assertEqual(tylko_cyfry_rachunku("PL66 2111 8080 1009 8828 5708 1733"), "66211180801009882857081733")

    def test_rachunek_z_kodem_kraju(self):
        # organizator (21.11): bez spacji, z kodem kraju, jeśli jest w dokumencie; inaczej same cyfry
        from silnik.konfig import ekstrahuj
        from silnik.narzedzia import rachunek
        self.assertEqual(rachunek("PL05 4706 8028 7581 5397 2145 5024"), "PL05470680287581539721455024")
        self.assertEqual(rachunek("05 4706 8028 7581 5397 2145 5024"), "05470680287581539721455024")
        self.assertEqual(rachunek("DE89 3704 0044 0532 0130 00"), "DE89370400440532013000")
        self.assertIsNone(rachunek("05 4706 8028"))
        konf = {"pola": {"z": {"regex": "z rachunku:? ({RACHUNEK})", "typ": "rachunek"},
                         "na": {"regex": "na rachunek:? ({RACHUNEK})", "typ": "rachunek"}}}
        tekst = ("z rachunku: DE89 3704 0044 0532 0130 00 Data operacji\n"
                 "na rachunek: PL05 4706 8028 7581 5397 2145 5024\n")
        self.assertEqual(ekstrahuj(tekst, konf), {"z": "DE89370400440532013000", "na": "PL05470680287581539721455024"})
        tekst2 = "z rachunku: 61 1090 1014 0000 0712 1981 2874, na rachunek: 05470680287581539721455024"
        self.assertEqual(ekstrahuj(tekst2, konf), {"z": "61109010140000071219812874", "na": "05470680287581539721455024"})

    def test_rachunek_od_modelu_z_kodem_kraju_z_tekstu(self):
        from silnik.llm import Weryfikator
        w = Weryfikator("Rachunek odbiorcy: PL05 4706 8028 7581 5397 2145 5024; nadawcy 61 1090 1014 0000 0712 1981 2874")
        spec = {"typ": "rachunek"}
        self.assertEqual(w.sprawdz("05470680287581539721455024", spec)[0], "PL05470680287581539721455024")  # PL z tekstu
        self.assertEqual(w.sprawdz("PL61109010140000071219812874", spec)[0], "61109010140000071219812874")  # bez PL w tekście
        self.assertEqual(w.sprawdz("99999999999999999999999999", spec), (None, "brak w tekście"))


class TestOcena(unittest.TestCase):
    def test_identycznosc(self):
        o = {"a": "X", "b": [{"n": "1"}, {"n": "2"}]}
        tp, fp, fn = _porownanie(o, o)
        self.assertEqual((fp, fn), (0, 0))
        self.assertEqual(tp, 3)

    def test_null_po_obu_stronach_nie_liczy_sie(self):
        self.assertEqual(_porownanie({"a": None}, {"a": None}), [0, 0, 0])
        self.assertEqual(_porownanie({"a": ""}, {"a": None}), [0, 0, 0])

    def test_bledna_wartosc_to_fp_i_fn(self):
        self.assertEqual(_porownanie({"a": "x"}, {"a": "y"}), [0, 1, 1])

    def test_brak_wartosci_to_fn_nadmiar_to_fp(self):
        self.assertEqual(_porownanie({"a": "x"}, {"a": None}), [0, 0, 1])
        self.assertEqual(_porownanie({"a": None}, {"a": "x"}), [0, 1, 0])

    def test_kolejnosc_listy_nie_ma_znaczenia(self):
        wz = {"l": [{"n": "A"}, {"n": "B"}]}
        pr = {"l": [{"n": "B"}, {"n": "A"}]}
        self.assertEqual(_porownanie(wz, pr), [2, 0, 0])

    def test_brakujacy_i_nadmiarowy_element_listy(self):
        wz = {"l": [{"n": "A"}, {"n": "B"}]}
        self.assertEqual(_porownanie(wz, {"l": [{"n": "A"}]}), [1, 0, 1])
        self.assertEqual(_porownanie({"l": [{"n": "A"}]}, {"l": [{"n": "A"}, {"n": "C"}]}), [1, 1, 0])

    def test_normalizacja_i_tryb_scisly(self):
        wz, pr = {"a": "Jan  Kowalski"}, {"a": "jan kowalski"}
        self.assertEqual(_porownanie(wz, pr), [1, 0, 0])
        self.assertEqual(_porownanie(wz, pr, scisle=True), [0, 1, 1])
        self.assertEqual(_porownanie({"k": 200.0}, {"k": 200}), [1, 0, 0])
        self.assertEqual(_porownanie({"p": 85071204278}, {"p": "85071204278"}), [1, 0, 0])

    def test_f1(self):
        self.assertEqual(ocena.f1(0, 0, 0), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(ocena.f1(2, 1, 1)[2], 2 / 3)


FAKTURA_YAML = (ROOT / "typy" / "_szablon" / "typ.yaml").read_text(encoding="utf-8")
FAKTURA_TEKST = """FAKTURA VAT nr FV/12/2026
Sprzedawca: Alfa sp. z o.o.
ul. Prosta 1, 00-001 Warszawa, NIP 123-456-32-18
Data wystawienia: 14 marca 2026
1. Papier A4 10 szt. 25,00
2. Toner czarny 2 szt. 310,50
Do zapłaty: 1 230,50
"""


class TestKonfiguracjaBezKodu(unittest.TestCase):
    """Nowy typ dokumentu = tylko plik YAML (szablon z typy/_szablon)."""

    def _typy_z_nowym(self, yaml_tekst=FAKTURA_YAML):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        k = Path(tmp.name) / "faktura"
        k.mkdir()
        (k / "typ.yaml").write_text(yaml_tekst, encoding="utf-8")
        from silnik.rdzen import wczytaj_typy
        return wczytaj_typy(Path(tmp.name))

    def test_nowy_typ_dziala_bez_kodu(self):
        typy = self._typy_z_nowym()
        self.assertEqual(klasyfikuj(FAKTURA_TEKST, typy), "faktura")
        f = typy["faktura"].wynik(FAKTURA_TEKST)["faktura"]
        self.assertEqual(f["numer"], "FV/12/2026")
        self.assertEqual(f["data_wystawienia"], "2026-03-14")
        self.assertEqual(f["suma_brutto"], 1230.5)
        self.assertEqual(f["nip_sprzedawcy"], "1234563218")
        self.assertEqual(f["rodzaj"], "zwykla")
        self.assertEqual(f["pozycje"], [{"nazwa": "Papier A4", "ilosc": 10, "cena": 25.0},
                                        {"nazwa": "Toner czarny", "ilosc": 2, "cena": 310.5}])

    def test_brak_dopasowania_daje_null(self):
        typy = self._typy_z_nowym()
        f = typy["faktura"].wynik("faktura vat\nnic wiecej")["faktura"]
        self.assertIsNone(f["numer"])
        self.assertIsNone(f["suma_brutto"])
        self.assertEqual(f["pozycje"], [])

    def test_blad_w_konfiguracji_pomija_typ_i_nie_psuje_reszty(self):
        zly = "klasyfikacja:\n  slowa_kluczowe: [x]\npola:\n  a.b:\n    regex: '(niedomkniety'\n"
        self.assertEqual(self._typy_z_nowym(zly), {})

    def test_wbudowane_typy_sa_w_rejestrze_a_szablon_pominiety(self):
        typy = wczytaj_typy()
        self.assertEqual(set(typy), {"odwolanie", "pelnomocnictwo", "przelew"})


class TestWalidacja(unittest.TestCase):
    """Klucz `walidacja` w konfiguracji: warunki dla wartości z reguł i od modelu."""

    def test_sumy_kontrolne(self):
        from silnik.narzedzia import suma_nip, suma_pesel, suma_rachunku, suma_regon
        self.assertTrue(suma_nip("123-456-32-18"))
        self.assertFalse(suma_nip("1234563219"))
        self.assertTrue(suma_regon("123456785"))
        self.assertFalse(suma_regon("123456786"))
        self.assertTrue(suma_pesel("44051401359"))
        self.assertFalse(suma_pesel("44051401358"))
        self.assertTrue(suma_rachunku("PL61 1090 1014 0000 0712 1981 2874"))
        self.assertTrue(suma_rachunku("61109010140000071219812874"))          # NRB bez PL
        self.assertFalse(suma_rachunku("61109010140000071219812875"))

    def test_warunki(self):
        import datetime
        from silnik.konfig import waliduj
        self.assertIsNone(waliduj("123456785", {"dlugosc": [9, 14]}))
        self.assertIn("długość", waliduj("12345", {"dlugosc": [9, 14]}))
        self.assertIsNone(waliduj("2025/BZP00270698", {"wzorzec": r"\d{4}/BZP\d{8}"}))
        self.assertIn("wzorca", waliduj("2025/BZP0027069", {"wzorzec": r"\d{4}/BZP\d{8}"}))
        self.assertIsNone(waliduj(4, {"wartosci": [1, 2, 3, 4]}))
        self.assertIsNotNone(waliduj(13, {"wartosci": [1, 2, 3, 4]}))
        self.assertIsNone(waliduj("2025-06-10", {"zakres": [datetime.date(2021, 1, 1), None]}))  # YAML: data
        self.assertIn("zakresem", waliduj("2015-06-10", {"zakres": ["2021-01-01", None]}))
        self.assertIn("zakresem", waliduj(0.0, {"zakres": [0.01, None]}))
        self.assertIn("suma kontrolna", waliduj("1234563219", {"suma_kontrolna": "nip"}))

    def _typ(self, pola_yaml: str):
        from silnik.rdzen import Typ
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        k = Path(tmp.name) / "nowy"
        k.mkdir()
        (k / "typ.yaml").write_text("klasyfikacja:\n  slowa_kluczowe: [umowa]\n" + pola_yaml, encoding="utf-8")
        return Typ(k)

    def test_pierwsza_poprawna_wartosc_i_raport_odrzucen(self):
        t = self._typ("pola:\n  nip:\n    regex: 'NIP:? ([\\d-]{10,13})'\n    typ: cyfry\n"
                      "    walidacja: {dlugosc: 10, suma_kontrolna: nip}\n"
                      "listy:\n  osoby:\n    regex: '^(?P<imie>\\w+) PESEL (?P<pesel>\\d{11})$'\n"
                      "    pola:\n      imie: imie\n"
                      "      pesel: {z_grupy: pesel, walidacja: {suma_kontrolna: pesel}}\n")
        os.environ["LLM_WYLACZ"] = "1"
        try:
            w, raport = t.wynik_z_raportem("umowa\nNIP 1234563219 (błąd OCR), NIP: 123-456-32-18\n"
                                           "Jan PESEL 44051401359\nOla PESEL 44051401358\n")
        finally:
            del os.environ["LLM_WYLACZ"]
        self.assertEqual(w["nip"], "1234563218")                    # drugie trafienie, pierwsze złe
        self.assertEqual(w["osoby"], [{"imie": "Jan", "pesel": "44051401359"}, {"imie": "Ola", "pesel": None}])
        self.assertEqual({(r["pole"], r["zrodlo"]) for r in raport}, {("nip", "reguly"), ("osoby[1].pesel", "reguly")})
        self.assertTrue(all("suma kontrolna" in r["powod_odrzucenia"] for r in raport))

    def test_czytelny_blad_walidacji_w_konfiguracji(self):
        from silnik.konfig import BladKonfiguracji
        with self.assertRaisesRegex(BladKonfiguracji, r"pola\.nip\.walidacja: nieznany warunek dlugość"):
            self._typ("pola:\n  nip: {regex: '(\\d+)', walidacja: {dlugość: 10}}\n")
        with self.assertRaisesRegex(BladKonfiguracji, r"suma_kontrolna: jedna z"):
            self._typ("pola:\n  nip: {regex: '(\\d+)', walidacja: {suma_kontrolna: nipp}}\n")

    def test_walidacja_wartosci_od_modelu(self):
        from silnik.llm import Weryfikator
        w = Weryfikator("PESEL 44051401359, drugi PESEL 44051401358")
        spec = {"typ": "cyfry", "dlugosc": 11, "walidacja": {"suma_kontrolna": "pesel"}}
        self.assertEqual(w.sprawdz("44051401359", spec), ("44051401359", None))
        self.assertEqual(w.sprawdz("44051401358", spec), (None, "błędna suma kontrolna (pesel)"))


class TestNowyTyp(unittest.TestCase):
    """Dodawanie typu bez kodu: sam YAML (reguły i/lub opisy pól dla LLM) + narzędzie nowy_typ.py."""

    def _typ(self, tresc_yaml: str):
        from silnik.rdzen import Typ
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        k = Path(tmp.name) / "nowy"
        k.mkdir()
        (k / "typ.yaml").write_text(tresc_yaml, encoding="utf-8")
        return Typ(k)

    def test_typ_tylko_z_opisami_llm_daje_pelna_strukture(self):
        t = self._typ("klasyfikacja:\n  slowa_kluczowe: [umowa]\nllm:\n  pola:\n"
                      "    umowa.numer: {typ: tekst, opis: x}\n"
                      "    strony: {typ: lista, element: {nazwa: {typ: tekst}}}\n")
        os.environ["LLM_WYLACZ"] = "1"
        try:
            w, raport = t.wynik_z_raportem("umowa nr 1")
        finally:
            del os.environ["LLM_WYLACZ"]
        self.assertEqual(w, {"umowa": {"numer": None}, "strony": []})
        self.assertEqual(raport, [])

    def test_czytelny_blad_konfiguracji_llm(self):
        from silnik.konfig import BladKonfiguracji
        with self.assertRaisesRegex(BladKonfiguracji, r"llm\.pola\.a\.b: nieznany typ 'datta'"):
            self._typ("klasyfikacja:\n  slowa_kluczowe: [x]\nllm:\n  pola:\n    a.b: {typ: datta}\n")

    def test_szkielet_z_wzorca_odgaduje_typy_pol(self):
        sys.path.insert(0, str(ROOT / "narzedzia"))
        import nowy_typ
        pola = nowy_typ._pola_z_wzorca({"a": {"data": "2025-06-05", "nip": "5250008525", "mail": "x@y.pl",
                                              "kwota": 12.5, "flaga": 1, "nazwa": "X"},
                                        "lista": [{"imie": "Jan"}]})
        self.assertEqual({k: v["typ"] for k, v in pola.items()},
                         {"a.data": "data", "a.nip": "cyfry", "a.mail": "email", "a.kwota": "kwota",
                          "a.flaga": "wybor", "a.nazwa": "tekst", "lista": "lista"})
        self.assertEqual(pola["a.nip"]["dlugosc"], 10)
        self.assertIn("imie", pola["lista"]["element"])

    def test_pokaz_ogloszenie_ted_same_reguly_zgodne_ze_wzorcem(self):
        from silnik.rdzen import Typ, czytaj_tekst
        k = ROOT / "typy" / "_pokaz_ogloszenie_ted"
        t = Typ(k)
        tekst = czytaj_tekst(k / "przyklady" / "negatywny_przyklad.pdf")
        wzorzec = json.loads((k / "przyklady" / "negatywny_przyklad.oczekiwany.json").read_text(encoding="utf-8"))
        self.assertEqual(t.wynik(tekst), wzorzec)
        self.assertGreater(t.punkty(tekst), 0)
        self.assertNotIn("_pokaz_ogloszenie_ted", wczytaj_typy())   # pokaz jest nieaktywny


class TestRdzen(unittest.TestCase):
    def test_klasyfikacja_rozstrzelonego_tytulu(self):
        typy = wczytaj_typy()
        self.assertEqual(klasyfikuj("P E Ł N O M O C N I C T W O\nMocodawca ustanawiam pełnomocnikiem", typy), "pelnomocnictwo")

    def test_nierozpoznany_tekst(self):
        self.assertIsNone(klasyfikuj("zupełnie inny dokument", wczytaj_typy()))

    def test_uszkodzony_pdf_nie_przerywa_przetwarzania(self):
        with tempfile.TemporaryDirectory() as tmp:
            we, wy = Path(tmp) / "in", Path(tmp) / "out"
            (we / "sprawa").mkdir(parents=True)
            (we / "sprawa" / "zepsuty.pdf").write_bytes(b"to nie jest pdf")
            self.assertEqual(przetworz_katalog(we, wy), 1)
            wynik = json.loads((wy / "sprawa" / "zepsuty.json").read_text(encoding="utf-8"))
            self.assertIn("blad", wynik)

    def test_run_py_zwraca_blad_dla_braku_katalogu(self):
        r = subprocess.run([sys.executable, str(ROOT / "run.py"), "--wejscie", str(ROOT / "nie_istnieje")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class TestUkladyOdwolania(unittest.TestCase):
    """Układy nagłówka spotykane w prawdziwych odwołaniach (teksty wymyślone, bez PDF)."""

    ADRESAT = "Warszawa, dnia 16 czerwca 2025 r.\nPrezes Krajowej Izby Odwoławczej\nul. Postępu 17a\n02-676 Warszawa\n"
    TRESC = ("Dotyczy: postępowania o udzielenie zamówienia publicznego pn. „Dostawa sprzętu” "
             "(postępowanie nr ZP.1.2025)\nODWOŁANIE\nDziałając w imieniu wykonawcy „Alfa Beta” Spółka "
             "z ograniczoną odpowiedzialnością, wnoszę odwołanie na czynności Zamawiającego, tj.: wobec treści\n"
             "Specyfikacji Warunków Zamówienia (SWZ). Zamawiający zamieścił ogłoszenie wraz z dokumentami w dniu "
             "10 czerwca 2025 r. Odwołanie jest wniesione w terminie.\n")

    def _wynik(self, naglowek: str) -> dict:
        typy = wczytaj_typy()
        tekst = self.ADRESAT + naglowek + self.TRESC
        self.assertEqual(klasyfikuj(tekst, typy), "odwolanie")
        return typy["odwolanie"].wynik(tekst)

    def test_etykieta_pod_blokiem(self):
        # dwie kolumny w skanie: OCR czyta dane strony, potem etykietę; nazwa w nagłówku zaczerniona
        w = self._wynik("|. BIE z ograniczona odpowiedzialnością\nKwiatowa 5, 00-001 Warszawa\n"
                        "KRS: 0000123456, NIP: 1234567890\nreprezentowany przez radcę prawnego ij KA\n"
                        "Odwołujący:\nGmina Przykładowo Wielkie Miasto Spółka z ograniczoną\nodpowiedzialnością\n"
                        "Rynek 1, 00-002 Przykładowo\nZamawiający:\n")
        o, z = w["Odwolujacy"][0], w["Zamawiajacy"][0]
        self.assertEqual(o["nazwa"], "Alfa Beta Spółka z ograniczoną odpowiedzialnością")   # z treści
        self.assertEqual((o["krs"], o["nip"], o["przez-pelnomocnika"]), ("0000123456", "1234567890", 1))
        self.assertIsNone(o["pelnomocnik"]["imie_nazwisko"])
        self.assertEqual(z["nazwa"], "Gmina Przykładowo Wielkie Miasto Spółka z ograniczoną odpowiedzialnością")
        self.assertEqual(len(w["Zamawiajacy"]), 1)
        self.assertEqual(w["OdwolanieKIO"]["czynnosc_powod_id"], 4)
        self.assertEqual(w["OdwolanieKIO"]["czynnosc_dzien"], "2025-06-10")

    def test_etykieta_nad_blokiem_bez_zamiany_stron(self):
        # zwykły układ: nazwa z nagłówka (nie z treści), strony nie zamienione
        w = self._wynik("Odwołujący:\nAlfa Beta sp. z o.o.\nul. Kwiatowa 5, 00-001 Warszawa\nKRS: 0000123456\n"
                        "Zamawiający:\nGmina Przykładowo\nul. Rynek 1, 00-002 Przykładowo\n")
        self.assertEqual(w["Odwolujacy"][0]["nazwa"], "Alfa Beta sp. z o.o.")
        self.assertEqual(w["Zamawiajacy"][0]["nazwa"], "Gmina Przykładowo")

    def test_nazwa_i_adres_w_jednej_linii_oraz_adres_bez_ul(self):
        w = self._wynik("Odwołujący: Alfa Beta sp. z o.o., ul. Kwiatowa 5, 00-001 Warszawa, KRS: 0000123456\n"
                        "Zamawiający:\nGmina Przykładowo\nRynek 1\n00-002 Przykładowo\nNIP: 9876543210\n")
        self.assertEqual((w["Odwolujacy"][0]["nazwa"], w["Odwolujacy"][0]["krs"]), ("Alfa Beta sp. z o.o.", "0000123456"))
        self.assertEqual((w["Zamawiajacy"][0]["nazwa"], w["Zamawiajacy"][0]["nip"]), ("Gmina Przykładowo", "9876543210"))

    # Tytuł zamówienia: cudzysłów w "dotyczy" bywa definicją pojęcia (prawdziwe odwołania z Internetu:
    # Lipie 2026 - „ustawą Pzp” przed tytułem; ZWPS 2022 - tytuł bez cudzysłowu po "pt.", potem „postępowanie”).
    def _dotyczy(self, dotyczy: str) -> dict:
        typy = wczytaj_typy()
        tekst = (self.ADRESAT + "Odwołujący:\nAlfa Beta sp. z o.o.\nul. Kwiatowa 5, 00-001 Warszawa\n"
                 "Zamawiający:\nZespół Przychodni w Katowicach,\nul. Rynek 1, 00-002 Katowice\n" + dotyczy
                 + "\nODWOŁANIE\nDziałając w imieniu wykonawcy Alfa Beta sp. z o.o. wnoszę odwołanie.\n")
        self.assertEqual(klasyfikuj(tekst, typy), "odwolanie")
        return typy["odwolanie"].wynik(tekst)

    def test_tytul_po_definicjach_w_cudzyslowie(self):
        w = self._dotyczy("Dotyczy: postępowania prowadzonego na podstawie ustawy z dnia 11 września 2019 r. Prawo "
                          "zamówień publicznych zwanej dalej: „ustawą Pzp” lub „uPzp”, pn.: „ BUDOWA BOISKA "
                          "WIELOFUNKCYJNEGO” Numer postępowania: ZP.271.24.2025")
        self.assertEqual(w["ZamowieniePubliczne"]["tytul"], "BUDOWA BOISKA WIELOFUNKCYJNEGO")

    def test_tytul_bez_cudzyslowu_po_pt(self):
        w = self._dotyczy("Dotyczy: postępowania w trybie podstawowym pt. Zakup 3 mikrotomów dla Pracowni "
                          "Patomorfologii. Numer sprawy: ZP / D / 8 / 22. Ogłoszenie nr 2022/BZP 00394256/01 "
                          "(dalej „postępowanie”).")
        self.assertEqual(w["ZamowieniePubliczne"]["tytul"], "Zakup 3 mikrotomów dla Pracowni Patomorfologii")
        self.assertEqual(w["Zamawiajacy"][0]["nazwa"], "Zespół Przychodni w Katowicach")   # bez przecinka

    def test_same_definicje_zostawiaja_tytul_pusty_dla_modelu(self):
        # wartość z reguły blokowałaby model (uzupełnia tylko puste pola), więc definicja nie może być tytułem
        w = self._dotyczy("Dotyczy: postępowania prowadzonego na podstawie ustawy „Prawo zamówień publicznych” "
                          "(dalej „postępowanie”), zwanej dalej „ustawą Pzp”.")
        self.assertIsNone(w["ZamowieniePubliczne"]["tytul"])


class TestLLM(unittest.TestCase):
    """Warstwa LLM z atrapą modelu (bez sieci): co przyjmujemy, a co odrzucamy."""

    TEKST = ("Warszawa, dnia 16 czerwca 2025 r.\nOdwołujący: ACME Spółka z o.o., KRS: 0000123456\n"
             "reprezentowany przez radcę prawnego Jana Kowalskiego\n"
             "Zamawiający zamieścił ogłoszenie wraz z SWZ w dniu 10 czerwca 2025 r.")
    KONF_TYPU = {
        "opis_dokumentu": "test",
        "pola": {
            "A.data": {"typ": "data"},
            "A.miejsce": {"typ": "tekst"},
            "A.powod": {"typ": "wybor", "wartosci": {4: "Publikacja SWZ", 9: "Unieważnienie"}, "opis_do": "A.opis"},
            "L": {"typ": "lista", "element": {
                "nazwa": {"typ": "tekst"}, "krs": {"typ": "cyfry", "dlugosc": 10},
                "osoba": {"typ": "osoba"}, "lider": {"typ": "wybor", "wartosci": [0, 1], "tylko_gdy_wiele": True}}},
        },
    }

    def _uruchom(self, wynik, odpowiedz):
        from unittest import mock
        from silnik import llm
        with mock.patch.object(llm, "konfiguracja", return_value={"adres": "x", "model": "m"}), \
                mock.patch.object(llm, "_zapytaj", return_value=odpowiedz) as zap:
            raport = llm.uzupelnij(self.TEKST, wynik, self.KONF_TYPU)
        return raport, zap

    def test_wypelnia_tylko_puste_i_weryfikuje(self):
        wynik = {"A": {"data": "2025-06-16", "miejsce": None, "powod": None, "opis": None}, "L": []}
        odp = {"A.data": "2099-01-01", "A.miejsce": "Kraków", "A.powod": "4",
               "L": [{"nazwa": "ACME Spółka z o.o.", "krs": "0000123456", "osoba": "ij KA", "lider": 1}]}
        raport, zap = self._uruchom(wynik, odp)
        self.assertEqual(wynik["A"]["data"], "2025-06-16")          # reguła ma pierwszeństwo
        self.assertIsNone(wynik["A"]["miejsce"])                     # "Kraków" nie ma w tekście
        self.assertEqual((wynik["A"]["powod"], wynik["A"]["opis"]), (4, "Publikacja SWZ"))
        self.assertEqual(wynik["L"], [{"nazwa": "ACME Spółka z o.o.", "krs": "0000123456",
                                       "osoba": None, "lider": None}])  # śmieć OCR i lider przy 1 elemencie
        pytane = list(zap.call_args[0][1][1]["content"].split("Zwróć JSON")[0].split('- "')[1:])
        self.assertFalse(any(p.startswith("A.data") for p in pytane))  # o wypełnione pole nie pytamy
        odrzucone = {r["pole"] for r in raport if not r["przyjete"]}
        self.assertEqual(odrzucone, {"A.miejsce", "L[0].osoba"})

    def test_zmyslona_data_i_spoza_listy_odrzucone(self):
        wynik = {"A": {"data": None, "miejsce": "Warszawa", "powod": None, "opis": None}, "L": [{"nazwa": "x"}]}
        self._uruchom(wynik, {"A.data": "2025-06-11", "A.powod": 12})
        self.assertIsNone(wynik["A"]["data"])
        self.assertIsNone(wynik["A"]["powod"])

    def test_osoba_pierwsza_z_listy_i_bez_tytulu(self):
        from silnik.llm import Weryfikator
        w = Weryfikator("reprezentowany przez: adw. Jan Kowalski, adw. Anna Nowak; ij KA")
        self.assertEqual(w.sprawdz("Jan Kowalski, Anna Nowak", {"typ": "osoba"})[0], "Jan Kowalski")
        self.assertEqual(w.sprawdz("adw. Anna Nowak", {"typ": "osoba"})[0], "Anna Nowak")
        self.assertIsNone(w.sprawdz("ij KA", {"typ": "osoba"})[0])
        self.assertIsNone(w.sprawdz("Piotr Zmyślony", {"typ": "osoba"})[0])

    def test_osoba_w_mianowniku_gdy_w_tekscie_inny_przypadek(self):
        from silnik.llm import Weryfikator
        w = Weryfikator("reprezentowany przez radcę prawnego Mateusza Szymańskiego oraz adw. Annę Nowak")
        self.assertEqual(w.sprawdz("Mateusz Szymański", {"typ": "osoba"})[0], "Mateusz Szymański")
        self.assertEqual(w.sprawdz("Anna Nowak", {"typ": "osoba"})[0], "Anna Nowak")
        self.assertIsNone(w.sprawdz("Marek Szymański", {"typ": "osoba"})[0])      # inne imię - odrzucone
        self.assertIsNone(w.sprawdz("Mateusz Kowalski", {"typ": "osoba"})[0])     # inne nazwisko - odrzucone

    def test_nazwa_z_kropka_formy_prawnej_zachowana(self):
        from silnik.llm import Weryfikator
        w = Weryfikator("Zamawiający: Alfa sp. z o.o., ul. Prosta 1")
        self.assertEqual(w.sprawdz("Alfa sp. z o.o.", {"typ": "tekst"})[0], "Alfa sp. z o.o.")

    def test_nic_do_uzupelnienia_brak_zapytania(self):
        wynik = {"A": {"data": "d", "miejsce": "m", "powod": 4, "opis": "o"}, "L": [{"nazwa": "x"}]}
        _, zap = self._uruchom(wynik, {})
        zap.assert_not_called()

    def test_dwa_bledy_polaczenia_z_rzedu_wylaczaja_llm(self):
        from silnik import llm
        k = {"adres": "http://127.0.0.1:9/v1", "model": "m", "limit_czasu_s": 5, "cache": ".cache_llm_test",
             "prog_wylaczenia": 2}
        try:
            self.assertIsNone(llm._zapytaj(k, [{"role": "user", "content": "x"}]))
            self.assertFalse(llm._stan["wylaczony_po_bledzie"])      # jedna czkawka sieci to za mało
            self.assertIsNone(llm._zapytaj(k, [{"role": "user", "content": "y"}]))
            self.assertTrue(llm._stan["wylaczony_po_bledzie"])
            self.assertIsNone(llm.konfiguracja())
        finally:
            llm._stan.update({"wylaczony_po_bledzie": False, "bledy_z_rzedu": 0})

    def test_wzor_klucza_nie_jest_kluczem_i_wylacza_kolejke(self):
        from unittest import mock
        from silnik import llm
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "llm.klucz").write_text("tutaj wpisz klucz\n", encoding="utf-8")
            k = {"klucz_zmienna": "TEST_KLUCZ_BRAK", "klucz_plik": "llm.klucz", "klucz_plik_lokalny": "llm.klucz.lokalny"}
            with mock.patch.object(llm, "KATALOG", Path(tmp)):
                self.assertEqual(llm._klucz(k), "")                         # wzór z repozytorium
                (Path(tmp) / "llm.klucz.lokalny").write_text("prawdziwy-klucz\n", encoding="utf-8")
                self.assertEqual(llm._klucz(k), "prawdziwy-klucz")          # plik lokalny ma pierwszeństwo
        # tryb kolejki bez klucza: konfiguracja None (bez łączenia się z hostingiem)
        stan = dict(llm._stan)
        try:
            with mock.patch.object(llm, "_klucz", return_value=""), \
                    mock.patch.dict(os.environ, {"LLM_TRYB": "kolejka"}):
                self.assertIsNone(llm.konfiguracja())
        finally:
            llm._stan.clear()
            llm._stan.update(stan)

    def test_repozytorium_zawiera_tylko_wzor_klucza(self):
        self.assertEqual((ROOT / "llm.klucz").read_text(encoding="utf-8").strip(), "tutaj wpisz klucz")

    def test_klucz_ze_zmiennej_ma_pierwszenstwo_przed_plikiem(self):
        from silnik import llm
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "k.txt").write_text("z-pliku\n", encoding="utf-8")
            from unittest import mock
            with mock.patch.object(llm, "KATALOG", Path(tmp)):
                k = {"klucz_zmienna": "TEST_KLUCZ_LLM", "klucz_plik": "k.txt"}
                self.assertEqual(llm._klucz(k), "z-pliku")
                with mock.patch.dict(os.environ, {"TEST_KLUCZ_LLM": "ze-zmiennej"}):
                    self.assertEqual(llm._klucz(k), "ze-zmiennej")


def _znajdz_php():
    import shutil
    kandydaci = [os.environ.get("PHP_EXE"), shutil.which("php")]
    kandydaci += [str(p) for p in Path(os.environ.get("LOCALAPPDATA", "/nonexistent"), "Microsoft", "WinGet",
                                       "Packages").glob("PHP.PHP*/php.exe")]
    return next((k for k in kandydaci if k and Path(k).is_file()), None)


@unittest.skipUnless(_znajdz_php(), "brak PHP (kolejka na hosting testowana tylko z PHP)")
class TestKolejkaPHP(unittest.TestCase):
    """serwer_kolejki/kolejka.php + pracownik (atrapa modelu) + klient LLM w trybie kolejki."""

    @classmethod
    def setUpClass(cls):
        import socket
        import time as _t
        cls.tmp = tempfile.TemporaryDirectory()
        www, dane = Path(cls.tmp.name, "www"), Path(cls.tmp.name, "dane")
        www.mkdir(), dane.mkdir()
        (www / "kolejka.php").write_bytes((ROOT / "serwer_kolejki" / "kolejka.php").read_bytes())
        cls.kk, cls.kp = "klient-test", "pracownik-test"
        (www / "kolejka_konfig.php").write_text(
            f"<?php return array('klucz_klienta' => '{cls.kk}', 'klucz_pracownika' => '{cls.kp}', "
            f"'katalog' => '{dane.as_posix()}', 'pracownik_aktywny_s' => 5);", encoding="utf-8")
        (Path(cls.tmp.name) / "kp.txt").write_text(cls.kp, encoding="utf-8")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.adres = f"http://127.0.0.1:{port}/kolejka.php"
        cls.php = subprocess.Popen([_znajdz_php(), "-S", f"127.0.0.1:{port}", "-t", str(www)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _t.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        cls.php.terminate()
        cls.php.wait(10)
        cls.tmp.cleanup()

    def _klient(self):
        from silnik import llm
        llm._stan.clear()
        llm._stan.update({"wylaczony_po_bledzie": False, "bledy_z_rzedu": 0})
        return llm, {"tryb": "kolejka", "adres": self.adres, "model": "m", "klucz_zmienna": "TEST_KK_KOLEJKI",
                     "cache": ".cache_llm_test", "limit_czekania_s": 4, "odstep_odpytywania_s": 0.2,
                     "limit_czasu_s": 30, "tryb_json": False}

    def test_pelny_obieg_z_pracownikiem(self):
        import shutil
        pracownik = subprocess.Popen([sys.executable, str(ROOT / "narzedzia" / "pracownik_kolejki.py"),
                                      "--adres", self.adres, "--klucz-plik", str(Path(self.tmp.name) / "kp.txt"),
                                      "--atrapa", "--odstep", "0.2", "--pozwol-usypiac"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            llm, k = self._klient()
            with mock_env({"TEST_KK_KOLEJKI": self.kk}):
                import time as _t
                _t.sleep(1.0)
                self.assertEqual(llm._zapytaj(k, [{"role": "user", "content": "test-obiegu"}]), {"atrapa": True})
        finally:
            pracownik.terminate()
            pracownik.wait(10)
            shutil.rmtree(ROOT / ".cache_llm_test", ignore_errors=True)

    def test_bez_pracownika_szybkie_wylaczenie(self):
        import time as _t
        llm, k = self._klient()
        _t.sleep(6)                                            # serce pracownika starsze niż 5 s
        with mock_env({"TEST_KK_KOLEJKI": self.kk}):
            t0 = _t.monotonic()
            self.assertIsNone(llm._zapytaj(k, [{"role": "user", "content": "nikt-nie-odbierze"}]))
            self.assertLess(_t.monotonic() - t0, 5)
        self.assertTrue(llm._stan["wylaczony_po_bledzie"])
        llm._stan.update({"wylaczony_po_bledzie": False})

    def test_zly_klucz_klienta(self):
        llm, k = self._klient()
        llm._stan["zdrowie_sprawdzone"] = True
        with mock_env({"TEST_KK_KOLEJKI": "zly"}):
            self.assertIsNone(llm._zapytaj(k, [{"role": "user", "content": "zly-klucz"}]))
        self.assertTrue(llm._stan["wylaczony_po_bledzie"])
        llm._stan.update({"wylaczony_po_bledzie": False})


def mock_env(zmienne: dict):
    from unittest import mock
    return mock.patch.dict(os.environ, zmienne)


SZABLON = DANE.parent / "szablon-aplikacja" / "input" / "case1"


@unittest.skipUnless(SZABLON.is_dir(), "brak przykładów z szablon-aplikacja")
class TestPrzykladyOrganizatora(unittest.TestCase):
    """Pliki z repozytorium UZP: realny skan odwołania i dokument spoza trzech typów."""

    def test_negatywny_przyklad_nie_jest_klasyfikowany(self):
        from silnik.rdzen import czytaj_tekst
        self.assertIsNone(klasyfikuj(czytaj_tekst(SZABLON / "negatywny przyklad.pdf"), wczytaj_typy()))

    def test_nierozpoznany_dokument_daje_pusty_plik_json(self):
        from silnik.rdzen import przetworz_plik
        with tempfile.TemporaryDirectory() as tmp:
            wy, rap = Path(tmp) / "out" / "x.json", Path(tmp) / "raport" / "x.json"
            przetworz_plik(SZABLON / "negatywny przyklad.pdf", wy, wczytaj_typy(), rap)
            self.assertTrue(wy.exists())                # organizator: "pusty z {}"
            self.assertEqual(wy.read_text(encoding="utf-8"), "{}")
            self.assertIn("nierozpoznany", json.loads(rap.read_text(encoding="utf-8"))["uwaga"])

    def test_skan_bez_ocr_nie_przerywa_pracy(self):
        from silnik import ocr
        from silnik.rdzen import czytaj_tekst
        os.environ["OCR_WYLACZ"] = "1"
        try:
            self.assertEqual(czytaj_tekst(SZABLON / "przyklad odwolanie.pdf").strip(), "")
        finally:
            del os.environ["OCR_WYLACZ"]
        self.assertIsNotNone(ocr)

    def test_skan_odwolania_przez_ocr(self):
        from silnik import ocr
        from silnik.rdzen import czytaj_tekst
        if ocr.dostepny_silnik() != "tesseract":
            self.skipTest("brak Tesseracta z językiem pol")
        typy = wczytaj_typy()
        tekst = czytaj_tekst(SZABLON / "przyklad odwolanie.pdf")
        self.assertEqual(klasyfikuj(tekst, typy), "odwolanie")
        w = typy["odwolanie"].wynik(tekst)
        # wartości odczytane ręcznie ze skanu (pierwsza strona)
        self.assertEqual(w["OdwolanieKIO"]["data"], "2025-06-16")
        self.assertEqual(w["OdwolanieKIO"]["miejscowosc"], "Warszawa")
        self.assertEqual(w["ZamowieniePubliczne"]["numer_bzp"], "2025/BZP00270698")
        self.assertEqual(w["ZamowieniePubliczne"]["numer_postepowania"], "RZP.27.29.2025.KD")
        # układ "etykieta pod blokiem" + nazwa odwołującego z treści (w nagłówku jest zaczerniona)
        self.assertEqual(w["Odwolujacy"][0]["nazwa"], "BIOARCUS Spółka z ograniczoną odpowiedzialnością")
        self.assertEqual(w["Odwolujacy"][0]["przez-pelnomocnika"], 1)
        self.assertIsNone(w["Odwolujacy"][0]["pelnomocnik"]["imie_nazwisko"])   # zaczernione - bez śmieci OCR
        self.assertEqual(w["Zamawiajacy"][0]["nazwa"], "Miejskie Przedsiębiorstwo Oczyszczania w m. st. "
                                                       "Warszawie Spółka z ograniczoną odpowiedzialnością")
        self.assertEqual(w["OdwolanieKIO"]["czynnosc_powod_id"], 4)
        self.assertEqual(w["OdwolanieKIO"]["czynnosc_dzien"], "2025-06-10")


@unittest.skipUnless(sys.platform == "win32", "run.bat tylko na Windows")
class TestRunBat(unittest.TestCase):
    def test_kod_wyjscia_przy_bledzie(self):
        r = subprocess.run(["cmd", "/c", str(ROOT / "run.bat"), str(ROOT / "nie_istnieje"), "out"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


@unittest.skipUnless(MA_DANE, "brak danych UZP (ustaw DANE_UZP)")
class TestRegresjaNaDanych(unittest.TestCase):
    """Cały przepływ PDF -> JSON na zestawach treningowych; progi chronią przed regresją."""

    @classmethod
    def setUpClass(cls):
        # zestawy tekstowe (przyklad_*) i negatywy; skany mierzone osobno (inny poziom jakości, OCR)
        import shutil
        cls.tmp = tempfile.TemporaryDirectory()
        cls.we, cls.wy = Path(cls.tmp.name) / "dane", Path(cls.tmp.name) / "out"
        for k in DANE.iterdir():
            if k.is_dir() and (k.name.startswith("przyklad_") or k.name == "negatywne"):
                shutil.copytree(k, cls.we / k.name)
        os.environ["LLM_WYLACZ"] = "1"             # regresja dotyczy reguł; LLM testowany osobno
        try:
            przetworz_katalog(cls.we, cls.wy)
        finally:
            del os.environ["LLM_WYLACZ"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_kazdy_pdf_ma_json_o_wlasciwym_typie(self):
        klucz = {"OdwolanieKIO", "mocodawca", "przelew_bankowy"}
        negatywy = 0
        for pdf in self.we.rglob("*.pdf"):
            plik = self.wy / pdf.relative_to(self.we).with_suffix(".json")
            wzorzec = pdf.with_suffix(".json")
            if not wzorzec.exists() and pdf.stem == "potwierdzenie_przelewu":
                wzorzec = pdf.with_name("przelew.json")
            if wzorzec.exists():                      # typ oczekiwany = klucz główny wzorca
                oczek = (set(json.loads(wzorzec.read_text(encoding="utf-8"))) & klucz).pop()
                self.assertIn(oczek, json.loads(plik.read_text(encoding="utf-8")), str(pdf))
            else:                                     # dokument negatywny UZP: plik .json z "{}"
                negatywy += 1
                self.assertEqual(plik.read_text(encoding="utf-8"), "{}", str(pdf))
        if (DANE / "negatywne").is_dir():
            self.assertGreater(negatywy, 0)

    def _f1_typu(self, typ):
        # wzorce treningowe UZP usuwają "PL" z rachunków, a organizator (21.11) każe go zachować,
        # jeśli jest w dokumencie - rachunki porównujemy więc bez kodu kraju (zapis z PL: test_rachunek_z_kodem_kraju)
        bufor = io.StringIO()
        with redirect_stdout(bufor):
            ocena.main(["--wzorzec", str(self.we), "--wynik", str(self.wy), "--bez-kodu-kraju"])
        ocena.USTAWIENIA["bez_kodu_kraju"] = False
        for linia in bufor.getvalue().splitlines():
            if linia.startswith(typ):
                return float(linia.split()[3])
        self.fail(f"brak typu {typ} w raporcie")

    def test_f1_przelew(self):
        self.assertGreaterEqual(self._f1_typu("przelew"), 0.99)

    def test_f1_pelnomocnictwo(self):
        self.assertGreaterEqual(self._f1_typu("pelnomocnictwo"), 0.99)

    def test_f1_odwolanie(self):
        self.assertGreaterEqual(self._f1_typu("odwolanie"), 0.99)

    def test_odwolanie_powod_i_opis_spojne_z_referencja(self):
        """Pary (id, opis) z wyniku muszą występować w danych referencyjnych (opis 1:1 z id)."""
        wzorzec = {}
        for p in DANE.glob("*/odwolanie.json"):
            k = json.loads(p.read_text(encoding="utf-8"))["OdwolanieKIO"]
            wzorzec[k["czynnosc_powod_id"]] = k["czynnosc_opis"]
        for p in self.wy.glob("*/odwolanie.json"):
            k = json.loads(p.read_text(encoding="utf-8"))["OdwolanieKIO"]
            self.assertEqual(wzorzec.get(k["czynnosc_powod_id"]), k["czynnosc_opis"], p.parent.name)

    def test_odwolanie_yaml_naglowek_i_powod_czynnosci(self):
        """Typ odwołania jest teraz samą konfiguracją YAML - sprawdzamy go przez wynik."""
        t = wczytaj_typy()["odwolanie"]
        self.assertIsNone(t.ekstraktor)                         # żadnego kodu w typie

        def kio(tekst):
            return t.wynik(tekst)["OdwolanieKIO"]

        self.assertEqual((kio("Gdańsk, dnia 3 września 2026 roku\n")["data"], kio("Gdańsk, dnia 3 września 2026 roku\n")["miejscowosc"]),
                         ("2026-09-03", "Gdańsk"))
        self.assertEqual(kio("Poznań, 28.08.2026\n")["data"], "2026-08-28")
        self.assertIsNone(kio("Brak daty w tym tekście\n")["data"])
        for zdanie, oczek in [("nie wykluczył z postępowania podmiotu", 8),
                              ("zaniechał unieważnienia postępowania", 10),
                              ("unieważnił postępowanie", 9),
                              ("przekazał odpowiedzi i wyjaśnienia do treści Specyfikacji", 6)]:
            k = kio(f"Zamawiający w dniu 8 lipca 2026 roku {zdanie}. Zarzut nr 1")
            self.assertEqual((k["czynnosc_powod_id"], k["czynnosc_dzien"]), (oczek, "2026-07-08"), zdanie)
            self.assertIsNotNone(k["czynnosc_opis"])

    def test_wszystkie_typy_konkursowe_bez_kodu(self):
        self.assertEqual(list((ROOT / "typy").rglob("*.py")), [])
        for nazwa, t in wczytaj_typy().items():
            self.assertIsNone(t.ekstraktor, nazwa)


if __name__ == "__main__":
    unittest.main()
