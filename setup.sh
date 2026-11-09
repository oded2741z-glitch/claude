#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash setup.sh http://192.168.1.10"
    exit 1
fi

if [ $# -ne 1 ]; then
    echo "Usage: sudo bash setup.sh <URL>"
    exit 1
fi

exec > >(tee -a /var/log/kiosk-setup.log) 2>&1

URL="$1"
KIOSK_USER="kiosk"
APT_PACKAGES="unclutter"
OFFLINE="$(cd "$(dirname "$0")" && pwd)/kiosk-offline"
LOCAL_REPO="/var/tmp/kiosk-debs"

if ! dpkg -s gdm3 >/dev/null 2>&1; then
    echo "Ubuntu Desktop (gdm3) is required."
    exit 1
fi

if [ ! -f "${OFFLINE}/complete" ]; then
    echo "Offline files missing or incomplete: ${OFFLINE}"
    echo "Run download.sh again on a computer with internet."
    exit 1
fi

echo "[1/9] Installing snaps"
while read -r stem; do
    [ -z "${stem}" ] && continue
    name="${stem%_*}"
    rev="${stem##*_}"
    current="$(snap list "${name}" 2>/dev/null | awk 'NR==2 {print $3}' || true)"
    if [ -z "${current}" ] || { [[ "${current}" =~ ^[0-9]+$ ]] && [ "${rev}" -gt "${current}" ]; }; then
        echo "  ${name}"
        snap ack "${OFFLINE}/snaps/${stem}.assert" < /dev/null
        snap install "${OFFLINE}/snaps/${stem}.snap" < /dev/null
    fi
done < "${OFFLINE}/snaps/order.txt"

echo "[2/9] Installing apt packages"
rm -rf "${LOCAL_REPO}"
cp -r "${OFFLINE}/debs" "${LOCAL_REPO}"
chmod -R a+rX "${LOCAL_REPO}"
echo "deb [trusted=yes] file:${LOCAL_REPO} ./" > "${LOCAL_REPO}/local.list"
APT_LOCAL=(-o "Dir::Etc::sourcelist=${LOCAL_REPO}/local.list" -o "Dir::Etc::sourceparts=-" -o "APT::Get::List-Cleanup=0")
apt-get "${APT_LOCAL[@]}" update
DEBIAN_FRONTEND=noninteractive apt-get "${APT_LOCAL[@]}" install -y ${APT_PACKAGES}

echo "[3/9] Creating user ${KIOSK_USER}"
if ! id "${KIOSK_USER}" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "${KIOSK_USER}"
fi
passwd -d "${KIOSK_USER}"

echo "[4/9] Saving kiosk URL"
mkdir -p /etc/kiosk
echo "${URL}" > /etc/kiosk/url

echo "[5/9] Creating kiosk session"
cat > /usr/local/bin/kiosk-session <<'EOF'
#!/bin/bash
xset s off
xset s noblank
xset -dpms
unclutter -idle 1 -root &

URL="$(cat /etc/kiosk/url)"
PREFS="$HOME/snap/chromium/common/chromium/Default/Preferences"

while true; do
    if [ -f "$PREFS" ]; then
        sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS"
    fi
    SIZE="$(xrandr --current 2>/dev/null | sed -n 's/.* current \([0-9]*\) x \([0-9]*\),.*/\1,\2/p' | head -n 1)"
    WINDOW=()
    if [ -n "$SIZE" ]; then
        WINDOW=(--window-position=0,0 --window-size="$SIZE")
    fi
    /snap/bin/chromium \
        --kiosk \
        "${WINDOW[@]}" \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=Translate \
        --no-first-run \
        --password-store=basic \
        --check-for-update-interval=31536000 \
        --overscroll-history-navigation=0 \
        --disable-pinch \
        "$URL"
    sleep 2
done
EOF
chmod 755 /usr/local/bin/kiosk-session

cat > /usr/share/xsessions/kiosk.desktop <<'EOF'
[Desktop Entry]
Name=Kiosk
Exec=/usr/local/bin/kiosk-session
Type=Application
EOF

mkdir -p /var/lib/AccountsService/users
cat > "/var/lib/AccountsService/users/${KIOSK_USER}" <<'EOF'
[User]
Session=kiosk
XSession=kiosk
SystemAccount=false
EOF

echo "[6/9] Enabling auto-login"
GDM_CONF="/etc/gdm3/custom.conf"
touch "${GDM_CONF}"
if ! grep -q '^\[daemon\]' "${GDM_CONF}"; then
    printf '[daemon]\n' >> "${GDM_CONF}"
fi
sed -i '/^#\?[[:space:]]*AutomaticLogin/d' "${GDM_CONF}"
sed -i "/^\[daemon\]/a AutomaticLoginEnable=true\nAutomaticLogin=${KIOSK_USER}" "${GDM_CONF}"

echo "[7/9] Enabling on-screen keyboard on login screen"
mkdir -p /etc/dconf/profile /etc/dconf/db/gdm.d
if [ ! -f /etc/dconf/profile/gdm ]; then
    printf 'user-db:user\nsystem-db:gdm\nfile-db:/usr/share/gdm/greeter-dconf-defaults\n' > /etc/dconf/profile/gdm
fi
cat > /etc/dconf/db/gdm.d/90-kiosk <<'EOF'
[org/gnome/desktop/a11y/applications]
screen-keyboard-enabled=true
EOF
dconf update

echo "[8/9] Locking browser and shortcuts"
mkdir -p /etc/chromium-browser/policies/managed
cat > /etc/chromium-browser/policies/managed/kiosk.json <<'EOF'
{
    "DeveloperToolsAvailability": 2,
    "IncognitoModeAvailability": 1,
    "BrowserGuestModeEnabled": false,
    "BrowserAddPersonEnabled": false,
    "PasswordManagerEnabled": false,
    "TranslateEnabled": false,
    "DownloadRestrictions": 3,
    "PrintingEnabled": false
}
EOF

mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/10-kiosk.conf <<'EOF'
Section "ServerFlags"
    Option "DontVTSwitch" "true"
    Option "DontZap" "true"
EndSection
EOF
systemctl mask ctrl-alt-del.target

echo "[9/9] Installing exit and brightness service"
cat > /usr/local/bin/kiosk-corner <<'EOF'
import fcntl
import os
import select
import struct
import subprocess
import time

KIOSK_USER = "kiosk"
CORNER = 0.08
HOLD = 5.0
STEP = 0.10
MIN_LEVEL = 0.05
DEBOUNCE = 0.15

EVENT = struct.Struct("llHHi")
EV_KEY = 1
EV_ABS = 3
ABS_X = 0
ABS_Y = 1
BTN_TOUCH = 0x14A
KEY_LEFTCTRL = 29
KEY_LEFTALT = 56
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_DELETE = 111
KEY_BRIGHTNESSDOWN = 224
KEY_BRIGHTNESSUP = 225
CTRL_KEYS = {KEY_LEFTCTRL, KEY_RIGHTCTRL}
ALT_KEYS = {KEY_LEFTALT, KEY_RIGHTALT}
WATCHED_KEYS = (KEY_DELETE, KEY_BRIGHTNESSDOWN, KEY_BRIGHTNESSUP)
INPUT_PROP_DIRECT = 1
BACKLIGHT = "/sys/class/backlight"
BACKLIGHT_TYPES = ("firmware", "platform", "raw")


def ioc_read(nr, size):
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


def abs_range(fd, axis):
    buf = bytearray(24)
    fcntl.ioctl(fd, ioc_read(0x40 + axis, 24), buf)
    values = struct.unpack("6i", buf)
    return values[1], values[2]


def is_direct(fd):
    buf = bytearray(4)
    fcntl.ioctl(fd, ioc_read(0x09, 4), buf)
    return bool(buf[0] & (1 << INPUT_PROP_DIRECT))


def has_watched_keys(fd):
    buf = bytearray(96)
    fcntl.ioctl(fd, ioc_read(0x20 + EV_KEY, 96), buf)
    return any(buf[code // 8] & (1 << (code % 8)) for code in WATCHED_KEYS)


def new_state(xr=None, yr=None):
    return {"x": xr, "y": yr, "px": None, "py": None, "down": False,
            "since": None, "fired": False, "held": set()}


def open_devices():
    devices = {}
    for name in sorted(os.listdir("/dev/input")):
        if not name.startswith("event"):
            continue
        try:
            fd = os.open("/dev/input/" + name, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        state = None
        try:
            if is_direct(fd):
                xr = abs_range(fd, ABS_X)
                yr = abs_range(fd, ABS_Y)
                if xr[1] > xr[0] and yr[1] > yr[0]:
                    state = new_state(xr, yr)
        except OSError:
            pass
        if state is None:
            try:
                if has_watched_keys(fd):
                    state = new_state()
            except OSError:
                pass
        if state is None:
            os.close(fd)
        else:
            devices[fd] = state
    return devices


def in_corner(d):
    if d["x"] is None or d["px"] is None or d["py"] is None:
        return False
    xlo, xhi = d["x"]
    ylo, yhi = d["y"]
    return (d["px"] - xlo) <= CORNER * (xhi - xlo) and (d["py"] - ylo) <= CORNER * (yhi - ylo)


def loginctl(*args):
    result = subprocess.run(["loginctl", *args], capture_output=True, text=True)
    return result.stdout.strip()


def kiosk_active():
    session = loginctl("show-seat", "seat0", "-p", "ActiveSession", "--value")
    if not session:
        return False
    return loginctl("show-session", session, "-p", "Name", "--value") == KIOSK_USER


def exit_kiosk():
    if kiosk_active():
        subprocess.run(["loginctl", "terminate-user", KIOSK_USER])


def backlight_path():
    try:
        names = sorted(os.listdir(BACKLIGHT))
    except OSError:
        return None
    for kind in BACKLIGHT_TYPES:
        for name in names:
            path = os.path.join(BACKLIGHT, name)
            try:
                with open(os.path.join(path, "type")) as f:
                    if f.read().strip() == kind:
                        return path
            except OSError:
                continue
    return None


def change_brightness(direction):
    path = backlight_path()
    if path is None:
        return
    try:
        with open(os.path.join(path, "max_brightness")) as f:
            maximum = int(f.read())
        with open(os.path.join(path, "brightness")) as f:
            current = int(f.read())
        step = max(1, round(maximum * STEP))
        low = max(1, round(maximum * MIN_LEVEL))
        value = min(maximum, max(low, current + direction * step))
        with open(os.path.join(path, "brightness"), "w") as f:
            f.write(str(value))
    except (OSError, ValueError):
        pass


def watch(devices):
    last_brightness = 0.0
    while True:
        ready, _, _ = select.select(list(devices), [], [], 0.2)
        for fd in ready:
            d = devices[fd]
            try:
                data = os.read(fd, EVENT.size * 64)
            except BlockingIOError:
                continue
            for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, code, value = EVENT.unpack_from(data, offset)
                if etype == EV_ABS and code == ABS_X:
                    d["px"] = value
                elif etype == EV_ABS and code == ABS_Y:
                    d["py"] = value
                elif etype != EV_KEY:
                    continue
                elif code == BTN_TOUCH:
                    d["down"] = value == 1
                elif code in CTRL_KEYS or code in ALT_KEYS:
                    if value:
                        d["held"].add(code)
                    else:
                        d["held"].discard(code)
                elif code == KEY_DELETE and value == 1:
                    if d["held"] & CTRL_KEYS and d["held"] & ALT_KEYS:
                        exit_kiosk()
                elif code in (KEY_BRIGHTNESSUP, KEY_BRIGHTNESSDOWN) and value in (1, 2):
                    now = time.monotonic()
                    if now - last_brightness >= DEBOUNCE and kiosk_active():
                        last_brightness = now
                        change_brightness(1 if code == KEY_BRIGHTNESSUP else -1)
        now = time.monotonic()
        for d in devices.values():
            if d["down"] and in_corner(d):
                if d["since"] is None:
                    d["since"] = now
                elif not d["fired"] and now - d["since"] >= HOLD:
                    d["fired"] = True
                    exit_kiosk()
            else:
                d["since"] = None
                d["fired"] = False


def main():
    while True:
        devices = open_devices()
        if not devices:
            time.sleep(5)
            continue
        try:
            watch(devices)
        except OSError:
            time.sleep(1)
        finally:
            for fd in devices:
                try:
                    os.close(fd)
                except OSError:
                    pass


main()
EOF
chmod 755 /usr/local/bin/kiosk-corner

cat > /etc/systemd/system/kiosk-corner.service <<'EOF'
[Unit]
Description=Kiosk exit and brightness buttons
After=systemd-logind.service

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/kiosk-corner
Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
EOF
systemctl daemon-reload
systemctl enable kiosk-corner.service

echo
echo "Done. Kiosk URL: ${URL}"
echo "Reboot to start the kiosk: sudo reboot"
