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
import threading
import time

import cv2
from flask import Flask, render_template
from flask_socketio import SocketIO

from deteccion import DetectorPresencia
from transmision_mqtt import ClienteMQTT
from logica_decision import MotorDecision
from actuador import Actuador

# ---------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------
USAR_MQTT = True           # False para correr todo en un solo proceso sin red
PUERTO_ARDUINO = None      # p.ej. "/dev/ttyUSB0" o "COM3"; None = modo simulado
TIMEOUT_INICIAL_SEG = 15   # tiempo de espera antes de apagar (ajustable en vivo)

# CAMARA_INDEX define la fuente de video:
#   - 0, 1, 2...             -> cámara local (webcam del computador)
#   - "http://IP:8080/video" -> cámara del CELULAR vía la app "IP Webcam"
#                                (Android) o similar. Ver README, sección
#                                "Usar la cámara del celular".
CAMARA_INDEX = 1

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
    mqtt_client = ClienteMQTT(on_deteccion=_al_recibir_deteccion_via_mqtt)
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

@socketio.on("listar_puertos_arduino")
def al_listar_puertos_arduino():
    """Refresca en vivo la lista de puertos seriales (p.ej. al enchufar el Arduino)."""
    socketio.emit("puertos_arduino", {"puertos": Actuador.listar_puertos()})


@socketio.on("conectar_arduino")
def al_conectar_arduino(data):
    """
    data = {"puerto": "/dev/ttyUSB0"}  (o "COM3" en Windows)
    Conecta el Arduino EN VIVO, sin reiniciar app.py. En cuanto conecta,
    el LED de la protoboard se sincroniza al instante con el estado
    lógico actual (no espera al siguiente ciclo de detección).
    """
    global _ultimo_contexto
    puerto = data.get("puerto")
    if not puerto:
        socketio.emit("resultado_conexion_arduino", {
            "exito": False, "mensaje": "Selecciona un puerto antes de conectar"
        })
        return

    resultado = actuador.conectar(puerto)
    socketio.emit("resultado_conexion_arduino", resultado)

    with _hilo_lock:
        _ultimo_contexto["hardware_conectado"] = actuador.conectado_hardware
        _ultimo_contexto["puerto_conectado"] = actuador.puerto if actuador.conectado_hardware else None
        _ultimo_contexto["iluminacion_on"] = actuador.estado_actual

    socketio.emit("estado_sistema", _ultimo_contexto)


@socketio.on("desconectar_arduino")
def al_desconectar_arduino():
    """Desconecta el Arduino en vivo; el flujo de detección/decisión sigue
    corriendo normalmente y el sistema vuelve a modo simulado."""
    global _ultimo_contexto
    resultado = actuador.desconectar()
    socketio.emit("resultado_conexion_arduino", resultado)

    with _hilo_lock:
        _ultimo_contexto["hardware_conectado"] = False
        _ultimo_contexto["puerto_conectado"] = None

    socketio.emit("estado_sistema", _ultimo_contexto)


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

    socketio.emit("estado_sistema", _ultimo_contexto)


if __name__ == "__main__":
    hilo = threading.Thread(target=bucle_captura, daemon=True)
    hilo.start()
    socketio.run(app, host="0.0.0.0", port=5000)
