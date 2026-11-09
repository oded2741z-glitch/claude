#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash download.sh"
    exit 1
fi

APT_PACKAGES="unclutter evtest ffmpeg x11vnc"
CHROME_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
CHROME_DEB="google-chrome-stable_current_amd64.deb"

OUT="$(cd "$(dirname "$0")" && pwd)/kiosk-offline"
DEB_DIR="${OUT}/debs"

for cmd in wget apt-ftparchive dpkg-deb; do
    if ! command -v "${cmd}" >/dev/null; then
        echo "Missing command: ${cmd}"
        exit 1
    fi
done

rm -rf "${OUT}"
mkdir -p "${DEB_DIR}"
cd "${DEB_DIR}"

echo "[1/2] Downloading Google Chrome"
if ! wget --tries=5 --timeout=60 -q -O "${CHROME_DEB}" "${CHROME_URL}"; then
    echo "Failed to download Google Chrome"
    exit 1
fi
CHROME_DEPS="$(dpkg-deb -f "${CHROME_DEB}" Depends | tr ',' '\n' | sed 's/|.*//; s/(.*)//; s/:any//; s/[[:space:]]//g' | grep -v '^$' | sort -u | tr '\n' ' ')"

echo "[2/2] Downloading apt packages"
apt-get -o Acquire::Retries=5 update
apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances ${APT_PACKAGES} ${CHROME_DEPS} \
    | grep -E '^[a-z0-9]' | sort -u | while read -r pkg; do
    apt-get -o Acquire::Retries=5 download "${pkg}" >/dev/null 2>&1 || true
done
apt-ftparchive packages . > Packages
for pkg in ${APT_PACKAGES} google-chrome-stable; do
    if ! grep -q "^Package: ${pkg}$" Packages; then
        echo "Failed to download: ${pkg}"
        exit 1
    fi
done

touch "${OUT}/complete"

echo
echo "Done. Files saved to: ${OUT}"
echo "Copy setup.sh and the kiosk-offline folder to the tablet."
