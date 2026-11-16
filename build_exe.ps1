# Builds "Midi Tool\Midi Tool.exe" into .\release (needs: python -m pip install pyinstaller).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$work = Join-Path $env:TEMP "miditool-build"   # keep build scratch out of OneDrive

& $py -m PyInstaller `
    --noconfirm --clean --windowed `
    --name "Midi Tool" `
    --icon "$root\assets\drum-finder.ico" `
    --paths "$root\scripts" `
    --add-data "$root\assets;assets" `
    --add-data "$root\chrome-extension;chrome-extension" `
    --distpath "$root\release" `
    --workpath $work `
    --specpath $work `
    "$root\scripts\find_drum_midi.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built: $root\release\Midi Tool\Midi Tool.exe"
