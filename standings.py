"""
Classifiche e calendario da Jolpica-F1 (successore pubblico e gratuito di Ergast).

Non servono abbonamenti né chiavi. L'API limita il numero di richieste, quindi
i dati vengono aggiornati al massimo ogni 10 minuti, in background.
"""
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

BASE = "https://api.jolpi.ca/ergast/f1/current"
REFRESH_SECS = 600


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _when(date, time_utc):
    """Data e ora UTC -> data e ora locali (dd/mm/yyyy HH:MM)."""
    if not date:
        return ""
    if not time_utc:
        return datetime.fromisoformat(date).strftime("%d/%m")
    dt = datetime.fromisoformat(f"{date}T{time_utc.rstrip('Z')}").replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%H:%M %d/%m")


def _ts(date, time_utc):
    """Data e ora UTC -> secondi dall'epoca (per il conto alla rovescia)."""
    if not date:
        return None
    t = (time_utc or "00:00:00Z").rstrip("Z")
    return datetime.fromisoformat(f"{date}T{t}").replace(tzinfo=timezone.utc).timestamp()


SESSION_NAMES = [
    ("FirstPractice", "Prove libere 1"),
    ("SecondPractice", "Prove libere 2"),
    ("ThirdPractice", "Prove libere 3"),
    ("SprintQualifying", "Qualifiche sprint"),
    ("Sprint", "Sprint"),
    ("Qualifying", "Qualifiche"),
    (None, "Gara"),
]


def _sessions(race):
    """Tutte le sessioni del weekend, in ordine di orario."""
    out = []
    for key, label in SESSION_NAMES:
        obj = {"date": race.get("date"), "time": race.get("time")} if key is None else race.get(key)
        if not obj or not obj.get("date"):
            continue
        out.append({"name": label, "ts": _ts(obj["date"], obj.get("time")),
                    "when": _when(obj["date"], obj.get("time"))})
    out.sort(key=lambda s: s["ts"] or 0)
    return out


class Standings:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"drivers": [], "constructors": [], "next": None, "updated": 0}
        threading.Thread(target=self._loop, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def _loop(self):
        while True:
            try:
                self.refresh()
            except Exception:
                pass            # rete assente o limite raggiunto: riprovo più tardi
            time.sleep(REFRESH_SECS)

    def refresh(self):
        table = _get("/driverstandings.json")["MRData"]["StandingsTable"]["StandingsLists"]
        drivers = []
        for s in (table[0].get("DriverStandings", []) if table else []):
            d = s["Driver"]
            drivers.append({
                "pos": int(s["position"]), "points": s["points"],
                "name": f"{d.get('givenName', '')} {d.get('familyName', '')}".strip(),
                "code": d.get("code", d.get("familyName", "")[:3].upper()),
                "team": s["Constructors"][0]["name"] if s.get("Constructors") else "",
            })

        table = _get("/constructorstandings.json")["MRData"]["StandingsTable"]["StandingsLists"]
        constructors = []
        for s in (table[0].get("ConstructorStandings", []) if table else []):
            constructors.append({
                "pos": int(s["position"]), "points": s["points"],
                "name": s["Constructor"]["name"],
            })

        races = _get("/next.json")["MRData"]["RaceTable"]["Races"]
        nxt = None
        if races:
            r = races[0]
            nxt = {
                "name": r["raceName"], "round": r["round"],
                "when": _when(r.get("date"), r.get("time")),
                "ts": _ts(r.get("date"), r.get("time")),
                "sessions": _sessions(r),
                "circuit": r["Circuit"]["circuitName"],
                "place": f"{r['Circuit']['Location']['locality']}, "
                         f"{r['Circuit']['Location']['country']}",
            }

        races = _get("/last/results.json")["MRData"]["RaceTable"]["Races"]
        last = None
        if races:
            r = races[0]
            rows = []
            for res in r.get("Results", []):
                d = res["Driver"]
                rows.append({
                    "pos": int(res["position"]),
                    "code": d.get("code", d.get("familyName", "")[:3].upper()),
                    "name": f"{d.get('givenName', '')} {d.get('familyName', '')}".strip(),
                    "team": res["Constructor"]["name"],
                    "time": (res.get("Time") or {}).get("time", res.get("status", "")),
                })
            last = {"name": r["raceName"], "round": r["round"], "rows": rows}

        with self.lock:
            self.data = {"drivers": drivers, "constructors": constructors,
                         "next": nxt, "last": last, "updated": time.time()}
