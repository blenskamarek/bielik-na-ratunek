@echo off
rem Uruchomienie: run.bat [wejscie] [wyjscie]   (domyslnie: input output)
rem Kod wyjscia = kod zwrocony przez run.py (0 = sukces).
setlocal
cd /d "%~dp0"
set "IN=%~1"
if "%IN%"=="" set "IN=input"
set "OUT=%~2"
if "%OUT%"=="" set "OUT=output"

rem Wybor interpretera: najpierw "py" (Windows launcher), potem "python".
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE (where python >nul 2>nul && set "PYEXE=python")
if not defined PYEXE (
  echo Nie znaleziono Pythona 3.13+. Zainstaluj go i uruchom: pip install -r requirements.txt
  exit /b 9009
)

%PYEXE% run.py --wejscie "%IN%" --wyjscie "%OUT%"
exit /b %errorlevel%
