"""
ETAPA 2 - TRANSMISION
======================
Publica y suscribe el dato de detección vía MQTT, tal como describe
la diapositiva "Transmisión — Envío del Dato" (protocolo MQTT sobre Wi-Fi).

Por defecto usa un broker público de pruebas (test.mosquitto.org) para
que el proyecto funcione sin instalar nada adicional. Para un despliegue
real, cambia BROKER_HOST por tu propio broker (Mosquitto local, HiveMQ, etc).
"""

import json
import threading
import paho.mqtt.client as mqtt

TOPIC_DETECCION = "iot_edge/deteccion"
TOPIC_ACTUACION = "iot_edge/actuacion"


class ClienteMQTT:
    def __init__(self, broker_host="test.mosquitto.org", broker_port=1883,
                 client_id_suffix="edge1", on_deteccion=None):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.on_deteccion_callback = on_deteccion
        self._conectado = False

        self.client = mqtt.Client(client_id=f"iot_edge_{client_id_suffix}",
                                   clean_session=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        self._conectado = (rc == 0)
        if self._conectado:
            client.subscribe(TOPIC_DETECCION)

    def _on_disconnect(self, client, userdata, rc):
        self._conectado = False

    def _on_message(self, client, userdata, msg):
        if msg.topic == TOPIC_DETECCION and self.on_deteccion_callback:
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                self.on_deteccion_callback(payload)
            except (ValueError, UnicodeDecodeError):
                pass

    def conectar(self, timeout=5):
        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=30)
            self.client.loop_start()
            return True
        except Exception:
            return False

    def esta_conectado(self):
        return self._conectado

    def publicar_deteccion(self, estado: dict):
        """Publica el dato crudo {persona, movimiento, hora}."""
        payload = json.dumps(estado)
        self.client.publish(TOPIC_DETECCION, payload, qos=0)

    def publicar_actuacion(self, iluminacion: bool, motivo: str):
        payload = json.dumps({"iluminacion": iluminacion, "motivo": motivo})
        self.client.publish(TOPIC_ACTUACION, payload, qos=0)

    def desconectar(self):
        self.client.loop_stop()
        self.client.disconnect()
