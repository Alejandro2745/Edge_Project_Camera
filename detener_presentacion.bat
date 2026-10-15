@echo off
rem Detiene todo lo que levanta iniciar_presentacion.bat (Edge, Fog en
rem vivo, Fog demo) y cierra sus ventanas de consola. Busca los procesos
rem por el comando que estan corriendo (run.py / fog_node.py), no por el
rem titulo de la ventana, porque Windows no siempre expone ese titulo.
rem Mosquitto NO se toca: sigue como servicio, listo para la proxima vez.

echo Cerrando Edge, Fog en vivo y Fog demo...

powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'run\.py' -or $_.CommandLine -match 'fog_node\.py' } | ForEach-Object { Write-Host ('  deteniendo PID ' + $_.ProcessId + ': ' + $_.CommandLine); try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}; try { Stop-Process -Id $_.ParentProcessId -Force -ErrorAction Stop } catch {} }"

echo.
echo Listo. Si alguna ventana se quedo abierta vacia, cierrala con la X.
echo.
pause
