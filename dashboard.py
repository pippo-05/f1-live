"""
Dashboard mostrata quando non c'è nessuna sessione in corso.

Tre colonne, tutto visibile insieme:
- ultima gara (classifica ufficiale)
- campionato piloti
- costruttori e prossimo Gran Premio con conto alla rovescia
"""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

# Colori delle squadre (palette indicativa, usata solo per l'aspetto)
TEAM_COLORS = [
    ("mercedes", "#27F4D2"), ("ferrari", "#E8002D"), ("mclaren", "#FF8000"),
    ("red bull", "#3671C6"), ("rb f1", "#6692FF"), ("alpine", "#FF87BC"),
    ("haas", "#B6BABD"), ("aston", "#229971"), ("williams", "#64C4FF"),
    ("audi", "#52E252"), ("sauber", "#52E252"), ("cadillac", "#C0C0C0"),
]
TEXT = "#e6eaf2"
MUTED = "#7d8698"
ACCENT = "#e10600"


def team_color(name):
    low = (name or "").lower()
    for key, color in TEAM_COLORS:
        if key in low:
            return color
    return TEXT


def surname(full_name):
    parts = (full_name or "").split()
    return parts[-1] if parts else ""


def caption(text):
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 2px;")
    return lbl


def make_table(headers, widths, row_h=30):
    """widths: larghezza fissa in pixel, oppure None per la colonna che si allarga."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(row_h)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    t.setFocusPolicy(Qt.NoFocus)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setFrameShape(QFrame.Shape.NoFrame)
    t.setStyleSheet("QHeaderView::section { padding: 6px 8px; font-size: 10px; }"
                    "QTableWidget { font-size: 13px; }"
                    "QTableWidget::item { padding: 0 6px; }")
    hh = t.horizontalHeader()
    hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    for i, w in enumerate(widths):
        if w is None:
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        else:
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            t.setColumnWidth(i, w)
    return t


def cell(text, color=None, bold=False, mono=False, right=False):
    item = QTableWidgetItem(str(text))
    align = Qt.AlignRight if right else Qt.AlignLeft
    item.setTextAlignment(align | Qt.AlignVCenter)
    font = QFont("Menlo" if mono else "Helvetica Neue")
    font.setBold(bold)
    item.setFont(font)
    if color:
        item.setForeground(QColor(color))
    return item


def fill(table, rows):
    table.setRowCount(len(rows))
    table.scrollToTop()
    for r, row in enumerate(rows):
        for c, item in enumerate(row):
            table.setItem(r, c, item)


def card(*widgets, stretch=1):
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    for w in widgets:
        if isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QWidget) or isinstance(w, QVBoxLayout):
            lay.addWidget(w) if isinstance(w, QWidget) else lay.addLayout(w)
    return frame, lay


class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.next_ts = None

        # Intestazione
        title = QLabel("F1  DASHBOARD")
        title.setStyleSheet("font-size: 22px; font-weight: 800; letter-spacing: 4px;")
        self.sub = QLabel("Nessuna sessione in corso")
        self.sub.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.pill = QLabel("OFF")
        self.pill.setStyleSheet("background: #2a3140; color: #e6eaf2; padding: 4px 12px;"
                                " border-radius: 10px; font-weight: 800; font-size: 11px;")
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(title)
        left.addWidget(self.sub)
        head.addLayout(left)
        head.addStretch()
        head.addWidget(self.pill)
        head_frame, _ = card()
        head_frame.layout().addLayout(head)

        # Ultima gara
        self.last_title = QLabel("—")
        self.last_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.last_table = make_table(["P", "PILOTA", "SQUADRA", "TEMPO"],
                                     [52, None, 150, 140])
        last_frame, _ = card(caption("Ultima gara"), self.last_title, self.last_table)
        last_frame.layout().setStretch(2, 1)

        # Campionato piloti
        self.drv_table = make_table(["P", "PILOTA", "SQUADRA", "PUNTI"],
                                    [52, None, 150, 70])
        drv_frame, _ = card(caption("Campionato piloti"), self.drv_table)
        drv_frame.layout().setStretch(1, 1)

        # Costruttori
        self.con_table = make_table(["P", "SQUADRA", "PUNTI"], [52, None, 70], row_h=28)
        con_frame, _ = card(caption("Costruttori"), self.con_table)
        con_frame.layout().setStretch(1, 1)

        # Prossimo appuntamento: prossima sessione e programma del weekend
        self.next_round = QLabel("")
        self.next_round.setStyleSheet(f"color: {ACCENT}; font-size: 12px; font-weight: 800;"
                                      " letter-spacing: 2px;")
        self.next_name = QLabel("—")
        self.next_name.setStyleSheet("font-size: 20px; font-weight: 800;")
        self.next_name.setWordWrap(True)
        self.next_place = QLabel("")
        self.next_place.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        self.next_session = QLabel("—")
        self.next_session.setStyleSheet("color: #ffd60a; font-size: 16px; font-weight: 700;")
        self.countdown = QLabel("")
        self.countdown.setStyleSheet("font-size: 26px; font-weight: 800;")
        self.weekend = make_table(["SESSIONE", "ORARIO", "STATO"], [None, 150, 70])
        next_frame, _ = card(caption("Prossimo appuntamento"), self.next_round,
                             self.next_name, self.next_place, 8,
                             caption("Prossima sessione"), self.next_session,
                             self.countdown, 10, caption("Programma weekend"), self.weekend)
        next_frame.layout().setStretch(next_frame.layout().count() - 1, 1)

        # Griglia: ultima gara e piloti a tutta altezza, a destra prossimo GP e costruttori
        grid = QGridLayout()
        grid.setSpacing(14)
        grid.addWidget(last_frame, 0, 0, 2, 1)
        grid.addWidget(drv_frame, 0, 1, 2, 1)
        grid.addWidget(next_frame, 0, 2)
        grid.addWidget(con_frame, 1, 2)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 5)
        grid.setColumnStretch(2, 4)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)
        root.addWidget(head_frame)
        root.addLayout(grid, 1)

        self.tick = QTimer(self, interval=30000, timeout=self._update_countdown)
        self.tick.start()

    def set_data(self, data):
        if data.get("updated"):
            stamp = time.strftime("%H:%M", time.localtime(data["updated"]))
            self.sub.setText(f"Aggiornato alle {stamp} · dati ufficiali Formula 1")

        last = data.get("last")
        if last:
            self.last_title.setText(f"{last['name']}  ·  Round {last['round']}")
            rows = []
            for r in last["rows"]:
                tc = team_color(r["team"])
                rows.append([cell(r["pos"], TEXT, bold=True),
                             cell(f"{r['code']}  {surname(r['name'])}", tc, bold=True),
                             cell(r["team"], tc),
                             cell(r["time"], TEXT, mono=True, right=True)])
            fill(self.last_table, rows)
        else:
            self.last_title.setText("Risultato non disponibile")
            fill(self.last_table, [])

        fill(self.drv_table, [
            [cell(s["pos"], TEXT, bold=True),
             cell(f"{s['code']}  {surname(s['name'])}", team_color(s["team"]), bold=True),
             cell(s["team"], team_color(s["team"])),
             cell(s["points"], TEXT, bold=True, right=True)]
            for s in data["drivers"]])

        fill(self.con_table, [
            [cell(s["pos"], TEXT, bold=True),
             cell(s["name"], team_color(s["name"]), bold=True),
             cell(s["points"], TEXT, bold=True, right=True)]
            for s in data["constructors"]])

        nxt = data.get("next")
        now = time.time()
        if nxt:
            self.next_round.setText(f"ROUND {nxt['round']}")
            self.next_name.setText(nxt["name"])
            self.next_place.setText(f"{nxt['circuit']}  ·  {nxt['place']}")
            sessions = nxt.get("sessions", [])
            upcoming = [x for x in sessions if x["ts"] and x["ts"] > now]
            cur = upcoming[0] if upcoming else None
            self.next_session.setText(f"{cur['name']}  ·  {cur['when']}" if cur
                                      else "Weekend concluso")
            self.next_ts = cur["ts"] if cur else None
            rows = []
            for x in sessions:
                is_cur = x is cur
                state = "▶" if is_cur else ("✓" if (x["ts"] or 0) <= now else "")
                rows.append([cell(x["name"], ACCENT if is_cur else TEXT, bold=is_cur),
                             cell(x["when"], TEXT, mono=True),
                             cell(state, MUTED)])
            fill(self.weekend, rows)
        else:
            self.next_round.setText("")
            self.next_name.setText("Calendario non disponibile")
            self.next_place.setText("")
            self.next_session.setText("")
            self.next_ts = None
            fill(self.weekend, [])
        self._update_countdown()

    def _update_countdown(self):
        if not self.next_ts:
            self.countdown.setText("")
            return
        left = int(self.next_ts - time.time())
        if left <= 0:
            self.countdown.setText("In corso o concluso")
            return
        d, rest = divmod(left, 86400)
        h, rest = divmod(rest, 3600)
        m = rest // 60
        self.countdown.setText(f"{d} g  {h:02d} h  {m:02d} min")
