@echo off
chcp 65001
cls
echo ================================
echo    Board Auction Monitor
echo ================================
echo.

echo [Step 1] Starting Flask Web Server...
start "Flask Server" cmd /k "cd /d E:\MyClaw\board-auction-monitor && python web_app.py"
timeout /t 3 /nobreak >nul

echo [Step 2] Starting Cloudflare Tunnel...
start "Cloudflare Tunnel" cmd /k "cd /d E:\MyClaw\board-auction-monitor && cloudflared.exe tunnel --url http://localhost:5000 --protocol http2"

echo.
echo ============================================
echo  Both services are starting in new windows
echo ============================================
echo.
echo Look for the URL in the Cloudflare window:
echo    https://xxxxxx.trycloudflare.com
echo.
pause
