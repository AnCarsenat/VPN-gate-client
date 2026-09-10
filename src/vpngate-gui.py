#!/usr/bin/env python3
import os
import sys

# Resolve the real directory of this script so the sibling core module and the
# assets/ tree are found whether the script is run from the repo, from an
# installed copy, or through the /usr/bin symlink.
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

REQUIREMENTS = os.path.join(SCRIPT_DIR, "assets", "requirements.txt")
REQUIRED_MODULES = {"requests": "python-requests", "PyQt6": "python-pyqt6"}


def check_dependencies():
    """Exit with install instructions if a required third-party module is missing."""
    missing = [(module, pkg) for module, pkg in REQUIRED_MODULES.items()
               if not _importable(module)]
    if not missing:
        return

    print("Missing dependencies: " + ", ".join(module for module, _ in missing))
    print("On Arch Linux:")
    print("    sudo pacman -S " + " ".join(pkg for _, pkg in missing))
    print("Elsewhere, inside a virtualenv:")
    print("    pip install -r " + REQUIREMENTS)
    sys.exit(1)


def _importable(module):
    try:
        __import__(module)
    except ImportError:
        return False
    return True


check_dependencies()

import json
import re

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QTableView, QPushButton, QLabel,
                             QHeaderView, QMessageBox, QSystemTrayIcon, QMenu,
                             QLineEdit, QAbstractItemView, QDialog,
                             QTableWidget, QTableWidgetItem, QDialogButtonBox)
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QTimer, QAbstractTableModel,
                          QModelIndex, QSortFilterProxyModel, QEvent)
from PyQt6.QtGui import (QIcon, QAction, QActionGroup, QColor, QKeySequence,
                         QPalette)

import vpngate_core as vpncore

ICON_DIR = os.path.join(SCRIPT_DIR, "assets", "icons")
ICON_256 = os.path.join(ICON_DIR, "256.png")
ICON_64 = os.path.join(ICON_DIR, "64.png")
ICON_32 = os.path.join(ICON_DIR, "32.png")

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "vpn-gate-client")
FAVOURITES_FILE = os.path.join(CONFIG_DIR, "favourites.json")


def load_app_icon():
    """Prefer the installed hicolor theme icon, fall back to the bundled PNGs."""
    icon = QIcon.fromTheme("vpngate-gui")
    if not icon.isNull():
        return icon

    for path in (ICON_256, ICON_64, ICON_32):
        if os.path.exists(path):
            return QIcon(path)

    return QIcon.fromTheme("network-vpn")


def country_flag(country_short):
    """Turn an ISO 3166-1 alpha-2 code into its regional-indicator flag emoji."""
    code = (country_short or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------

class Favourites:
    """Favourite servers, keyed by HostName and persisted under XDG config.

    HostName is the key rather than IP: a full API fetch returns unique
    HostName values but can repeat an IP, and VPN Gate rotates IPs between
    fetches.
    """

    def __init__(self):
        self._hosts = set()
        self.load()

    def load(self):
        try:
            with open(FAVOURITES_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                self._hosts = {str(host) for host in data}
        except (OSError, ValueError):
            self._hosts = set()

    def save(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(FAVOURITES_FILE, "w", encoding="utf-8") as handle:
                json.dump(sorted(self._hosts), handle, indent=2)
        except OSError as error:
            print(f"Could not save favourites: {error}")

    def contains(self, server):
        return server.get("HostName", "") in self._hosts

    def toggle(self, server):
        host = server.get("HostName", "")
        if not host:
            return False
        if host in self._hosts:
            self._hosts.discard(host)
        else:
            self._hosts.add(host)
        self.save()
        return host in self._hosts


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class Worker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, action, server=None, proto=None):
        super().__init__()
        self.action = action
        self.server = server
        self.proto = proto

    def run(self):
        try:
            if self.action == "connect":
                success, msg = vpncore.connect_vpn(self.server, force_proto=self.proto)
            else:
                success, msg = vpncore.disconnect_vpn()
            self.finished.emit(success, msg)
        except Exception as e:
            self.finished.emit(False, str(e))


class FetchWorker(QThread):
    """Fetch the server list off the GUI thread, emitting rows as they land.

    The API is one large response, so nothing can make the download itself
    finish sooner. Emitting each parsed batch lets the table fill from the
    first second rather than staying empty until the body is complete.
    """
    batch_ready = pyqtSignal(object)
    fetched = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self):
        try:
            servers = vpncore.stream_servers(
                on_batch=self.batch_ready.emit,
                should_stop=self.isInterruptionRequested)
            if self.isInterruptionRequested():
                return
            self.fetched.emit(servers)
        except Exception as e:
            print(f"Fetch failed: {e}")
            self.failed.emit(str(e))
            self.fetched.emit([])


class StatsWorker(QThread):
    stats_updated = pyqtSignal(object)

    def run(self):
        stats = vpncore.get_stats()
        self.stats_updated.emit(stats)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

COL_FAV, COL_COUNTRY, COL_PING, COL_RATING, COL_IP, COL_PROTO = range(6)

# Rating tiers, worst to best. Score is a raw integer from the API, roughly
# 0 - 3,000,000 across a full fetch.
RATING_STEP = 600000
RATING_LABELS = ["Very low", "Low", "Fair", "Good", "Excellent"]

# Ping thresholds in ms, best to worst.
PING_TIERS = [(60, 0), (120, 1), (200, 2), (400, 3)]


def server_ping(server):
    try:
        return int(server.get("Ping", "0"))
    except (TypeError, ValueError):
        return 9999


def server_score(server):
    try:
        return int(server.get("Score", "0"))
    except (TypeError, ValueError):
        return 0


def rating_tier(server):
    return min(server_score(server) // RATING_STEP, 4)


class ServerModel(QAbstractTableModel):
    HEADERS = ["★", "Country", "Ping", "Rating", "IP", "Proto"]

    def __init__(self, favourites, parent=None):
        super().__init__(parent)
        self._servers = []
        self._favourites = favourites
        self._prefer_tcp = False
        self._best_to_worst = []
        self._worst_to_best = []

    # -- data plumbing ------------------------------------------------------

    def set_servers(self, servers):
        self.beginResetModel()
        self._servers = list(servers)
        self.endResetModel()

    def append_servers(self, servers):
        """Add a streamed batch without resetting the view."""
        servers = list(servers)
        if not servers:
            return
        first = len(self._servers)
        self.beginInsertRows(QModelIndex(), first, first + len(servers) - 1)
        self._servers.extend(servers)
        self.endInsertRows()

    def set_prefer_tcp(self, prefer_tcp):
        """Protocol preference changes the Proto column for dual-stack servers."""
        self._prefer_tcp = prefer_tcp
        self.refresh_all()

    def set_palette_colours(self, palette):
        """Pick tier colours that stay legible against the active theme."""
        dark = palette.color(QPalette.ColorRole.Base).lightness() < 128
        if dark:
            self._worst_to_best = ["#e74c3c", "#e67e22", "#f1c40f", "#7ed957", "#2ecc71"]
        else:
            self._worst_to_best = ["#c0392b", "#b05a00", "#8a7000", "#3f9142", "#1e8449"]
        self._best_to_worst = list(reversed(self._worst_to_best))
        self.refresh_all()

    def refresh_all(self):
        if self._servers:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._servers) - 1, len(self.HEADERS) - 1))

    def refresh_row(self, row):
        if 0 <= row < len(self._servers):
            self.dataChanged.emit(self.index(row, 0),
                                  self.index(row, len(self.HEADERS) - 1))

    def server_at(self, row):
        if 0 <= row < len(self._servers):
            return self._servers[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._servers)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)

    # -- per-column values --------------------------------------------------

    def protocol(self, server):
        if not server.get("has_udp"):
            return "TCP"
        if self._prefer_tcp and server.get("has_tcp"):
            return "TCP"
        return "UDP"

    def display(self, server, col):
        if col == COL_FAV:
            return "★" if self._favourites.contains(server) else ""
        if col == COL_COUNTRY:
            flag = country_flag(server.get("CountryShort", ""))
            code = server.get("CountryShort", "??")
            return f"{flag} {code}".strip()
        if col == COL_PING:
            ping = server_ping(server)
            return "n/a" if ping >= 9999 else f"{ping} ms"
        if col == COL_RATING:
            return RATING_LABELS[rating_tier(server)]
        if col == COL_IP:
            return server.get("IP", "")
        if col == COL_PROTO:
            return self.protocol(server)
        return ""

    def sort_key(self, server, col):
        if col == COL_FAV:
            return 1 if self._favourites.contains(server) else 0
        if col == COL_COUNTRY:
            return server.get("CountryShort", "")
        if col == COL_PING:
            return server_ping(server)
        if col == COL_RATING:
            return server_score(server)
        if col == COL_IP:
            # Sort dotted quads numerically rather than lexically. Zero-padded
            # octets, not a packed integer: anything above 127.x.x.x exceeds
            # the 32-bit int a QVariant carries and wraps negative, and a tuple
            # does not survive the round trip at all.
            parts = server.get("IP", "").split(".")
            if len(parts) != 4:
                return ""
            try:
                return "".join(f"{int(part):03d}" for part in parts)
            except ValueError:
                return ""
        if col == COL_PROTO:
            return self.protocol(server)
        return ""

    def colour(self, server, col):
        if not self._worst_to_best:
            return None
        if col == COL_RATING:
            return QColor(self._worst_to_best[rating_tier(server)])
        if col == COL_PING:
            ping = server_ping(server)
            tier = 4
            for threshold, index in PING_TIERS:
                if ping < threshold:
                    tier = index
                    break
            return QColor(self._best_to_worst[tier])
        if col == COL_PROTO:
            return QColor("#3498db") if self.protocol(server) == "UDP" else QColor("#e67e22")
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        server = self._servers[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return self.display(server, col)
        if role == Qt.ItemDataRole.UserRole:
            return self.sort_key(server, col)
        if role == Qt.ItemDataRole.ForegroundRole:
            return self.colour(server, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_FAV, COL_PING, COL_PROTO):
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        if role == Qt.ItemDataRole.ToolTipRole:
            return (f"{server.get('CountryLong', '')}\n"
                    f"Host: {server.get('HostName', '')}\n"
                    f"Score: {server.get('Score', '0')}\n"
                    f"Sessions: {server.get('NumVpnSessions', '?')}")
        return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

QUALIFIER_RE = re.compile(
    r"@?(host|country|ip|proto|protocol|rating|ping|sort)\s*:\s*(\S+)", re.IGNORECASE)
# Longest first: "sort-desc\b" would not match inside "sort-descending", but
# keeping them ordered makes the intent obvious.
FLAG_RE = re.compile(
    r"@(sort-descending|sort-desc|descending|desc|favorite|favourite|fav|udp|tcp)\b",
    re.IGNORECASE)
FAVOURITE_FLAGS = {"favorite", "favourite", "fav"}
DESCENDING_FLAGS = {"sort-descending", "sort-desc", "descending", "desc"}

# What @sort: accepts, mapped to a column.
SORT_COLUMNS = {
    "favorite": COL_FAV, "favourite": COL_FAV, "fav": COL_FAV, "star": COL_FAV,
    "country": COL_COUNTRY, "flag": COL_COUNTRY,
    "ping": COL_PING, "latency": COL_PING,
    "rating": COL_RATING, "score": COL_RATING,
    "ip": COL_IP, "address": COL_IP,
    "proto": COL_PROTO, "protocol": COL_PROTO,
}


class ServerFilterProxy(QSortFilterProxyModel):
    """GitHub-style search: `@country:FR @favorite fast`.

    Qualifiers: @host: @country: @ip: @proto: @rating: @ping:
    Flags:      @favorite @udp @tcp
    Anything left over is free text, matched against country, IP and host.
    """

    def __init__(self, favourites, parent=None):
        super().__init__(parent)
        self._favourites = favourites
        self._qualifiers = []
        self._flags = set()
        self._free_text = ""
        # (column, order) asked for by @sort in the query, or None. The window
        # applies it; the proxy only parses it.
        self.sort_request = None
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def set_query(self, text):
        text = text or ""
        qualifiers = [(key.lower(), value.lower())
                      for key, value in QUALIFIER_RE.findall(text)]
        self._flags = {flag.lower() for flag in FLAG_RE.findall(text)}

        # @sort: steers the view rather than filtering, so keep it out of the
        # qualifiers that rows are matched against.
        self._qualifiers = [(key, value) for key, value in qualifiers if key != "sort"]
        self.sort_request = self._parse_sort(qualifiers)

        leftover = FLAG_RE.sub(" ", QUALIFIER_RE.sub(" ", text))
        self._free_text = " ".join(leftover.split()).lower()
        self.invalidateFilter()

    def _parse_sort(self, qualifiers):
        """Resolve @sort:<column> and @sort-descending into (column, order)."""
        descending = bool(self._flags & DESCENDING_FLAGS)
        order = (Qt.SortOrder.DescendingOrder if descending
                 else Qt.SortOrder.AscendingOrder)

        column = None
        for key, value in qualifiers:
            if key == "sort":
                column = SORT_COLUMNS.get(value)

        if column is None:
            # A bare @sort-descending flips the column already in use.
            return (None, order) if descending else None
        return (column, order)

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        server = model.server_at(source_row)
        if server is None:
            return False

        if self._flags & FAVOURITE_FLAGS and not self._favourites.contains(server):
            return False

        protocol = model.protocol(server).lower()
        if "udp" in self._flags and protocol != "udp":
            return False
        if "tcp" in self._flags and protocol != "tcp":
            return False

        for key, value in self._qualifiers:
            if not self._matches(model, server, key, value):
                return False

        if self._free_text:
            haystack = " ".join([
                server.get("CountryShort", ""), server.get("CountryLong", ""),
                server.get("IP", ""), server.get("HostName", ""),
            ]).lower()
            if self._free_text not in haystack:
                return False

        return True

    def _matches(self, model, server, key, value):
        if key == "host":
            return value in server.get("HostName", "").lower()
        if key == "country":
            return (value in server.get("CountryShort", "").lower()
                    or value in server.get("CountryLong", "").lower())
        if key == "ip":
            return value in server.get("IP", "").lower()
        if key in ("proto", "protocol"):
            return model.protocol(server).lower() == value
        if key == "rating":
            return value in model.display(server, COL_RATING).lower()
        if key == "ping":
            return self._compare_number(server_ping(server), value)
        return True

    @staticmethod
    def _compare_number(actual, expression):
        """Support @ping:<100, @ping:>50, and a bare @ping:40 upper bound."""
        try:
            if expression.startswith("<"):
                return actual < int(expression[1:])
            if expression.startswith(">"):
                return actual > int(expression[1:])
            return actual <= int(expression)
        except ValueError:
            return True


# Event types that mean "the theme moved under us". ThemeChange is not exposed
# by every PyQt6 build, and an AttributeError raised inside a reimplemented
# virtual is turned into an abort() with no traceback, so resolve it defensively.
THEME_EVENTS = tuple(
    event_type for event_type in (
        getattr(QEvent.Type, "PaletteChange", None),
        getattr(QEvent.Type, "ApplicationPaletteChange", None),
        getattr(QEvent.Type, "ThemeChange", None),
        getattr(QEvent.Type, "StyleChange", None),
    ) if event_type is not None)


def format_bps(value):
    """The API reports Speed in bits per second."""
    try:
        bits = float(value)
    except (TypeError, ValueError):
        return "n/a"
    for unit, scale in (("Gbps", 1e9), ("Mbps", 1e6), ("kbps", 1e3)):
        if bits >= scale:
            return f"{bits / scale:.2f} {unit}"
    return f"{bits:.0f} bps"


def format_bytes(value):
    try:
        count = float(value)
    except (TypeError, ValueError):
        return "n/a"
    for unit, scale in (("TB", 1024 ** 4), ("GB", 1024 ** 3),
                        ("MB", 1024 ** 2), ("KB", 1024)):
        if count >= scale:
            return f"{count / scale:.2f} {unit}"
    return f"{count:.0f} B"


def format_uptime(value):
    """Uptime arrives as milliseconds."""
    try:
        seconds = int(value) // 1000
    except (TypeError, ValueError):
        return "n/a"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def technical_rows(server, model):
    """Every field worth showing, raw value alongside the friendly one."""
    score = server.get("Score", "0")
    ping = server.get("Ping", "")
    speed = server.get("Speed", "0")
    protocols = ", ".join(
        [name for name, present in (("UDP", server.get("has_udp")),
                                    ("TCP", server.get("has_tcp"))) if present]) or "unknown"

    return [
        ("Host name", server.get("HostName", "")),
        ("IP address", server.get("IP", "")),
        ("Country", f"{server.get('CountryLong', '')} ({server.get('CountryShort', '')})"),
        ("Score (raw)", f"{score}  \u2192  {model.display(server, COL_RATING)}"),
        ("Ping (raw)", f"{ping or 'n/a'} ms"),
        ("Speed", f"{format_bps(speed)}   ({speed} bps)"),
        ("Protocols offered", protocols),
        ("Shown as", model.protocol(server)),
        ("VPN sessions", server.get("NumVpnSessions", "?")),
        ("Total users", server.get("TotalUsers", "?")),
        ("Total traffic", f"{format_bytes(server.get('TotalTraffic', 0))}"),
        ("Uptime", f"{format_uptime(server.get('Uptime', 0))}"),
        ("Log policy", server.get("LogType", "unknown")),
        ("Operator", server.get("Operator", "")),
        ("Message", server.get("Message", "")),
    ]


class ServerDetailsDialog(QDialog):
    """Read-only dump of everything the API reports for one server."""

    def __init__(self, server, model, parent=None):
        super().__init__(parent)
        self.server = server
        self.setWindowTitle(f"Technical details - {server.get('HostName', 'server')}")
        self.resize(560, 480)

        self.rows = technical_rows(server, model)

        layout = QVBoxLayout(self)
        table = QTableWidget(len(self.rows), 2, self)
        table.setHorizontalHeaderLabels(["Field", "Value"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(True)

        for row, (label, value) in enumerate(self.rows):
            name_item = QTableWidgetItem(label)
            font = name_item.font()
            font.setBold(True)
            name_item.setFont(font)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(str(value)))

        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.resizeRowsToContents()
        layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        copy_all = buttons.addButton("Copy all", QDialogButtonBox.ButtonRole.ActionRole)
        copy_all.clicked.connect(self.copy_all)
        copy_config = buttons.addButton("Copy OpenVPN config",
                                        QDialogButtonBox.ButtonRole.ActionRole)
        copy_config.setEnabled(bool(server.get("config_text")))
        copy_config.clicked.connect(self.copy_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def as_text(self):
        width = max(len(label) for label, _ in self.rows) + 1
        return "\n".join(f"{label + ':':<{width}} {value}" for label, value in self.rows)

    def copy_all(self):
        QApplication.clipboard().setText(self.as_text())

    def copy_config(self):
        QApplication.clipboard().setText(self.server.get("config_text", ""))


def status_colours(palette):
    """Connected / disconnected colours that stay readable in either theme."""
    if palette.color(QPalette.ColorRole.Base).lightness() < 128:
        return "#2ecc71", "#e74c3c"
    return "#1e8449", "#c0392b"


def button_for(action, parent=None):
    """A QPushButton driven by a QAction.

    QPushButton has no setDefaultAction (that is QToolButton), so mirror the
    action's label and enabled state manually. This keeps one QAction as the
    single source of truth for the menubar, the buttons and the row menu.
    """
    button = QPushButton(action.text().replace("&", ""), parent)
    button.clicked.connect(action.trigger)

    def sync():
        button.setText(action.text().replace("&", ""))
        button.setEnabled(action.isEnabled())
        button.setToolTip(action.shortcut().toString())

    sync()
    action.changed.connect(sync)
    return button


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class VPNWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # setWindowTitle below already delivers a change event, and PyQt6 turns
        # any exception raised inside a reimplemented virtual into an abort()
        # rather than a traceback. Keep this first so changeEvent can bail out
        # until the model exists.
        self.model = None

        self.setWindowTitle("VPN Gate Client")
        self.setMinimumSize(900, 600)

        self.favourites = Favourites()
        self.is_busy = False
        self.is_quitting = False
        self.vpn_active = False
        self.worker = None
        self.streaming = False
        self.is_fetching = False
        self.fetch_error = None

        self.model = ServerModel(self.favourites, self)
        self.proxy = ServerFilterProxy(self.favourites, self)
        self.proxy.setSourceModel(self.model)

        self.build_actions()
        self.build_menubar()
        self.build_body()
        self.build_tray()

        self.model.set_palette_colours(self.palette())

        self.stats_worker = StatsWorker()
        self.stats_worker.stats_updated.connect(self.on_stats_updated)
        self.fetch_worker = FetchWorker()
        self.fetch_worker.batch_ready.connect(self.on_servers_batch)
        self.fetch_worker.fetched.connect(self.on_servers_fetched)
        self.fetch_worker.failed.connect(self.on_fetch_failed)

        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.tick)
        self.stats_timer.start(3000)

        self.refresh_active_state()
        self.load_cached_servers()
        self.load_servers()

    # -- construction -------------------------------------------------------

    def build_actions(self):
        self.act_refresh = QAction("&Refresh List", self)
        self.act_refresh.setShortcut(QKeySequence("Ctrl+R"))
        self.act_refresh.triggered.connect(self.load_servers)

        self.act_connect = QAction("&Connect", self)
        self.act_connect.setShortcut(QKeySequence("Ctrl+K"))
        self.act_connect.triggered.connect(self.start_connect)

        self.act_disconnect = QAction("&Disconnect", self)
        self.act_disconnect.setShortcut(QKeySequence("Ctrl+D"))
        self.act_disconnect.triggered.connect(self.start_disconnect)

        self.act_details = QAction("Technical &details...", self)
        self.act_details.setShortcut(QKeySequence("Ctrl+I"))
        self.act_details.triggered.connect(self.show_details_for_selection)

        self.act_focus_search = QAction("&Search", self)
        self.act_focus_search.setShortcut(QKeySequence.StandardKey.Find)
        self.act_focus_search.triggered.connect(self.focus_search)

        self.act_quit = QAction("&Quit", self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_quit.triggered.connect(self.quit_app)
        self.addAction(self.act_quit)

    def build_menubar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        file_menu.addAction(self.act_refresh)
        file_menu.addAction(self.act_focus_search)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        conn_menu = menubar.addMenu("&Connection")
        conn_menu.addAction(self.act_connect)
        conn_menu.addAction(self.act_disconnect)
        conn_menu.addSeparator()
        conn_menu.addAction(self.act_details)

        prefs_menu = menubar.addMenu("&Preferences")
        proto_menu = prefs_menu.addMenu("Protocol preference")
        self.proto_group = QActionGroup(self)
        self.proto_group.setExclusive(True)
        for label, key in (("Prefer &UDP", "udp"), ("Prefer &TCP", "tcp"),
                           ("Show &All", "all")):
            action = proto_menu.addAction(label)
            action.setCheckable(True)
            action.setData(key)
            self.proto_group.addAction(action)
        self.proto_group.actions()[0].setChecked(True)
        self.proto_group.triggered.connect(self.on_proto_changed)

        prefs_menu.addSeparator()
        self.act_favourites_only = prefs_menu.addAction("Show &favourites only")
        self.act_favourites_only.setCheckable(True)
        self.act_favourites_only.toggled.connect(self.on_favourites_only)

        help_menu = menubar.addMenu("&Help")
        help_menu.addAction("Search &syntax", self.show_search_help)

    def build_body(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(
            "Search  —  @country:FR   @favorite   @ping:<100   "
            "@sort:ping   @sort-descending")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.on_search_changed)
        layout.addWidget(self.search_box)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(COL_RATING, Qt.SortOrder.DescendingOrder)
        self.view.verticalHeader().setDefaultSectionSize(24)
        self.view.doubleClicked.connect(self.on_row_activated)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.show_row_menu)

        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_COUNTRY, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_IP, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.view)

        self.status_label = QLabel("Status: DISCONNECTED")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.status_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.status_label.setFont(font)
        layout.addWidget(self.status_label)

        self.stats_label = QLabel("")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.stats_label)

        controls = QHBoxLayout()
        controls.addStretch()
        self.btn_refresh = button_for(self.act_refresh, self)
        self.btn_connect = button_for(self.act_connect, self)
        self.btn_disconnect = button_for(self.act_disconnect, self)
        for button in (self.btn_refresh, self.btn_connect, self.btn_disconnect):
            button.setMinimumHeight(34)
            button.setMinimumWidth(120)
            controls.addWidget(button)
        layout.addLayout(controls)

    def build_tray(self):
        icon = load_app_icon()
        # Deliberately no setWindowIcon: the title bar stays bare. An explicit
        # empty icon is needed because otherwise the window inherits the
        # application icon.
        self.setWindowIcon(QIcon())

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("VPN Gate Client")

        tray_menu = QMenu()
        show_action = QAction("Open Client", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(self.act_disconnect)
        tray_menu.addSeparator()
        tray_menu.addAction(self.act_quit)

        self.tray_menu = tray_menu
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    # -- events -------------------------------------------------------------

    def changeEvent(self, event):
        """Re-derive tier colours when the system theme changes under us."""
        if self.model is not None and event.type() in THEME_EVENTS:
            self.model.set_palette_colours(self.palette())
            self.update_ui_state()
        super().changeEvent(event)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show_window()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def focus_search(self):
        self.search_box.setFocus()
        self.search_box.selectAll()

    def closeEvent(self, event):
        # The X button hides to the tray. Ctrl+Q / Quit is the real exit and
        # sets is_quitting first, so this handler steps out of its way.
        if self.is_quitting:
            event.accept()
            return

        # With no tray to restore from, hiding would leave the app running with
        # no window and no way to reach it - Ctrl+Q needs a focused window.
        if not QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.quit_app()
            return

        self.hide()
        event.ignore()

    def quit_app(self):
        if self.is_quitting:
            return
        self.is_quitting = True

        # Stop everything that could restart work or block the event loop
        # before tearing the VPN down.
        self.stats_timer.stop()
        self.tray_icon.hide()
        self.status_label.setText("Status: Shutting down...")
        QApplication.processEvents()

        # Abandon an in-flight fetch rather than sitting through the download.
        self.fetch_worker.requestInterruption()

        for thread in (self.worker, self.fetch_worker, self.stats_worker):
            if thread is not None and thread.isRunning():
                thread.wait(15000)

        try:
            vpncore.disconnect_vpn()
        except Exception as error:
            print(f"Cleanup failed: {error}")

        self.close()
        QApplication.instance().quit()

    # -- state --------------------------------------------------------------

    def refresh_active_state(self):
        """The one place that shells out to nmcli, so filtering stays cheap."""
        self.vpn_active = vpncore.is_active()
        return self.vpn_active

    def tick(self):
        if self.is_busy or self.is_quitting:
            return

        # Keep watching the VPN even mid-refresh: a fetch can take seconds, and
        # the status line should not go stale if the tunnel drops during one.
        was_active = self.vpn_active
        if self.refresh_active_state() != was_active:
            self.update_ui_state()
        if self.vpn_active and not self.is_fetching and not self.stats_worker.isRunning():
            self.stats_worker.start()

    def update_ui_state(self, is_busy=None):
        if is_busy is not None:
            self.is_busy = is_busy
        active = self.vpn_active

        # A fetch writes its own progress into the status line, so only
        # overwrite it with the VPN state once nothing transient is running.
        if not self.is_busy and not self.is_fetching:
            ok_colour, bad_colour = status_colours(self.palette())
            if active:
                self.status_label.setText("Status: VPN IS ACTIVE")
                self.status_label.setStyleSheet(f"color: {ok_colour};")
            else:
                self.status_label.setText("Status: DISCONNECTED")
                self.status_label.setStyleSheet(f"color: {bad_colour};")
                self.stats_label.setText("")

        self.act_connect.setEnabled(not active and not self.is_busy)
        self.act_disconnect.setEnabled(active and not self.is_busy)
        self.act_refresh.setEnabled(not self.is_busy and not self.is_fetching)
        # Only a connect/disconnect locks the table. A fetch streams into it,
        # so it stays usable throughout.
        self.view.setEnabled(not self.is_busy)

    def on_stats_updated(self, stats):
        if stats and not self.is_busy and not self.is_quitting:
            up, down, ping, loss = stats
            self.stats_label.setText(
                f"DOWNLOAD: {down:.1f} KB/s   |   UPLOAD: {up:.1f} KB/s   |   "
                f"PING: {ping}   |   LOSS: {loss}")

    # -- servers ------------------------------------------------------------

    def load_cached_servers(self):
        """Show the previous fetch immediately, so the window is never empty."""
        servers, age = vpncore.load_cached_servers()
        if not servers:
            return
        self.model.set_servers(servers)
        minutes = int(age // 60)
        when = f"{minutes} min" if minutes < 90 else f"{minutes // 60} h"
        self.status_label.setText(
            f"Status: showing {len(servers)} cached servers ({when} old), refreshing...")

    def load_servers(self):
        if self.fetch_worker.isRunning() or self.is_quitting:
            return
        self.fetch_error = None
        self.streaming = False
        self.is_fetching = True
        if self.model.rowCount() == 0:
            self.status_label.setText("Status: fetching servers...")
        self.update_ui_state()
        self.fetch_worker.start()

    def on_servers_batch(self, batch):
        """First batch replaces the cached list; later ones extend it."""
        if self.is_quitting:
            return
        if not self.streaming:
            self.streaming = True
            self.model.set_servers(batch)
        else:
            self.model.append_servers(batch)
        self.status_label.setText(f"Status: loading... {self.model.rowCount()} servers")

    def on_fetch_failed(self, message):
        self.fetch_error = message

    def on_servers_fetched(self, servers):
        # A failed stream keeps whatever the cache gave us rather than
        # blanking the table.
        if servers:
            self.model.set_servers(servers)
        self.streaming = False
        self.is_fetching = False
        self.update_ui_state()
        if not servers and self.model.rowCount() == 0:
            self.status_label.setText("Status: Could not fetch server list")
        elif not servers:
            self.status_label.setText("Status: refresh failed, showing cached servers")

    def on_proto_changed(self, action):
        self.model.set_prefer_tcp(action.data() == "tcp")
        self.proxy.invalidateFilter()

    def on_search_changed(self, text):
        self.proxy.set_query(text)
        self.apply_sort_request()
        # Keep the menu checkbox in step with a hand-typed @favorite.
        has_flag = bool(FLAG_RE.search(text) and
                        FAVOURITE_FLAGS & {m.lower() for m in FLAG_RE.findall(text)})
        if self.act_favourites_only.isChecked() != has_flag:
            self.act_favourites_only.blockSignals(True)
            self.act_favourites_only.setChecked(has_flag)
            self.act_favourites_only.blockSignals(False)

    def apply_sort_request(self):
        """Apply @sort / @sort-descending from the query to the header."""
        request = self.proxy.sort_request
        if request is None:
            return
        column, order = request
        if column is None:
            # Bare @sort-descending: keep the column, change the direction.
            column = self.view.horizontalHeader().sortIndicatorSection()
            if column < 0:
                column = COL_RATING
        self.view.sortByColumn(column, order)

    def on_favourites_only(self, checked):
        text = self.search_box.text()
        if checked:
            if not FAVOURITE_FLAGS & {m.lower() for m in FLAG_RE.findall(text)}:
                self.search_box.setText((text + " @favorite").strip())
        else:
            cleaned = re.sub(r"@(favorite|favourite|fav)\b", " ", text, flags=re.IGNORECASE)
            self.search_box.setText(" ".join(cleaned.split()))

    def show_details_for_selection(self):
        server = self.selected_server()
        if server is None:
            QMessageBox.warning(self, "Selection Required", "Please select a server.")
            return
        self.show_details(server)

    def show_details(self, server):
        ServerDetailsDialog(server, self.model, self).exec()

    def show_search_help(self):
        QMessageBox.information(self, "Search syntax", (
            "Qualifiers:\n"
            "  @host:public-vpn-78    match the host name\n"
            "  @country:FR            country code or full name\n"
            "  @ip:219.100            match the IP\n"
            "  @proto:udp             exact protocol\n"
            "  @ping:<100             ping below a value; also >N, or a bare\n"
            "                         number meaning at most N\n"
            "  @rating:good           match the rating label\n\n"
            "Sorting:\n"
            "  @sort:ping             sort by a column - one of favorite,\n"
            "                         country, ping, rating, ip, proto\n"
            "  @sort-descending       sort the other way round; on its own it\n"
            "                         flips the column already in use\n\n"
            "Flags:\n"
            "  @favorite    @udp    @tcp\n\n"
            "Anything else is free text, matched against country, IP and host.\n\n"
            "Examples:\n"
            "  @country:JP @udp @ping:<50\n"
            "  @sort:rating @sort-descending"))

    # -- row actions --------------------------------------------------------

    def server_at_view_index(self, index):
        if not index.isValid():
            return None
        return self.model.server_at(self.proxy.mapToSource(index).row())

    def selected_server(self):
        rows = self.view.selectionModel().selectedRows()
        if not rows:
            return None
        return self.server_at_view_index(rows[0])

    def on_row_activated(self, index):
        server = self.server_at_view_index(index)
        if server is not None:
            self.connect_to(server)

    def show_row_menu(self, pos):
        # customContextMenuRequested reports viewport coordinates, and a right
        # click does not move the selection, so resolve the row under the
        # cursor rather than trusting the current selection.
        index = self.view.indexAt(pos)
        server = self.server_at_view_index(index)
        if server is None:
            return

        menu = QMenu(self)

        connect = menu.addAction("Connect")
        connect.setEnabled(not self.vpn_active and not self.is_busy)
        connect.triggered.connect(lambda: self.connect_to(server))

        disconnect = menu.addAction("Disconnect")
        disconnect.setEnabled(self.vpn_active and not self.is_busy)
        disconnect.triggered.connect(self.start_disconnect)

        menu.addSeparator()

        details = menu.addAction("Technical details...")
        details.triggered.connect(lambda: self.show_details(server))

        menu.addSeparator()

        is_fav = self.favourites.contains(server)
        fav = menu.addAction("Remove from favourites" if is_fav else "Add to favourites")
        fav.triggered.connect(lambda: self.toggle_favourite(index))

        menu.addSeparator()

        column = index.column()
        if column != COL_FAV:
            cell = menu.addAction(f"Copy {self.model.HEADERS[column]}")
            cell.triggered.connect(
                lambda: self.copy_text(self.model.display(server, column)))

        copy_menu = menu.addMenu("Copy field")
        for label, value in (
            ("Host name", server.get("HostName", "")),
            ("Country", server.get("CountryLong", "")),
            ("Ping", self.model.display(server, COL_PING)),
            ("Rating", self.model.display(server, COL_RATING)),
            ("IP", server.get("IP", "")),
            ("Protocol", self.model.protocol(server)),
        ):
            action = copy_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, text=value: self.copy_text(text))

        full = menu.addAction("Copy full row")
        full.triggered.connect(lambda: self.copy_text(self.row_as_text(server)))

        menu.exec(self.view.viewport().mapToGlobal(pos))

    def row_as_text(self, server):
        return "\n".join([
            f"Host:     {server.get('HostName', '')}",
            f"Country:  {server.get('CountryLong', '')} ({server.get('CountryShort', '')})",
            f"IP:       {server.get('IP', '')}",
            f"Ping:     {self.model.display(server, COL_PING)}",
            f"Rating:   {self.model.display(server, COL_RATING)} "
            f"(score {server.get('Score', '0')})",
            f"Protocol: {self.model.protocol(server)}",
            f"Sessions: {server.get('NumVpnSessions', '?')}",
        ])

    def copy_text(self, text):
        QApplication.clipboard().setText(str(text))

    def toggle_favourite(self, index):
        server = self.server_at_view_index(index)
        if server is None:
            return
        source_row = self.proxy.mapToSource(index).row()
        self.favourites.toggle(server)
        self.model.refresh_row(source_row)
        self.proxy.invalidateFilter()

    # -- connection ---------------------------------------------------------

    def start_connect(self, *_args):
        server = self.selected_server()
        if server is None:
            QMessageBox.warning(self, "Selection Required", "Please select a server.")
            return
        self.connect_to(server)

    def connect_to(self, server):
        if self.is_busy:
            return
        if self.refresh_active_state():
            QMessageBox.critical(self, "Error", "A VPN is already running.")
            self.update_ui_state()
            return

        checked = self.proto_group.checkedAction()
        proto = "tcp" if checked is not None and checked.data() == "tcp" else None
        self.status_label.setText(f"Status: Connecting to {server['IP']} (10s timeout)...")
        self.update_ui_state(is_busy=True)
        self.worker = self.start_worker(Worker("connect", server, proto))

    def start_disconnect(self, *_args):
        if self.is_busy:
            return
        self.status_label.setText("Status: Disconnecting...")
        self.update_ui_state(is_busy=True)
        self.worker = self.start_worker(Worker("disconnect"))

    def start_worker(self, worker):
        """Hand a worker to Qt for ownership before starting it.

        self.worker is rebound on the next action. If the previous QThread were
        owned only by that name, rebinding could drop its last reference while
        it is still running, which aborts the process rather than raising.
        Parenting keeps it alive until Qt deletes it after it finishes.
        """
        worker.setParent(self)
        worker.finished.connect(self.on_action_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        return worker

    def on_action_finished(self, success, message):
        self.is_busy = False
        self.refresh_active_state()
        if not success:
            QMessageBox.critical(self, "VPN Error", message)
        self.update_ui_state()


if __name__ == "__main__":
    # Respect an explicit platform choice (offscreen testing, forced xcb) and
    # only fall back to the Wayland-then-X11 default.
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")

    app = QApplication(sys.argv)
    # No style or palette is forced here: Qt picks up the system theme.
    app.setDesktopFileName("vpngate-gui")
    app.setApplicationName("VPN Gate Client")
    app.setQuitOnLastWindowClosed(False)

    window = VPNWindow()
    window.show()
    sys.exit(app.exec())
