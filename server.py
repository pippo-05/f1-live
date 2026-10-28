"""
F1 Live Dashboard - backend.

Avvia un server locale che serve la dashboard (index.html) e un'API JSON
con lo stato della gara.

  python server.py            -> usa il simulatore (funziona sempre)
  python server.py --live     -> prova a usare i dati live (vedi LiveSource)

Apri poi http://localhost:8000 nel browser.
"""
import argparse
import json
import math
import os
import random
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))

DRIVERS = [
    ("VER", "Verstappen", "#3671C6"), ("NOR", "Norris", "#FF8000"),
    ("LEC", "Leclerc", "#E8002D"), ("HAM", "Hamilton", "#27F4D2"),
    ("RUS", "Russell", "#27F4D2"), ("PIA", "Piastri", "#FF8000"),
    ("SAI", "Sainz", "#1E41FF"), ("ALO", "Alonso", "#229971"),
    ("GAS", "Gasly", "#0093CC"), ("OCO", "Ocon", "#B6BABD"),
    ("TSU", "Tsunoda", "#6692FF"), ("ALB", "Albon", "#64C4FF"),
    ("STR", "Stroll", "#229971"), ("HUL", "Hulkenberg", "#B6BABD"),
    ("BOT", "Bottas", "#52E252"), ("ZHO", "Zhou", "#52E252"),
    ("MAG", "Magnussen", "#B6BABD"), ("RIC", "Ricciardo", "#6692FF"),
    ("SAR", "Sargeant", "#64C4FF"), ("LAW", "Lawson", "#6692FF"),
]

TOTAL_LAPS = 57
LAP_LENGTH_M = 5000


def track_point(t):
    """Tracciato di esempio (ovale con curve), t in [0, 1)."""
    a = 2 * math.pi * t
    x = 500 + 380 * math.cos(a) + 60 * math.cos(3 * a)
    y = 300 + 200 * math.sin(a) + 40 * math.sin(2 * a)
    return x, y


def lap_timing(code, lap_no, pace):
    """Tempo totale e tre settori di un giro (simulati ma stabili nel tempo)."""
    rng = random.Random(f"{code}-{lap_no}")
    total = 110.0 / pace + rng.uniform(-0.6, 0.9)
    w1 = 0.31 + rng.uniform(-0.02, 0.02)
    w2 = 0.36 + rng.uniform(-0.02, 0.02)
    sectors = [round(total * w1, 3), round(total * w2, 3),
               round(total * (1 - w1 - w2), 3)]
    return round(total, 3), sectors


class Simulator:
    """Simulatore di gara: genera posizioni, distacchi, gomme e telemetria."""

    def __init__(self):
        self.start = time.time()
        self.drivers = []
        for i, (code, name, color) in enumerate(DRIVERS):
            self.drivers.append({
                "code": code, "name": name, "color": color,
                "pace": random.uniform(0.96, 1.0),
                "offset": random.uniform(0, 0.02),
                "tyre": "S", "pit_done": False,
            })

    def state(self):
        elapsed = time.time() - self.start
        base_speed = 1 / 110.0  # giri al secondo di base
        rows = []
        for d in self.drivers:
            prog = (elapsed * base_speed * d["pace"]) - d["offset"] * 0 - (
                DRIVERS.index((d["code"], d["name"], d["color"])) * 0.004)
            prog = max(prog, 0.0)
            lap = min(int(prog) + 1, TOTAL_LAPS)
            frac = prog - int(prog)
            if prog >= TOTAL_LAPS:
                lap, frac = TOTAL_LAPS, 0.0
            if lap >= 18 and not d["pit_done"] and random.random() < 0.002:
                d["pit_done"] = True
                d["tyre"] = "M"
            x, y = track_point(frac)
            speed = 280 + 40 * math.sin(frac * 2 * math.pi * 3) + random.uniform(-3, 3)
            throttle = max(0, min(100, 100 - 60 * abs(math.sin(frac * 6 * math.pi))))
            brake = 100 - throttle if throttle < 40 else 0
            rows.append({
                **{k: d[k] for k in ("code", "name", "color", "tyre")},
                "lap": lap, "progress": prog, "x": x, "y": y,
                "speed": round(speed), "throttle": round(throttle),
                "brake": round(brake), "gear": min(8, 1 + int(speed // 50)),
                "rpm": int(9000 + 3000 * math.sin(frac * 10)),
            })
        rows.sort(key=lambda r: -r["progress"])
        leader_prog = rows[0]["progress"]
        for pos, r in enumerate(rows, 1):
            r["position"] = pos
            gap = (leader_prog - r["progress"]) * 110
            r["gap"] = "LEADER" if pos == 1 else f"+{gap:.1f}s"
            pace = next(d["pace"] for d in self.drivers if d["code"] == r["code"])
            done = min(int(r["progress"]), TOTAL_LAPS)
            laps = [lap_timing(r["code"], k, pace) for k in range(1, done + 1)]
            if laps:
                r["last_lap"] = laps[-1][0]
                r["last_sectors"] = laps[-1][1]
                r["best_lap"] = min(l[0] for l in laps)
                r["best_sectors"] = [min(l[1][i] for l in laps) for i in range(3)]
            else:
                r["last_lap"] = r["best_lap"] = None
                r["last_sectors"] = r["best_sectors"] = [None] * 3
            del r["progress"]
        # Viola = miglior tempo assoluto del settore, verde = record personale
        overall = [min((r["best_sectors"][i] for r in rows
                        if r["best_sectors"][i] is not None), default=None)
                   for i in range(3)]
        for r in rows:
            cols = []
            for i in range(3):
                t = r["last_sectors"][i]
                if t is None:
                    cols.append("")
                elif t <= overall[i] + 1e-9:
                    cols.append("purple")
                elif t <= r["best_sectors"][i] + 1e-9:
                    cols.append("green")
                else:
                    cols.append("yellow")
            r["sector_colors"] = cols
        return {
            "session": "Gran Premio - Simulazione",
            "lap": min(rows[0]["lap"], TOTAL_LAPS),
            "total_laps": TOTAL_LAPS,
            "track_status": "GREEN",
            "track": [track_point(i / 200) for i in range(200)],
            "drivers": rows,
            "source": "simulator",
            "timestamp": time.time(),
        }


class LiveSource:
    """
    Sorgente dati reale. Qui va collegato livef1 (pacchetto PyPI 'livef1').
    Consulta la documentazione di livef1 per i metodi esatti di sessione
    e di telemetria, poi converti i risultati nel formato usato da
    Simulator.state().
    """

    def __init__(self):
        try:
            import livef1  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Installa livef1: pip install livef1") from exc
        # TODO: inizializzare la sessione live con livef1 qui.

    def state(self):
        raise NotImplementedError(
            "Collega livef1 in LiveSource.state() (vedi commento).")


def make_handler(source):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=HERE, **kwargs)

        def do_GET(self):
            if self.path.startswith("/api/state"):
                body = json.dumps(source.state()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

        def log_message(self, *args):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="usa dati live")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    source = LiveSource() if args.live else Simulator()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(source))
    print(f"Dashboard su http://localhost:{args.port}  (sorgente: "
          f"{'live' if args.live else 'simulatore'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
