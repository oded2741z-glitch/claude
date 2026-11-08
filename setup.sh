#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash setup.sh https://example.com"
    exit 1
fi

if [ $# -ne 1 ]; then
    echo "Usage: sudo bash setup.sh <URL>"
    exit 1
fi

URL="$1"
KIOSK_USER="kiosk"
KIOSK_HOME="/home/${KIOSK_USER}"

echo "[1/8] Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends xserver-xorg xserver-xorg-legacy xinit x11-xserver-utils unclutter
snap install chromium

echo "[2/8] Creating user ${KIOSK_USER}"
if ! id "${KIOSK_USER}" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "${KIOSK_USER}"
    passwd -d "${KIOSK_USER}"
fi

echo "[3/8] Enabling auto-login on tty1"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${KIOSK_USER} --noclear --skip-login --nonewline --noissue %I \$TERM
Restart=always
EOF

echo "[4/8] Starting X automatically after login"
cat > "${KIOSK_HOME}/.bash_profile" <<'EOF'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx -- -nocursor >/dev/null 2>&1
fi
EOF

echo "[5/8] Creating browser loop"
cat > "${KIOSK_HOME}/.xinitrc" <<EOF
#!/bin/bash
xset s off
xset s noblank
xset -dpms
unclutter -idle 1 -root &

PREFS="\$HOME/snap/chromium/common/chromium/Default/Preferences"

while true; do
    if [ -f "\$PREFS" ]; then
        sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "\$PREFS"
    fi
    /snap/bin/chromium \\
        --kiosk \\
        --noerrdialogs \\
        --disable-infobars \\
        --disable-session-crashed-bubble \\
        --disable-features=Translate \\
        --no-first-run \\
        --check-for-update-interval=31536000 \\
        --overscroll-history-navigation=0 \\
        --disable-pinch \\
        "${URL}"
    sleep 2
done
EOF
chmod +x "${KIOSK_HOME}/.xinitrc"
chown "${KIOSK_USER}:${KIOSK_USER}" "${KIOSK_HOME}/.bash_profile" "${KIOSK_HOME}/.xinitrc"

echo "[6/8] Locking browser with policies"
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

echo "[7/8] Blocking system shortcuts"
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/10-kiosk.conf <<'EOF'
Section "ServerFlags"
    Option "DontVTSwitch" "true"
    Option "DontZap" "true"
EndSection
EOF
cat > /etc/X11/Xwrapper.config <<'EOF'
allowed_users=console
needs_root_rights=yes
EOF
systemctl mask ctrl-alt-del.target

echo "[8/8] Disabling desktop environment"
systemctl set-default multi-user.target
systemctl daemon-reload

echo
echo "Done. Kiosk URL: ${URL}"
echo "Reboot to start the kiosk: sudo reboot"
