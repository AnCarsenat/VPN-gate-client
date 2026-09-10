# GUI rework plan

> **Status:** steps 0-6 are implemented. What remains is step 7 (theme-aware
> tray icon and the icon redesign) — the two annotations that were dropped from
> the final spec. The rest of this document is kept as the record of why the
> code is shaped the way it is.

Derived from the annotations in `issues.png`. Everything below concerns
`src/vpngate-gui.py` only — `vpngate_core.py` needs no changes except where
noted in step 0.

## The annotations, grouped

| # | Annotation | Where it lands |
|---|---|---|
| 1 | RMB menu on servers: connect, disconnect, favourite | Step 3 |
| 2 | Double-click a server to connect | Step 3 |
| 3 | Clicking a header should let you search | Step 4 |
| 4 | Country flags would look better | Step 5 |
| 5 | "No" column has no purpose, row numbers are on the left | Step 2 |
| 6 | Protocol can take less space | Step 2 |
| 7 | Ping should be `{ping}ms` and narrower | Step 2 |
| 8 | Rating: colour coded, readable unit | Step 2 |
| 9 | Move the UDP/TCP/All radios to a top menubar | Step 6 |
| 10 | Refresh List button doesn't follow the window theme | Step 1 |
| 11 | Tray icon should match the Qt theme | Step 7 |
| 12 | Simplify the app icon | Step 7 |

Do them in the order below. Steps 0 and 1 are prerequisites that make the rest
small; skipping them means fighting the current structure eight separate times.

---

## Step 0 — Move the table to a real model (do this first)

**Why this is not optional.** Six of the twelve annotations (searching,
favourites, per-column formatting, colour coding, flags, sorting) are things
`QSortFilterProxyModel` gives you for free and that `QTableWidget` makes you
hand-roll. There is also a latent bug waiting for you:

`src/vpngate-gui.py:348-353` connects using `self.table.currentRow()` as an
index straight into `self.filtered_servers`. That only works because
`update_table()` happens to render `filtered_servers[:100]` in the same order.
The moment you add favourites-pinned-to-top, a search filter, or Qt-side
sorting, the visual row and the list index diverge and **the app connects to
the wrong server** — silently, since every row looks plausible.

**What to build.**

Replace `QTableWidget` with `QTableView` + a `QAbstractTableModel` holding the
server dicts, behind a `QSortFilterProxyModel`.

```python
class ServerModel(QAbstractTableModel):
    COLUMNS = ["", "Country", "Ping", "Speed", "Rating", "IP", "Proto"]

    def data(self, index, role):
        server = self._servers[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(server, col)     # "13 ms", "37.6 Mbps", flag
        if role == Qt.ItemDataRole.UserRole:
            return self._sort_key(server, col)    # int/float, for correct sorting
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._colour(server, col)      # rating / protocol colouring
        return None
```

Then:

```python
proxy = QSortFilterProxyModel()
proxy.setSourceModel(model)
proxy.setSortRole(Qt.ItemDataRole.UserRole)   # sort on the number, not the string
proxy.setFilterKeyColumn(-1)                  # search every column
view.setModel(proxy)
view.setSortingEnabled(True)
```

Sorting by `UserRole` is the important line. Right now
`apply_filter()` (`:314-323`) maintains a hand-written `key_map` per column
purely to stop `"1480793"` sorting before `"9"`. That whole block disappears.

**Getting the selected server, safely — the one rule to follow:**

```python
def selected_server(self):
    rows = self.view.selectionModel().selectedRows()
    if not rows:
        return None
    return self.model.server_at(self.proxy.mapToSource(rows[0]).row())
```

Never index a Python list with a view row again. Every later step depends on
this.

**Also fix while you're in here:** `load_servers()` (`:279-284`) calls
`vpncore.get_servers()` on the GUI thread. That is a blocking HTTP request —
the window is frozen for its duration, including on startup at `:217`. You
already have the pattern for this in `Worker`/`StatsWorker`; add a
`FetchWorker(QThread)` that emits the server list. The Refresh button should
disable itself and show "Refreshing…" while it runs.

*Rough size: ~200 lines changed. This is the big one; the remaining steps are
20-40 lines each.*

---

## Step 1 — Fix the theme cascade (annotation 10)

The Refresh List button doesn't match because of `src/vpngate-gui.py:115`:

```python
self.setStyleSheet(f"background-color: {self.bg_dark}; color: {self.text_light};")
```

A stylesheet set on `QMainWindow` **cascades to every child widget**. Every
button inherits `background-color: #1e1e1e` and loses its Fusion bevel,
hover and pressed states. Connect and Disconnect *look* fine only because they
each set their own `background-color` and `border-radius` at `:171` and `:177`,
which overrides the inherited value. Refresh has no override, so it renders as
a flat dark rectangle.

Don't paper over it by giving Refresh a stylesheet too — that's a third
hard-coded colour to keep in sync. Delete line 115 entirely. `set_dark_theme()`
(`:375-397`) already sets a full `QPalette` on the application, which colours
the window correctly *and* keeps native button rendering.

Then convert the two coloured buttons from stylesheets to palette roles, or
keep their stylesheets but add the states they're currently missing:

```python
self.btn_connect.setStyleSheet(f"""
    QPushButton {{ background-color: {self.accent_green}; color: white;
                   font-weight: bold; border-radius: 4px; padding: 8px; }}
    QPushButton:hover    {{ background-color: #27ae60; }}
    QPushButton:disabled {{ background-color: #3d5a4a; color: #7f7f7f; }}
""")
```

The `:disabled` rule matters — `update_ui_state()` disables Connect whenever a
VPN is active (`:254`), and right now a disabled Connect button is still bright
green, which reads as clickable.

---

## Step 2 — Rebuild the columns (annotations 5, 6, 7, 8)

Current: `["No", "Ping", "Rating", "Country", "IP", "Protocol"]`, all six
stretched equally by `QHeaderView.ResizeMode.Stretch` (`:121`). That stretch is
why Protocol and Ping waste so much width.

Proposed: `["★", "Country", "Ping", "Speed", "Rating", "IP", "Proto"]`

**Drop "No".** It renders `gui_idx` (`:334`), the server's position in the
unfiltered fetch order — a number that changes meaning the moment you sort or
filter, displayed right next to `QTableView`'s own row numbers. Keep the
vertical header, delete the column.

**Ping** — `"13"` becomes `"13 ms"`. Display string in `DisplayRole`, the raw
`int` in `UserRole` so sorting stays numeric. Note the API returns `"-"` for
some servers; `get_ping()` at `:306-309` already handles that by falling back
to `9999`, keep that behaviour so unreachable servers sort last.

**Rating** — `Score` is a raw integer, `2611933` in the screenshot. Two
problems: the unit is meaningless to a user, and the magnitude is noise. Map it
to five bars plus colour:

```python
def rating_bars(score):
    # Observed range across a full fetch is roughly 0 - 3,000,000.
    tier = min(int(score) // 600_000, 4)
    return "█" * (tier + 1) + "░" * (4 - tier)

RATING_COLOURS = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#27ae60"]
```

Return the colour from `ForegroundRole`, the bars from `DisplayRole`, and the
raw `int(score)` from `UserRole`. Verify the tier boundaries against a live
fetch before settling on `600_000` — the distribution is skewed and you may
want quantiles over the current list instead of fixed cutoffs.

**Speed** — a new column, and the one users actually want. The API already
returns it in bits per second (`Speed=379652065`), it's just never shown.
Format as `f"{int(speed)/1_000_000:.1f} Mbps"`.

**Protocol** — rename the header to "Proto", keep the cyan/yellow colouring
from `:338-339`, and stop stretching it:

```python
header = self.view.horizontalHeader()
header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)      # Country
header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)      # IP
for col in (0, 2, 3, 4, 6):
    header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
```

Only Country and IP absorb slack; the numeric and badge columns shrink to their
content. That alone reclaims most of the wasted width in the screenshot.

---

## Step 3 — Double-click and right-click (annotations 1, 2)

Both are small once step 0 is done.

```python
self.view.doubleClicked.connect(self.start_connect)

self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
self.view.customContextMenuRequested.connect(self.show_row_menu)

def show_row_menu(self, pos):
    server = self.selected_server()
    if server is None:
        return
    menu = QMenu(self)
    connect = menu.addAction("Connect")
    connect.setEnabled(not vpncore.is_active() and not self.is_busy)
    disconnect = menu.addAction("Disconnect")
    disconnect.setEnabled(vpncore.is_active() and not self.is_busy)
    menu.addSeparator()
    fav = menu.addAction("Remove from favourites" if self.is_favourite(server)
                         else "Add to favourites")
    menu.addSeparator()
    copy_ip = menu.addAction("Copy IP")
    ...
    menu.exec(self.view.viewport().mapToGlobal(pos))
```

Two things to watch:

- `customContextMenuRequested` gives coordinates **relative to the viewport**,
  not the widget. Use `self.view.viewport().mapToGlobal(pos)` or the menu opens
  offset by the header height.
- Right-clicking does not change the selection by default, so
  `selected_server()` may return a *different* row than the one under the
  cursor. Resolve the server from the position instead:
  `self.proxy.mapToSource(self.view.indexAt(pos))`.

Reuse the existing `start_connect` guard at `:342-346` — double-click must not
become a way to bypass the "already running" check.

### Favourites need somewhere to live

Store them in an XDG config file:

```python
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "vpn-gate-client")
FAVOURITES = os.path.join(CONFIG_DIR, "favourites.json")
```

**Key them on `HostName`, not `IP`.** I checked a live fetch: 98 servers, 98
unique `HostName` values, 97 unique IPs — one IP was shared by two entries, and
VPN Gate IPs rotate between fetches anyway. `HostName` (`public-vpn-78`) is the
stable identifier.

Show favourites as a ★ in column 0, and give the proxy a sort that floats them
to the top regardless of the active sort column (override
`QSortFilterProxyModel.lessThan`, checking favourite status before deferring to
the column comparison).

---

## Step 4 — Search (annotation 3)

The annotation says "clicking on a header should let you search", but note the
conflict: header clicks are currently how you sort (`:130`, `sort_by_column` at
`:286-293`), and step 0 hands sorting to `setSortingEnabled(True)`. If a click
opens a search box, you lose click-to-sort.

Pick one:

- **A — filter row under the header.** A row of `QLineEdit`s below the header,
  one per column, feeding `proxy.setFilterKeyColumn(col)`. Closest to what the
  annotation describes, keeps click-to-sort intact. Most work.
- **B — one search box above the table** (recommended). A single `QLineEdit`
  with `proxy.setFilterKeyColumn(-1)` searching all columns, `Ctrl+F` to focus.
  Ten lines, covers the actual need — "show me the Japanese ones" — and leaves
  headers alone.
- **C — right-click the header** for a per-column "Filter…" menu. Preserves
  both interactions but is undiscoverable.

I'd ship B now and only move to A if per-column filtering turns out to be
something you reach for. Use `setFilterCaseSensitivity(Qt.CaseInsensitive)`
either way.

---

## Step 5 — Country flags (annotation 4)

`CountryShort` is ISO 3166-1 alpha-2, which maps to flag emoji arithmetically —
no dependency, no image files:

```python
def flag(country_short):
    if len(country_short) != 2 or not country_short.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_short.upper())
```

Display `f"{flag(cs)}  {server['CountryLong']}"` — `CountryLong` ("Japan") is in
the API response and never used today; it reads better than "JP".

**Caveat worth testing before you commit to this:** flag emoji rendering on
Linux depends on the font stack. Without `noto-fonts-emoji` installed you get
two letter-boxes (🇯🇵 as "JP" in boxes) rather than a flag. Check on your setup
first. If it looks bad, the fallback is bundling a flag sprite sheet under
`src/assets/flags/` and returning a `QIcon` from `DecorationRole` — more work,
and it means adding ~250 small PNGs to the package.

---

## Step 6 — Top menubar (annotation 9)

Move the three radios out of the bottom control row into `QMainWindow`'s
menubar, which is already available and unused:

```python
menubar = self.menuBar()

servers_menu = menubar.addMenu("&Servers")
servers_menu.addAction(self.act_refresh)     # Ctrl+R
servers_menu.addSeparator()
proto_group = QActionGroup(self)
proto_group.setExclusive(True)
for label, key in [("&UDP Preference", "udp"), ("&TCP Preference", "tcp"), ("Show &All", "all")]:
    act = servers_menu.addAction(label)
    act.setCheckable(True)
    act.setData(key)
    proto_group.addAction(act)
proto_group.triggered.connect(self.on_proto_changed)

conn_menu = menubar.addMenu("&Connection")
conn_menu.addAction(self.act_connect)        # Ctrl+K
conn_menu.addAction(self.act_disconnect)     # Ctrl+D
```

Define Connect / Disconnect / Refresh as `QAction`s **once** and attach them to
the menubar, the buttons (`btn.setDefaultAction(act)`), and the row context
menu from step 3. Then `act.setEnabled(...)` updates all three call sites at
once, and `update_ui_state()` / `set_controls_enabled()` (`:238-264`) collapse
from per-widget enable calls into per-action ones.

That leaves the bottom row as just Connect and Disconnect, which is what the
annotation is asking for.

While you're restructuring: `update_ui_state()` calls `vpncore.is_active()`
(`:240`), which shells out to `nmcli`. It's called from `apply_filter()`
(`:325`), which runs on every sort and every filter change. With a search box
firing on every keystroke that becomes a subprocess spawn per character. Cache
the active state and refresh it on the 3-second timer that already exists
(`:213-215`), not inside the filter path.

---

## Step 7 — Icons (annotations 11, 12)

**Tray icon matching the theme.** The tray currently gets the full-colour app
PNG via `load_app_icon()` (`:55-65`). Tray areas expect a monochrome symbolic
icon that inverts with the panel theme — light glyph on dark panels, dark on
light.

Draw a single-colour SVG (`src/assets/icons/tray.svg`), then recolour it at
runtime from the palette:

```python
def tray_icon(palette):
    renderer = QSvgRenderer(os.path.join(ICON_DIR, "tray.svg"))
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), palette.color(QPalette.ColorRole.WindowText))
    painter.end()
    return QIcon(pixmap)
```

`QSvgRenderer` lives in `PyQt6.QtSvg`. The bindings ship inside `python-pyqt6`,
but the Qt library behind them, `qt6-svg`, is only an **optional** dependency of
it — so the import works on your machine and fails on a clean install. If you
take this route, add `qt6-svg` to `depends` in `PKGBUILD`. If you would rather
not take the dependency, ship two pre-rendered PNGs
(`tray-light.png`, `tray-dark.png`) and pick between them by the luminance of
`palette().window().color()`.

Consider also giving the tray icon state: a green dot overlay when connected,
grey when not. It's the main reason to look at a tray icon at all, and you
already track that state.

**Icon redesign.** Out of scope for the code, but the constraint to design
against is the 32×32 rendering — the current icon has detail that turns to mush
at tray size. A single strong silhouette (shield, globe, or padlock) with no
internal detail, one flat colour, and heavy strokes is what survives. Keep
`128.svg` as the source of truth and re-export the PNGs from it.

---

## Suggested commit sequence

Each of these is independently testable and independently revertable:

1. `refactor(gui): move server list to QAbstractTableModel + proxy` (step 0)
2. `fix(gui): stop the window stylesheet cascading onto buttons` (step 1)
3. `feat(gui): rework columns — drop No, add Speed, format ping and rating` (step 2)
4. `feat(gui): double-click and right-click actions on server rows` (step 3)
5. `feat(gui): favourites, persisted to XDG config` (step 3)
6. `feat(gui): search box filtering all columns` (step 4)
7. `feat(gui): country flags` (step 5)
8. `feat(gui): move protocol preference into a menubar` (step 6)
9. `feat(gui): theme-aware tray icon` (step 7)

## Testing as you go

`QT_QPA_PLATFORM=offscreen ./src/vpngate-gui.py` catches import and
construction errors without a display, which covers most of what breaks during
a model refactor. It cannot tell you whether anything *looks* right, so run it
normally too.

The one behaviour to re-verify after every step, because it is the one that
fails silently:

> Sort by a column, then search, then select a row that is not the first, then
> Connect — and confirm the IP in the status line matches the row you clicked.

That is the wrong-server bug from step 0. Everything else announces itself.
