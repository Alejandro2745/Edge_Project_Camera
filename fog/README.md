# Capa Fog (pendiente)

Carpeta reservada para la capa de **Fog Computing** que se añadirá más
adelante al proyecto (según lo indicado por el profesor).

## Idea general

Hoy el proyecto tiene una sola capa, **Edge** (`edge/`): la cámara
detecta, decide y actúa localmente, todo en el mismo nodo.

Una arquitectura Edge → Fog típica agrega un nivel intermedio entre el
nodo edge y la nube:

```
Edge (edge/)            Fog (fog/)                 Cloud (futuro)
──────────────          ──────────────────         ──────────────
Cámara + sensor    →    Agregación de varios   →   Almacenamiento
Detección rápida        nodos edge                 histórico,
Decisión local           Reglas más complejas       dashboards,
                         Filtrado/resumen de         analítica
                         datos antes de subir
                         Cache / buffering si
                         se cae la conexión
```

## Qué podría vivir aquí

- Un servidor intermedio (p. ej. Flask/FastAPI o un broker MQTT propio)
  que reciba los eventos de **uno o varios** nodos edge.
- Lógica de agregación (ej. "si 2 de 3 cámaras detectan actividad,
  confirma la alerta") en vez de depender de un único sensor.
- Un buffer/cache local que siga funcionando si se pierde la conexión
  a internet, y sincronice luego con la nube.
- Reglas de negocio más pesadas que no conviene correr en el nodo edge
  (poca batería/CPU), p. ej. analítica histórica de consumo.

## Convención de carpeta

Misma convención que `edge/`: un paquete Python (`__init__.py`),
módulos por responsabilidad, y si expone su propio dashboard/API, sus
plantillas en `fog/templates/`.
