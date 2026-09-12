@echo off
setlocal
cd /d "%~dp0"

if not exist venv (
    echo [setup] creando venv...
    python -m venv venv
)

call venv\Scripts\activate.bat

python -c "import aiohttp" 2>nul
if errorlevel 1 (
    echo [setup] instalando aiohttp...
    pip install aiohttp
)

echo [run] arrancando standalone_mcp_server.py en el puerto 18777
python standalone_mcp_server.py --port 18777

pause
