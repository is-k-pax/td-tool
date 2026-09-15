@echo off
setlocal
cd /d "%~dp0"

set PORT=18777
set RULE_NAME=TD Tool %PORT%

:: Comprobar si ya existe una regla de firewall de entrada para este
:: puerto -- esto NO requiere permisos de administrador, solo consultar.
powershell -NoProfile -Command "if (Get-NetFirewallRule -DisplayName '%RULE_NAME%' -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo [setup] no existe regla de firewall para el puerto %PORT%.
    echo [setup] pidiendo elevacion para crearla -- acepta el UAC si aparece...
    powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -Command \"New-NetFirewallRule -DisplayName ''%RULE_NAME%'' -Direction Inbound -Protocol TCP -LocalPort %PORT% -Action Allow -Profile Any\"'"
    powershell -NoProfile -Command "if (Get-NetFirewallRule -DisplayName '%RULE_NAME%' -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
    if errorlevel 1 (
        echo [setup] AVISO: no se pudo confirmar la regla de firewall -- si el UAC se cancelo, este PC solo respondera en localhost hasta crearla a mano.
    ) else (
        echo [setup] regla de firewall creada correctamente.
    )
) else (
    echo [setup] regla de firewall ya existe, saltando.
)

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

echo [run] arrancando standalone_mcp_server.py en el puerto %PORT%
python standalone_mcp_server.py --port %PORT%

pause
