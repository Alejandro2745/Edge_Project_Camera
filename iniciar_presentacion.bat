@echo off
rem Levanta todo lo necesario para la sustentacion:
rem   - Edge (camara + Arduino + dashboard)         -> localhost:5000
rem   - Fog EN VIVO (se llena mientras haces la demo) -> localhost:5001
rem   - Fog DEMO (7 dias de datos simulados)         -> localhost:5002
rem Mosquitto no hace falta arrancarlo: ya corre como servicio de Windows.

cd /d "%~dp0"

start "Edge - Dashboard" cmd /k "set BROKER_HOST=localhost && python run.py"
timeout /t 3 >nul

start "Fog - En vivo" cmd /k "cd /d "%~dp0fog" && python fog_node.py"
timeout /t 2 >nul

start "Fog - Demo historico" cmd /k "cd /d "%~dp0fog" && python fog_node.py --demo --web-port 5002"
timeout /t 3 >nul

start http://localhost:5000
start http://localhost:5001
start http://localhost:5002

echo.
echo Todo iniciado. Quedan 3 ventanas de consola abiertas (una por cada servicio).
echo Para terminar la sustentacion, cierra esas 3 ventanas.
echo.
pause
