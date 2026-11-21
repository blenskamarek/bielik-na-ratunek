<#
Sprawdzenie zdalnego Bielika tak, jak zobaczy go sędzia (najlepiej uruchomić z INNEJ sieci).
  1. /zdrowie (bez klucza),
  2. zapytanie bez klucza -> oczekiwane 401,
  3. pełny przebieg run.py na skanie UZP przez podany adres -> ile pól przyjęto od modelu i w jakim czasie.
Użycie:
  powershell -ExecutionPolicy Bypass -File narzedzia\tunel\sprawdz_zdalny.ps1 -Adres https://bielik.twoja-domena.pl
#>
param(
    [Parameter(Mandatory = $true)][string]$Adres,
    [string]$KluczPlik = "",
    [string]$Wejscie = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
# tryb kolejki na hostingu (adres kończy się na kolejka.php) albo bramka/tunel (adres bazowy)
$kolejka = $Adres -match "kolejka\.php$"
if ($kolejka) {
    $baza = $Adres; $urlZdrowie = "$Adres`?a=zdrowie"; $urlZadanie = "$Adres`?a=zadanie"; $adresLLM = $Adres
    if (-not $KluczPlik) { $KluczPlik = Join-Path $root "llm.klucz.lokalny" }    # klucz klienta (u sędziego: llm.klucz)
} else {
    $baza = $Adres.TrimEnd("/") -replace "/v1$", ""
    $urlZdrowie = "$baza/zdrowie"; $urlZadanie = "$baza/v1/chat/completions"; $adresLLM = "$baza/v1"
    if (-not $KluczPlik) { $KluczPlik = Join-Path $root "narzedzia\bramka.klucz" }
}
if (-not $Wejscie) { $Wejscie = Join-Path $root "..\dane_uzp\hacknation-2026-main\szablon-aplikacja\input" }
$wynik = $true

Write-Host "1. Zdrowie: $urlZdrowie"
try { $z = Invoke-RestMethod $urlZdrowie -TimeoutSec 15; Write-Host "   $($z | ConvertTo-Json -Compress)"; if (-not $z.ok) { $wynik = $false } }
catch { Write-Host "   BŁĄD: $($_.Exception.Message)"; exit 1 }

Write-Host "2. Zapytanie bez klucza (oczekiwane 401)"
try {
    Invoke-RestMethod $urlZadanie -Method Post -ContentType "application/json" -Body '{"messages":[]}' -TimeoutSec 15 | Out-Null
    Write-Host "   BŁĄD: przeszło bez klucza!"; $wynik = $false
} catch {
    $kod = [int]$_.Exception.Response.StatusCode
    Write-Host "   HTTP $kod"; if ($kod -ne 401) { $wynik = $false }
}

Write-Host "3. Pełny przebieg przez $adresLLM"
$tmp = Join-Path $env:TEMP ("sprawdz_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$env:LLM_ADRES = $adresLLM
$env:LLM_TRYB = $(if ($kolejka) { "kolejka" } else { "api" })
$env:LLM_API_KEY = (Get-Content $KluczPlik -TotalCount 1).Trim()
$py = (Get-Command py -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = "python" }
# komunikaty programu na stderr nie są błędem (Windows PowerShell 5.1 traktuje je tak przy "Stop")
$ErrorActionPreference = "Continue"
$czas = Measure-Command { & $py (Join-Path $root "run.py") --wejscie $Wejscie --wyjscie "$tmp\out" --raport "$tmp\raport" 2>$null | Out-Null }
$ErrorActionPreference = "Stop"
Remove-Item Env:LLM_ADRES, Env:LLM_API_KEY, Env:LLM_TRYB
$przyjete = 0; $odrzucone = 0
Get-ChildItem "$tmp\raport" -Recurse -Filter *.json | ForEach-Object {
    $r = Get-Content $_.FullName -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($w in @($r.llm)) { if ($w) { if ($w.przyjete) { $przyjete++ } else { $odrzucone++ } } }
}
Write-Host ("   czas: {0:N1} s, pola od modelu: przyjęte {1}, odrzucone {2}" -f $czas.TotalSeconds, $przyjete, $odrzucone)
if ($przyjete -eq 0) { Write-Host "   BŁĄD: model nic nie uzupełnił (sprawdź klucz, adres i logi bramki)"; $wynik = $false }
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

if ($wynik) { Write-Host "WYNIK: OK" } else { Write-Host "WYNIK: SĄ PROBLEMY"; exit 1 }
