"""
app.py
======
Orquesta las 5 etapas del flujo de datos (Captura -> Transmision ->
Procesamiento -> Decision -> Actuacion) y expone un dashboard web en
tiempo real que integra en un solo lugar:

  1. El video de la cámara (con el recuadro de detección de persona)
  2. Una representación esquemática de Arduino + protoboard con el LED
     que se enciende/apaga en vivo
  3. Una simulación visual del bombillo (encendido/apagado + brillo)
  4. Un panel de control para modo automático/manual y ajuste del
     tiempo de espera (X minutos)

Ejecutar:
    pip install -r requirements.txt
    python app.py
    abrir http://localhost:5000 en el navegador
"""

import base64
import os
import threading
import time

import cv2
from flask import Flask, render_template
from flask_socketio import SocketIO

from edge.deteccion import DetectorPresencia
from edge.transmision_mqtt import ClienteMQTT
from edge.logica_decision import MotorDecision
from edge.actuador import Actuador

# ---------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------
USAR_MQTT = True           # False para correr todo en un solo proceso sin red
# Puerto del Arduino: variable de entorno PUERTO_ARDUINO (p.ej. "COM3",
# "/dev/ttyACM0" o "/dev/ttyUSB0"). Vacío o "none" = modo simulado.
# Sin variable se mantiene COM4, el puerto del equipo de la sustentación.
_puerto_env = os.environ.get("PUERTO_ARDUINO", "COM4").strip()
PUERTO_ARDUINO = None if _puerto_env.lower() in ("", "none") else _puerto_env
TIMEOUT_INICIAL_SEG = int(os.environ.get("TIMEOUT_SEG", "15"))  # espera antes de apagar (ajustable en vivo)

# Broker MQTT: por defecto el público de pruebas. Para apuntar a un Mosquitto
# local (recomendado para probar la capa Fog, ver fog/README.md), define la
# variable de entorno BROKER_HOST=localhost antes de ejecutar, sin tocar código.
BROKER_HOST = os.environ.get("BROKER_HOST", "test.mosquitto.org")
BROKER_PORT = int(os.environ.get("BROKER_PORT", "1883"))

# CAMARA_INDEX define la fuente de video:
#   - 0, 1, 2...             -> cámara local (webcam del computador)
#   - "http://IP:8080/video" -> cámara del CELULAR vía la app "IP Webcam"
#                                (Android) o similar. Ver README, sección
#                                "Usar la cámara del celular".
# Se puede definir con la variable de entorno CAMARA (p.ej. CAMARA=0 o
# CAMARA=http://192.168.1.15:8080/video). Sin variable se usa 1.
_camara_env = os.environ.get("CAMARA", "1").strip()
CAMARA_INDEX = int(_camara_env) if _camara_env.isdigit() else _camara_env

app = Flask(__name__)
app.config["SECRET_KEY"] = "iot-edge-demo"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

detector = DetectorPresencia(camera_index=CAMARA_INDEX)
motor = MotorDecision(timeout_segundos=TIMEOUT_INICIAL_SEG)
actuador = Actuador(puerto=PUERTO_ARDUINO)

_hilo_lock = threading.Lock()
_ultimo_contexto = {}


def _al_recibir_deteccion_via_mqtt(payload: dict):
    """Callback ejecutado cuando llega un mensaje MQTT con el dato crudo.
    Aquí se ejecutan las Etapas 3, 4 y 5."""
    global _ultimo_contexto
    with _hilo_lock:
        contexto = motor.procesar(payload)
        resultado_actuacion = actuador.aplicar_estado(contexto["iluminacion_on"])
        contexto.update(resultado_actuacion)
        _ultimo_contexto = contexto

    if USAR_MQTT and mqtt_client is not None:
        mqtt_client.publicar_actuacion(contexto["iluminacion_on"], contexto["motivo"])

    socketio.emit("estado_sistema", contexto)


mqtt_client = None
if USAR_MQTT:
    mqtt_client = ClienteMQTT(broker_host=BROKER_HOST, broker_port=BROKER_PORT,
                               on_deteccion=_al_recibir_deteccion_via_mqtt)
    mqtt_client.conectar()


def bucle_captura():
    """Hilo en segundo plano: Etapas 1 y 2 (Captura + Transmision)."""
    if not detector.abrir():
        socketio.emit("error_sistema", {
            "mensaje": "No se pudo abrir la cámara. Si usas la cámara del "
                       "celular, verifica que el celular y el computador "
                       "estén en la misma red WiFi y que la URL en "
                       "CAMARA_INDEX sea correcta."
        })
        return

    while True:
        frame, estado_crudo = detector.leer_estado()
        if frame is None:
            time.sleep(0.5)
            continue

        # ETAPA 2 - Transmision
        if USAR_MQTT and mqtt_client is not None and mqtt_client.esta_conectado():
            mqtt_client.publicar_deteccion(estado_crudo)
        else:
            # Sin MQTT (o broker no disponible): procesar directo en el mismo proceso
            _al_recibir_deteccion_via_mqtt(estado_crudo)

        # Enviar el frame de video al dashboard
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            b64 = base64.b64encode(buffer).decode("utf-8")
            socketio.emit("video_frame", {"imagen": b64})

        time.sleep(0.08)  # ~12 fps, suficiente para el demo


@app.route("/")
def index():
    return render_template(
        "index.html",
        timeout_inicial=TIMEOUT_INICIAL_SEG,
        puertos_disponibles=Actuador.listar_puertos(),
        camaras_disponibles=DetectorPresencia.listar_camaras(),
        camara_actual=CAMARA_INDEX,
    )


@socketio.on("connect")
def al_conectar():
    socketio.emit("estado_sistema", _ultimo_contexto)


@socketio.on("cambiar_timeout")
def al_cambiar_timeout(data):
    segundos = int(data.get("segundos", TIMEOUT_INICIAL_SEG))
    motor.establecer_timeout(segundos)

@socketio.on("cambiar_camara")
def al_cambiar_camara(data):
    """
    data = {"camara": 0}                         -> cámara local por índice
    data = {"camara": "http://192.168.1.5:8080/video"}  -> cámara IP/celular
    """
    nuevo_valor = data.get("camara")
    # Si viene como string numérico ("0", "1"), lo convertimos a int
    if isinstance(nuevo_valor, str) and nuevo_valor.isdigit():
        nuevo_valor = int(nuevo_valor)

    with _hilo_lock:
        exito = detector.cambiar_camara(nuevo_valor)

    socketio.emit("resultado_cambio_camara", {
        "exito": exito,
        "camara": nuevo_valor,
        "mensaje": "Cámara cambiada correctamente" if exito
                   else "No se pudo abrir esa cámara; se mantiene la anterior"
    })

@socketio.on("control_manual")
def al_control_manual(data):
    """
    data = {"modo": "manual", "encender": true/false}  o
    data = {"modo": "automatico"}
    """
    global _ultimo_contexto
    if data.get("modo") == "manual":
        with _hilo_lock:
            motor.forzar_manual(bool(data.get("encender", False)))
            resultado = actuador.aplicar_estado(motor.iluminacion_on)
            _ultimo_contexto.update(resultado)
            _ultimo_contexto["modo_manual"] = True
            _ultimo_contexto["iluminacion_on"] = motor.iluminacion_on
            _ultimo_contexto["motivo"] = "Control manual desde el dashboard"
    else:
        motor.volver_a_automatico()
        with _hilo_lock:
            _ultimo_contexto["modo_manual"] = False
            _ultimo_contexto["motivo"] = "Modo automático restablecido desde el dashboard"

    # Publicar también los cambios manuales, igual que los automáticos,
    # para que la capa Fog (que solo escucha MQTT) los registre.
    if USAR_MQTT and mqtt_client is not None:
        mqtt_client.publicar_actuacion(_ultimo_contexto.get("iluminacion_on", False),
                                        _ultimo_contexto.get("motivo", ""))

    socketio.emit("estado_sistema", _ultimo_contexto)


def main():
    hilo = threading.Thread(target=bucle_captura, daemon=True)
    hilo.start()
    # allow_unsafe_werkzeug: Flask-SocketIO se niega a usar su servidor de
    # desarrollo cuando no hay una terminal interactiva (como en Docker).
    # Para este sistema en red local es suficiente.
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
