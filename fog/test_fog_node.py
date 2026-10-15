"""Pruebas del nodo Fog (sin broker, sin cámara). Ejecutar: python -m unittest test_fog_node -v"""
import os
import tempfile
import unittest

import fog_node
from fog_node import FogStore, FogIngestor, calcular_energia


class Reloj:
    def __init__(self, t0): self.t = t0
    def __call__(self): return self.t


class TestFog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = FogStore(os.path.join(self.tmp, "t.db"))
        self.reloj = Reloj(1_800_000_000.0)  # instante fijo
        self.ing = FogIngestor(self.store, clock=self.reloj)

    def avanzar(self, seg, ilum, motivo="x", paso=0.5):
        """Simula mensajes de actuación cada `paso` s durante `seg` s."""
        n = int(round(seg / paso))
        for _ in range(n):
            self.ing.on_actuacion({"iluminacion": ilum, "motivo": motivo}, ts=self.reloj.t)
            self.reloj.t += paso

    def totales(self):
        self.ing.flush()
        with self.store._lock:
            r = self.store.db.execute("SELECT SUM(seg_on), SUM(seg_off) FROM resumen_minuto").fetchone()
        return (r[0] or 0.0), (r[1] or 0.0)

    def test_acumula_segundos_on_off(self):
        self.avanzar(60, True)
        self.avanzar(30, False)
        self.avanzar(0.5, False)  # cierra el último intervalo
        on, off = self.totales()
        self.assertAlmostEqual(on, 60.0, delta=0.6)
        self.assertAlmostEqual(off, 30.0, delta=0.6)

    def test_solo_registra_cambios_de_estado(self):
        self.avanzar(20, True)
        self.avanzar(20, False)
        self.avanzar(20, True)
        self.ing.flush()
        ev = self.store.eventos(10)
        self.assertEqual([e["iluminacion"] for e in ev], [True, False, True])  # 3 filas, no 120 mensajes

    def test_hueco_del_edge_no_cuenta_como_monitoreado(self):
        self.avanzar(10, True)
        self.reloj.t += 3600  # Edge apagado 1 hora
        self.avanzar(10, True)
        on, off = self.totales()
        self.assertLess(on, 21.0)  # no suma la hora sin señal
        self.assertEqual(off, 0.0)

    def test_agrega_detecciones_por_minuto(self):
        for i in range(100):
            self.ing.on_deteccion({"persona": i % 4 == 0, "movimiento": i % 2 == 0}, ts=self.reloj.t)
            self.reloj.t += 0.1
        self.ing.flush()
        with self.store._lock:
            r = self.store.db.execute("SELECT SUM(frames), SUM(frames_persona), SUM(frames_movimiento) "
                                      "FROM resumen_minuto").fetchone()
        self.assertEqual((r[0], r[1], r[2]), (100, 25, 50))
        self.assertEqual(self.store.estadisticas()["mensajes_recibidos"], 100)

    def test_energia(self):
        pe, pb, tope = fog_node.P_ESPERA_W, fog_node.P_BOMBILLO_W, fog_node.OLVIDO_MAX_H_DIA
        # 2 h encendida + 22 h apagada en un día
        real, evitado, espera = calcular_energia(2 * 3600, 22 * 3600)
        self.assertAlmostEqual(espera, pe * 24 / 1000, places=6)
        self.assertAlmostEqual(real, (pe * 24 + pb * 2) / 1000, places=6)
        self.assertAlmostEqual(evitado, pb * min(22, tope) / 1000, places=6)  # topado por OLVIDO_MAX_H_DIA
        # con solo 1 h apagada no se supera lo realmente apagado
        _, evitado2, _ = calcular_energia(23 * 3600, 3600)
        self.assertAlmostEqual(evitado2, pb * min(1, tope) / 1000, places=6)

    def test_reporte_periodo(self):
        # el reloj fijo cae en el pasado: usar datos de "hoy" real
        import time
        t0 = time.time() // 60 * 60 - 600
        self.reloj.t = t0
        self.ing = FogIngestor(self.store, clock=self.reloj)
        self.avanzar(120, True)
        self.avanzar(180, False)
        self.avanzar(0.5, False)
        self.ing.flush()
        rep = self.store.reporte_periodo("dia")
        t = rep["totales"]
        self.assertAlmostEqual(t["horas_on"], 120 / 3600, delta=0.006)
        self.assertAlmostEqual(t["horas_off"], 180 / 3600, delta=0.006)
        self.assertEqual(t["activaciones"], 1)
        self.assertEqual(len(rep["perfil_horario"]), 24)

    def test_demo_y_reporte(self):
        st = fog_node.generar_demo(os.path.join(self.tmp, "demo.db"))
        rep = st.reporte_periodo("semana")
        self.assertTrue(rep["demo"])
        self.assertGreaterEqual(rep["totales"]["dias_con_datos"], 7)
        self.assertGreater(rep["totales"]["kwh_evitado"], 0)
        est = st.estadisticas()
        self.assertGreater(est["mensajes_recibidos"], est["filas_bd"] * 10)


if __name__ == "__main__":
    unittest.main()
