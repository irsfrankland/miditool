@echo off
rem Starts the drum MIDI finder with whichever Python 3 is installed.
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 scripts\find_drum_midi.py & exit /b)
where pythonw >nul 2>nul && (start "" pythonw scripts\find_drum_midi.py & exit /b)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" (start "" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" scripts\find_drum_midi.py & exit /b)
echo Python 3 was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
