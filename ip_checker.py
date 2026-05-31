#!/usr/bin/env python3
"""IP checker - validates an IP address and fetches public info about it."""

import argparse
import ipaddress
import json
import socket
import sys
from urllib.request import urlopen
from urllib.error import URLError


def classify(ip_str):
    ip = ipaddress.ip_address(ip_str)
    return {
        "address": str(ip),
        "version": f"IPv{ip.version}",
        "is_private": ip.is_private,
        "is_global": ip.is_global,
        "is_loopback": ip.is_loopback,
        "is_multicast": ip.is_multicast,
        "is_reserved": ip.is_reserved,
        "is_link_local": ip.is_link_local,
    }


def reverse_dns(ip_str):
    try:
        return socket.gethostbyaddr(ip_str)[0]
    except (socket.herror, socket.gaierror):
        return None


def geo_lookup(ip_str):
    try:
        with urlopen(f"http://ip-api.com/json/{ip_str}", timeout=5) as r:
            data = json.loads(r.read().decode())
        if data.get("status") != "success":
            return None
        return {
            "country": data.get("country"),
            "region": data.get("regionName"),
            "city": data.get("city"),
            "isp": data.get("isp"),
            "org": data.get("org"),
            "as": data.get("as"),
        }
    except (URLError, socket.timeout, json.JSONDecodeError):
        return None


def my_public_ip():
    try:
        with urlopen("https://api.ipify.org", timeout=5) as r:
            return r.read().decode().strip()
    except URLError:
        return None


def check(ip_str):
    try:
        info = classify(ip_str)
    except ValueError:
        return {"error": f"'{ip_str}' is not a valid IP address"}

    info["reverse_dns"] = reverse_dns(ip_str)
    if info["is_global"]:
        info["geo"] = geo_lookup(ip_str)
    return info


def print_report(info):
    if "error" in info:
        print(f"ERROR: {info['error']}")
        return
    print(f"Address     : {info['address']} ({info['version']})")
    print(f"Reverse DNS : {info.get('reverse_dns') or '-'}")
    flags = [k for k in ("is_private", "is_global", "is_loopback",
                         "is_multicast", "is_reserved", "is_link_local")
             if info.get(k)]
    print(f"Flags       : {', '.join(flags) or '-'}")
    geo = info.get("geo")
    if geo:
        print("Geolocation :")
        for k, v in geo.items():
            print(f"  {k:8}: {v or '-'}")


def main():
    p = argparse.ArgumentParser(description="Check an IP address.")
    p.add_argument("ip", nargs="?", help="IP to check (omit to use your public IP)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    args = p.parse_args()

    ip = args.ip or my_public_ip()
    if not ip:
        print("Could not determine an IP to check.", file=sys.stderr)
        sys.exit(1)

    info = check(ip)
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print_report(info)
    sys.exit(0 if "error" not in info else 2)


if __name__ == "__main__":
    main()
