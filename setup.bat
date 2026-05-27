@echo off
title STALKER — Setup
color 0A
echo.
echo  ============================================
echo   STALKER — Indian Stock Market Analyzer
echo   One-Click Setup
echo  ============================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found!
    echo  Please install Python 3.9+ from https://python.org
    pause
    exit /b 1
)

echo  [1/4] Python found OK
echo.

:: Install packages
echo  [2/4] Installing required packages...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo  [ERROR] Package installation failed
    pause
    exit /b 1
)
echo  Packages installed OK
echo.

:: Create .env from example
if not exist .env (
    copy .env.example .env >nul
    echo  [3/4] Created .env file — please fill in your email settings
) else (
    echo  [3/4] .env already exists
)
echo.

:: Create data and reports folders
mkdir data 2>nul
mkdir reports 2>nul
mkdir logs 2>nul
echo  [4/4] Folders created
echo.

echo  ============================================
echo   Setup Complete!
echo  ============================================
echo.
echo  Next steps:
echo.
echo  1. Open .env and add your Gmail details
echo     (for EOD email reports)
echo.
echo  2. Run a test scan:
echo     python main.py --mode test
echo.
echo  3. Run a full scan right now:
echo     python main.py --mode scan
echo.
echo  4. Open the dashboard:
echo     python main.py --mode serve
echo.
echo  5. Run automatically every day:
echo     python main.py --mode run
echo.
pause
