# Capa Fog

Nodo Fog que se **suma** al Edge (`edge/`) sin modificar su lógica de decisión
en tiempo real. Se suscribe a los mismos tópicos MQTT que ya publica el Edge
(`iot_edge/deteccion` e `iot_edge/actuacion`), agrega los datos por minuto,
los guarda en SQLite y calcula consumo/ahorro energético en un dashboard web
propio (puerto 5001) y por línea de comandos.

Es solo **observador**: nunca publica comandos. Si este nodo se apaga o
falla, el Edge sigue encendiendo/apagando la luz con normalidad.

## Instalar

Ya cubierto por el `requirements.txt` de la raíz del proyecto (el Fog
reutiliza `flask` y `paho-mqtt`, que ya instalaste para el Edge):

```bash
pip install -r requirements.txt
```

## Conectarlo al Edge (elige una opción)

**A) Sin tocar nada.** El `edge/transmision_mqtt.py` actual publica en el
broker público `test.mosquitto.org` por defecto, así que el Fog se conecta
ahí:
```bash
python fog/fog_node.py --broker test.mosquitto.org
```
Limitación: es un broker compartido y sin autenticación (cualquiera puede
leer o publicar en esos tópicos), y depende de que tengas internet estable
durante la sustentación.

**B) Broker local (recomendado para la sustentación).**
1. Instala Mosquitto (https://mosquitto.org/download/). El instalador de
   Windows ya deja un servicio (`Mosquitto Broker`, inicio automático)
   escuchando en `localhost:1883` con conexiones anónimas locales — no hace
   falta pasarle `fog\mosquitto.conf` a mano salvo que quieras personalizar
   algo. (En esta máquina ya quedó instalado y corriendo así.)
2. Antes de arrancar el Edge, define la variable de entorno `BROKER_HOST`
   para que apunte al broker local (no hace falta editar código —
   `edge/app.py` ya la lee):
   ```bash
   set BROKER_HOST=localhost
   python run.py
   ```
3. En otra terminal: `python fog/fog_node.py` → dashboard en
   `http://localhost:5001`

Si Mosquitto y el Fog corren en otro equipo (p. ej. una Raspberry Pi), usa su
IP en vez de `localhost` tanto en `BROKER_HOST` como en
`python fog/fog_node.py --broker <IP>`.

## Comandos

- `python fog/fog_node.py --reporte dia|semana|mes` — imprime el reporte y termina.
- `python fog/fog_node.py --demo` — dashboard con 7 días **simulados** (banner visible; no son mediciones).
- `python -m unittest test_fog_node -v` (ejecutado desde dentro de `fog/`) — pruebas unitarias, no necesitan broker.
- `python -m fog.simulacion_edge [duracion_seg] [timeout_seg]` (ejecutado desde la raíz del repo) — corre el Edge real con una cámara simulada, útil para probar la integración Edge → MQTT → Fog sin webcam ni personas.

## Modelo de energía (variables de entorno)

- `P_BOMBILLO_W=9.0` — el bombillo LED real que este sistema está diseñado
  para controlar en producción. **Importante:** como la integración con un
  bombillo inteligente/Alexa no se logró, lo que se demuestra en vivo es un
  LED/relé sobre protoboard; sean explícitos en la sustentación de que el
  circuito es un proxy y que el modelo de ahorro calcula lo que se ahorraría
  con el bombillo real.
- `P_ESPERA_W=0.4` — consumo propio **del circuito de actuación** (Arduino +
  módulo de relé) en reposo, no de la PC que corre la cámara/Flask durante el
  desarrollo. Si mides el consumo real de tu circuito y es distinto, ajusta
  este valor.
- `OLVIDO_MAX_H_DIA=4.0` — supuesto del proyecto: sin el sistema, la luz
  quedaría encendida innecesariamente ~4 h/día.
- Bombillo evitado (bruto) = 9 W × min(horas apagadas, 4 h/día) ≤ 36 Wh/día ≈ 1.08 kWh/mes.
- Ahorro neto = bruto − consumo propio del sistema (espera × tiempo monitoreado).
- Con los valores actuales el neto es positivo: 0.4 W las 24 h son solo
  9.6 Wh/día, muy por debajo del máximo evitado de 36 Wh/día.

## Qué se probó y qué no

- Compila y los 7 tests unitarios pasan en Windows (verificado en este equipo).
- `--demo --reporte semana` genera un reporte real con datos simulados (verificado en este equipo).
- **Prueba end-to-end con hardware real, en este equipo (Windows):**
  Mosquitto instalado como servicio de Windows (`Mosquitto Broker`, inicio
  automático, escuchando en `localhost:1883`, conexiones anónimas locales) +
  Edge real (`python -m fog.simulacion_edge`, cámara simulada pero actuador
  real sobre el Arduino conectado por USB en `COM4`, LED en el pin 8) + Fog
  (`fog_node.py`) escuchando ese broker. Primero dos corridas cortas de 25 s
  (mismo patrón en ambas: la luz se enciende en `t≈0.5s` y se apaga en
  `t≈14.5s`, 462 y 466 mensajes MQTT, 1 activación cada una), confirmado
  también **físicamente**: el LED del pin 8 encendió y apagó en el momento
  esperado. Luego una corrida de 150 s con 5 ciclos completos ON/OFF
  (2442 mensajes MQTT → 14 filas agregadas, 5 activaciones), capturada en vivo
  en el dashboard del Fog — ver [`evidencia_pruebas/`](evidencia_pruebas/).
- Los archivos en [`evidencia_pruebas/`](evidencia_pruebas/) son de esta
  última prueba (datos **reales**, de este equipo) y de `--demo` (datos
  **simulados**, claramente marcados):
  - `fog_dashboard_real_hoy.png` / `reporte_real_hoy.txt` — captura y reporte
    de la corrida de 150 s con el Arduino real (datos reales, de hoy).
  - `fog_dashboard_demo_7dias.png` / `reporte_demo_7dias_SIMULADO.txt` —
    dashboard y reporte con `--demo` (7 días de datos simulados, banner
    visible en el dashboard).
  - `pruebas_unitarias.txt` — salida de `python -m unittest test_fog_node -v`.

## Estructura de la carpeta

```
fog/
├── __init__.py
├── README.md              # este archivo
├── fog_node.py             # Implementación: MQTT + SQLite + dashboard + reportes
├── test_fog_node.py        # Pruebas unitarias (no requieren broker)
├── simulacion_edge.py      # Corre el Edge real con cámara simulada (sin webcam)
├── mosquitto.conf          # Config mínima para un broker Mosquitto local
└── evidencia_pruebas/      # Capturas y reportes de una prueba previa (datos simulados)
```
