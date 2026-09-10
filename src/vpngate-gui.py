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

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPushButton, QLabel, QRadioButton, QButtonGroup, 
                             QHeaderView, QMessageBox, QSystemTrayIcon, QMenu)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon, QAction, QPalette, QColor

import vpngate_core as vpncore

ICON_DIR = os.path.join(SCRIPT_DIR, "assets", "icons")
ICON_256 = os.path.join(ICON_DIR, "256.png")
ICON_64 = os.path.join(ICON_DIR, "64.png")
ICON_32 = os.path.join(ICON_DIR, "32.png")


def load_app_icon():
    """Prefer the installed hicolor theme icon, fall back to the bundled PNGs."""
    icon = QIcon.fromTheme("vpngate-gui")
    if not icon.isNull():
        return icon

    for path in (ICON_256, ICON_64, ICON_32):
        if os.path.exists(path):
            return QIcon(path)

    return QIcon.fromTheme("network-vpn")


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

class StatsWorker(QThread):
    stats_updated = pyqtSignal(object)
    
    def run(self):
        stats = vpncore.get_stats()
        self.stats_updated.emit(stats)

class VPNWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VPN Gate Client")
        self.setMinimumSize(950, 650)
        
        self.all_servers = []
        self.filtered_servers = []
        self.is_busy = False
        
        # Dark Theme Palette
        self.bg_dark = "#1e1e1e"
        self.bg_card = "#2d2d2d"
        self.text_light = "#ecf0f1"
        self.accent_green = "#2ecc71"
        self.accent_red = "#e74c3c"
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.setStyleSheet(f"background-color: {self.bg_dark}; color: {self.text_light};")
        
        # 1. TOP: Server List
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["No", "Ping", "Rating", "Country", "IP", "Protocol"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background-color: {self.bg_card}; color: {self.text_light}; gridline-color: #444; border: none; }}
            QHeaderView::section {{ background-color: #333; color: white; padding: 5px; border: 1px solid #444; }}
            QTableWidget::item:selected {{ background-color: #3498db; }}
        """)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_by_column)
        self.current_sort_col = 2  # Default sort by Rating
        self.sort_reverse = True
        self.main_layout.addWidget(self.table)
        
        # 2. MIDDLE: Detailed Status
        self.status_container = QWidget()
        self.status_layout = QVBoxLayout(self.status_container)
        self.status_label = QLabel("Status: DISCONNECTED")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_layout.addWidget(self.status_label)
        
        self.stats_label = QLabel("")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stats_label.setStyleSheet("font-family: monospace; color: #bdc3c7;")
        self.status_layout.addWidget(self.stats_label)
        
        self.status_container.setStyleSheet(f"background: {self.bg_card}; border: 1px solid #444; border-radius: 8px; margin: 5px;")
        self.main_layout.addWidget(self.status_container)
        
        # 3. BOTTOM: Controls
        controls_layout = QHBoxLayout()
        self.radio_group = QButtonGroup(self)
        self.radio_udp = QRadioButton("UDP Preference")
        self.radio_tcp = QRadioButton("TCP Preference")
        self.radio_all = QRadioButton("Show All")
        self.radio_udp.setChecked(True)
        
        for r in [self.radio_udp, self.radio_tcp, self.radio_all]:
            self.radio_group.addButton(r)
            controls_layout.addWidget(r)
        
        self.radio_group.buttonClicked.connect(self.apply_filter)
        controls_layout.addStretch()
        
        # Action Buttons
        self.btn_refresh = QPushButton("Refresh List")
        self.btn_refresh.clicked.connect(self.load_servers)
        
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setStyleSheet(f"background-color: {self.accent_green}; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_connect.setMinimumHeight(40)
        self.btn_connect.setMinimumWidth(120)
        self.btn_connect.clicked.connect(self.start_connect)
        
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.setStyleSheet(f"background-color: {self.accent_red}; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_disconnect.setMinimumHeight(40)
        self.btn_disconnect.setMinimumWidth(120)
        self.btn_disconnect.clicked.connect(self.start_disconnect)
        
        controls_layout.addWidget(self.btn_refresh)
        controls_layout.addWidget(self.btn_connect)
        controls_layout.addWidget(self.btn_disconnect)
        self.main_layout.addLayout(controls_layout)
        
        # System Tray
        self.tray_icon = QSystemTrayIcon(self)
        
        icon = load_app_icon()
            
        self.tray_icon.setIcon(icon)
        self.setWindowIcon(icon)
        
        tray_menu = QMenu()
        show_action = QAction("Open Client", self)
        show_action.triggered.connect(self.showNormal)
        quit_action = QAction("Kill App & VPN", self)
        quit_action.triggered.connect(self.quit_app)
        
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_activated)

        # Workers & Timers
        self.stats_worker = StatsWorker()
        self.stats_worker.stats_updated.connect(self.on_stats_updated)
        
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self.request_stats)
        self.stats_timer.start(3000)
        
        self.load_servers()
        self.update_ui_state()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.activateWindow()

    def closeEvent(self, event):
        # Only hide when user clicks the window X button
        self.hide()
        event.ignore()

    def quit_app(self):
        print("Cleaning up VPN and exiting...")
        vpncore.disconnect_vpn()
        QApplication.instance().quit()

    def update_ui_state(self, is_busy=False):
        self.is_busy = is_busy
        active = vpncore.is_active()
        
        if active:
            self.status_label.setText("Status: VPN IS ACTIVE")
            self.status_label.setStyleSheet(f"color: {self.accent_green}; font-size: 16px; font-weight: bold;")
        else:
            self.status_label.setText("Status: DISCONNECTED")
            self.status_label.setStyleSheet(f"color: {self.accent_red}; font-size: 16px; font-weight: bold;")
            self.stats_label.setText("")

        if is_busy:
            self.set_controls_enabled(False)
        else:
            self.table.setEnabled(not active)
            self.btn_connect.setEnabled(not active)
            self.btn_refresh.setEnabled(not active)
            self.btn_disconnect.setEnabled(active)
            for b in self.radio_group.buttons(): b.setEnabled(not active)

    def set_controls_enabled(self, enabled):
        self.table.setEnabled(enabled)
        self.btn_connect.setEnabled(enabled)
        self.btn_disconnect.setEnabled(enabled)
        self.btn_refresh.setEnabled(enabled)
        for b in self.radio_group.buttons(): b.setEnabled(enabled)

    def request_stats(self):
        if not self.is_busy and vpncore.is_active():
            if not self.stats_worker.isRunning():
                self.stats_worker.start()
        elif not self.is_busy:
            if "ACTIVE" in self.status_label.text():
                self.update_ui_state()

    def on_stats_updated(self, stats):
        if stats and not self.is_busy:
            up, down, ping, loss = stats
            self.stats_label.setText(f"DOWNLOAD: {down:.1f} KB/s  |  UPLOAD: {up:.1f} KB/s  |  PING: {ping}  |  LOSS: {loss}")

    def load_servers(self):
        self.status_label.setText("Status: Fetching servers...")
        self.all_servers = vpncore.get_servers()
        for i, s in enumerate(self.all_servers):
            s['gui_idx'] = i
        self.apply_filter()

    def sort_by_column(self, col):
        if self.current_sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.current_sort_col = col
            # Numbers descending by default, strings ascending
            self.sort_reverse = True if col in [0, 1, 2] else False
        self.apply_filter()

    def apply_filter(self):
        self.filtered_servers = []
        pref_udp = self.radio_udp.isChecked()
        pref_tcp = self.radio_tcp.isChecked()
        pref_all = self.radio_all.isChecked()
        
        for s in self.all_servers:
            if pref_all: self.filtered_servers.append(s)
            elif pref_udp and s['has_udp']: self.filtered_servers.append(s)
            elif pref_tcp and s['has_tcp']: self.filtered_servers.append(s)
            
        def get_ping(s):
            p = s.get('Ping', '0')
            try: return int(p)
            except: return 9999

        def get_proto(s):
            return "TCP" if (self.radio_tcp.isChecked() and s['has_tcp']) or not s['has_udp'] else "UDP"

        key_map = {
            0: lambda x: int(x.get('gui_idx', 0)),
            1: get_ping,
            2: lambda x: int(x.get('Score', 0)),
            3: lambda x: x.get('CountryShort', ''),
            4: lambda x: x.get('IP', ''),
            5: get_proto
        }
        
        self.filtered_servers.sort(key=key_map.get(self.current_sort_col, lambda x: 0), reverse=self.sort_reverse)
        self.update_table()
        self.update_ui_state()

    def update_table(self):
        self.table.setRowCount(0)
        pref_tcp = self.radio_tcp.isChecked()
        for i, s in enumerate(self.filtered_servers[:100]):
            self.table.insertRow(i)
            p_display = "TCP" if (pref_tcp and s['has_tcp']) or not s['has_udp'] else "UDP"
            # Order: No, Ping, Rating, Country, IP, Protocol
            items = [str(s.get('gui_idx', i)), s.get('Ping', 'N/A'), s.get('Score', '0'), 
                     s.get('CountryShort', '??'), s.get('IP', ''), p_display]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                if col == 5: # Protocol column
                    item.setForeground(Qt.GlobalColor.cyan if text == "UDP" else Qt.GlobalColor.yellow)
                self.table.setItem(i, col, item)

    def start_connect(self):
        if vpncore.is_active():
            QMessageBox.critical(self, "Error", "A VPN is already running.")
            self.update_ui_state()
            return

        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Selection Required", "Please select a server.")
            return
            
        server = self.filtered_servers[row]
        proto = "tcp" if self.radio_tcp.isChecked() else None
        self.status_label.setText(f"Status: Connecting to {server['IP']} (10s timeout)...")
        self.update_ui_state(is_busy=True)
        self.worker = Worker("connect", server, proto)
        self.worker.finished.connect(self.on_action_finished)
        self.worker.start()

    def start_disconnect(self):
        self.status_label.setText("Status: Disconnecting...")
        self.update_ui_state(is_busy=True)
        self.worker = Worker("disconnect")
        self.worker.finished.connect(self.on_action_finished)
        self.worker.start()

    def on_action_finished(self, success, message):
        self.status_label.setText(f"Status: {message}")
        self.is_busy = False
        if not success:
            QMessageBox.critical(self, "VPN Error", message)
        self.update_ui_state()

def set_dark_theme(app):
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(236, 240, 241))
    palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(236, 240, 241))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.Text, QColor(236, 240, 241))
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(236, 240, 241))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(231, 76, 60))
    palette.setColor(QPalette.ColorRole.Link, QColor(52, 152, 219))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(52, 152, 219))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    
    # Disabled state colors
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(127, 127, 127))
    
    app.setPalette(palette)

if __name__ == "__main__":
    os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
    app = QApplication(sys.argv)
    set_dark_theme(app)
    
    # Add this line so Wayland knows which .desktop file to associate with this process
    app.setDesktopFileName("vpngate-gui") 
    
    app.setQuitOnLastWindowClosed(False)
    window = VPNWindow()
    window.show()
    sys.exit(app.exec())
