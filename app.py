"""
F1 Live Timing - applicazione desktop (PySide6).

Installazione:  pip3 install PySide6
Avvio:          python3 app.py            (simulatore)
                python3 app.py --live     (dati live, vedi server.py)

Doppio clic su un pilota in tabella: si apre la sua scheda con i dati
completi e la sua posizione sul tracciato.
"""
import sys
import threading
import time
import urllib.request

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QSize
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QFrame, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLabel, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QListWidget, QListWidgetItem,
    QTabWidget, QStackedWidget,
)
from standings import Standings
from dashboard import Dashboard

from server import Simulator
from live import LiveSource

POLL_MS = 500
SECTOR_COLORS = {"purple": "#b44cff", "green": "#27d17f", "yellow": "#ffd60a"}
TYRE_COLORS = {"S": "#ff4d4d", "M": "#ffd60a", "H": "#e8e8e8",
               "I": "#3ddc57", "W": "#2a7cff"}
GREEN, RED = "#27d17f", "#e10600"
FLAG_STYLE = {   # stato -> (colore, titolo, cosa significa)
    "GREEN": (GREEN, "BANDIERA VERDE", "Pista libera, si può girare normalmente."),
    "YELLOW": ("#ffd60a", "BANDIERA GIALLA",
               "Pericolo in pista. Vietato sorpassare in quel settore."),
    "SC": ("#ff9f1a", "SAFETY CAR",
           "Safety car in pista. Giri neutralizzati, vietato sorpassare."),
    "VSC": ("#ff9f1a", "VIRTUAL SAFETY CAR",
            "VSC attiva. Rispettare il delta, vietato sorpassare."),
    "RED": (RED, "BANDIERA ROSSA", "Sessione sospesa."),
}

QSS = """
QMainWindow, QDialog { background: #07090d; }
QWidget { color: #e6eaf2; font-family: "Helvetica Neue", Arial, sans-serif; font-size: 14px; }
QFrame#card { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
              stop:0 #151b28, stop:1 #0c1017);
              border: 1px solid #1f2736; border-radius: 14px; }
QLabel#title { font-size: 20px; font-weight: 800; letter-spacing: 3px; }
QLabel#sub { color: #7d8698; font-size: 12px; letter-spacing: 1px; }
QLabel#lap { font-size: 22px; font-weight: 800; }
QLabel#pill { background: #1f7a4d; color: #eafff4; padding: 5px 14px;
              border-radius: 12px; font-weight: 800; letter-spacing: 1px; }
QLabel#caption { color: #6f7a8f; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; }
QLabel#value { font-size: 22px; font-weight: 800; }
QTableWidget { background: #0f131b; border: 1px solid #1c2230; border-radius: 12px;
               gridline-color: transparent; alternate-background-color: #131823;
               selection-background-color: #1e2538; selection-color: #e6eaf2; }
QTableWidget::item { padding: 6px 14px; border: none; }
QHeaderView::section { background: #0f131b; color: #6f7a8f; border: none;
                       border-bottom: 1px solid #1c2230; padding: 10px;
                       font-size: 11px; font-weight: 700; letter-spacing: 1.5px; }
QProgressBar { background: #1a1f2b; border: none; border-radius: 5px; height: 10px; }
QProgressBar::chunk { background: #e10600; border-radius: 5px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #11161f; color: #7d8698; padding: 8px 14px;
               border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 4px; }
QTabBar::tab:selected { background: #1c2433; color: #e6eaf2; font-weight: 700; }
"""


FLASH_SECS = 3.0
FLASH_BG = {"best": "#123d2c",   # record personale migliorato: verde
            "up": "#15304f",     # guadagna posizioni (sorpasso): blu
            "down": "#3d1818"}   # perde posizioni: rosso
PHOTO_BYTES = {}   # url -> dati immagine scaricati (b"" se non disponibile)
PHOTO_ICONS = {}   # url -> icona già pronta per la tabella
_PHOTO_PENDING = set()


def _fetch_photo(url):
    """Scarica la foto in background, così la finestra non si blocca."""
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            PHOTO_BYTES[url] = r.read()
    except Exception:
        PHOTO_BYTES[url] = b""
    finally:
        _PHOTO_PENDING.discard(url)


def driver_icon(url):
    """Restituisce l'icona del pilota, avviando il download se serve."""
    if not url:
        return None
    if url in PHOTO_ICONS:
        return PHOTO_ICONS[url]
    data = PHOTO_BYTES.get(url)
    if data is None:
        if url not in _PHOTO_PENDING:
            _PHOTO_PENDING.add(url)
            threading.Thread(target=_fetch_photo, args=(url,), daemon=True).start()
        return None
    pixmap = QPixmap()
    icon = None
    if data and pixmap.loadFromData(data):
        icon = QIcon(pixmap.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    PHOTO_ICONS[url] = icon
    return icon


def mono(size, bold=False):
    """Font a spaziatura fissa per i tempi: le cifre restano allineate."""
    f = QFont("Menlo")
    f.setPointSize(size)
    f.setBold(bold)
    return f


def fmt(t):
    """93.456 -> '1:33.456', 58.123 -> '58.123'."""
    if t is None:
        return "—"
    m = int(t // 60)
    s = f"{t - 60 * m:06.3f}" if m else f"{t:.3f}"
    return f"{m}:{s}" if m else s


class TrackWidget(QWidget):
    """Tracciato con tutte le auto; una (focus) viene evidenziata."""

    def __init__(self, focus=None, min_height=360):
        super().__init__()
        self.focus = focus
        self.track = []
        self.info = ""
        self.cars = {}      # code -> posizione interpolata
        self.colors = {}    # code -> colore squadra
        self.setMinimumHeight(min_height)
        self.timer = QTimer(self, interval=16, timeout=self.update)
        self.timer.start()

    def set_data(self, state):
        self.track = state["track"]
        self.info = state.get("info", "")
        now = time.monotonic()
        self.colors = {d["code"]: d["color"] for d in state["drivers"]}
        for d in state["drivers"]:
            code, target = d["code"], (d["x"], d["y"])
            if code not in self.cars:
                self.cars[code] = {"from": target, "to": target, "t0": now}
            else:
                self.cars[code] = {"from": self._pos(code, now),
                                   "to": target, "t0": now}

    def _pos(self, code, now):
        c = self.cars[code]
        a = min(1.0, (now - c["t0"]) / (POLL_MS / 1000))
        fx, fy = c["from"]
        tx, ty = c["to"]
        return (fx + (tx - fx) * a, fy + (ty - fy) * a)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0b0e14"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        if not self.track:
            p.setPen(QColor("#6f7a8f"))
            p.setFont(mono(13))
            text = "Tracciato in costruzione: attendi qualche giro"
            if self.info:
                text += "\n" + self.info
            p.drawText(self.rect(), Qt.AlignCenter, text)
            p.end()
            return
        w, h = self.width(), self.height()
        s = min((w - 60) / 1000, (h - 60) / 600)
        ox, oy = (w - 1000 * s) / 2, (h - 600 * s) / 2

        def to_screen(x, y):
            return QPointF(ox + x * s, oy + y * s)

        path = QPainterPath(to_screen(*self.track[0]))
        for pt in self.track[1:]:
            path.lineTo(to_screen(*pt))
        path.closeSubpath()
        # Bordo esterno, asfalto e tratteggio centrale
        for color, width in (("#1a2130", 30 * s), ("#2b3345", 20 * s)):
            pen = QPen(QColor(color), width)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        center = QPen(QColor("#4b5773"), 1.5)
        center.setStyle(Qt.DashLine)
        p.setPen(center)
        p.drawPath(path)

        now = time.monotonic()
        p.setFont(mono(9, True))
        fm = p.fontMetrics()
        order = sorted(self.cars, key=lambda c: c == self.focus)
        for code in order:
            pt = to_screen(*self._pos(code, now))
            is_focus = code == self.focus
            r = 13 if is_focus else 8
            p.setPen(QPen(QColor("#0b0e14"), 2))
            p.setBrush(QColor(self.colors.get(code, "#ffffff")))
            p.drawEllipse(pt, r, r)
            # Etichetta in una piccola pillola scura, leggibile su ogni sfondo
            tw = fm.horizontalAdvance(code) + 12
            rect = QRectF(pt.x() + r + 2, pt.y() - 9, tw, 17)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(8, 10, 14, 225))
            p.drawRoundedRect(rect, 5, 5)
            p.setPen(QColor("#ffffff") if is_focus else QColor("#d6dce8"))
            p.drawText(rect, Qt.AlignCenter, code)
        p.end()


def tile(caption):
    """Riquadro con etichetta piccola e valore grande."""
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 10, 14, 12)
    cap = QLabel(caption)
    cap.setObjectName("caption")
    val = QLabel("—")
    val.setObjectName("value")
    val.setFont(mono(20, True))
    lay.addWidget(cap)
    lay.addWidget(val)
    return frame, val


class DriverDialog(QDialog):
    """Scheda di un singolo pilota, aggiornata in tempo reale."""

    def __init__(self, code, get_state, parent=None):
        super().__init__(parent)
        self.code = code
        self.get_state = get_state
        self.resize(1120, 700)

        self.name = QLabel()
        self.name.setStyleSheet("font-size: 26px; font-weight: 800;")
        self.sub = QLabel()
        self.sub.setObjectName("sub")
        self.pos_big = QLabel()
        self.pos_big.setFont(mono(34, True))
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self.name)
        left.addWidget(self.sub)
        head.addLayout(left)
        head.addStretch()
        head.addWidget(self.pos_big)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.tiles = {}
        spec = [("gap", "DISTACCO"), ("tyre", "GOMMA"), ("lap", "GIRO"),
                ("last", "ULTIMO GIRO"), ("best", "MIGLIOR GIRO"), ("s1", "SETTORE 1"),
                ("s2", "SETTORE 2"), ("s3", "SETTORE 3"), ("speed", "VELOCITÀ"),
                ("gear", "MARCIA"), ("rpm", "GIRI MOTORE"),
                ("interval", "INTERVALLO"), ("stops", "SOSTE"), ("age", "ETÀ GOMMA"),
                ("thr_txt", "ACCELERATORE")]
        for i, (key, cap) in enumerate(spec):
            frame, val = tile(cap)
            self.tiles[key] = val
            grid.addWidget(frame, i // 2, i % 2)

        self.thr = QProgressBar()
        self.brk = QProgressBar()
        for bar in (self.thr, self.brk):
            bar.setRange(0, 100)
            bar.setTextVisible(False)
        self.brk.setStyleSheet("QProgressBar::chunk { background: #e10600; border-radius: 5px; }")
        self.thr.setStyleSheet("QProgressBar::chunk { background: #27d17f; border-radius: 5px; }")

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addLayout(head)
        right.addLayout(grid)
        right.addWidget(self._caption("ACCELERATORE"))
        right.addWidget(self.thr)
        right.addWidget(self._caption("FRENO"))
        right.addWidget(self.brk)
        right.addStretch()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        layout.addLayout(right)

        self.timer = QTimer(self, interval=POLL_MS, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    @staticmethod
    def _caption(text):
        lbl = QLabel(text)
        lbl.setObjectName("caption")
        return lbl

    def refresh(self):
        state = self.get_state()
        d = next((x for x in state["drivers"] if x["code"] == self.code), None)
        if d is None:
            return
        t = self.tiles
        self.setWindowTitle(f"{d['code']} · {d['name']}")
        self.name.setText(d["name"])
        self.name.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {d['color']};")
        self.sub.setText(f"{d['code']}   ·   {state['session']}")
        self.pos_big.setText(f"P{d['position']}")

        t["gap"].setText(d["gap"])
        t["tyre"].setText(f"● {d['tyre']}")
        t["tyre"].setStyleSheet(f"color: {TYRE_COLORS.get(d['tyre'], '#e6eaf2')};")
        t["lap"].setText(f"{d['lap']} / {state['total_laps']}")
        t["last"].setText(fmt(d["last_lap"]))
        t["best"].setText(fmt(d["best_lap"]))
        for i in range(3):
            color = SECTOR_COLORS.get(d["sector_colors"][i], "#e6eaf2")
            t[f"s{i + 1}"].setText(fmt(d["last_sectors"][i]))
            t[f"s{i + 1}"].setStyleSheet(f"color: {color};")
        t["speed"].setText(f"{d['speed']} km/h")
        t["gear"].setText(str(d["gear"]))
        t["rpm"].setText(f"{d['rpm']}")
        t["thr_txt"].setText(f"{d['throttle']}%")
        t["interval"].setText(d["interval"])
        t["stops"].setText(str(d.get("pit_stops", 0)))
        t["age"].setText(f"{d['tyre_age']} giri" if d.get("tyre_age") else "—")
        self.thr.setValue(d["throttle"])
        self.brk.setValue(d["brake"])


class MainWindow(QMainWindow):
    COLUMNS = ["P", "PILOTA", "GOMMA", "DIST.", "ULTIMO", "MIGLIORE", "S1", "S2", "S3"]

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.dialogs = {}
        self.msg_count = -1  # quanti comunicati sono nell'elenco
        self.standings = Standings()
        self.std_stamp = None
        self.result_sig = None
        self.was_finished = False
        self.mode_live = None
        self.prev = {}     # codice -> ultimo miglior giro e posizione visti
        self.flash = {}    # codice -> (tipo evidenziatura, fino a quando)
        self.setWindowTitle("F1 Live Timing")

        # Intestazione: titolo, progresso giri, stato pista
        self.title = QLabel("F1 LIVE TIMING")
        self.title.setObjectName("title")
        self.sub = QLabel()
        self.sub.setObjectName("sub")
        titles = QVBoxLayout()
        titles.addWidget(self.title)
        titles.addWidget(self.sub)

        self.lap = QLabel()
        self.lap.setObjectName("lap")
        self.clock = QLabel()
        self.clock.setFont(mono(20, True))
        self.clock.setStyleSheet("color: #ffd60a;")
        self.clock.hide()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(260)
        self.status = QLabel("GREEN")
        self.status.setObjectName("pill")
        self.status.setStyleSheet(f"background: {GREEN};")

        header = QFrame()
        header.setObjectName("card")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 14, 20, 14)
        hl.addLayout(titles)
        hl.addStretch()
        hl.addWidget(self.clock)
        hl.addSpacing(24)
        hl.addWidget(self.lap)
        hl.addSpacing(16)
        hl.addWidget(self.progress)
        hl.addSpacing(16)
        hl.addWidget(self.status)

        self.flag_card = QFrame()
        self.flag_card.setObjectName("card")
        fl = QVBoxLayout(self.flag_card)
        fl.setContentsMargins(24, 24, 24, 24)
        fl.setSpacing(12)
        self.flag_strip = QFrame()
        self.flag_strip.setFixedHeight(14)
        self.flag_big = QLabel()
        self.flag_desc = QLabel()
        self.flag_desc.setObjectName("sub")
        self.flag_desc.setWordWrap(True)
        fl.addWidget(self.flag_strip)
        fl.addWidget(self.flag_big)
        fl.addWidget(self.flag_desc)
        fl.addStretch()

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(40, 40))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)  # colonne uniformi
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 250)                        # nome pilota
        hh.setDefaultAlignment(Qt.AlignCenter)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setMinimumWidth(620)
        self.table.cellDoubleClicked.connect(self.open_driver)

        body = self.body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self.table, 1)      # classifica: occupa lo spazio disponibile

        list_style = ("QListWidget { background: transparent; border: none; font-size: 13px; }"
                      "QListWidget::item { padding: 8px 4px; border-bottom: 1px solid #1f2736; }")
        self.side = QTabWidget()
        self.side.setFixedWidth(360)
        self.msg_list = QListWidget()
        self.msg_list.setWordWrap(True)
        self.msg_list.setStyleSheet(list_style)
        self.result_list = QListWidget()
        self.result_list.setStyleSheet(list_style)
        self.champ_list = QListWidget()
        self.champ_list.setStyleSheet(list_style)
        self.next_list = QListWidget()
        self.next_list.setStyleSheet(list_style)
        self.side.addTab(self.msg_list, "Comunicati")
        self.side.addTab(self.result_list, "Ultima gara")
        self.side.addTab(self.champ_list, "Campionato")
        self.side.addTab(self.next_list, "Prossimo GP")

        # Colonna fissa dei comunicati nella vista live, a destra della classifica
        self.side.removeTab(self.side.indexOf(self.msg_list))
        self.msg_frame = QFrame()
        self.msg_frame.setObjectName("card")
        self.msg_frame.setFixedWidth(340)
        mc = QVBoxLayout(self.msg_frame)
        mc.setContentsMargins(16, 14, 16, 14)
        cap = QLabel("COMUNICAZIONI")
        cap.setObjectName("caption")
        mc.addWidget(cap)
        mc.addWidget(self.msg_list, 1)
        self.msg_list.show()   # la scheda precedente l'aveva nascosta
        body.addWidget(self.msg_frame)


        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        layout.addWidget(header)
        layout.addLayout(body, 1)
        self.live_page = QWidget()
        self.live_page.setLayout(layout)
        self.dashboard = Dashboard()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.live_page)
        self.stack.addWidget(self.dashboard)
        self.setCentralWidget(self.stack)

        self.toast_queue = []
        self.toast = QLabel(self.live_page)
        self.toast.setWordWrap(True)
        self.toast.setStyleSheet(
            "background: #1b2233; color: #ffffff; border: 2px solid #ffd60a;"
            " border-radius: 12px; padding: 14px 18px; font-size: 15px; font-weight: 600;")
        self.toast.hide()
        self.toast_timer = QTimer(self, singleShot=True, timeout=self._next_toast)

        self.timer = QTimer(self, interval=POLL_MS, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def cell(self, text, color=None, bold=False, align=Qt.AlignCenter,
             use_mono=True):
        item = QTableWidgetItem(text)
        item.setTextAlignment(align)
        font = mono(14, bold) if use_mono else QFont()
        if not use_mono:
            font.setPointSize(16)
            font.setBold(bold)
        item.setFont(font)
        if color:
            item.setForeground(QColor(color))
        return item

    def refresh(self):
        state = self.source.state()
        self._update_messages()
        self._update_standings()
        self._apply_mode(state.get("live", True))
        self.sub.setText(state["session"].upper())
        color, title, desc = FLAG_STYLE.get(state["track_status"], FLAG_STYLE["GREEN"])
        self.flag_strip.setStyleSheet(f"background: {color}; border-radius: 6px;")
        self.flag_big.setText(title)
        self.flag_big.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: 800;")
        self.flag_desc.setText(desc)
        self.status.setText(title)
        self.status.setToolTip(desc)
        self.status.setStyleSheet(
            f"background: {color}; color: #0b0d12; padding: 5px 14px;"
            " border-radius: 12px; font-weight: 800;")
        live = state.get("live", True)
        remaining = state.get("remaining")
        self.clock.setText(f"⏱ {remaining}" if remaining else "")
        self.clock.setVisible(live and bool(remaining))
        total = state["total_laps"]
        self.lap.setText(f"GIRO {state['lap']} / {total}" if total else f"GIRO {state['lap']}")
        self.lap.setVisible(live)
        self.progress.setVisible(live and bool(total))
        self.progress.setRange(0, total or 1)
        self.progress.setValue(state["lap"])

        drivers = state["drivers"]
        self.table.setRowCount(len(drivers))
        for r, d in enumerate(drivers):
            sc = d["sector_colors"]
            last_best = d["last_lap"] is not None and d["last_lap"] == d["best_lap"]
            items = [
                self.cell(str(d["position"]), bold=True, align=Qt.AlignCenter, color="#ffffff"),
                self.cell(f"{d['code']}   {d['name']}", color=d["color"], bold=True,
                          align=Qt.AlignLeft | Qt.AlignVCenter, use_mono=False),
                self.cell(f"● {d['tyre']}"
                          + (f"  {d['tyre_age']}g" if d.get("tyre_age") else ""),
                          color=TYRE_COLORS.get(d["tyre"]),
                          align=Qt.AlignCenter),
                self.cell(d["interval"], color="#ffffff" if d["position"] == 1 else None),
                self.cell(fmt(d["last_lap"]) + (" box" if d.get("last_lap_pit") else ""),
                          color=("#7d8698" if d.get("last_lap_pit")
                                 else (GREEN if last_best else None))),
                self.cell(fmt(d["best_lap"])),
                self.cell(fmt(d["last_sectors"][0]), color=SECTOR_COLORS.get(sc[0]),
                          bold=bool(sc[0])),
                self.cell(fmt(d["last_sectors"][1]), color=SECTOR_COLORS.get(sc[1]),
                          bold=bool(sc[1])),
                self.cell(fmt(d["last_sectors"][2]), color=SECTOR_COLORS.get(sc[2]),
                          bold=bool(sc[2])),
            ]
            items[1].setData(Qt.UserRole, d["code"])
            icon = driver_icon(d.get("photo"))
            if icon:
                items[1].setIcon(icon)
            flash = self._update_flash(d)
            if flash == "best":
                items[5].setText("★ " + fmt(d["best_lap"]))
            elif flash in ("up", "down"):
                items[0].setText(("▲ " if flash == "up" else "▼ ") + str(d["position"]))
            if flash:
                for it in items:
                    it.setBackground(QBrush(QColor(FLASH_BG[flash])))
            for c, it in enumerate(items):
                self.table.setItem(r, c, it)

    def _update_results(self, state):
        """A gara conclusa mostra la classifica finale e apre automaticamente la scheda."""
        finished = bool(state.get("finished"))
        if finished and not self.was_finished:
            self.side.setCurrentWidget(self.result_list)
        self.was_finished = finished
        if not finished:
            return
        rows = state["drivers"]
        sig = tuple((d["code"], d["position"], d["gap"]) for d in rows)
        if sig == self.result_sig:
            return
        self.result_sig = sig
        self.result_list.clear()
        head = QListWidgetItem("CLASSIFICA FINALE")
        head.setForeground(QColor(RED))
        head.setFlags(Qt.NoItemFlags)
        self.result_list.addItem(head)
        for d in rows:
            item = QListWidgetItem(f"{d['position']:>2}   {d['code']}   {d['name']}"
                                   f"\n        {d['gap']}")
            item.setForeground(QColor(d["color"]))
            self.result_list.addItem(item)

    def _update_standings(self):
        data = self.standings.snapshot()
        if data["updated"] == self.std_stamp:
            return
        self.std_stamp = data["updated"]
        self.dashboard.set_data(data)
        self.result_list.clear()
        last = data.get("last")
        if last:
            head = QListWidgetItem(f"{last['name']} · Round {last['round']}")
            head.setFlags(Qt.NoItemFlags)
            head.setForeground(QColor(RED))
            self.result_list.addItem(head)
            for r in last["rows"]:
                self.result_list.addItem(f"{r['pos']:>2}  {r['code']}  {r['name']}"
                                         f"   {r['time']}")
        else:
            self.result_list.addItem("Risultato non disponibile")
        self.champ_list.clear()
        if not data["drivers"]:
            self.champ_list.addItem("Classifiche non disponibili (controlla la connessione)")
        else:
            head = QListWidgetItem("PILOTI")
            head.setFlags(Qt.NoItemFlags)
            head.setForeground(QColor(RED))
            self.champ_list.addItem(head)
            for s in data["drivers"][:20]:
                self.champ_list.addItem(f"{s['pos']:>2}  {s['code']}  {s['name']}"
                                        f"   {s['points']} pt")
            head = QListWidgetItem("COSTRUTTORI")
            head.setFlags(Qt.NoItemFlags)
            head.setForeground(QColor(RED))
            self.champ_list.addItem(head)
            for s in data["constructors"]:
                self.champ_list.addItem(f"{s['pos']:>2}  {s['name']}   {s['points']} pt")
        self.next_list.clear()
        nxt = data["next"]
        if nxt:
            for line in (f"Round {nxt['round']}", nxt["name"], nxt["circuit"],
                         nxt["place"], f"Quando: {nxt['when']} (ora locale)"):
                self.next_list.addItem(line)
        else:
            self.next_list.addItem("Calendario non disponibile")

    def _apply_mode(self, live):
        """Con sessione in corso: timing live. Altrimenti: dashboard."""
        self.stack.setCurrentWidget(self.live_page if live else self.dashboard)

    def _update_messages(self):
        msgs = getattr(self.source, "messages", lambda: [])()
        if len(msgs) == self.msg_count:
            return                      # niente di nuovo: non ricostruisco l'elenco
        self.msg_count = len(msgs)
        self.msg_list.clear()
        for m in msgs:
            when = m["utc"][11:16] if len(m["utc"]) >= 16 else ""
            text = m["text"]
            item = QListWidgetItem(f"{when}   {text}")
            up = text.upper()
            if "RED" in up:
                color = RED
            elif "SAFETY" in up or "VSC" in up:
                color = "#ff9f1a"
            elif "YELLOW" in up:
                color = "#ffd60a"
            else:
                color = "#e6eaf2"
            item.setForeground(QColor(color))
            self.msg_list.addItem(item)

    def show_toast(self, text):
        """Accoda un comunicato: se nessun popup è visibile, lo mostra subito."""
        self.toast_queue.append(text)
        if not self.toast.isVisible():
            self._next_toast()

    def _next_toast(self):
        if not self.toast_queue:
            self.toast.hide()
            return
        self.toast.setText(self.toast_queue.pop(0))
        self.toast.setFixedWidth(min(560, self.centralWidget().width() - 60))
        self.toast.adjustSize()
        central = self.centralWidget()
        self.toast.move(central.width() - self.toast.width() - 30,
                        central.height() - self.toast.height() - 30)
        self.toast.show()
        self.toast.raise_()
        self.toast_timer.start(7000)   # resta visibile 7 secondi

    def _update_flash(self, d):
        """Confronta con il giro precedente e restituisce l'evidenziatura attiva, se c'è."""
        now = time.monotonic()
        code = d["code"]
        prev = self.prev.get(code)
        if prev:
            best, pos = d["best_lap"], d["position"]
            if best is not None and prev["best"] is not None and best < prev["best"] - 1e-6:
                self.flash[code] = ("best", now + FLASH_SECS)
            if pos != 99 and prev["pos"] != 99 and pos != prev["pos"]:
                self.flash[code] = ("up" if pos < prev["pos"] else "down",
                                    now + FLASH_SECS)
        self.prev[code] = {"best": d["best_lap"], "pos": d["position"]}
        active = self.flash.get(code)
        if active and active[1] > now:
            return active[0]
        self.flash.pop(code, None)
        return None

    def open_driver(self, row, _col):
        code = self.table.item(row, 1).data(Qt.UserRole)
        dlg = self.dialogs.get(code)
        if dlg is None:
            dlg = DriverDialog(code, self.source.state, self)
            self.dialogs[code] = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    source = (LiveSource(debug="--debug" in sys.argv)
              if "--live" in sys.argv else Simulator())
    win = MainWindow(source)
    win.resize(1360, 920)
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
