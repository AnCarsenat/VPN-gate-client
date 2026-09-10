#!/usr/bin/env python3
import requests
import base64
import gzip
import subprocess
import tempfile
import os
import re
import time

API_URL = "https://www.vpngate.net/api/iphone/"
CONNECTION_NAME = "vpngate-active"
PID_FILE = "/tmp/vpngate-cli.pid"

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "vpn-gate-client")
CACHE_FILE = os.path.join(CACHE_DIR, "servers.csv.gz")


def parse_server(header, line):
    """Turn one CSV row into a server dict, or None if it is not one."""
    if not line or line.startswith("*") or line.startswith("#") or not line.strip():
        return None
    parts = line.split(",")
    if len(parts) < 15:
        return None

    server = dict(zip(header, parts))
    try:
        config_data = base64.b64decode(
            server['OpenVPN_ConfigData_Base64']).decode('utf-8', errors='ignore')
    except Exception:
        return None

    lowered = config_data.lower()
    server['has_udp'] = "proto udp" in lowered
    server['has_tcp'] = "proto tcp" in lowered or "proto udp" not in lowered
    server['config_text'] = config_data
    return server


def parse_lines(lines):
    """Parse a whole response body that is already in memory."""
    header = None
    servers = []
    for line in lines:
        if header is None:
            if line.startswith("#"):
                header = line[1:].split(",")
            continue
        server = parse_server(header, line)
        if server is not None:
            servers.append(server)
    return servers


def stream_servers(on_batch=None, batch_size=8, timeout=20, cache=True,
                   should_stop=None):
    """Fetch the server list, parsing rows as they arrive off the socket.

    The API is a single ~1.3 MB response and the whole cost is the download;
    parsing is free. Streaming does not make it finish sooner, it makes the
    first rows usable about a second in instead of after the whole body.

    on_batch, if given, is called with each new group of servers as it is
    parsed. The full list is returned at the end regardless.

    should_stop, if given, is polled per row; returning True abandons the
    download. That keeps shutdown from having to sit through a fetch.
    """
    header = None
    servers = []
    batch = []
    raw_lines = []

    with requests.get(API_URL, timeout=timeout, stream=True) as response:
        response.raise_for_status()
        for raw in response.iter_lines(decode_unicode=True):
            if should_stop is not None and should_stop():
                return servers

            line = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
            raw_lines.append(line)

            if header is None:
                if line.startswith("#"):
                    header = line[1:].split(",")
                continue

            server = parse_server(header, line)
            if server is None:
                continue

            servers.append(server)
            batch.append(server)
            if on_batch is not None and len(batch) >= batch_size:
                on_batch(list(batch))
                batch = []

    if on_batch is not None and batch:
        on_batch(list(batch))

    if cache and servers:
        save_cache(raw_lines)
    return servers


def get_servers():
    """Blocking fetch of the whole list. Kept for the CLI."""
    try:
        return stream_servers()
    except Exception as e:
        print(f"Error fetching servers: {e}")
        return []


def save_cache(lines):
    """Write the cache atomically.

    A half-written file is worse than no file: it is read back at startup, so
    a crash or a full disk mid-write would break every subsequent launch.
    """
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        try:
            with gzip.open(os.fdopen(fd, "wb"), "wt", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
            os.replace(temp_path, CACHE_FILE)
        except BaseException:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
    except OSError as error:
        print(f"Could not write cache: {error}")


def load_cached_servers():
    """Last fetch, for showing something instantly while the refresh runs.

    Returns (servers, age_in_seconds), or ([], None) when there is no usable
    cache. However old the cache is, it is still returned: the caller labels it
    with its age, which beats showing an empty window.

    Every failure has to end up as ([], None). This runs during startup, so
    anything raised here stops the app from opening at all. A truncated gzip
    raises EOFError, which is not an OSError.
    """
    try:
        age = time.time() - os.path.getmtime(CACHE_FILE)
        with gzip.open(CACHE_FILE, "rt", encoding="utf-8") as handle:
            servers = parse_lines(handle.read().splitlines())
        return servers, age
    except FileNotFoundError:
        return [], None            # first run, nothing cached yet
    except Exception as error:
        print(f"Ignoring unreadable cache: {error}")
        return [], None

def is_active():
    res = subprocess.run(["nmcli", "-t", "-f", "NAME,STATE", "connection", "show", "--active"], capture_output=True, text=True)
    return CONNECTION_NAME in res.stdout

def get_stats():
    """Returns (up_speed, down_speed, ping, loss) or None if not active"""
    if not is_active():
        return None
    
    res = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], capture_output=True, text=True)
    device = None
    for line in res.stdout.splitlines():
        if line.startswith(CONNECTION_NAME):
            device = line.split(":")[1]
            break
    
    if not device:
        return None

    def get_bytes():
        try:
            with open("/proc/net/dev", "r") as f:
                for line in f:
                    if device in line:
                        parts = line.split()
                        return int(parts[1]), int(parts[9])
        except:
            pass
        return 0, 0

    b1_rx, b1_tx = get_bytes()
    time.sleep(1)
    b2_rx, b2_tx = get_bytes()
    
    down_speed = (b2_rx - b1_rx) / 1024
    up_speed = (b2_tx - b1_tx) / 1024

    # Ping through the tunnel to a reliable public DNS
    # Note: This measures end-to-end latency, not just to the VPN server
    ping_res = subprocess.run(["ping", "-c", "3", "-W", "2", "8.8.8.8"], capture_output=True, text=True)
    ping_val = "N/A"
    loss_val = "100%"
    
    if ping_res.returncode == 0:
        loss_match = re.search(r"(\d+)% packet loss", ping_res.stdout)
        if loss_match:
            loss_val = loss_match.group(1) + "%"
        
        avg_match = re.search(r"avg/max/mdev = [\d\.]+/([\d\.]+)/", ping_res.stdout)
        if avg_match:
            ping_val = avg_match.group(1) + " ms"

    return up_speed, down_speed, ping_val, loss_val

def connect_vpn(server, force_proto=None):
    if is_active():
        return False, "Error: A VPN connection is already active. Stop it first."

    config_data = server['config_text']
    
    if force_proto == "tcp" and "proto tcp" in config_data.lower() and "proto udp" in config_data.lower():
        config_data = re.sub(r"^proto udp", ";proto udp", config_data, flags=re.MULTILINE | re.IGNORECASE)
        config_data = re.sub(r"^[; \t]*proto tcp", "proto tcp", config_data, flags=re.MULTILINE | re.IGNORECASE)
    elif force_proto == "udp" and "proto udp" in config_data.lower() and "proto tcp" in config_data.lower():
        config_data = re.sub(r"^proto tcp", ";proto tcp", config_data, flags=re.MULTILINE | re.IGNORECASE)
        config_data = re.sub(r"^[; \t]*proto udp", "proto udp", config_data, flags=re.MULTILINE | re.IGNORECASE)

    # The config embeds a client certificate and its RSA private key. mkstemp
    # gives an unpredictable name with 0600, rather than a fixed world-readable
    # path in /tmp that another user could read or pre-empt with a symlink.
    fd, temp_ovpn = tempfile.mkstemp(prefix="vpngate-", suffix=".ovpn")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(config_data)
        return _import_and_connect(server, config_data, temp_ovpn)
    finally:
        if os.path.exists(temp_ovpn):
            os.remove(temp_ovpn)


def _import_and_connect(server, config_data, temp_ovpn):
    subprocess.run(["nmcli", "connection", "delete", CONNECTION_NAME], capture_output=True)

    import_res = subprocess.run(["nmcli", "connection", "import", "type", "openvpn", "file", temp_ovpn], capture_output=True, text=True)

    if import_res.returncode != 0:
        return False, f"Failed to import: {import_res.stderr}"

    remote_match = re.search(r"^remote\s+([\d\.]+)\s+(\d+)", config_data, re.MULTILINE)
    remote_ip = remote_match.group(1) if remote_match else server['IP']
    remote_port = remote_match.group(2) if remote_match else "443"
    
    subprocess.run(["nmcli", "connection", "modify", CONNECTION_NAME, 
                    "vpn.user-name", "vpn",
                    "vpn.secrets", "password=vpn",
                    "+vpn.data", f"auth=SHA1, cipher=AES-128-CBC, data-ciphers=AES-256-GCM:AES-128-GCM:AES-128-CBC, data-ciphers-fallback=AES-128-CBC, connection-type=password, remote={remote_ip}, port={remote_port}"], capture_output=True)

    try:
        up_res = subprocess.run(["timeout", "10s", "nmcli", "connection", "up", CONNECTION_NAME], capture_output=True, text=True)
        
        if up_res.returncode == 0:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
            return True, "Successfully connected!"
        elif up_res.returncode == 124:
            subprocess.run(["nmcli", "connection", "delete", CONNECTION_NAME], capture_output=True)
            return False, "Connection timed out (>10s)."
        else:
            subprocess.run(["nmcli", "connection", "delete", CONNECTION_NAME], capture_output=True)
            return False, f"Connection failed: {up_res.stderr}"
    except Exception as e:
        subprocess.run(["nmcli", "connection", "delete", CONNECTION_NAME], capture_output=True)
        return False, str(e)

def disconnect_vpn():
    if not is_active():
        subprocess.run(["nmcli", "connection", "delete", CONNECTION_NAME], capture_output=True)
        return False, "No active VPN connection found."

    subprocess.run(["nmcli", "connection", "down", CONNECTION_NAME], capture_output=True)
    subprocess.run(["nmcli", "connection", "delete", CONNECTION_NAME], capture_output=True)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    return True, "VPN disconnected."
