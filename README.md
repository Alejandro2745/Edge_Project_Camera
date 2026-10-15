# Edge Computing — Consumo Inteligente de Iluminación

Implementación en Python del flujo descrito en las presentaciones **"El reto: consumir
energía cuando realmente se necesita"** y **"Flujo de Datos: de la Detección a la
Acción"**: un nodo edge que detecta presencia con cámara, transmite el dato por MQTT,
lo procesa, aplica reglas de decisión y actúa sobre una luminaria — con un dashboard
web que integra cámara, circuito (Arduino + protoboard) y bombillo simulado en un
mismo lugar.

## 1. Arquitectura (mapea 1:1 con las 5 etapas de las diapositivas)

Todo el código de la capa **Edge** vive en el paquete [`edge/`](edge/):

| Etapa | Módulo | Qué hace |
|---|---|---|
| 1. Captura | [`edge/deteccion.py`](edge/deteccion.py) | Lee la cámara, detecta persona (HOG+SVM de OpenCV) y movimiento (diferencia de frames) |
| 2. Transmisión | [`edge/transmision_mqtt.py`](edge/transmision_mqtt.py) | Publica `{persona, movimiento, hora}` por MQTT (protocolo real usado en IoT) |
| 3. Procesamiento | [`edge/logica_decision.py`](edge/logica_decision.py) | Interpreta el payload y construye el contexto (¿hay actividad?, ¿cuánto tiempo sin detección?) |
| 4. Decisión | [`edge/logica_decision.py`](edge/logica_decision.py) | Regla: si hay persona → encender; si no hay detección por X segundos → apagar |
| 5. Actuación | [`edge/actuador.py`](edge/actuador.py) | Cambia el "interruptor virtual"; si hay un Arduino conectado por USB, le envía `ON`/`OFF` por serial |
| Orquestación + Dashboard | [`edge/app.py`](edge/app.py), [`edge/templates/index.html`](edge/templates/index.html) | Une todo con Flask-SocketIO y sirve el panel visual en tiempo real |

La carpeta [`fog/`](fog/) está reservada para la próxima capa de **Fog
Computing** que se agregará al proyecto (ver [`fog/README.md`](fog/README.md)).

## 2. Instalación y ejecución

```bash
python -m venv venv && source venv/bin/activate   # opcional pero recomendado
pip install -r requirements.txt
python run.py
```

Abre `http://localhost:5000`. Verás tres paneles en vivo (cámara, circuito, bombillo)
más el panel de control (modo automático/manual, ajuste del tiempo de espera, y el
registro cronológico de las 5 etapas).

Por defecto el sistema publica y consume MQTT contra el broker público de pruebas
`test.mosquitto.org`, así que funciona sin instalar un broker propio. Para producción,
cambia `broker_host` en `edge/transmision_mqtt.py` por tu propio Mosquitto/HiveMQ, o pon
`USAR_MQTT = False` en `edge/app.py` para que todo corra en un solo proceso sin red.

## 3. Usar la cámara del celular (en vez de la webcam del computador)

Tus diapositivas de "Flujo de Datos" describen que **el celular** es quien captura
la imagen. Puedes lograr esto sin escribir código: instalando una app en el celular
que expone su cámara como un stream de video en la red WiFi, y apuntando el programa
a esa dirección.

### Paso a paso (Android — app "IP Webcam")

1. Instala la app **"IP Webcam"** (de Pavel Khlebovich) desde Google Play.
2. Conecta el celular a la **misma red WiFi** que el computador donde corre `app.py`
   (esto es obligatorio: deben estar en la misma red local).
3. Abre la app, baja hasta el final y toca **"Start server"**.
4. La app mostrará una dirección como `http://192.168.1.15:8080`. Anótala.
5. En `edge/app.py`, cambia la línea:
   ```python
   CAMARA_INDEX = 0
   ```
   por:
   ```python
   CAMARA_INDEX = "http://192.168.1.15:8080/video"
   ```
   (usa la IP que te mostró tu propia app — no copies el ejemplo literal).
6. Guarda y ejecuta `python run.py` como siempre. El dashboard ahora mostrará la
   imagen del celular en el panel de cámara.

### Paso a paso (iPhone)

iOS no tiene un equivalente exacto a "IP Webcam", pero apps como **"DroidCam"** o
**"Iriun Webcam"** (disponibles también para iPhone) cumplen la misma función: exponen
la cámara del teléfono como un stream accesible por WiFi. El procedimiento es el mismo:
instalar la app, anotar la URL/IP que muestra, y pegarla en `CAMARA_INDEX`.

### Notas importantes

- **Ambos dispositivos deben estar en la misma red WiFi.** Si el computador está por
  cable y el celular por WiFi, deben ser la misma red/router (revisa que no estén en
  redes de invitados separadas).
- Si el firewall de Windows pregunta si permite la conexión a "IP Webcam" o a Python,
  acepta — si no, el stream no llegará.
- El código ya reintenta reconectar automáticamente cada 3 segundos si el WiFi se
  corta momentáneamente, así que no es necesario reiniciar el programa ante un corte
  breve de señal.
- Puedes verificar primero, sin este proyecto, que el stream funciona abriendo
  `http://IP:8080/video` directamente en el navegador del computador: deberías ver el
  video en vivo del celular.

## 4. Sobre el hardware (Arduino + protoboard)

El programa **no requiere hardware físico** para funcionar: si no defines
`PUERTO_ARDUINO` en `edge/app.py`, el sistema queda en modo 100% simulado y el panel
"Arduino + Protoboard" y el bombillo reaccionan igual, solo que en software.

Si sí tienes un Arduino Uno/Nano/ESP32 con un relé o LED en una protoboard:

1. Carga `arduino/rele_control/rele_control.ino` con el IDE de Arduino.
2. Conecta el pin 8 al relé (o a un LED con resistencia de 220Ω si quieres simular
   directamente el bombillo).
3. En `edge/app.py`, define `PUERTO_ARDUINO = "/dev/ttyUSB0"` (Linux/Mac) o `"COM3"`
   (Windows). Puedes listar los puertos disponibles con `Actuador.listar_puertos()`.

## 5. ¿Y si no tienes ningún componente físico? — simuladores externos

Para "ver" el Arduino y la protoboard de forma más realista que el diagrama SVG
del dashboard, puedes combinar este proyecto con un simulador de circuitos:

- **Wokwi (wokwi.com)** — el más recomendable. Simula Arduino/ESP32 + protoboard +
  LED/relé en el navegador, corriendo el *mismo* `rele_control.ino`. Con
  [`wokwi-cli`](https://docs.wokwi.com/wokwi-ci/cli-installation) puedes correr esa
  simulación en modo headless y exponerla como un puerto serial virtual, de forma que
  `actuador.py` le hable exactamente igual que a un Arduino real (mismo protocolo
  `ON\n`/`OFF\n`). Así obtienes una simulación fiel del circuito sin comprarlo, y
  puedes embeber el simulador de Wokwi en un `<iframe>` dentro de `index.html` para
  verlo junto a los otros paneles.
- **Tinkercad Circuits (tinkercad.com)** — más sencillo para armar visualmente el
  circuito (arduino + protoboard + LED) y ver el código correr, pero no expone una
  forma oficial de conectarlo a un programa externo por serial; sirve sobre todo como
  vista de referencia/educativa en una pestaña aparte.
- **Diagrama SVG incluido** — el panel central del dashboard ya dibuja Arduino +
  protoboard + LED y reacciona en tiempo real al estado del sistema; es la opción
  "cero configuración" si solo necesitas la representación visual, no una simulación
  eléctrica real.

## 6. Personalización rápida

- Cambiar el tiempo de espera por defecto: `TIMEOUT_INICIAL_SEG` en `edge/app.py`
  (también ajustable en vivo desde el dashboard).
- Cambiar la cámara usada: `CAMARA_INDEX` en `edge/app.py`.
- El botón **Forzar ON/OFF** en el dashboard permite "simular o controlar" el
  funcionamiento manualmente, como pediste, sin tocar código.

## 7. Estructura de archivos

```
Edge_Project_Camera/
├── run.py                       # Punto de entrada: python run.py
├── requirements.txt
├── edge/                        # Capa EDGE (lo que existe hoy)
│   ├── app.py                   # Orquestador + servidor web (Flask-SocketIO)
│   ├── deteccion.py             # Etapa 1: cámara + detección
│   ├── transmision_mqtt.py      # Etapa 2: MQTT
│   ├── logica_decision.py       # Etapas 3-4: procesamiento y reglas
│   ├── actuador.py              # Etapa 5: actuación (serial o simulada)
│   └── templates/index.html     # Dashboard integrado
├── fog/                         # Capa FOG (reservada para lo que pida el profesor)
│   └── README.md                # Qué podría vivir aquí y cómo encajará con edge/
├── arduino/rele_control/rele_control.ino   # Firmware para Arduino real o Wokwi
├── tools/                       # Scripts sueltos / utilidades de desarrollo
│   ├── cam.py                   # Lista los índices de cámara disponibles
│   └── voz.py                   # Reservado para una futura función de voz
└── docs/
    └── Manual_Ejecucion_Proyecto_IoT.docx
```

A medida que agregues la capa fog (u otras), sigue la misma convención: un
paquete Python con su propio `__init__.py` y módulos separados por
responsabilidad, igual que `edge/`.
