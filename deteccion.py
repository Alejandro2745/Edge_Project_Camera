"""
ETAPA 1 - CAPTURA
=================
Encapsula la cámara y la lógica de detección de persona / movimiento.
Genera el "dato crudo" del sistema: {persona, movimiento, hora}
tal como se describe en la diapositiva "Flujo de Datos" (Etapa 1).

Fuente de video admitida (parámetro `camera_index`):
  - int  (0, 1, 2...)  -> cámara local (webcam del computador)
  - str  ("http://192.168.x.x:8080/video") -> cámara IP, por ejemplo
    el celular corriendo la app "IP Webcam" (Android) o "DroidCam"
    (Android/iOS), en la misma red WiFi que el computador.
"""

import cv2
import time
import numpy as np


class DetectorPresencia:
    """
    Envuelve una cámara (webcam local o cámara IP/celular) y produce,
    en cada frame, el estado {persona: bool, movimiento: bool, hora: str}.

    - Detección de persona: HOG + SVM preentrenado de OpenCV
      (cv2.HOGDescriptor_getDefaultPeopleDetector), no requiere
      modelos externos ni conexión a internet.
    - Detección de movimiento: diferencia absoluta entre frames
      consecutivos en escala de grises.
    - Si la fuente es una URL de red (cámara del celular), se reintenta
      la conexión automáticamente ante cortes de WiFi.
    """

    def __init__(self, camera_index=0, resize_width: int = 480):
        self.camera_index = camera_index
        self.es_camara_red = isinstance(camera_index, str)
        self.resize_width = resize_width
        self.cap = None
        self._prev_gray = None
        self._ultimo_intento_reconexion = 0.0

        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def abrir(self) -> bool:
        self.cap = cv2.VideoCapture(self.camera_index)
        # Para streams de red (MJPEG del celular), un buffer pequeño evita
        # que el video se vea "atrasado" respecto al momento real.
        if self.es_camara_red:
            try:
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
        return self.cap.isOpened()

    def cerrar(self):
        if self.cap is not None:
            self.cap.release()

    def _reconectar_si_hace_falta(self):
        """Solo aplica a cámaras de red: reintenta cada 3s si se cayó la conexión."""
        if not self.es_camara_red:
            return
        ahora = time.time()
        if ahora - self._ultimo_intento_reconexion < 3.0:
            return
        self._ultimo_intento_reconexion = ahora
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.camera_index)

    def _redimensionar(self, frame):
        h, w = frame.shape[:2]
        if w == 0:
            return frame
        scale = self.resize_width / float(w)
        return cv2.resize(frame, (self.resize_width, int(h * scale)))

    def _detectar_movimiento(self, gray) -> bool:
        movimiento = False
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            movimiento = cv2.countNonZero(thresh) > (thresh.size * 0.01)
        self._prev_gray = gray
        return movimiento

    def _detectar_persona(self, frame):
        # detectMultiScale es costoso; se llama sobre el frame ya reducido
        rects, _ = self.hog.detectMultiScale(
            frame, winStride=(8, 8), padding=(8, 8), scale=1.05
        )
        return len(rects) > 0, rects

    def leer_estado(self):
        """
        Captura un frame y retorna (frame_anotado, estado_dict) o
        (None, None) si la cámara falla (en cuyo caso, si es una cámara
        de red, intentará reconectar en la siguiente llamada).
        """
        if self.cap is None or not self.cap.isOpened():
            self._reconectar_si_hace_falta()
            return None, None

        ok, frame = self.cap.read()
        if not ok:
            self._reconectar_si_hace_falta()
            return None, None

        frame = self._redimensionar(frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        movimiento = self._detectar_movimiento(gray)
        persona, rects = self._detectar_persona(frame)

        for (x, y, w, h) in rects:
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 200, 0), 2)

        estado = {
            "persona": bool(persona),
            "movimiento": bool(movimiento),
            "hora": time.strftime("%H:%M:%S"),
        }
        return frame, estado
    
    def cambiar_camara(self, nuevo_index) -> bool:
        """
        Cambia la fuente de video en caliente (sin reiniciar el proceso).
        Acepta un índice local (int) o una URL de cámara IP (str).
        Retorna True si la nueva cámara se abrió correctamente; si falla,
        se intenta restaurar la cámara anterior para no dejar el sistema
        sin video.
        """
        anterior_index = self.camera_index
        anterior_cap = self.cap
    
        self.camera_index = nuevo_index
        self.es_camara_red = isinstance(nuevo_index, str)
    
        nuevo_cap = cv2.VideoCapture(self.camera_index)
        if self.es_camara_red:
            try:
                nuevo_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
    
        if not nuevo_cap.isOpened():
            # Rollback: nos quedamos con la cámara anterior
            nuevo_cap.release()
            self.camera_index = anterior_index
            self.es_camara_red = isinstance(anterior_index, str)
            return False
    
        # Solo aquí liberamos la anterior, una vez confirmamos que la nueva sirve
        if anterior_cap is not None:
            anterior_cap.release()
    
        self.cap = nuevo_cap
        self._prev_gray = None  # resetear detección de movimiento (resolución/fuente distinta)
        self._ultimo_intento_reconexion = 0.0
        return True

