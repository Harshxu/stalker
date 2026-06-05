@echo off
:: STALKER Auto-Start Script
:: Runs both the scheduler (7AM/8:30AM/etc tasks) and API server
:: This file is registered with Windows Task Scheduler to run at login

cd /d "C:\Users\ADMIN\Desktop\antigravity\Stalker"

echo [STALKER] Starting at %DATE% %TIME% >> logs\startup.log

:: Start the scheduler (runs all daily tasks: 7AM scan, 8:30AM email, 4PM EOD)
start "STALKER-Scheduler" /min python main.py --mode run

:: Wait 3 seconds, then start the API server (dashboard)
timeout /t 3 /nobreak >nul
start "STALKER-Server" /min python main.py --mode serve

echo [STALKER] Both processes started. >> logs\startup.log
