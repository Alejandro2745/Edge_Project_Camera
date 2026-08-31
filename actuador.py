"""
ETAPA 5 - ACTUACION
====================
Cambia el estado del "interruptor virtual" y, si hay un Arduino real
conectado por USB, le envía el comando por puerto serial para que
accione el relé/LED físico. Si no hay hardware conectado, el sistema
sigue funcionando en modo 100% simulado (útil para probar sin componentes).

Protocolo serial (ver arduino/rele_control/rele_control.ino):
  Python -> Arduino: "ON\\n" o "OFF\\n"
  Arduino -> Python:  "ACK:ON\\n" / "ACK:OFF\\n"
"""

try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_DISPONIBLE = True
except ImportError:
    PYSERIAL_DISPONIBLE = False


class Actuador:
    def __init__(self, puerto: str = None, baudrate: int = 9600):
        self.puerto = puerto
        self.baudrate = baudrate
        self._ser = None
        self.conectado_hardware = False
        self.estado_actual = False  # False = apagado

        if PYSERIAL_DISPONIBLE and puerto:
            self._conectar(puerto)

    @staticmethod
    def listar_puertos():
        if not PYSERIAL_DISPONIBLE:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    def _conectar(self, puerto):
        try:
            self._ser = serial.Serial(puerto, self.baudrate, timeout=1)
            self.conectado_hardware = True
        except Exception:
            self._ser = None
            self.conectado_hardware = False

    def aplicar_estado(self, encender: bool):
        """
        Aplica el estado a la 'luminaria'. Siempre actualiza el estado
        simulado; adicionalmente escribe al Arduino real si está conectado.
        """
        self.estado_actual = encender

        if self.conectado_hardware and self._ser is not None:
            try:
                comando = b"ON\n" if encender else b"OFF\n"
                self._ser.write(comando)
            except Exception:
                self.conectado_hardware = False

        return {
            "iluminacion_on": self.estado_actual,
            "hardware_conectado": self.conectado_hardware,
        }

    def cerrar(self):
        if self._ser is not None:
            self._ser.close()
