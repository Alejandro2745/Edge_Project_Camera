# Imagen del nodo FOG: observador MQTT + SQLite + dashboard de energía
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Bogota

# tzdata: los reportes agrupan por día/hora local, no en UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY docker/requirements-fog.txt .
RUN pip install -r requirements-fog.txt

COPY fog/fog_node.py fog/test_fog_node.py ./

# La base SQLite vive en un volumen montado en /data
RUN mkdir -p /data
ENV FOG_DB=/data/fog_datos.db

EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/api/estado' % os.environ.get('FOG_WEB_PORT','5001'), timeout=4)" || exit 1

CMD ["python", "fog_node.py"]
