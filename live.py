"""
Sorgente dati LIVE del cronometraggio F1.

FastF1 riceve il feed SignalR e lo scrive su live_raw.txt; questo modulo lo legge
in tempo reale e produce il formato usato dall'app (come il simulatore).

Oltre ai dati di base calcola, per ogni pilota, sullo storico di tutta la sessione:
- record personali di settore e confronto con il miglior settore assoluto
- giri validi (senza i giri di ingresso e uscita dai box) e miglior giro valido
- numero di soste ed età delle gomme
- distacco in secondi dal pilota davanti

Uso:  python3 app.py --live
      python3 app.py --live --debug
"""
import ast
import base64
import json
import os
import threading
import time
import traceback
import zlib

try:
    # Certificati SSL per il server F1 (Python da python.org non usa quelli di sistema)
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

import fastf1.livetiming.client as _ftc
from fastf1.livetiming.client import SignalRClient

# Senza account F1 TV: token vuoto (stringa, non None, perché la libreria
# concatena "Bearer " + token).
_ftc.get_auth_token = lambda *args, **kwargs: ""

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_FILE = os.path.join(HERE, "live_raw.txt")
TOPICS = ["DriverList", "TimingData", "CarData.z", "Position.z",
          "TimingAppData", "LapCount", "TrackStatus", "SessionInfo",
          "ExtrapolatedClock", "SessionData", "RaceControlMessages", "SessionStatus"]
TYRE_LETTER = {"SOFT": "S", "MEDIUM": "M", "HARD": "H",
               "INTERMEDIATE": "I", "WET": "W"}
STATUS_BY_CODE = {"1": "GREEN", "2": "YELLOW", "4": "SC",
                  "5": "RED", "6": "VSC", "7": "VSC"}
STATUS_BY_MESSAGE = {"AllClear": "GREEN", "Green": "GREEN", "Yellow": "YELLOW",
                     "SCDeployed": "SC", "SCEnding": "SC",
                     "VSCDeployed": "VSC", "VSCEnding": "VSC", "Red": "RED"}
TRACK_BOX = (1000, 600)
MAX_TRACK_POINTS = 2000
EPS = 1e-6


def seconds(value):
    """'1:32.456' -> 92.456, '28.123' -> 28.123, altrimenti None."""
    if not value or not isinstance(value, str):
        return None
    try:
        if ":" in value:
            m, s = value.split(":", 1)
            return int(m) * 60 + float(s)
        return float(value)
    except ValueError:
        return None


def as_list(obj):
    """Il feed usa a volte liste, a volte dizionari con chiavi numeriche."""
    if isinstance(obj, dict):
        return [obj[k] for k in sorted(obj, key=lambda k: int(k) if str(k).isdigit() else 0)]
    return obj if isinstance(obj, list) else []


def _normalize(line):
    """Converte la lista dei settori in dizionario {indice: dato} per l'unione."""
    if isinstance(line.get("Sectors"), list):
        line = dict(line)
        line["Sectors"] = {str(i): s for i, s in enumerate(line["Sectors"]) if s}
    return line


def _merge(dst, src):
    """Unisce un aggiornamento parziale: ignora i campi vuoti o assenti."""
    for k, v in src.items():
        if v is None or v == "":
            continue
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def _gap_seconds(text):
    """'+0.075' -> '+0.075' (testo pulito); valori non numerici restano così come sono."""
    if not text:
        return "—"
    return str(text)


class LiveSource:
    def __init__(self, debug=False):
        self.debug = debug
        self.lock = threading.Lock()
        self.seen = set()
        self.drivers = {}        # numero -> {code, name, color, photo}
        self.timing = {}         # numero -> dati di cronometraggio uniti
        self.car = {}            # numero -> canali telemetria
        self.pos = {}            # numero -> (x, y) grezzi
        self.tyre = {}           # numero -> gomma corrente (lettera)
        self.stints = {}         # numero -> stint della gomma
        self.track_raw = []      # punti grezzi del tracciato
        self.session = "In attesa di dati live"
        self.error = "In collegamento al cronometraggio..."
        self.lap, self.total_laps, self.track_status = 0, 0, "GREEN"
        self.remaining = None
        self.remaining_at = 0.0
        self.extrapolating = False
        self.quali_part = None
        self.session_status = ""

        # Storico per pilota (tutta la sessione)
        self.pit_flag = {}       # numero -> il giro in corso ha toccato i box
        self.last_lap_str = {}   # numero -> ultimo tempo di giro già registrato
        self.last_invalid = {}   # numero -> l'ultimo giro era di ingresso/uscita box
        self.lap_valid = {}      # numero -> tempi dei giri validi
        self.lap_invalid = {}    # numero -> tempi dei giri di ingresso/uscita box
        self.sector_hist = {}    # numero -> [[settore 1 visti], [settore 2], [settore 3]]

        self.rc_seen = set()
        self.rc_history = []

        threading.Thread(target=self._receive_forever, daemon=True).start()
        threading.Thread(target=self._read_forever, daemon=True).start()

    # ---------- ricezione (FastF1 -> file) ----------
    def _receive_forever(self):
        """Avvia FastF1 e lo riavvia se la connessione cade."""
        while True:
            try:
                client = SignalRClient(filename=RAW_FILE, filemode="w", timeout=0)
                client.topics = TOPICS
                client.start()
                self.error = "Connessione chiusa, nuovo tentativo..."
            except Exception as exc:
                self.error = (f"Connessione fallita ({type(exc).__name__}): "
                              "nessuna sessione in corso o rete bloccata")
                if self.debug:
                    traceback.print_exc()
            time.sleep(15)

    # ---------- lettura (file -> dati) ----------
    def _read_forever(self):
        pos = 0
        while True:
            try:
                if not os.path.exists(RAW_FILE):
                    time.sleep(0.5)
                    continue
                if os.path.getsize(RAW_FILE) < pos:
                    pos = 0
                with open(RAW_FILE, encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    while True:
                        line = f.readline()
                        if not line:
                            time.sleep(0.2)
                            if os.path.getsize(RAW_FILE) < pos:
                                break
                            continue
                        if not line.endswith("\n"):
                            f.seek(f.tell() - len(line))
                            time.sleep(0.1)
                            continue
                        pos = f.tell()
                        self._parse_line(line)
            except OSError:
                time.sleep(0.5)

    def _parse_line(self, line):
        """Ogni riga è una lista Python: [topic, dati] oppure [topic, json, '']."""
        try:
            item = ast.literal_eval(line.strip())
        except (ValueError, SyntaxError) as exc:
            if self.debug:
                print(f"[debug] riga non leggibile ({type(exc).__name__}): {line[:120]!r}")
            return
        if not isinstance(item, list) or len(item) < 2 or not isinstance(item[0], str):
            return
        data = item[1]
        if isinstance(data, str):
            try:
                if item[0].endswith(".z"):
                    raw = zlib.decompress(base64.b64decode(data), -zlib.MAX_WBITS)
                    data = json.loads(raw.decode("utf-8"))
                else:
                    data = json.loads(data)
            except (ValueError, zlib.error) as exc:
                if self.debug:
                    print(f"[debug] {item[0]}: decodifica fallita "
                          f"({type(exc).__name__}): {data[:80]!r}")
                return
        self.error = None
        self._handle({"topic": item[0], "data": data})

    def _handle(self, record):
        topic, p = record["topic"], record["data"]
        if not isinstance(p, dict):
            return
        if self.debug and topic not in self.seen:
            self.seen.add(topic)
            print(f"[debug] {topic}: {str(p)[:400]}")
        with self.lock:
            if topic.startswith("DriverList"):
                self._drivers(p)
            elif topic.startswith("TimingData"):
                for num, line in p.get("Lines", {}).items():
                    line = _normalize(line)
                    if line.get("InPit") or line.get("PitOut"):
                        self.pit_flag[num] = True   # giro di ingresso o uscita box
                    _merge(self.timing.setdefault(num, {}), line)
            elif topic.startswith("CarData"):
                entries = as_list(p.get("Entries", []))
                if entries:
                    for num, c in entries[-1].get("Cars", {}).items():
                        self.car[num] = c.get("Channels", {})
            elif topic.startswith("Position"):
                entries = as_list(p.get("Position", []))
                if entries:
                    for num, v in entries[-1].get("Entries", {}).items():
                        if isinstance(v, dict) and "X" in v:
                            x, y = float(v["X"]), float(v["Y"])
                            self.pos[num] = (x, y)
                            on_track = v.get("Status", "OnTrack") == "OnTrack"
                            if on_track and (x != 0 or y != 0):
                                self._collect_track(num, x, y)
            elif topic.startswith("TimingAppData"):
                for num, line in p.get("Lines", {}).items():
                    stints = as_list(line.get("Stints", []))
                    if stints:
                        self.stints[num] = stints
                        comp = str(stints[-1].get("Compound", "")).upper()
                        self.tyre[num] = TYRE_LETTER.get(comp, "-")
            elif topic.startswith("LapCount"):
                self.lap = int(p.get("CurrentLap", self.lap) or self.lap)
                self.total_laps = int(p.get("TotalLaps", self.total_laps) or self.total_laps)
            elif topic.startswith("TrackStatus"):
                status = (STATUS_BY_MESSAGE.get(str(p.get("Message", "")))
                          or STATUS_BY_CODE.get(str(p.get("Status", ""))))
                if status:
                    self.track_status = status
            elif topic.startswith("ExtrapolatedClock"):
                if p.get("Remaining"):
                    self.remaining = str(p["Remaining"])
                    self.remaining_at = time.monotonic()
                self.extrapolating = bool(p.get("Extrapolating", self.extrapolating))
            elif topic.startswith("RaceControlMessages"):
                for m in as_list(p.get("Messages", [])):
                    if not isinstance(m, dict) or not m.get("Message"):
                        continue
                    key = (m.get("Utc"), m["Message"])
                    if key not in self.rc_seen:
                        self.rc_seen.add(key)
                        self.rc_history.append({"utc": str(m.get("Utc", "")),
                                                "text": str(m["Message"])})
            elif topic.startswith("SessionData"):
                for entry in as_list(p.get("Series", [])):
                    if isinstance(entry, dict) and entry.get("QualifyingPart"):
                        self.quali_part = int(entry["QualifyingPart"])
            elif topic.startswith("SessionStatus"):
                self.session_status = str(p.get("Status", self.session_status))
            elif topic.startswith("SessionInfo"):
                meeting = (p.get("Meeting") or {}).get("Name", "")
                self.session = f"{meeting} · {p.get('Name', '')}".strip(" ·")

    def _drivers(self, p):
        for num, info in p.items():
            if isinstance(info, dict) and "Tla" in info:
                self.drivers[num] = {
                    "code": info.get("Tla", num),
                    "name": info.get("LastName", ""),
                    "color": "#" + str(info.get("TeamColour", "FFFFFF")).lstrip("#"),
                    "photo": info.get("HeadshotUrl", ""),
                }

    def _collect_track(self, num, x, y):
        if not self.track_raw or self.track_raw[0][0] == num:
            if len(self.track_raw) < MAX_TRACK_POINTS:
                self.track_raw.append((num, x, y))

    def messages(self):
        with self.lock:
            return sorted(self.rc_history, key=lambda m: m["utc"], reverse=True)

    def _remaining_now(self):
        if not self.remaining:
            return None
        try:
            secs = sum(float(v) * 60 ** i
                       for i, v in enumerate(reversed(self.remaining.split(":"))))
        except ValueError:
            return None
        if self.extrapolating:
            secs -= time.monotonic() - self.remaining_at
        secs = max(0, int(secs))
        h, rest = divmod(secs, 3600)
        m, s = divmod(rest, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    # ---------- storico per pilota ----------
    def _record(self, num, t, sectors):
        """Registra il giro completato (se nuovo) e i settori visti."""
        lt = (t.get("LastLapTime") or {}).get("Value")
        if lt and lt != self.last_lap_str.get(num):
            self.last_lap_str[num] = lt
            secs = seconds(lt)
            invalid = bool(self.pit_flag.get(num))
            self.last_invalid[num] = invalid
            if secs is not None:
                target = self.lap_invalid if invalid else self.lap_valid
                target.setdefault(num, []).append(secs)
            self.pit_flag[num] = False
        hist = self.sector_hist.setdefault(num, [[], [], []])
        for i, v in enumerate(sectors):
            if v is not None and v not in hist[i]:
                hist[i].append(v)

    # ---------- conversione nel formato dell'app ----------
    def _transform(self):
        pts = [(x, y) for _, x, y in self.track_raw]
        if len(pts) < 30:
            return None
        xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
        n = len(pts)
        minx, maxx = xs[int(n * 0.01)], xs[min(n - 1, int(n * 0.99))]
        miny, maxy = ys[int(n * 0.01)], ys[min(n - 1, int(n * 0.99))]
        w, h = TRACK_BOX
        s = min(w * 0.9 / max(maxx - minx, 1), h * 0.9 / max(maxy - miny, 1))
        ox, oy = (w - (maxx - minx) * s) / 2, (h - (maxy - miny) * s) / 2
        return lambda x, y: (ox + (x - minx) * s, oy + (y - miny) * s)

    def state(self):
        with self.lock:
            tf = self._transform()
            rows = []
            for num, info in self.drivers.items():
                t = self.timing.get(num, {})
                ch = self.car.get(num, {})
                px, py = self.pos.get(num, (None, None))
                if tf and px is not None:
                    x, y = tf(px, py)
                else:
                    x, y = TRACK_BOX[0] / 2, TRACK_BOX[1] / 2
                secs = t.get("Sectors") or {}
                sectors = [seconds((secs.get(str(i)) or {}).get("Value")) for i in range(3)]
                self._record(num, t, sectors)

                valid = self.lap_valid.get(num, [])
                hist = self.sector_hist.get(num, [[], [], []])
                stints = self.stints.get(num, [])
                pos_txt = str(t.get("Position", "99"))
                pos = int(pos_txt) if pos_txt.isdigit() else 99
                age = stints[-1].get("TotalLaps") if stints else None
                rows.append({
                    "code": info["code"], "name": info["name"], "color": info["color"],
                    "photo": info.get("photo", ""),
                    "tyre": self.tyre.get(num, "-"),
                    "tyre_age": int(age) if str(age).isdigit() else None,
                    "pit_stops": max(len(stints) - 1, 0),
                    "lap": int(t.get("NumberOfLaps", self.lap) or 0),
                    "position": pos,
                    "gap": "LEADER" if pos_txt == "1" else str(t.get("GapToLeader", "—")),
                    "interval": "LEADER" if pos_txt == "1" else _gap_seconds(
                        t.get("TimeDiffToPositionAhead")),
                    "x": x, "y": y,
                    "speed": int(ch.get("2", 0) or 0),
                    "rpm": int(ch.get("0", 0) or 0),
                    "gear": int(ch.get("3", 0) or 0),
                    "throttle": int(ch.get("4", 0) or 0),
                    "brake": 100 if str(ch.get("5", "0")) in ("1", "True", "true") else 0,
                    "last_lap": seconds((t.get("LastLapTime") or {}).get("Value")),
                    "last_lap_pit": bool(self.last_invalid.get(num)),
                    "best_lap": min(valid) if valid else None,
                    "last_sectors": sectors,
                    "best_sectors": [min(h) if h else None for h in hist],
                })

            rows.sort(key=lambda r: r["position"])
            # Miglior settore assoluto della sessione, per ciascun settore
            overall = [min((r["best_sectors"][i] for r in rows
                            if r["best_sectors"][i] is not None), default=None)
                       for i in range(3)]
            for r in rows:
                cols = []
                for i in range(3):
                    v = r["last_sectors"][i]
                    own = r["best_sectors"][i]
                    if v is None:
                        cols.append("")
                    elif overall[i] is not None and v <= overall[i] + EPS:
                        cols.append("purple")          # record assoluto
                    elif own is not None and v <= own + EPS:
                        cols.append("green")           # record personale
                    else:
                        cols.append("yellow")
                r["sector_colors"] = cols

            step = max(1, len(self.track_raw) // 200)
            tf_track = self._transform()
            track = [tf_track(x, y) for _, x, y in self.track_raw[::step]] if tf_track else []
            return {
                "session": (self.error or self.session)
                           + (f" · Q{self.quali_part}" if self.quali_part else ""),
                "lap": max([self.lap] + [r["lap"] for r in rows]),
                "total_laps": self.total_laps,
                "track_status": self.track_status,
                "track": track,
                "info": f"posizioni ricevute: {len(self.pos)} · punti pista: {len(self.track_raw)}",
                "remaining": self._remaining_now(),
                "finished": self.session_status in ("Finished", "Finalised", "Ends"),
                "live": self.error is None and self.session_status not in
                        ("", "Inactive", "Finished", "Finalised", "Ends"),
                "drivers": rows,
                "source": "live",
            }
