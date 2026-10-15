"""
run.py
======
Punto de entrada del proyecto. Arranca el nodo edge (cámara + MQTT +
decisión + actuación) y el dashboard web.

Ejecutar:
    pip install -r requirements.txt
    python run.py
    abrir http://localhost:5000 en el navegador
"""

from edge.app import main

if __name__ == "__main__":
    main()
