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
REQUIRED_MODULES = {"requests": "python-requests"}


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

import argparse
import vpngate_core as vpncore

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VPN Gate CLI Connector")
    parser.add_argument("--status", action="store_true", help="Check if VPN is active")
    parser.add_argument("--stop", action="store_true", help="Disconnect and delete the VPN")
    parser.add_argument("--tcp", action="store_true", help="Show TCP servers only")
    parser.add_argument("--all", action="store_true", help="Show all servers")
    args = parser.parse_args()

    if args.stop:
        success, msg = vpncore.disconnect_vpn()
        print(msg)
        sys.exit(0 if success else 1)
    
    if args.status:
        active = vpncore.is_active()
        print(f"Status: {vpncore.CONNECTION_NAME} is {'ACTIVE' if active else 'NOT active'}")
        if active:
            stats = vpncore.get_stats()
            if stats:
                up, down, ping, loss = stats
                print(f"Down: {down:.1f} KB/s | Up: {up:.1f} KB/s")
                print(f"Ping: {ping} | Loss: {loss}")
        sys.exit(0 if active else 1)

    proto_pref = "all" if args.all else ("tcp" if args.tcp else "udp")
    print(f"Fetching servers (Preference: {proto_pref.upper()})...")
    servers = vpncore.get_servers()
    
    filtered = []
    for s in servers:
        if proto_pref == "udp" and s['has_udp']: filtered.append(s)
        elif proto_pref == "tcp" and s['has_tcp']: filtered.append(s)
        elif proto_pref == "all": filtered.append(s)
    
    filtered.sort(key=lambda x: int(x['Score']), reverse=True)
    
    print(f"{'Idx':<4} | {'Proto':<5} | {'Country':<15} | {'IP':<15} | {'Score':<10} | {'Ping':<5}")
    print("-" * 75)
    for i, s in enumerate(filtered[:20]):
        p = "UDP" if (proto_pref != "tcp" and s['has_udp']) else "TCP"
        print(f"{i:<4} | {p:<5} | {s['CountryShort']:<15} | {s['IP']:<15} | {s['Score']:<10} | {s['Ping']:<5}")

    try:
        choice = input("\nEnter Index to connect (or 'q' to quit): ")
        if choice.lower() == 'q': sys.exit(0)
        idx = int(choice)
        if 0 <= idx < len(filtered):
            success, msg = vpncore.connect_vpn(filtered[idx], force_proto=("tcp" if proto_pref == "tcp" else None))
            print(msg)
        else:
            print("Invalid index.")
    except (ValueError, KeyboardInterrupt):
        print("\nExiting.")
