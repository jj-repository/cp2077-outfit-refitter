@echo off
rem Builds the one-folder Windows version in dist\OutfitRefitter\ (players then need no Python).
rem Run from this folder with Python 3.11+ installed. Uses a local .venv so nothing else is changed.
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install --upgrade numpy scipy pyinstaller || exit /b 1
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onedir --windowed --name OutfitRefitter ^
  --add-data "bodies.json;." --add-data "README.md;." --add-data "LICENSE;." outfit_refitter.py || exit /b 1
echo.
echo Done: dist\OutfitRefitter\OutfitRefitter.exe
