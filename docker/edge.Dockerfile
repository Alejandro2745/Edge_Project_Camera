# Imagen del nodo EDGE: cámara + detección (OpenCV) + MQTT + Arduino + dashboard
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Bogota

# tzdata: hora local de Colombia en los payloads {"hora": ...}
# libglib2.0-0: la necesita OpenCV aun en su versión headless
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY docker/requirements-edge.txt .
RUN pip install -r requirements-edge.txt

COPY run.py .
COPY edge/ edge/

EXPOSE 5000
CMD ["python", "run.py"]
