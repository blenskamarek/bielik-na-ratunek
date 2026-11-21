<#
Start usług na czas oceny (na naszym komputerze z GPU):
  1. sprawdza Ollamę i wariant modelu (tworzy go z Modelfile, jeśli brak),
  2. wczytuje model do pamięci na stałe (keep_alive = -1, bez zmiany ustawień Ollamy),
  3. uruchamia bramkę z kluczem (narzedzia/bramka_llm.py) - blokuje uśpienie systemu, dopóki działa,
  4. opcjonalnie uruchamia nazwany tunel Cloudflare,
  5. pokazuje stan /zdrowie.
Użycie:
  powershell -ExecutionPolicy Bypass -File narzedzia\tunel\start_ocena.ps1 -Tunel bielik
Logi: narzedzia\tunel\logi\
#>
param(
    [string]$Model = "bielik-11b-6k",
    [int]$Port = 8080,
    [string]$Tunel = "",         # nazwa tunelu cloudflared; puste = bez tunelu (test lokalny)
    [string]$Kolejka = ""        # adres kolejka.php na hostingu: zamiast bramki i tunelu startuje pracownik
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$logi = Join-Path $PSScriptRoot "logi"
New-Item -ItemType Directory -Force $logi | Out-Null

function Find-Exe([string]$nazwa, [string[]]$zapasowe) {
    $c = Get-Command $nazwa -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($z in $zapasowe) { if (Test-Path $z) { return $z } }
    return $null
}
$ollama = Find-Exe "ollama" @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")
$python = Find-Exe "py" @()
if (-not $python) { $python = Find-Exe "python" @() }

# 1. Ollama i model
try { $tags = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 }
catch { Write-Host "BŁĄD: Ollama nie odpowiada - uruchom aplikację Ollama i powtórz."; exit 1 }
if (-not ($tags.models | Where-Object { $_.name -like "$Model*" })) {
    Write-Host "Tworzę wariant modelu $Model ..."
    & $ollama create $Model -f (Join-Path $root "narzedzia\modelfile\$Model.Modelfile")
}

# 2. Model na stałe w pamięci (pierwsze zapytanie sędziego bez zimnego startu)
Write-Host "Wczytuję model do pamięci ..."
$cialo = @{ model = $Model; prompt = "ok"; stream = $false; keep_alive = -1; options = @{ num_predict = 1 } } | ConvertTo-Json
Invoke-RestMethod "http://127.0.0.1:11434/api/generate" -Method Post -Body $cialo -ContentType "application/json" -TimeoutSec 300 | Out-Null

# 3a. Tryb kolejki na hostingu: tylko pracownik (połączenia wychodzące), bez bramki i tunelu
if ($Kolejka) {
    if (-not (Test-Path (Join-Path $root "narzedzia\pracownik.klucz"))) {
        Write-Host "BŁĄD: brak klucza pracownika. Utwórz: py narzedzia\pracownik_kolejki.py --nowe-klucze"; exit 1
    }
    if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "pracownik_kolejki.py" }) {
        Write-Host "Pracownik już działa."
    } else {
        Start-Process $python -ArgumentList "narzedzia\pracownik_kolejki.py --adres $Kolejka --model $Model" `
            -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardError (Join-Path $logi "pracownik.log") `
            -RedirectStandardOutput (Join-Path $logi "pracownik.out")
    }
    for ($i = 0; $i -lt 15; $i++) {
        try { $z = Invoke-RestMethod "$Kolejka`?a=zdrowie" -TimeoutSec 10; if ($z.ok) { break } } catch { }
        Start-Sleep -Seconds 1
    }
    if ($z.ok) { Write-Host "OK: pracownik odpytuje kolejkę ($Kolejka). Logi: $logi" }
    else { Write-Host "BŁĄD: kolejka nie widzi pracownika - zobacz $logi\pracownik.log"; exit 1 }
    exit 0
}

# 3b. Bramka
if (-not (Test-Path (Join-Path $root "narzedzia\bramka.klucz"))) {
    Write-Host "BŁĄD: brak klucza. Utwórz: py narzedzia\bramka_llm.py --nowy-klucz"; exit 1
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Bramka już działa na porcie $Port."
} else {
    Start-Process $python -ArgumentList "narzedzia\bramka_llm.py --port $Port --model $Model" `
        -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardError (Join-Path $logi "bramka.log") `
        -RedirectStandardOutput (Join-Path $logi "bramka.out")
}

# 4. Tunel
if ($Tunel) {
    $cf = Find-Exe "cloudflared" @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe")
    if (-not $cf) { Write-Host "BŁĄD: brak cloudflared."; exit 1 }
    Start-Process $cf -ArgumentList "tunnel run $Tunel" -WindowStyle Hidden `
        -RedirectStandardError (Join-Path $logi "tunel.log") -RedirectStandardOutput (Join-Path $logi "tunel.out")
}

# 5. Stan
for ($i = 0; $i -lt 20; $i++) {
    try { $z = Invoke-RestMethod "http://127.0.0.1:$Port/zdrowie" -TimeoutSec 3; break } catch { Start-Sleep -Milliseconds 500 }
}
if ($z.ok) { Write-Host "OK: bramka działa (model $($z.model)). Logi: $logi" }
else { Write-Host "BŁĄD: bramka nie odpowiada - zobacz $logi\bramka.log"; exit 1 }
