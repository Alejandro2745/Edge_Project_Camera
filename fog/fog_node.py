#!/usr/bin/env python3
"""
fog_node.py  -  CAPA FOG del proyecto
======================================
Nodo Fog que se SUMA al Edge sin tocar su decisión en tiempo real.

Qué hace:
  1. Se suscribe al broker MQTT local y escucha los mismos tópicos que ya
     publica el Edge:  iot_edge/deteccion  e  iot_edge/actuacion.
  2. AGREGA localmente: en vez de guardar ~12 mensajes por segundo, acumula
     por minuto (segundos con luz encendida/apagada, cuadros con persona o
     movimiento) y registra cada cambio de estado de la luz.
  3. Guarda todo en SQLite (un solo archivo, sin servidor de base de datos).
  4. Calcula consumo y ahorro y los expone en un dashboard web (puerto 5001)
     y por línea de comandos.

Es solo OBSERVADOR: nunca publica comandos. Si este nodo se apaga o falla,
el Edge sigue encendiendo/apagando la luz con normalidad.

Uso:
    python fog_node.py                    # nodo Fog completo (MQTT + web)
    python fog_node.py --reporte semana   # imprime el reporte y termina
    python fog_node.py --demo             # dashboard con datos SIMULADOS de 7 días

Dependencias: flask, paho-mqtt (ver requirements_fog.txt). No usa OpenCV,
así que puede correr en una Raspberry Pi aparte.
"""

import argparse
import json
import logging
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, request

TOPIC_DETECCION = "iot_edge/deteccion"
TOPIC_ACTUACION = "iot_edge/actuacion"

# ---------------------------------------------------------------------
# Configuración (variables de entorno o argumentos de línea de comandos)
# ---------------------------------------------------------------------
BROKER_HOST = os.environ.get("BROKER_HOST", "localhost")
BROKER_PORT = int(os.environ.get("BROKER_PORT", "1883"))
DB_PATH = os.environ.get("FOG_DB", "fog_datos.db")
WEB_PORT = int(os.environ.get("FOG_WEB_PORT", "5001"))

# Potencias medidas en el proyecto (W)
# P_ESPERA_W: consumo propio del CIRCUITO de actuación en reposo (Arduino +
# módulo de relé conectados por USB), no de la PC que corre la cámara/Flask
# -- esa PC existe por ser una demo de laptop, no por el producto final.
# ~0.4 W es un valor realista para un Arduino Uno + relé en reposo.
P_ESPERA_W = float(os.environ.get("P_ESPERA_W", "0.4"))
P_BOMBILLO_W = float(os.environ.get("P_BOMBILLO_W", "9.0"))   # bombillo LED
# Supuesto del proyecto: sin el sistema, ~4 h/día de luz innecesaria
OLVIDO_MAX_H_DIA = float(os.environ.get("OLVIDO_MAX_H_DIA", "4.0"))

MAX_GAP_SEG = float(os.environ.get("MAX_GAP_SEG", "5"))   # más que esto = Edge sin señal
FLUSH_CADA_SEG = 5.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS resumen_minuto (
    minuto            INTEGER PRIMARY KEY,   -- epoch (s) del inicio del minuto
    seg_on            REAL    NOT NULL DEFAULT 0,
    seg_off           REAL    NOT NULL DEFAULT 0,
    frames            INTEGER NOT NULL DEFAULT 0,
    frames_persona    INTEGER NOT NULL DEFAULT 0,
    frames_movimiento INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cambios_estado (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    iluminacion INTEGER NOT NULL,
    motivo      TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""

UPSERT_MINUTO = """
INSERT INTO resumen_minuto (minuto, seg_on, seg_off, frames, frames_persona, frames_movimiento)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(minuto) DO UPDATE SET
    seg_on            = seg_on            + excluded.seg_on,
    seg_off           = seg_off           + excluded.seg_off,
    frames            = frames            + excluded.frames,
    frames_persona    = frames_persona    + excluded.frames_persona,
    frames_movimiento = frames_movimiento + excluded.frames_movimiento
"""


# ---------------------------------------------------------------------
# Cálculo de energía
# ---------------------------------------------------------------------
def calcular_energia(seg_on: float, seg_off: float):
    """Devuelve (kwh_real, kwh_bombillo_evitado, kwh_espera) para UN día.

    - kwh_real:  consumo medido = espera del sistema durante todo el tiempo
                 monitoreado + bombillo mientras estuvo encendido.
    - evitado:   energía del bombillo que NO se gastó gracias al sistema.
                 Modelo conservador: horas apagadas, con tope diario de
                 OLVIDO_MAX_H_DIA (supuesto: sin sistema la luz quedaría
                 encendida innecesariamente ~4 h/día).
    - kwh_espera: consumo propio del sistema (se descuenta del ahorro neto).
    """
    t_mon = seg_on + seg_off
    kwh_espera = P_ESPERA_W * t_mon / 3600.0 / 1000.0
    kwh_real = kwh_espera + P_BOMBILLO_W * seg_on / 3600.0 / 1000.0
    horas_evitadas = min(seg_off / 3600.0, OLVIDO_MAX_H_DIA)
    kwh_evitado = P_BOMBILLO_W * horas_evitadas / 1000.0
    return kwh_real, kwh_evitado, kwh_espera


def rango_periodo(periodo: str, ahora: datetime = None):
    ahora = ahora or datetime.now()
    hoy0 = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    n = {"dia": 1, "semana": 7, "mes": 30}.get(periodo, 7)
    desde = hoy0 - timedelta(days=n - 1)
    hasta = hoy0 + timedelta(days=1)
    return desde.timestamp(), hasta.timestamp()


# ---------------------------------------------------------------------
# Almacenamiento (SQLite)
# ---------------------------------------------------------------------
class FogStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- escritura ----
    def sumar_minutos(self, buckets: dict, msgs: int = 0):
        with self._lock:
            self.db.executemany(UPSERT_MINUTO, [
                (m, d["seg_on"], d["seg_off"], d["frames"],
                 d["frames_persona"], d["frames_movimiento"])
                for m, d in buckets.items()
            ])
            if msgs:
                self.db.execute(
                    "INSERT INTO meta(clave, valor) VALUES('mensajes_recibidos', ?) "
                    "ON CONFLICT(clave) DO UPDATE SET "
                    "valor = CAST(valor AS INTEGER) + CAST(excluded.valor AS INTEGER)",
                    (str(msgs),))
            self.db.commit()

    def registrar_cambio(self, ts: float, iluminacion: bool, motivo: str):
        with self._lock:
            self.db.execute(
                "INSERT INTO cambios_estado(ts, iluminacion, motivo) VALUES (?, ?, ?)",
                (ts, 1 if iluminacion else 0, motivo))
            self.db.commit()

    def set_meta(self, clave: str, valor: str):
        with self._lock:
            self.db.execute(
                "INSERT INTO meta(clave, valor) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))
            self.db.commit()

    # ---- lectura ----
    def get_meta(self, clave: str, defecto=None):
        with self._lock:
            r = self.db.execute("SELECT valor FROM meta WHERE clave = ?", (clave,)).fetchone()
        return r["valor"] if r else defecto

    def estadisticas(self):
        with self._lock:
            n_min = self.db.execute("SELECT COUNT(*) FROM resumen_minuto").fetchone()[0]
            n_cam = self.db.execute("SELECT COUNT(*) FROM cambios_estado").fetchone()[0]
        return {
            "filas_bd": n_min + n_cam,
            "mensajes_recibidos": int(self.get_meta("mensajes_recibidos", "0") or 0),
            "demo": self.get_meta("demo", "0") == "1",
        }

    def eventos(self, limite: int = 20):
        with self._lock:
            filas = self.db.execute(
                "SELECT ts, iluminacion, motivo FROM cambios_estado ORDER BY ts DESC LIMIT ?",
                (limite,)).fetchall()
        return [{
            "hora": datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S"),
            "iluminacion": bool(r["iluminacion"]),
            "motivo": r["motivo"],
        } for r in filas]

    def reporte_periodo(self, periodo: str = "semana"):
        desde, hasta = rango_periodo(periodo)
        with self._lock:
            filas = self.db.execute(
                "SELECT strftime('%Y-%m-%d', minuto, 'unixepoch', 'localtime') AS dia, "
                "SUM(seg_on) AS seg_on, SUM(seg_off) AS seg_off, SUM(frames) AS frames, "
                "SUM(frames_persona) AS fp, SUM(frames_movimiento) AS fm "
                "FROM resumen_minuto WHERE minuto >= ? AND minuto < ? "
                "GROUP BY dia ORDER BY dia", (desde, hasta)).fetchall()
            act = {r["dia"]: r["n"] for r in self.db.execute(
                "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS dia, COUNT(*) AS n "
                "FROM cambios_estado WHERE iluminacion = 1 AND ts >= ? AND ts < ? GROUP BY dia",
                (desde, hasta))}
            horas = self.db.execute(
                "SELECT CAST(strftime('%H', minuto, 'unixepoch', 'localtime') AS INTEGER) AS h, "
                "SUM(seg_on) AS seg_on FROM resumen_minuto "
                "WHERE minuto >= ? AND minuto < ? GROUP BY h", (desde, hasta)).fetchall()

        dias = []
        tot = {"seg_on": 0.0, "seg_off": 0.0, "kwh_real": 0.0, "kwh_evitado": 0.0,
               "kwh_espera": 0.0, "activaciones": 0}
        for r in filas:
            seg_on, seg_off = r["seg_on"] or 0.0, r["seg_off"] or 0.0
            kwh_real, kwh_evitado, kwh_espera = calcular_energia(seg_on, seg_off)
            n_act = act.get(r["dia"], 0)
            dias.append({
                "dia": r["dia"],
                "horas_on": round(seg_on / 3600, 2),
                "horas_off": round(seg_off / 3600, 2),
                "horas_monitoreadas": round((seg_on + seg_off) / 3600, 2),
                "kwh_real": round(kwh_real, 4),
                "kwh_evitado": round(kwh_evitado, 4),
                "kwh_espera": round(kwh_espera, 4),
                "kwh_ahorro_neto": round(kwh_evitado - kwh_espera, 4),
                "activaciones": n_act,
            })
            tot["seg_on"] += seg_on
            tot["seg_off"] += seg_off
            tot["kwh_real"] += kwh_real
            tot["kwh_evitado"] += kwh_evitado
            tot["kwh_espera"] += kwh_espera
            tot["activaciones"] += n_act

        n_dias = len(dias)
        totales = {
            "dias_con_datos": n_dias,
            "horas_on": round(tot["seg_on"] / 3600, 2),
            "horas_off": round(tot["seg_off"] / 3600, 2),
            "horas_monitoreadas": round((tot["seg_on"] + tot["seg_off"]) / 3600, 2),
            "kwh_real": round(tot["kwh_real"], 4),
            "kwh_evitado": round(tot["kwh_evitado"], 4),
            "kwh_espera": round(tot["kwh_espera"], 4),
            "kwh_ahorro_neto": round(tot["kwh_evitado"] - tot["kwh_espera"], 4),
            "activaciones": tot["activaciones"],
            "proyeccion_mensual_bruto_kwh": round(tot["kwh_evitado"] / n_dias * 30, 3) if n_dias else 0.0,
            "proyeccion_mensual_neto_kwh": round(
                (tot["kwh_evitado"] - tot["kwh_espera"]) / n_dias * 30, 3) if n_dias else 0.0,
        }
        perfil = [0.0] * 24
        for r in horas:
            perfil[int(r["h"])] = round((r["seg_on"] or 0.0) / 3600, 3)
        return {
            "periodo": periodo,
            "desde": datetime.fromtimestamp(desde).strftime("%Y-%m-%d"),
            "hasta": (datetime.fromtimestamp(hasta) - timedelta(days=1)).strftime("%Y-%m-%d"),
            "dias": dias,
            "totales": totales,
            "perfil_horario": perfil,
            "parametros": {"p_espera_w": P_ESPERA_W, "p_bombillo_w": P_BOMBILLO_W,
                           "olvido_max_h_dia": OLVIDO_MAX_H_DIA},
            "demo": self.get_meta("demo", "0") == "1",
        }


# ---------------------------------------------------------------------
# Ingesta: lógica de agregación (independiente de MQTT, fácil de probar)
# ---------------------------------------------------------------------
class FogIngestor:
    def __init__(self, store: FogStore, clock=time.time):
        self.store = store
        self.clock = clock
        self._lock = threading.RLock()
        self._acum = {}
        self._msgs_pend = 0
        self._ult_flush = clock()
        self._ultimo_ts = None
        self._ultimo_estado = None
        self.estado_actual = {"iluminacion": None, "motivo": "", "ts": None}
        self.ultima_deteccion = {"persona": None, "movimiento": None, "hora": None, "ts": None}
        self.msgs_sesion = 0

    def _bucket(self, ts: float):
        m = int(ts // 60 * 60)
        return self._acum.setdefault(m, {"seg_on": 0.0, "seg_off": 0.0, "frames": 0,
                                         "frames_persona": 0, "frames_movimiento": 0})

    def on_deteccion(self, payload: dict, ts: float = None):
        ts = self.clock() if ts is None else ts
        with self._lock:
            self._msgs_pend += 1
            self.msgs_sesion += 1
            b = self._bucket(ts)
            b["frames"] += 1
            if payload.get("persona"):
                b["frames_persona"] += 1
            if payload.get("movimiento"):
                b["frames_movimiento"] += 1
            self.ultima_deteccion = {"persona": bool(payload.get("persona")),
                                     "movimiento": bool(payload.get("movimiento")),
                                     "hora": payload.get("hora"), "ts": ts}
            self._flush_si_toca(ts)

    def on_actuacion(self, payload: dict, ts: float = None):
        ts = self.clock() if ts is None else ts
        ilum = bool(payload.get("iluminacion"))
        motivo = str(payload.get("motivo", ""))
        with self._lock:
            self._msgs_pend += 1
            self.msgs_sesion += 1
            if self._ultimo_ts is not None:
                dt = ts - self._ultimo_ts
                # Se atribuye el intervalo al estado anterior. Si hubo un hueco
                # (Edge apagado/sin señal) NO se cuenta como tiempo monitoreado.
                if 0 < dt <= MAX_GAP_SEG:
                    self._bucket(ts)["seg_on" if self._ultimo_estado else "seg_off"] += dt
            if self._ultimo_estado is None or ilum != self._ultimo_estado:
                self.store.registrar_cambio(ts, ilum, motivo)
            self._ultimo_estado = ilum
            self._ultimo_ts = ts
            self.estado_actual = {"iluminacion": ilum, "motivo": motivo, "ts": ts}
            self._flush_si_toca(ts)

    def _flush_si_toca(self, ahora: float):
        if ahora - self._ult_flush >= FLUSH_CADA_SEG:
            self._flush_locked(ahora)

    def _flush_locked(self, ahora: float):
        if self._acum or self._msgs_pend:
            self.store.sumar_minutos(self._acum, self._msgs_pend)
            self._acum = {}
            self._msgs_pend = 0
        self._ult_flush = ahora

    def flush(self):
        with self._lock:
            self._flush_locked(self.clock())

    def snapshot(self):
        with self._lock:
            ahora = self.clock()
            ult = self.estado_actual["ts"]
            hace = None if ult is None else round(ahora - ult, 1)
            return {
                "iluminacion": self.estado_actual["iluminacion"],
                "motivo": self.estado_actual["motivo"],
                "ultimo_mensaje_hace_seg": hace,
                "edge_activo": hace is not None and hace <= MAX_GAP_SEG,
                "ultima_deteccion": {k: v for k, v in self.ultima_deteccion.items() if k != "ts"},
                "mensajes_sesion": self.msgs_sesion,
            }


# ---------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------
class FogMQTT:
    def __init__(self, ingestor: FogIngestor, host: str, port: int):
        import paho.mqtt.client as mqtt
        self.host, self.port = host, port
        self.ingestor = ingestor
        self.conectado = False
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                                      client_id="iot_fog_node", clean_session=True)  # paho 2.x
        except AttributeError:
            self.client = mqtt.Client(client_id="iot_fog_node", clean_session=True)   # paho 1.x
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)

    def _on_connect(self, client, userdata, flags, rc):
        self.conectado = (rc == 0)
        if self.conectado:
            client.subscribe([(TOPIC_DETECCION, 0), (TOPIC_ACTUACION, 0)])
            print(f"[fog] conectado al broker {self.host}:{self.port}")

    def _on_disconnect(self, client, userdata, rc):
        self.conectado = False
        print("[fog] desconectado del broker; reintentando...")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if msg.topic == TOPIC_DETECCION:
            self.ingestor.on_deteccion(payload)
        elif msg.topic == TOPIC_ACTUACION:
            self.ingestor.on_actuacion(payload)

    def iniciar(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def detener(self):
        self.client.loop_stop()
        self.client.disconnect()


# ---------------------------------------------------------------------
# Datos de DEMOSTRACIÓN (simulados, solo para ver el dashboard con 7 días)
# ---------------------------------------------------------------------
def generar_demo(path: str, dias: int = 7, seed: int = 42):
    for sufijo in ("", "-wal", "-shm"):
        if os.path.exists(path + sufijo):
            os.remove(path + sufijo)
    store = FogStore(path)
    rnd = random.Random(seed)
    ahora = datetime.now()
    hoy0 = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    ultimo_min = int(ahora.timestamp() // 60 * 60)
    timeout_min = 5
    buckets, cambios, msgs = {}, [], 0

    for d in range(dias - 1, -1, -1):
        dia0 = hoy0 - timedelta(days=d)
        fin_semana = dia0.weekday() >= 5
        if fin_semana:
            bloques = [(rnd.randint(8 * 60, 10 * 60), rnd.randint(60, 150)),
                       (rnd.randint(17 * 60, 19 * 60), rnd.randint(180, 330))]
        else:
            bloques = [(rnd.randint(6 * 60, 6 * 60 + 40), rnd.randint(25, 60)),
                       (rnd.randint(18 * 60, 19 * 60 + 30), rnd.randint(150, 300))]
        bloques += [(rnd.randint(8 * 60, 22 * 60), rnd.randint(2, 12)) for _ in range(rnd.randint(3, 8))]
        ocupado = [0] * 1440
        for ini, dur in bloques:
            for m in range(ini, min(1440, ini + dur)):
                ocupado[m] = 1
        luz = [0] * 1440
        for m in range(1440):
            if ocupado[m]:
                for k in range(m, min(1440, m + 1 + timeout_min)):
                    luz[k] = 1
        previo = None
        for m in range(1440):
            ts = int(dia0.timestamp()) + m * 60
            if ts > ultimo_min:
                break
            frames = 750
            buckets[ts] = {
                "seg_on": 60.0 if luz[m] else 0.0, "seg_off": 0.0 if luz[m] else 60.0,
                "frames": frames,
                "frames_persona": int(frames * rnd.uniform(0.6, 0.9)) if ocupado[m] else 0,
                "frames_movimiento": int(frames * rnd.uniform(0.3, 0.7)) if ocupado[m] else 0,
            }
            msgs += frames * 2
            if previo is None or luz[m] != previo:
                cambios.append((float(ts), luz[m], "Persona/movimiento detectado" if luz[m]
                                else f"Sin detección por {timeout_min * 60}s (>= {timeout_min * 60}s)"))
            previo = luz[m]

    store.sumar_minutos(buckets, msgs)
    with store._lock:
        store.db.executemany("INSERT INTO cambios_estado(ts, iluminacion, motivo) VALUES (?, ?, ?)", cambios)
        store.db.commit()
    store.set_meta("demo", "1")
    return store


# ---------------------------------------------------------------------
# Reporte por consola
# ---------------------------------------------------------------------
def imprimir_reporte(store: FogStore, periodo: str):
    rep = store.reporte_periodo(periodo)
    t = rep["totales"]
    titulo = {"dia": "HOY", "semana": "ULTIMOS 7 DIAS", "mes": "ULTIMOS 30 DIAS"}[periodo]
    print(f"\nREPORTE FOG - {titulo}  ({rep['desde']} a {rep['hasta']})")
    if rep["demo"]:
        print("*** DATOS DE DEMOSTRACION (simulados), no son mediciones reales ***")
    print(f"Parametros: bombillo {P_BOMBILLO_W} W | espera del sistema {P_ESPERA_W} W | "
          f"tope de luz innecesaria evitada {OLVIDO_MAX_H_DIA} h/dia\n")
    cab = f"{'Dia':<11}{'Luz ON(h)':>10}{'Luz OFF(h)':>11}{'Monit.(h)':>10}{'Real(kWh)':>10}" \
          f"{'Evitado(kWh)':>13}{'Espera(kWh)':>12}{'Neto(kWh)':>10}{'Activ.':>7}"
    print(cab)
    print("-" * len(cab))
    for d in rep["dias"]:
        print(f"{d['dia']:<11}{d['horas_on']:>10.2f}{d['horas_off']:>11.2f}{d['horas_monitoreadas']:>10.2f}"
              f"{d['kwh_real']:>10.3f}{d['kwh_evitado']:>13.3f}{d['kwh_espera']:>12.3f}"
              f"{d['kwh_ahorro_neto']:>10.3f}{d['activaciones']:>7}")
    print("-" * len(cab))
    print(f"{'TOTAL':<11}{t['horas_on']:>10.2f}{t['horas_off']:>11.2f}{t['horas_monitoreadas']:>10.2f}"
          f"{t['kwh_real']:>10.3f}{t['kwh_evitado']:>13.3f}{t['kwh_espera']:>12.3f}"
          f"{t['kwh_ahorro_neto']:>10.3f}{t['activaciones']:>7}")
    print(f"\nProyeccion mensual: ahorro bruto del bombillo {t['proyeccion_mensual_bruto_kwh']} kWh/mes | "
          f"ahorro neto (descontando la espera del sistema) {t['proyeccion_mensual_neto_kwh']} kWh/mes")
    est = store.estadisticas()
    print(f"Datos: {est['mensajes_recibidos']} mensajes MQTT recibidos -> {est['filas_bd']} filas almacenadas\n")


# ---------------------------------------------------------------------
# Dashboard web del nodo Fog
# ---------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nodo Fog · Histórico y analítica energética</title>
<style>
:root{--bg:#0d1020;--card:#161a33;--line:#2a2f57;--tx:#e6e8ff;--mut:#9aa0cf;--ac:#8b7cff;--on:#ffb454;--off:#2fd4b0;--bad:#ff6b81}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 "Segoe UI",system-ui,sans-serif;padding:22px}
h1{font-size:22px;margin:0 0 2px}.sub{color:var(--mut);margin-bottom:16px}
.pills{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 16px}
.pill{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 12px;font-size:12px;color:var(--mut)}
.pill b{color:var(--tx)}.ok b{color:var(--off)}.warn b{color:var(--bad)}
.banner{background:#3a2a10;border:1px solid #7a5a1e;color:#ffd48a;padding:8px 12px;border-radius:8px;margin-bottom:14px;display:none}
.tabs{display:flex;gap:6px;margin-bottom:14px}.tabs button{background:var(--card);color:var(--mut);border:1px solid var(--line);padding:6px 16px;border-radius:8px;cursor:pointer}
.tabs button.act{background:var(--ac);color:#fff;border-color:var(--ac)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.kpi .v{font-size:22px;font-weight:600}.kpi .l{color:var(--mut);font-size:12px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}@media(max-width:900px){.row{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h2{font-size:13px;margin:0 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:6px 8px;text-align:right;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:500}
.ev{display:flex;gap:10px;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}.ev .t{color:var(--mut);min-width:150px}
.dot{width:9px;height:9px;border-radius:50%;margin-top:5px;flex:none}.foot{color:var(--mut);font-size:12px;margin-top:10px}
svg text{fill:var(--mut);font-size:10px}
</style></head><body>
<h1>Nodo Fog · Histórico y analítica energética</h1>
<div class="sub">Capa Fog del proyecto: observa el flujo MQTT del Edge, agrega por minuto y calcula consumo y ahorro. No interviene en la decisión.</div>
<div class="banner" id="banner">DATOS DE DEMOSTRACIÓN (simulados): no son mediciones reales.</div>
<div class="pills">
 <div class="pill" id="pBroker">Broker: <b>…</b></div><div class="pill" id="pEdge">Edge: <b>…</b></div>
 <div class="pill" id="pLuz">Luz: <b>…</b></div><div class="pill" id="pDet">Última detección: <b>…</b></div>
</div>
<div class="tabs"><button data-p="dia">Hoy</button><button data-p="semana" class="act">7 días</button><button data-p="mes">30 días</button></div>
<div class="grid" id="kpis"></div>
<div class="row">
 <div class="card"><h2>Horas por día (luz encendida / apagada)</h2><div id="chDias"></div></div>
 <div class="card"><h2>Horas con luz encendida por hora del día</h2><div id="chHoras"></div></div>
</div>
<div class="row">
 <div class="card"><h2>Reporte por día</h2><div id="tabla"></div></div>
 <div class="card"><h2>Últimos cambios de estado</h2><div id="eventos"></div></div>
</div>
<div class="foot" id="foot"></div>
<script>
const $=id=>document.getElementById(id);let periodo='semana';
const j=async u=>(await fetch(u)).json();const f=(n,d=2)=>Number(n).toFixed(d);
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{periodo=b.dataset.p;
 document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('act',x===b));refrescar();});
function pill(id,txt,ok){const e=$(id);e.className='pill '+(ok?'ok':'warn');e.innerHTML=e.innerHTML.split(':')[0]+': <b>'+txt+'</b>';}
function barrasDias(dias){
 if(!dias.length)return '<div class="foot">Sin datos todavía.</div>';
 const W=520,H=210,pad=28,bw=Math.min(46,(W-pad)/dias.length-8);let s=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
 [0,6,12,18,24].forEach(h=>{const y=H-24-(h/24)*(H-44);s+=`<line x1="${pad}" x2="${W}" y1="${y}" y2="${y}" stroke="#2a2f57"/><text x="2" y="${y+3}">${h}h</text>`});
 dias.forEach((d,i)=>{const x=pad+8+i*((W-pad)/dias.length);const hOn=(d.horas_on/24)*(H-44),hOff=(d.horas_off/24)*(H-44);
  s+=`<rect x="${x}" y="${H-24-hOn}" width="${bw}" height="${hOn}" fill="#ffb454"/>`;
  s+=`<rect x="${x}" y="${H-24-hOn-hOff}" width="${bw}" height="${hOff}" fill="#2fd4b0" opacity=".85"/>`;
  s+=`<text x="${x}" y="${H-10}">${d.dia.slice(5)}</text>`});
 return s+'</svg><div class="foot"><span style="color:#ffb454">■</span> luz encendida &nbsp; <span style="color:#2fd4b0">■</span> luz apagada</div>';}
function barrasHoras(p){
 const W=520,H=210,pad=28,mx=Math.max(...p,0.01),bw=(W-pad)/24-3;let s=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
 p.forEach((v,h)=>{const x=pad+h*((W-pad)/24),hh=(v/mx)*(H-44);
  s+=`<rect x="${x}" y="${H-24-hh}" width="${bw}" height="${hh}" fill="#8b7cff"/>`;
  if(h%3==0)s+=`<text x="${x}" y="${H-10}">${h}h</text>`});
 return s+`</svg><div class="foot">Máximo: ${f(mx)} h acumuladas en una misma hora del día.</div>`;}
async function refrescar(){
 const [e,r,ev]=await Promise.all([j('/api/estado'),j('/api/reporte?periodo='+periodo),j('/api/eventos?limit=12')]);
 $('banner').style.display=r.demo?'block':'none';
 pill('pBroker',e.broker_conectado?'conectado':'sin conexión',e.broker_conectado);
 pill('pEdge',e.edge_activo?'activo':'sin señal',e.edge_activo);
 pill('pLuz',e.iluminacion===null?'—':(e.iluminacion?'encendida':'apagada'),e.iluminacion!==null);
 const ud=e.ultima_deteccion||{};$('pDet').innerHTML='Última detección: <b>'+(ud.hora?('persona '+(ud.persona?'sí':'no')+' · mov. '+(ud.movimiento?'sí':'no')+' · '+ud.hora):'—')+'</b>';
 const t=r.totales;
 $('kpis').innerHTML=[
  ['Horas con luz encendida',f(t.horas_on)+' h'],['Horas apagadas por el sistema',f(t.horas_off)+' h'],
  ['Consumo real',f(t.kwh_real,3)+' kWh'],['Bombillo evitado (bruto)',f(t.kwh_evitado,3)+' kWh'],
  ['Consumo propio del sistema',f(t.kwh_espera,3)+' kWh'],['Ahorro neto',f(t.kwh_ahorro_neto,3)+' kWh'],
  ['Proyección mensual bruta',f(t.proyeccion_mensual_bruto_kwh,2)+' kWh'],['Activaciones de la luz',t.activaciones]
 ].map(([l,v])=>`<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
 $('chDias').innerHTML=barrasDias(r.dias);$('chHoras').innerHTML=barrasHoras(r.perfil_horario);
 $('tabla').innerHTML='<table><tr><th>Día</th><th>ON (h)</th><th>OFF (h)</th><th>Real</th><th>Evitado</th><th>Neto</th></tr>'+
  r.dias.map(d=>`<tr><td>${d.dia}</td><td>${f(d.horas_on)}</td><td>${f(d.horas_off)}</td><td>${f(d.kwh_real,3)}</td><td>${f(d.kwh_evitado,3)}</td><td>${f(d.kwh_ahorro_neto,3)}</td></tr>`).join('')+'</table>'+
  '<div class="foot">Valores de energía en kWh.</div>';
 $('eventos').innerHTML=ev.length?ev.map(x=>`<div class="ev"><span class="dot" style="background:${x.iluminacion?'#ffb454':'#2fd4b0'}"></span><span class="t">${x.hora}</span><span>${x.iluminacion?'Encendida':'Apagada'} · ${x.motivo}</span></div>`).join(''):'<div class="foot">Sin eventos todavía.</div>';
 const p=r.parametros;
 $('foot').innerHTML=`Datos: ${e.mensajes_recibidos} mensajes MQTT recibidos → ${e.filas_bd} filas almacenadas (agregación local en el nodo Fog).<br>`+
  `Modelo: bombillo ${p.p_bombillo_w} W · espera del sistema ${p.p_espera_w} W · ahorro bruto = horas apagadas (tope ${p.olvido_max_h_dia} h/día de luz innecesaria evitada) × potencia del bombillo; ahorro neto = bruto − consumo propio del sistema.`;
}
refrescar();setInterval(refrescar,3000);
</script></body></html>
"""


def crear_app(store: FogStore, ingestor: FogIngestor = None, mqtt_fog: FogMQTT = None):
    app = Flask(__name__)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return Response(HTML, mimetype="text/html")

    @app.route("/api/estado")
    def api_estado():
        est = store.estadisticas()
        snap = ingestor.snapshot() if ingestor else {
            "iluminacion": None, "motivo": "", "ultimo_mensaje_hace_seg": None,
            "edge_activo": False, "ultima_deteccion": {}, "mensajes_sesion": 0}
        snap.update(est)
        snap["broker_conectado"] = bool(mqtt_fog and mqtt_fog.conectado)
        return jsonify(snap)

    @app.route("/api/reporte")
    def api_reporte():
        periodo = request.args.get("periodo", "semana")
        if periodo not in ("dia", "semana", "mes"):
            periodo = "semana"
        return jsonify(store.reporte_periodo(periodo))

    @app.route("/api/eventos")
    def api_eventos():
        limite = max(1, min(200, request.args.get("limit", 20, type=int)))
        return jsonify(store.eventos(limite))

    return app


# ---------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Nodo Fog: histórico y analítica del proyecto de iluminación")
    ap.add_argument("--broker", default=BROKER_HOST, help="host del broker MQTT (def. localhost)")
    ap.add_argument("--port", type=int, default=BROKER_PORT, help="puerto MQTT (def. 1883)")
    ap.add_argument("--db", default=None, help="archivo SQLite (def. fog_datos.db)")
    ap.add_argument("--web-port", type=int, default=WEB_PORT, help="puerto del dashboard (def. 5001)")
    ap.add_argument("--reporte", choices=["dia", "semana", "mes"],
                    help="imprime el reporte del periodo y termina")
    ap.add_argument("--demo", action="store_true",
                    help="genera 7 días de datos SIMULADOS y abre solo el dashboard (sin MQTT)")
    args = ap.parse_args()

    if args.demo:
        db = args.db or "fog_demo.db"
        store = generar_demo(db)
        print(f"[fog] base de datos de DEMOSTRACION generada en {db} (datos simulados)")
        if args.reporte:
            imprimir_reporte(store, args.reporte)
            return
        app = crear_app(store)
        print(f"[fog] dashboard de demostracion en http://localhost:{args.web_port}")
        app.run(host="0.0.0.0", port=args.web_port, threaded=True, use_reloader=False)
        return

    store = FogStore(args.db or DB_PATH)
    if args.reporte:
        imprimir_reporte(store, args.reporte)
        return

    ingestor = FogIngestor(store)
    mqtt_fog = FogMQTT(ingestor, args.broker, args.port)
    mqtt_fog.iniciar()
    print(f"[fog] escuchando {TOPIC_DETECCION} y {TOPIC_ACTUACION} en {args.broker}:{args.port}")

    def vaciar_periodicamente():
        while True:
            time.sleep(FLUSH_CADA_SEG)
            ingestor.flush()
    threading.Thread(target=vaciar_periodicamente, daemon=True).start()

    app = crear_app(store, ingestor, mqtt_fog)
    print(f"[fog] dashboard en http://localhost:{args.web_port}")
    try:
        app.run(host="0.0.0.0", port=args.web_port, threaded=True, use_reloader=False)
    finally:
        ingestor.flush()
        mqtt_fog.detener()


if __name__ == "__main__":
    main()
