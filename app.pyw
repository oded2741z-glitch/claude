import subprocess
import re
import socket
import platform
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, render_template

# On Windows every subprocess call normally flashes a CMD window.
# CREATE_NO_WINDOW (0x08000000) suppresses that completely. The network
# scan makes hundreds of these calls, so without this the screen would
# fill with flickering console windows.
_IS_WIN = platform.system().lower().startswith("win")
_NO_WINDOW = 0x08000000 if _IS_WIN else 0

# Hidden STARTUPINFO as a belt-and-braces second layer on Windows
_STARTUPINFO = None
if _IS_WIN:
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE


def _silent_run(cmd, shell=False, timeout=None):
    """subprocess.run that never pops a console window on Windows."""
    return subprocess.run(
        cmd, shell=shell, timeout=timeout,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO)


def _silent_output(cmd, shell=False):
    """subprocess.check_output that never pops a console window."""
    return subprocess.check_output(
        cmd, shell=shell, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO)


app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True


# Allow the dashboard to call the API even when index.html is opened
# directly as a local file (file://) instead of via http://127.0.0.1:5000
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# Expanded OUI (MAC prefix) -> vendor map. Covers phones, computers,
# printers, TVs, VR headsets, consoles, smart speakers and IoT.
MAC_VENDORS = {
    # Apple (iPhone / iPad / Mac / Apple TV / Vision Pro)
    "dc:a9:04": "Apple", "00:1c:b3": "Apple", "28:cf:e9": "Apple",
    "64:16:66": "Apple", "ac:bc:32": "Apple", "f0:18:98": "Apple",
    "a4:83:e7": "Apple", "3c:06:30": "Apple", "90:9c:4a": "Apple",
    # Samsung (Galaxy phones / Samsung TVs)
    "cc:b8:a8": "Samsung", "f4:7b:5e": "Samsung", "48:8b:49": "Samsung",
    "8c:79:67": "Samsung", "5c:49:7d": "Samsung", "e8:50:8b": "Samsung",
    # Printers
    "00:26:ab": "Epson (Printer)", "00:24:81": "HP (Printer)",
    "00:80:77": "Brother (Printer)", "00:00:48": "Epson (Printer)",
    "30:cd:a7": "Samsung (Printer)", "00:15:99": "Samsung (Printer)",
    "9c:93:4e": "Xerox (Printer)", "00:1b:a9": "Brother (Printer)",
    # TV / streaming
    "00:1c:62": "LG", "a8:23:fe": "LG", "c8:08:e9": "LG", "10:f1:f2": "LG",
    "00:24:44": "Sony", "00:bb:3a": "Sony", "28:a0:2b": "Sony",
    "f4:35:8d": "Hisense", "b0:41:1d": "TCL", "d8:31:34": "Roku",
    "b0:a7:37": "Roku", "cc:6d:a0": "Roku", "dc:3a:5e": "Vizio",
    "10:4f:a8": "Sony (Bravia)", "ac:9b:0a": "Sony",
    # Phones (other)
    "18:d6:c7": "Xiaomi", "00:ec:0a": "Xiaomi", "a4:50:46": "Xiaomi",
    "f8:8f:ca": "Motorola", "00:18:bd": "Motorola",
    "80:5a:04": "Google (Pixel)", "f8:8a:5e": "Google",
    "2c:5b:b8": "OnePlus", "94:65:2d": "OnePlus", "c0:ee:fb": "OnePlus",
    "00:9a:cd": "Huawei", "48:db:50": "Huawei", "ac:e2:15": "Huawei",
    # VR headsets
    "2c:26:17": "Meta (Oculus/Quest)", "8c:de:52": "Meta (Oculus/Quest)",
    "00:32:c8": "Meta (Oculus/Quest)", "ac:bc:b5": "Meta (Oculus/Quest)",
    "dc:0c:2d": "HTC (Vive)", "00:0c:43": "HTC (Vive)",
    "00:1b:dc": "Valve (Index)", "f8:46:1c": "Pico (VR)",
    # Game consoles
    "7c:bb:8a": "Nintendo (Switch)", "98:b6:e9": "Nintendo (Switch)",
    "00:09:bf": "Nintendo", "e8:4e:ce": "Nintendo (Switch)",
    "00:d9:d1": "Sony (PlayStation)", "00:04:1f": "Sony (PlayStation)",
    "f8:46:1c": "Sony (PlayStation)", "00:50:f2": "Microsoft (Xbox)",
    "7c:1e:52": "Microsoft (Xbox)", "98:5f:d3": "Microsoft (Xbox)",
    # Smart speakers / home hubs
    "44:65:0d": "Amazon (Echo)", "fc:a1:83": "Amazon (Echo)",
    "68:54:fd": "Amazon (Echo)", "f0:ef:86": "Google (Nest/Home)",
    "1c:f2:9a": "Google (Nest/Home)", "30:fd:38": "Google (Nest)",
    # Networking / IoT
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi",
    "e4:5f:01": "Raspberry Pi", "00:17:88": "Philips (Hue)",
    "ec:fa:bc": "TP-Link (IoT)", "50:c7:bf": "TP-Link",
}

# Open ports that hint at a device category
PORT_HINTS = {
    "Printer":    [9100, 631, 515],
    "Smart TV":   [8009, 8008, 8001, 8002, 8060, 7676, 9080, 55000],
    "Game Console": [9295, 9296, 9297, 1935],          # PS Remote Play / streaming
    "VR Headset": [4455, 9943, 9944],                    # Quest casting / ALVR
    "Computer":   [22, 445, 3389, 5900, 139],            # SSH/SMB/RDP/VNC
    "Smart Speaker": [8008, 8009, 10001],
}


def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def get_hostname(ip):
    """Reverse-DNS lookup. Hostnames often reveal the real device
    (e.g. 'Galaxy-S23', 'OculusQuest', 'LIVINGROOM-TV', 'PS5-1234')."""
    try:
        socket.setdefaulttimeout(0.25)
        name = socket.gethostbyaddr(ip)[0]
        return name.split('.')[0]
    except Exception:
        return ""
    finally:
        socket.setdefaulttimeout(None)


def _port_open(ip, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.05)
            return s.connect_ex((ip, port)) == 0
    except Exception:
        return False


def classify_by_hostname(host):
    """Strong signal: many devices broadcast a descriptive hostname."""
    h = host.lower()
    if not h:
        return None
    if any(k in h for k in ["quest", "oculus", "vive", "valve-index", "pico", "vr-"]):
        return "VR Headset"
    if any(k in h for k in ["ps5", "ps4", "playstation", "xbox", "nintendo", "switch"]):
        return "Game Console"
    if any(k in h for k in ["tv", "bravia", "appletv", "firetv", "roku", "chromecast", "shield"]):
        return "Smart TV"
    if any(k in h for k in ["iphone", "ipad", "galaxy", "pixel", "oneplus", "redmi", "android", "-phone"]):
        return "Phone / Tablet"
    if any(k in h for k in ["printer", "epson", "officejet", "laserjet", "brother", "mfc", "mg", "canon"]):
        return "Printer"
    if any(k in h for k in ["echo", "alexa", "homepod", "nest", "google-home", "speaker"]):
        return "Smart Speaker"
    if any(k in h for k in ["macbook", "imac", "desktop", "laptop", "pc", "-mbp", "thinkpad", "dell", "win"]):
        return "Computer"
    if "raspberry" in h or h.startswith("rpi"):
        return "Raspberry Pi"
    return None


def guess_device_type(ip, mac):
    if ip.endswith('.1') or ip.endswith('.254'):
        return "Router / Gateway"

    mac_clean = mac.replace('-', ':').lower()
    mac_prefix = mac_clean[:8]
    vendor = MAC_VENDORS.get(mac_prefix, "")

    # 1. Hostname is the strongest signal when available
    host = get_hostname(ip)
    by_host = classify_by_hostname(host)
    if by_host:
        tag = f" ({vendor})" if vendor else ""
        return f"{by_host}{tag}"

    # 2. Port scan for category hints
    if _port_open(ip, 9100) or _port_open(ip, 631) or _port_open(ip, 515):
        return f"Printer ({vendor})" if vendor else "Printer"
    if any(_port_open(ip, p) for p in PORT_HINTS["Game Console"]):
        return f"Game Console ({vendor})" if vendor else "Game Console"
    if any(_port_open(ip, p) for p in PORT_HINTS["VR Headset"]):
        return f"VR Headset ({vendor})" if vendor else "VR Headset"
    if any(_port_open(ip, p) for p in PORT_HINTS["Smart TV"]):
        return f"Smart TV ({vendor})" if vendor else "Smart TV"
    if any(_port_open(ip, p) for p in PORT_HINTS["Computer"]):
        return f"Computer ({vendor})" if vendor else "Computer"

    # 3. Randomized MAC -> almost always a phone/tablet (privacy feature)
    if len(mac_clean) > 1 and mac_clean[1] in "26ae":
        return "Phone / Tablet (Private MAC)"

    # 4. Fall back to the vendor map
    if "Printer" in vendor:
        return "Printer"
    if "VR" in vendor or "Oculus" in vendor or "Quest" in vendor or "Vive" in vendor or "Index" in vendor or "Pico" in vendor:
        return f"VR Headset ({vendor})"
    if "PlayStation" in vendor or "Xbox" in vendor or "Nintendo" in vendor or "Switch" in vendor:
        return f"Game Console ({vendor})"
    if "Echo" in vendor or "Nest" in vendor or "Home" in vendor:
        return f"Smart Speaker ({vendor})"
    if "Raspberry Pi" in vendor:
        return "Raspberry Pi (Computer)"
    if vendor in ["LG", "Sony", "Hisense", "TCL", "Roku", "Vizio"] or "Bravia" in vendor:
        return f"Smart TV ({vendor})"
    if vendor == "Samsung":
        return "Smart TV / Phone (Samsung)"
    if any(v in vendor for v in ["Apple", "Xiaomi", "Motorola", "Google", "OnePlus", "Huawei"]):
        return f"Phone / Tablet ({vendor})"
    if vendor:
        return f"Device ({vendor})"

    return "Computer / Device"

def _ping_one(ip):
    """Send one fast ICMP ping. Cross-platform (Windows vs Unix flags)."""
    is_win = platform.system().lower().startswith("win")
    if is_win:
        cmd = ["ping", "-n", "1", "-w", "350", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    try:
        _silent_run(cmd, timeout=2)
    except Exception:
        pass
    return ip


# Common ports across phones, VR headsets, TVs, consoles, IoT.
# A TCP connect attempt forces an ARP exchange even if the device
# silently drops ICMP pings (many phones / Quest headsets do this).
_PROBE_PORTS = [80, 443, 8080, 5555, 62078, 9100, 8009, 7676, 5353, 1900]


def _tcp_poke(ip):
    """Touch a few ports so the OS has to ARP-resolve the device,
    even when it ignores ping."""
    for port in _PROBE_PORTS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.12)
                # connect_ex triggers the ARP request regardless of result
                if s.connect_ex((ip, port)) == 0:
                    return ip
        except Exception:
            pass
    return ip


def _flush_arp():
    """Drop stale ARP entries so a freshly powered-on / reconnected
    device isn't masked by an old cached record."""
    try:
        if _IS_WIN:
            _silent_run("arp -d *", shell=True, timeout=4)
        else:
            _silent_run("ip -s -s neigh flush all", shell=True, timeout=4)
    except Exception:
        pass


def _subnet_hosts(base_ip):
    try:
        net = ipaddress.ip_network(base_ip + "/24", strict=False)
        return [str(h) for h in net.hosts()]          # .1 .. .254
    except Exception:
        prefix = ".".join(base_ip.split(".")[:3])
        return [f"{prefix}.{i}" for i in range(1, 255)]


def discover_hosts(base_ip):
    """Two-stage discovery: ICMP ping + TCP poke, in parallel, so
    devices that block ping are still forced into the ARP table."""
    hosts = _subnet_hosts(base_ip)
    with ThreadPoolExecutor(max_workers=128) as pool:
        list(pool.map(_ping_one, hosts))
        list(pool.map(_tcp_poke, hosts))


def _read_arp_table():
    devices = []
    output = _silent_output("arp -a", shell=True).decode('utf-8', errors='ignore')
    pattern = re.compile(
        r"(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F:-]{11,17})\s+(dynamic|static)",
        re.IGNORECASE)
    seen = set()
    for ip, mac, _ in pattern.findall(output):
        if ip.startswith("224.") or ip.startswith("239.") or ip.endswith(".255"):
            continue
        if ip in seen:
            continue
        seen.add(ip)
        devices.append({
            "ip": ip,
            "mac": mac.replace('-', ':').upper(),
            "role": guess_device_type(ip, mac)
        })
    return devices


def get_network_devices(do_sweep=True):
    """Active discovery with a flush + double pass so newly connected
    devices (a phone you just joined, a VR headset you just powered on)
    show up without needing a manual wait."""
    import time
    try:
        base_ip = get_local_ip()

        if do_sweep:
            _flush_arp()                 # clear stale records
            discover_hosts(base_ip)      # pass 1: ping + tcp poke
            time.sleep(1.0)              # let new devices finish DHCP
            discover_hosts(base_ip)      # pass 2: catch the slow joiners

        devices = _read_arp_table()
        devices.sort(key=lambda d: tuple(int(o) for o in d["ip"].split(".")))
        return devices

    except Exception as e:
        print(f"Error scanning network: {e}")
        return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/network-info', methods=['GET'])
def network_info():
    return jsonify({
        "status": "success",
        "ip_address": get_local_ip(),
        "devices": get_network_devices()
    })

@app.route('/download')
def download_app():
    """Build a ZIP of the project on the fly and send it to the browser."""
    import io
    import os
    import sys
    import zipfile
    from flask import send_file

    base = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, 'frozen', False) else __file__
    ))
    files = [
        ("app.pyw", os.path.join(base, "app.pyw")),
        ("templates/index.html", os.path.join(base, "templates", "index.html")),
        ("README.txt", os.path.join(base, "README.txt")),
    ]

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in files:
            if os.path.exists(path):
                zf.write(path, arcname)
    mem.seek(0)
    return send_file(
        mem,
        mimetype='application/zip',
        as_attachment=True,
        download_name='pulse-speedtest.zip'
    )

def _open_browser(url):
    """Open the dashboard in the default browser once the server is up.
    Tries a desktop-style app window first (Chrome --app), then falls
    back to a normal browser tab."""
    import time
    import webbrowser
    import shutil

    # Wait until the server is actually accepting connections
    for _ in range(40):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.25)
                if s.connect_ex(("127.0.0.1", 5000)) == 0:
                    break
        except Exception:
            pass
        time.sleep(0.25)

    # Try Chrome/Edge in "app mode" -> looks like a real desktop app
    # (no address bar / tabs). Falls back to a normal browser tab.
    chrome_candidates = [
        "chrome", "google-chrome", "google-chrome-stable",
        "chromium", "chromium-browser", "msedge",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in chrome_candidates:
        exe = c if (len(c) > 3 and ("\\" in c or "/" in c)) else shutil.which(c)
        if exe:
            try:
                subprocess.Popen(
                    [exe, f"--app={url}", "--window-size=1100,860"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW)
                return
            except Exception:
                pass

    # Fallback: default browser, normal tab
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _run_server_silently():
    """Run Flask with no console output so a pywebview window is the
    only visible UI."""
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    try:
        import flask.cli
        flask.cli.show_server_banner = lambda *a, **k: None
    except Exception:
        pass
    app.run(host='127.0.0.1', port=5000, debug=False,
            use_reloader=False, threaded=True)


def _wait_until_up(host="127.0.0.1", port=5000, tries=60):
    import time
    for _ in range(tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.25)
                if s.connect_ex((host, port)) == 0:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


if __name__ == '__main__':
    import threading

    URL = "http://127.0.0.1:5000"

    # 1. Start the web server in a background thread (no console spam)
    threading.Thread(target=_run_server_silently, daemon=True).start()
    _wait_until_up()

    # 2. Try to open a REAL desktop window (no browser bar, no CMD)
    opened_native = False
    try:
        import webview  # pip install pywebview
        webview.create_window(
            "Pulse - Speed Test & Network Scanner",
            URL,
            width=1120,
            height=860,
            min_size=(900, 700),
        )
        opened_native = True
        webview.start()      # blocks here until the window is closed
    except ImportError:
        # pywebview not installed -> fall back to a browser window
        _open_browser(URL)
    except Exception:
        _open_browser(URL)

    # 3. If we used the browser fallback, keep the process alive so the
    #    server doesn't die while the user is still using the page.
    if not opened_native:
        import sys
        if sys.stdout is not None:
            try:
                print("\n  Pulse is running at " + URL)
                print("  (Install 'pywebview' for a windowed app: pip install pywebview)")
                print("  Press CTRL+C to stop.\n")
            except Exception:
                pass
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
