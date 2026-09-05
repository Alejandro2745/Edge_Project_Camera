"""
ETAPA 5 - ACTUACION
====================
Cambia el estado del "interruptor virtual" y, si hay un Arduino real
(con protoboard + LED/relé) conectado por USB, le envía el comando por
puerto serial para que accione el LED físico. Si no hay hardware
conectado, el sistema sigue funcionando en modo 100% simulado (útil
para probar sin componentes).

A diferencia de la versión inicial, la conexión al Arduino ya NO se fija
solo al arrancar el proceso: se puede conectar, cambiar de puerto o
desconectar EN VIVO desde el dashboard (ver `conectar()` / `desconectar()`
y los eventos de socket `conectar_arduino` / `desconectar_arduino` en
`app.py`), sin reiniciar `app.py` ni perder el flujo de detección.

Protocolo serial (ver arduino/rele_control/rele_control.ino):
  Python -> Arduino: "ON\\n" / "OFF\\n" / "STATUS\\n"
  Arduino -> Python:  "ACK:ON\\n" / "ACK:OFF\\n"
"""

import threading
import time

try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_DISPONIBLE = True
except ImportError:
    PYSERIAL_DISPONIBLE = False


class Actuador:
    def __init__(self, puerto: str = None, baudrate: int = 9600):
        self.puerto = None
        self.baudrate = baudrate
        self._ser = None
        self.conectado_hardware = False
        self.estado_actual = False  # False = apagado
        self._lock = threading.Lock()  # protege el puerto serial entre hilos (captura + eventos del dashboard)

        if PYSERIAL_DISPONIBLE and puerto:
            self.conectar(puerto)

    @staticmethod
    def listar_puertos():
        """Lista los puertos seriales disponibles ahora mismo (para refrescar
        en vivo el selector del dashboard cuando se enchufa el Arduino)."""
        if not PYSERIAL_DISPONIBLE:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    def conectar(self, puerto: str) -> dict:
        """
        Conecta (o reconecta) EN VIVO al Arduino en el puerto indicado,
        sin reiniciar el proceso. Si ya había una conexión abierta a otro
        puerto, la cierra primero. En cuanto conecta, sincroniza de inmediato
        el LED físico con el estado lógico actual del sistema (para que no
        haya que esperar a la siguiente detección para verlo reaccionar).
        """
        if not PYSERIAL_DISPONIBLE:
            return {"exito": False, "mensaje": "pyserial no está instalado en el entorno"}

        with self._lock:
            self._cerrar_conexion_actual()
            try:
                self._ser = serial.Serial(puerto, self.baudrate, timeout=1)
                # Al abrir el puerto, la placa Arduino se reinicia (auto-reset por DTR);
                # hay que darle un instante para que el firmware termine de arrancar
                # antes de mandarle el primer comando.
                time.sleep(2)
                self.puerto = puerto
                self.conectado_hardware = True
                self._escribir_comando(self.estado_actual)
            except Exception as e:
                self._ser = None
                self.puerto = None
                self.conectado_hardware = False
                return {"exito": False, "mensaje": f"No se pudo conectar a {puerto}: {e}"}

        return {"exito": True, "mensaje": f"Arduino conectado en {puerto}", "puerto": puerto}

    def desconectar(self) -> dict:
        """Cierra la conexión serial en vivo; el sistema vuelve a modo simulado
        sin perder el flujo de detección/decisión, que sigue corriendo."""
        with self._lock:
            self._cerrar_conexion_actual()
        return {"exito": True, "mensaje": "Arduino desconectado; sistema en modo simulado"}

    def _cerrar_conexion_actual(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self.puerto = None
        self.conectado_hardware = False

    def _escribir_comando(self, encender: bool):
        if self.conectado_hardware and self._ser is not None:
            try:
                comando = b"ON\n" if encender else b"OFF\n"
                self._ser.write(comando)
            except Exception:
                # El Arduino se desconectó físicamente (se sacó el USB, etc.):
                # el sistema debe seguir funcionando en modo simulado.
                self.conectado_hardware = False

    def aplicar_estado(self, encender: bool):
        """
        Aplica el estado a la 'luminaria'. Siempre actualiza el estado
        simulado; adicionalmente escribe al Arduino real (LED en la
        protoboard) si está conectado en ese momento.
        """
        self.estado_actual = encender

        with self._lock:
            self._escribir_comando(encender)

        return {
            "iluminacion_on": self.estado_actual,
            "hardware_conectado": self.conectado_hardware,
            "puerto_conectado": self.puerto if self.conectado_hardware else None,
        }

    def cerrar(self):
        self.desconectar()
