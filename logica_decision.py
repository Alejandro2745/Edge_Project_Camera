"""
ETAPA 3 - PROCESAMIENTO  /  ETAPA 4 - DECISION
================================================
Convierte el dato crudo {persona, movimiento, hora} en un estado con
contexto, y aplica la regla de negocio:

  - Si hay persona            -> mantener/encender iluminación
  - Si NO hay persona por X minutos -> apagar iluminación

Corresponde 1:1 con las diapositivas "Procesamiento — Interpretación"
y "Decisión — Aplicación de Reglas".
"""

import time


class MotorDecision:
    def __init__(self, timeout_segundos: int = 300):
        """
        timeout_segundos: minutos sin detección (X) antes de apagar,
        expresados en segundos para mayor precisión en pruebas.
        """
        self.timeout_segundos = timeout_segundos
        self.iluminacion_on = False
        self._ultima_deteccion_ts = None
        self.modo_manual = False  # si True, ignora el sensor

    def establecer_timeout(self, segundos: int):
        self.timeout_segundos = max(1, int(segundos))

    def forzar_manual(self, encender: bool):
        """Permite controlar la luz manualmente desde el dashboard."""
        self.modo_manual = True
        self.iluminacion_on = encender

    def volver_a_automatico(self):
        self.modo_manual = False

    def procesar(self, estado_crudo: dict):
        """
        estado_crudo: {"persona": bool, "movimiento": bool, "hora": str}
        Retorna un dict con el contexto interpretado + la decisión tomada.
        """
        ahora = time.time()
        persona = bool(estado_crudo.get("persona"))
        movimiento = bool(estado_crudo.get("movimiento"))

        # ETAPA 3 - Procesamiento: construir contexto
        hay_actividad = persona or movimiento
        if hay_actividad:
            self._ultima_deteccion_ts = ahora

        segundos_sin_deteccion = (
            0.0 if self._ultima_deteccion_ts is None
            else ahora - self._ultima_deteccion_ts
        )

        # ETAPA 4 - Decision: aplicar reglas (si no está en modo manual)
        motivo = ""
        if not self.modo_manual:
            if hay_actividad:
                self.iluminacion_on = True
                motivo = "Persona/movimiento detectado"
            elif self._ultima_deteccion_ts is not None and \
                    segundos_sin_deteccion >= self.timeout_segundos:
                self.iluminacion_on = False
                motivo = f"Sin detección por {int(segundos_sin_deteccion)}s (>= {self.timeout_segundos}s)"
            else:
                motivo = "Manteniendo estado, dentro del periodo de espera"
        else:
            motivo = "Modo manual activo"

        return {
            "persona": persona,
            "movimiento": movimiento,
            "hora": estado_crudo.get("hora"),
            "hay_actividad": hay_actividad,
            "segundos_sin_deteccion": round(segundos_sin_deteccion, 1),
            "timeout_segundos": self.timeout_segundos,
            "iluminacion_on": self.iluminacion_on,
            "modo_manual": self.modo_manual,
            "motivo": motivo,
        }
