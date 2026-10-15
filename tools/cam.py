# Utilidad suelta: lista qué índices de cámara detecta OpenCV en esta máquina.
import cv2
for i in range(4):
    cap = cv2.VideoCapture(i)
    if cap.isOpened():
        print(f"Índice {i}: disponible")
    cap.release()