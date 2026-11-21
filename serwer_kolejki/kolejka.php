<?php
/*
 * Kolejka zadań dla zdalnego Bielika (hosting współdzielony, PHP 7.0+, bez rozszerzeń i bazy).
 *
 * Program (u sędziego) wrzuca zapytanie do modelu i odpytuje o wynik; "pracownik" na komputerze
 * z Bielikiem sam odpytuje kolejkę (tylko połączenia WYCHODZĄCE), liczy odpowiedź i ją odsyła.
 *
 *   POST ?a=zadanie            [klucz klienta]     treść: zapytanie /chat/completions  -> {"id": ...}
 *   GET  ?a=wynik&id=ID        [klucz klienta]     -> {"status": "czeka|w_trakcie|gotowe|blad", ...}
 *   GET  ?a=nastepne           [klucz pracownika]  -> 200 {"id", "tresc"} albo 204 (pusto)
 *   POST ?a=odpowiedz&id=ID    [klucz pracownika]  treść: {"odpowiedz": {...}} albo {"blad": "..."}
 *   GET  ?a=zdrowie            [bez klucza]        -> {"ok": pracownik aktywny, "w_kolejce": N}
 *
 * Klucz: nagłówek X-Api-Key albo Authorization: Bearer. Konfiguracja: kolejka_konfig.php.
 * Zadania są plikami JSON; przejęcie zadania = atomowe rename() (dwa pobrania nie wezmą tego samego).
 * Treść dokumentów (dane osobowe) leży na serwerze tylko do odebrania wyniku i jest usuwana.
 */

$KONF = array(
    'klucz_klienta' => '',
    'klucz_pracownika' => '',
    'katalog' => null,                  // null = katalog poza public_html (jeśli zapisywalny), inaczej obok skryptu
    'max_bajtow' => 600000,
    'max_w_kolejce' => 300,
    'limit_przetwarzania_s' => 180,     // zadanie bez odpowiedzi po tym czasie wraca do kolejki (1 raz)
    'zycie_zadania_s' => 900,           // starsze pliki są usuwane (klient i tak już zrezygnował)
    'pracownik_aktywny_s' => 30,        // pracownik odpytuje co 1-5 s, więc 30 s ciszy = nie działa
);
$plik_konf = __DIR__ . '/kolejka_konfig.php';
if (is_file($plik_konf)) {
    $KONF = array_merge($KONF, (array) (include $plik_konf));
}

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function odpowiedz($kod, $dane = null) {
    http_response_code($kod);
    if ($dane !== null) {
        echo json_encode($dane, JSON_UNESCAPED_UNICODE);
    }
    exit;
}

function podany_klucz() {
    if (!empty($_SERVER['HTTP_X_API_KEY'])) {
        return trim($_SERVER['HTTP_X_API_KEY']);
    }
    foreach (array('HTTP_AUTHORIZATION', 'REDIRECT_HTTP_AUTHORIZATION') as $k) {
        if (!empty($_SERVER[$k]) && stripos($_SERVER[$k], 'Bearer ') === 0) {
            return trim(substr($_SERVER[$k], 7));
        }
    }
    return '';
}

function wymagaj_klucza($oczekiwany) {
    $podany = podany_klucz();
    if ($oczekiwany === '' || $podany === '' || !hash_equals($oczekiwany, $podany)) {
        odpowiedz(401, array('error' => array('message' => 'brak lub zły klucz API')));
    }
}

function katalog_danych($konf) {
    $kandydaci = array();
    if (!empty($konf['katalog'])) {
        $kandydaci[] = $konf['katalog'];
    } else {
        $kandydaci[] = dirname(__DIR__) . '/bielik_kolejka_dane';   // poza katalogiem publicznym
        $kandydaci[] = __DIR__ . '/dane_kolejki';
    }
    foreach ($kandydaci as $k) {
        if (!is_dir($k)) {
            @mkdir($k, 0700, true);
        }
        if (is_dir($k) && is_writable($k)) {
            foreach (array('czeka', 'w_trakcie', 'gotowe') as $p) {
                if (!is_dir("$k/$p")) {
                    mkdir("$k/$p", 0700);
                }
            }
            if (!is_file("$k/.htaccess")) {
                // zawsze blokujemy dostęp z WWW (katalog "nad" subdomeną bywa publiczny dla domeny głównej)
                file_put_contents("$k/.htaccess", "<IfModule mod_authz_core.c>\n  Require all denied\n</IfModule>\n"
                    . "<IfModule !mod_authz_core.c>\n  Order deny,allow\n  Deny from all\n</IfModule>\n");
                file_put_contents("$k/index.html", '');
            }
            return $k;
        }
    }
    odpowiedz(500, array('error' => array('message' => 'brak zapisywalnego katalogu kolejki')));
}

function poprawne_id($id) {
    return is_string($id) && preg_match('/^[0-9]{13}_[0-9a-f]{12}$/', $id);
}

function sprzataj($kat, $konf) {
    $teraz = time();
    foreach (array('czeka', 'w_trakcie', 'gotowe') as $p) {
        foreach (glob("$kat/$p/*.json") ?: array() as $f) {
            if ($teraz - filemtime($f) > $konf['zycie_zadania_s']) {
                @unlink($f);
            }
        }
    }
    // zadania porzucone przez pracownika wracają do kolejki (raz), potem błąd
    foreach (glob("$kat/w_trakcie/*.json") ?: array() as $f) {
        if ($teraz - filemtime($f) > $konf['limit_przetwarzania_s']) {
            $id = basename($f, '.json');
            $z = json_decode((string) @file_get_contents($f), true);
            if (is_array($z) && empty($z['ponowione'])) {
                $z['ponowione'] = 1;
                if (@rename($f, "$kat/czeka/$id.json")) {
                    file_put_contents("$kat/czeka/$id.json", json_encode($z, JSON_UNESCAPED_UNICODE), LOCK_EX);
                }
            } elseif (@rename($f, "$kat/gotowe/$id.json")) {
                file_put_contents("$kat/gotowe/$id.json", json_encode(array('blad' => 'przekroczony czas przetwarzania')), LOCK_EX);
            }
        }
    }
}

$a = isset($_GET['a']) ? $_GET['a'] : '';
$metoda = $_SERVER['REQUEST_METHOD'];
$kat = katalog_danych($KONF);

if ($a === 'zdrowie' && $metoda === 'GET') {
    $serce = "$kat/pracownik.serce";
    $temu = is_file($serce) ? time() - filemtime($serce) : null;
    odpowiedz(200, array(
        'ok' => $temu !== null && $temu <= $KONF['pracownik_aktywny_s'],
        'pracownik_widziany_s_temu' => $temu,
        'w_kolejce' => count(glob("$kat/czeka/*.json") ?: array()),
        'w_trakcie' => count(glob("$kat/w_trakcie/*.json") ?: array()),
    ));
}

if ($a === 'zadanie' && $metoda === 'POST') {
    wymagaj_klucza($KONF['klucz_klienta']);
    sprzataj($kat, $KONF);
    $tresc = file_get_contents('php://input');
    if ($tresc === '' || strlen($tresc) > $KONF['max_bajtow']) {
        odpowiedz(413, array('error' => array('message' => 'zapytanie puste lub za duże')));
    }
    $dane = json_decode($tresc, true);
    if (!is_array($dane) || !isset($dane['messages']) || !is_array($dane['messages'])) {
        odpowiedz(400, array('error' => array('message' => 'niepoprawny JSON (brak messages)')));
    }
    if (count(glob("$kat/czeka/*.json") ?: array()) >= $KONF['max_w_kolejce']) {
        odpowiedz(429, array('error' => array('message' => 'kolejka pełna')));
    }
    $id = sprintf('%013d', (int) round(microtime(true) * 1000)) . '_' . bin2hex(random_bytes(6));
    file_put_contents("$kat/czeka/$id.json.tmp", json_encode(array('tresc' => $dane), JSON_UNESCAPED_UNICODE), LOCK_EX);
    rename("$kat/czeka/$id.json.tmp", "$kat/czeka/$id.json");
    odpowiedz(202, array('id' => $id));
}

if ($a === 'wynik' && $metoda === 'GET') {
    wymagaj_klucza($KONF['klucz_klienta']);
    $id = isset($_GET['id']) ? $_GET['id'] : '';
    if (!poprawne_id($id)) {
        odpowiedz(400, array('error' => array('message' => 'złe id')));
    }
    if (is_file("$kat/gotowe/$id.json")) {
        $z = json_decode((string) file_get_contents("$kat/gotowe/$id.json"), true);
        @unlink("$kat/gotowe/$id.json");               // wynik odebrany - usuwamy dane
        if (isset($z['blad'])) {
            odpowiedz(200, array('status' => 'blad', 'blad' => $z['blad']));
        }
        odpowiedz(200, array('status' => 'gotowe', 'odpowiedz' => $z['odpowiedz']));
    }
    if (is_file("$kat/w_trakcie/$id.json")) {
        odpowiedz(200, array('status' => 'w_trakcie'));
    }
    if (is_file("$kat/czeka/$id.json")) {
        odpowiedz(200, array('status' => 'czeka'));
    }
    odpowiedz(404, array('error' => array('message' => 'nieznane zadanie')));
}

if ($a === 'nastepne' && $metoda === 'GET') {
    wymagaj_klucza($KONF['klucz_pracownika']);
    touch("$kat/pracownik.serce");
    sprzataj($kat, $KONF);
    $pliki = glob("$kat/czeka/*.json") ?: array();
    sort($pliki);                                       // najstarsze najpierw (id zaczyna się od czasu)
    foreach ($pliki as $f) {
        $id = basename($f, '.json');
        if (@rename($f, "$kat/w_trakcie/$id.json")) {   // atomowe przejęcie zadania
            touch("$kat/w_trakcie/$id.json");
            $z = json_decode((string) file_get_contents("$kat/w_trakcie/$id.json"), true);
            odpowiedz(200, array('id' => $id, 'tresc' => $z['tresc']));
        }
    }
    odpowiedz(204);
}

if ($a === 'odpowiedz' && $metoda === 'POST') {
    wymagaj_klucza($KONF['klucz_pracownika']);
    touch("$kat/pracownik.serce");
    $id = isset($_GET['id']) ? $_GET['id'] : '';
    if (!poprawne_id($id) || !is_file("$kat/w_trakcie/$id.json")) {
        odpowiedz(404, array('error' => array('message' => 'nieznane zadanie')));
    }
    $dane = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($dane) || (!isset($dane['odpowiedz']) && !isset($dane['blad']))) {
        odpowiedz(400, array('error' => array('message' => 'oczekiwano odpowiedz albo blad')));
    }
    $wpis = isset($dane['blad']) ? array('blad' => (string) $dane['blad']) : array('odpowiedz' => $dane['odpowiedz']);
    file_put_contents("$kat/gotowe/$id.json.tmp", json_encode($wpis, JSON_UNESCAPED_UNICODE), LOCK_EX);
    rename("$kat/gotowe/$id.json.tmp", "$kat/gotowe/$id.json");
    @unlink("$kat/w_trakcie/$id.json");
    odpowiedz(200, array('ok' => true));
}

odpowiedz(404, array('error' => array('message' => 'nieznana akcja')));
