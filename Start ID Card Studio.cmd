@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -c "import pypdfium2, reportlab, openpyxl, xlsxwriter; from PIL import features; assert features.check('raqm')" >nul 2>&1
  if not errorlevel 1 goto bundled
)
python -m venv .venv
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" -c "import pypdfium2, reportlab, openpyxl, xlsxwriter; from PIL import features; assert features.check('raqm')" >nul 2>&1
if not errorlevel 1 goto launch
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:launch
".venv\Scripts\python.exe" app.py --open
goto done
:bundled
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py --open
:done
if not errorlevel 1 exit /b 0
:failed
echo The app could not start. Check the message above.
pause
