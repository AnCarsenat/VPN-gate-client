# VPN Gate CLI Connector

A lightweight Python script to connect to [VPN Gate](https://www.vpngate.net/) servers using NetworkManager (`nmcli`). Optimized for modern Linux systems with strict OpenSSL requirements.

**Note:** Tested on CachyOS (Arch) only, fully vibe coded, without an ounce of networking knowledge, so YMMV. Good luck 

## Features

- **Protocol Preference:** Defaults to **UDP** for lower latency and better speed.
- **Legacy Support:** Automatically configures older encryption standards (`AES-128-CBC`, `SHA1`) required by most VPN Gate servers.
- **Speed Optimized:** Implements a **10-second connection timeout**—if a server doesn't connect instantly, it's skipped, because I'm ADHD ish
- **Easy Cleanup:** One command to disconnect and completely remove the VPN configuration from your system.
- **No Persistence:** Doesn't leave clutter in your NetworkManager list after use.

## Installation

### 1. AUR (Arch Linux)
The project is currently being submitted to the AUR. You can try installing it with:
```bash
yay -S vpn-gate-client
```

### 2. Manual Installation
Clone the repository and run the scripts directly:
```bash
git clone https://github.com/Me3paw/vpn-gate-client.git
cd vpn-gate-client
```

Install the dependencies yourself. On Arch, use the system packages:
```bash
sudo pacman -S python-requests python-pyqt6 networkmanager networkmanager-openvpn
```

Elsewhere, or if you prefer an isolated environment:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r src/assets/requirements.txt
```

The scripts do not install anything for you; they print the command to run if a
dependency is missing. `python-requests` is enough for the CLI, the GUI also
needs `python-pyqt6`.

## Usage

### 1. GUI Mode (Recommended)
Launch the graphical interface:
```bash
./src/vpngate-gui.py      # from a clone
vpngate-gui               # once installed
```

The window follows your system Qt theme. Closing it hides to the tray;
**Ctrl+Q** actually quits, tearing down the VPN on the way out.

**Search.** The box above the list takes GitHub-style qualifiers, which combine:

| Qualifier | Matches |
|---|---|
| `@host:public-vpn-78` | host name |
| `@country:FR` | country code or full name |
| `@ip:219.100` | IP address |
| `@proto:udp` | exact protocol |
| `@ping:<100` | ping below a value — also `>50`, or a bare `40` meaning at most 40 |
| `@rating:good` | rating label |
| `@favorite` `@udp` `@tcp` | bare flags |

Anything else is free text, matched against country, IP and host name. So
`@country:JP @udp @ping:<50` narrows to fast Japanese UDP servers.

**Right-click a row** for Connect, Disconnect, favourite toggle, copy the
clicked cell, copy any single field, or copy the whole row. **Double-click**
connects. Favourites are marked ★ and persist to
`~/.config/vpn-gate-client/favourites.json`.

**Technical details.** `Ctrl+I`, the Connection menu, or the row menu opens a
dialog with every field the API reports — the raw Score behind the rating, raw
ping, speed in bps, uptime, total traffic and users, log policy, operator. It
can copy the whole table or the server's OpenVPN config.

**Shortcuts:** `Ctrl+R` refresh, `Ctrl+F` search, `Ctrl+I` details,
`Ctrl+K` connect, `Ctrl+D` disconnect, `Ctrl+Q` quit.

**Startup speed.** The API is a single ~1.3 MB response, so the fetch takes a
few seconds and nothing can shrink it. The window does not wait for it: the
last fetch is cached under `~/.cache/vpn-gate-client/` and shown immediately,
then rows stream in as they arrive and replace it.

### 2. CLI Mode
Connect to a VPN using the command line:
```bash
./src/vpngate-cli.py      # from a clone
vpngate                   # once installed
```

### 2. Connection Options
- **Filter for TCP:** Use if UDP is blocked on your network.
  ```bash
  vpngate --tcp
  ```
- **Show All Protocols:**
  ```bash
  vpngate --all
  ```

### 3. Management
- **Check Status:** See if the VPN is currently active.
  ```bash
  vpngate --status
  ```
- **Disconnect & Delete:** Stops the connection and removes it from NetworkManager.
  ```bash
  vpngate --stop
  ```

## Troubleshooting

### Connection Timeouts
The script enforces a 10s timeout. Many VPN Gate servers are hosted by volunteers and may be offline or congested. If a connection times out:
1. Run `vpngate` again.
2. Pick a different server index (try one with a slightly higher ping but high score).
3. If UDP consistently fails, try `vpngate --tcp`.
4. If network slow, increase timeout to whatever u want in the code

### Technical Details
- **Connection Name:** `vpngate-active`
- **Configuration:** Uses `nmcli connection import` followed by manual `vpn.data` modification to inject `data-ciphers-fallback` and legacy providers.
- **Credentials:** Automatically sets username `vpn` and password `vpn`.

## Contributing

### Project layout

```
src/vpngate-cli.py          CLI entry point (argparse, server table, prompt)
src/vpngate-gui.py          GUI entry point (PyQt6 window, tray icon, stats)
src/vpngate_core.py         All the logic: fetch, parse, connect, disconnect
src/assets/icons/           Application icons (32/64/128/256 px, plus SVG)
src/assets/requirements.txt Python dependencies
src/assets/vpngate-gui.desktop  Desktop entry
PKGBUILD, .SRCINFO          Arch packaging
```

Two rules keep this working:

- **`vpngate_core.py` uses an underscore, the entry points use hyphens.** The
  core is imported, so its name must be a valid Python identifier; a file named
  `vpngate-core.py` cannot be imported by any syntax. The entry points are only
  ever executed, so hyphens are fine there.
- **Entry points and the core must sit in the same directory, with `assets/`
  beside them.** Each script resolves `SCRIPT_DIR` from its own realpath and
  looks for `assets/...` relative to it. That single relative path is why the
  same code works from a clone, from `/usr/share/vpn-gate-client/`, and through
  the `/usr/bin` symlinks. If you move a file, move it in `PKGBUILD` too.

Put logic in `vpngate_core.py`, not in the entry points, so the CLI and GUI stay
in sync.

### Running from a clone

```bash
./src/vpngate-cli.py --status     # safe, no root, no network
./src/vpngate-cli.py --all        # lists servers, needs network
./src/vpngate-gui.py
```

`--status` and the server listing are harmless. Connecting talks to
NetworkManager and will ask for polkit authentication. `./src/vpngate-cli.py
--stop` always cleans up: it takes the connection down *and* deletes it, so a
failed experiment leaves nothing behind.

To check the GUI starts without a display:

```bash
QT_QPA_PLATFORM=offscreen ./src/vpngate-gui.py
```

### Testing the package

`makepkg` builds from the **git tag on GitHub**, so it will not see your local
edits. To test packaging changes, build from your working clone instead:

> **Do not run `makepkg` in the repo root.** Its `$srcdir` is `./src`, which is
> where this project's source lives — it would clone into and clutter your
> source tree. Always build from a scratch directory.

```bash
mkdir -p /tmp/pkgtest && cd /tmp/pkgtest
sed -e "s|^source=.*|source=(\"\${pkgname}::git+file:///path/to/your/VPN-gate-client\")|" \
    -e '/#tag=/d' /path/to/your/VPN-gate-client/PKGBUILD > PKGBUILD
makepkg -f
tar tf *.pkg.tar.zst        # check the installed layout
```

`git+file://` clones **committed** state only, so commit before each run.

Then install it for real and exercise the entry points:

```bash
sudo pacman -U *.pkg.tar.zst
vpngate --status
vpngate-gui
sudo pacman -R vpn-gate-client
```

Run `namcap PKGBUILD *.pkg.tar.zst` (from the `namcap` package) to catch missing
dependencies and bad paths.

### Cutting a release

The AUR package installs from the tag named in `source=`, so a release is not
live until the tag exists:

1. Bump `pkgver` in `PKGBUILD`.
2. `makepkg --printsrcinfo > .SRCINFO`
3. Commit, then `git tag vX.Y.Z && git push --tags`.

Skipping the tag leaves `yay -S vpn-gate-client` pointing at a commit that does
not exist.

### Pull requests

- Keep changes to `vpngate_core.py` behaviour-compatible with both front ends.
- Say which distro and desktop environment you tested on. This project has only
  been exercised on Arch with NetworkManager.
- If you touch installed paths, include the `tar tf` output from a local
  `makepkg` run.

### Credits
VPN Gate : https://www.vpngate.net/en/
u/fluidmechanicsdoubts on Reddit 
