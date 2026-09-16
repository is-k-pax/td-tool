@echo off
setlocal

:: --- Auto-elevacion a administrador -----------------------------------
:: El standalone necesita permisos de admin para tareas como parar o
:: arrancar servicios/apps (p.ej. RustDesk). En vez de tener que hacer
:: click derecho -> "Ejecutar como administrador" cada vez, el propio
:: script se relanza elevado si detecta que no lo esta -- pide UAC UNA
:: vez, en una ventana nueva, y la original se cierra. El proceso Python
:: que queda corriendo dentro de esa instancia elevada hereda ese token
:: de administrador, asi que todo lo que el standalone ejecute despues
:: (run_command, etc.) ya corre elevado mientras el servidor siga vivo.
::
:: Nota: si este .bat se lanza en el futuro desde una Tarea Programada
:: con "Ejecutar con los privilegios mas altos" marcado, este chequeo
:: no hace nada (ya somos admin) -- el mismo script sirve para doble
:: clic manual y para autostart, sin mantener dos versiones.
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [setup] se necesitan permisos de administrador -- pidiendo elevacion...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

set PORT=18777
set RULE_NAME=TD Tool %PORT%

:: Ya estamos elevados (ver arriba), asi que basta con comprobar/crear
:: la regla directamente, sin sub-relanzar powershell para pedir permiso.
powershell -NoProfile -Command "if (Get-NetFirewallRule -DisplayName '%RULE_NAME%' -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo [setup] creando regla de firewall para el puerto %PORT%...
    powershell -NoProfile -Command "New-NetFirewallRule -DisplayName '%RULE_NAME%' -Direction Inbound -Protocol TCP -LocalPort %PORT% -Action Allow -Profile Any" >nul
    echo [setup] regla de firewall creada.
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

echo [run] arrancando standalone_mcp_server.py en el puerto %PORT% (elevado)
python standalone_mcp_server.py --port %PORT%

pause
