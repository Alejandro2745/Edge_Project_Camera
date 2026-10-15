"""
simulacion_edge.py
==================
Ejecuta el app.py REAL del Edge (misma lógica de MQTT, decisión y actuación)
con una CÁMARA SIMULADA en vez de la webcam, para probar la integración
Edge -> MQTT -> Fog sin necesitar cámara ni personas frente a ella.

La actividad se simula con un bloque en movimiento (activa el canal de
"movimiento" del detector, igual que una persona caminando):
  cada 60 s:  0-10 s activo | 10-30 s quieto | 30-38 s activo | 38-60 s quieto

Uso:  python -m fog.simulacion_edge [duracion_seg] [timeout_seg]
      (ejecutar desde la raíz del repo; por defecto 75 s de simulación y
      timeout de 5 s para ver apagados rápido)
Variables: BROKER_HOST / BROKER_PORT (igual que edge/app.py).
"""
import os
import sys
import threading
import time

import cv2
import numpy as np

import edge.app as edge   # importa el Edge real (crea detector, motor, actuador y cliente MQTT)

DURACION = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 5
VENTANAS_ACTIVAS = [(0, 10), (30, 38)]   # dentro de un ciclo de 60 s


class CamaraSimulada:
    def __init__(self):
        self.t0 = time.time()
        self._abierta = True

    def isOpened(self):
        return self._abierta

    def release(self):
        self._abierta = False

    def read(self):
        t = (time.time() - self.t0) % 60
        frame = np.full((360, 480, 3), 40, dtype=np.uint8)
        cv2.putText(frame, "CAMARA SIMULADA (sin personas reales)", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
        if any(a <= t < b for a, b in VENTANAS_ACTIVAS):
            x = int(20 + (t * 80) % 380)
            cv2.rectangle(frame, (x, 120), (x + 60, 260), (0, 165, 255), -1)
        return True, frame


if __name__ == "__main__":
    edge.detector.abrir = lambda: True
    edge.detector.cap = CamaraSimulada()
    edge.motor.establecer_timeout(TIMEOUT)
    print(f"[edge-sim] broker={edge.BROKER_HOST}:{edge.BROKER_PORT} timeout={TIMEOUT}s duracion={DURACION}s")
    threading.Thread(target=edge.bucle_captura, daemon=True).start()
    t_ini = time.time()
    estado_prev = None
    while time.time() - t_ini < DURACION:
        time.sleep(0.5)
        est = edge.motor.iluminacion_on
        if est != estado_prev:
            print(f"[edge-sim] t={time.time()-t_ini:5.1f}s  luz={'ENCENDIDA' if est else 'apagada'}  "
                  f"mqtt_conectado={edge.mqtt_client.esta_conectado()}")
            estado_prev = est
    print("[edge-sim] fin de la simulacion", flush=True)
    os._exit(0)  # el hilo de captura es infinito; evita errores al cerrar el interprete
