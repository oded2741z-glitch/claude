#!/usr/bin/env bash
# P2P Intercom - Linux installer (systemd user service).
#
#   ./install.sh a                 computer A: signalling server + peer
#   ./install.sh b 10.0.0.5        computer B: peer, pointed at A
#   ./install.sh a "" ~/intercom   third argument overrides the folder
#
# A *user* service on purpose, not a system one: a system service runs outside
# the login session and cannot reach the user's PipeWire/PulseAudio, so the
# call would connect and stay silent.
set -euo pipefail

ROLE="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
SERVER_IP="${2:-}"
DEST="${3:-$HOME/intercom}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="intercom-${ROLE}.service"

die() { printf '[X] %s\n' "$*" >&2; exit 1; }

case "$ROLE" in
    a|b) ;;
    *)
        cat >&2 <<USAGE

  Usage: ./install.sh a|b [server-ip] [install-folder]

  a = this computer runs the signalling server and takes part in the call
  b = this computer is a client only; pass the address of computer A

USAGE
        exit 1 ;;
esac

UPPER="$(printf '%s' "$ROLE" | tr '[:lower:]' '[:upper:]')"
printf '\n=== P2P Intercom installer - role %s ===\n\n' "$UPPER"

# --- 1. Python -------------------------------------------------------------
command -v python3 >/dev/null || die "python3 is not installed."
printf '[1/5] Found %s\n' "$(python3 --version)"

# --- 2. Dependencies -------------------------------------------------------
echo "[2/5] Installing sounddevice and numpy..."
python3 -m pip install --quiet --user --upgrade sounddevice numpy \
    || die "pip failed. On Debian/Ubuntu you may need: sudo apt install python3-pip"
python3 -c "import sounddevice, numpy" 2>/dev/null || die \
    "sounddevice does not import - PortAudio is missing: sudo apt install libportaudio2"

# --- 3. Files --------------------------------------------------------------
printf '[3/5] Copying to %s ...\n' "$DEST"
[ -f "$SRC/intercom_${UPPER}.py" ] || die "cannot find $SRC/intercom_${UPPER}.py"
mkdir -p "$DEST"
install -m 0755 "$SRC/intercom_${UPPER}.py" "$DEST/"

# --- 4. Service ------------------------------------------------------------
echo "[4/5] Writing the systemd user unit..."
ARGS=""
if [ "$ROLE" = "b" ] && [ -n "$SERVER_IP" ]; then
    ARGS="--server-ip $SERVER_IP"
fi
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$UNIT" <<UNITEOF
[Unit]
Description=P2P Intercom node ${UPPER}
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${DEST}
ExecStart=$(command -v python3) ${DEST}/intercom_${UPPER}.py ${ARGS}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNITEOF

# --- 5. Enable -------------------------------------------------------------
echo "[5/5] Enabling the service..."
if systemctl --user daemon-reload 2>/dev/null; then
    systemctl --user enable --now "$UNIT"
    # בלי linger השירות יורד ברגע שהמשתמש מתנתק, וגם לא עולה עם המחשב
    loginctl enable-linger "$USER" 2>/dev/null \
        || echo "[!] enable-linger failed; the node will only run while you are logged in."
else
    echo "[!] No systemd user session here. Start it by hand:"
    echo "    cd $DEST && python3 intercom_${UPPER}.py $ARGS"
fi

cat <<DONE

=== Done ===
  Turn call on:   echo on  > $DEST/switch_${UPPER}.txt
  Turn call off:  echo off > $DEST/switch_${UPPER}.txt
  See state:      cat $DEST/status_${UPPER}.txt
  Follow the log: journalctl --user -u $UNIT -f
  Stop:           systemctl --user stop $UNIT

DONE
if [ "$ROLE" = "a" ]; then
    echo "  Edit $DEST/control_A.txt after the first run to set ext_ip / local_mode."
    echo "  Open UDP 9999 inbound if a firewall is running."
else
    echo "  Check that server_ip in $DEST/control_B.txt points at computer A."
fi
echo
