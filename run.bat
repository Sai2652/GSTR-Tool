@echo off
REM GSTR-1 Generator - Web UI launcher (Windows)
REM Only installs packages that are actually missing.

cd /d "%~dp0"

echo Checking dependencies...
python -c "import flask, pandas, openpyxl, rapidfuzz, bcrypt, xlrd" 2>NUL
if errorlevel 1 (
    echo Some dependencies are missing. Installing only what's needed...
    python -c "import flask" 2>NUL || python -m pip install flask --disable-pip-version-check
    python -c "import pandas" 2>NUL || python -m pip install pandas --disable-pip-version-check
    python -c "import openpyxl" 2>NUL || python -m pip install openpyxl --disable-pip-version-check
    python -c "import rapidfuzz" 2>NUL || python -m pip install rapidfuzz --disable-pip-version-check
    python -c "import bcrypt" 2>NUL || python -m pip install bcrypt --disable-pip-version-check
    python -c "import xlrd" 2>NUL || python -m pip install xlrd --disable-pip-version-check

    REM Verify everything imports now
    python -c "import flask, pandas, openpyxl, rapidfuzz, bcrypt, xlrd" 2>NUL
    if errorlevel 1 (
        echo.
        echo ===============================================================
        echo Dependency install failed. Try this manually:
        echo     pip install flask pandas openpyxl rapidfuzz bcrypt xlrd
        echo ===============================================================
        pause
        exit /b 1
    )
)

echo All dependencies OK.
echo.
echo ===============================================================
echo   GSTR-1 Generator
echo   Open http://127.0.0.1:5050 in your browser
echo   Press Ctrl+C to stop
echo ===============================================================
echo.

cd web
python app.py
pause
