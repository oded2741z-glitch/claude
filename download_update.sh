#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash download_update.sh"
    exit 1
fi

PACKAGES="ubuntu-desktop gdm3"
ATTEMPTS=5

OUT="$(cd "$(dirname "$0")" && pwd)/ubuntu-update"
DEB_DIR="${OUT}/debs"

if ! command -v apt-ftparchive >/dev/null; then
    echo "Missing command: apt-ftparchive"
    exit 1
fi

if [ "${1:-}" = "--clean" ]; then
    echo "Removing previous downloads"
    rm -rf "${OUT}"
fi

mkdir -p "${DEB_DIR}"
rm -f "${OUT}/complete"
MISSING=()

echo "[1/3] Updating package lists"
updated=0
for attempt in $(seq 1 "${ATTEMPTS}"); do
    if apt-get -o Acquire::Retries=3 update --error-on=any; then
        updated=1
        break
    fi
    echo "  retry ${attempt}/${ATTEMPTS}: apt-get update"
    sleep 5
done
if [ "${updated}" -ne 1 ]; then
    echo "Failed to update package lists. Check the internet connection and run download_update.sh again."
    exit 1
fi

echo "[2/3] Downloading packages"
cd "${DEB_DIR}"
rm -f ./*_i386.deb
mapfile -t PKGS < <(
    {
        dpkg-query -W -f='${Package}:${Architecture} ${Status}\n' | awk '$4 == "installed" {print $1}'
        apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances ${PACKAGES} \
            | grep -E '^[a-z0-9]'
    } | sed 's/:amd64$//; s/:all$//' | grep -v ':' | sort -u
)

total="${#PKGS[@]}"
count=0
for pkg in "${PKGS[@]}"; do
    count=$((count + 1))
    line="$(apt-get download --print-uris "${pkg}:amd64" 2>/dev/null | head -n 1 || true)"
    if [ -z "${line}" ]; then
        continue
    fi
    file="$(awk '{print $2}' <<< "${line}")"
    if [ -s "${file}" ]; then
        continue
    fi
    echo "  [${count}/${total}] ${pkg}"
    ok=0
    for attempt in $(seq 1 "${ATTEMPTS}"); do
        rm -f "${file}"
        if apt-get -o Acquire::Retries=3 download "${pkg}:amd64" >/dev/null 2>&1 && [ -s "${file}" ]; then
            ok=1
            break
        fi
        echo "    retry ${attempt}/${ATTEMPTS}"
        sleep 5
    done
    if [ "${ok}" -ne 1 ]; then
        rm -f "${file}"
        MISSING+=("${pkg}")
    fi
done

echo "[3/3] Creating package index"
apt-ftparchive packages . > Packages
for pkg in ${PACKAGES}; do
    if ! grep -q "^Package: ${pkg}$" Packages; then
        MISSING+=("${pkg}")
    fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
    echo
    echo "Not downloaded (${#MISSING[@]}):"
    for m in "${MISSING[@]}"; do
        echo "  ${m}"
    done
    echo
    echo "Run download_update.sh again to continue. Files already downloaded are kept."
    exit 1
fi

touch "${OUT}/complete"

echo
echo "Done. Files saved to: ${OUT}"
echo "Size: $(du -sh "${OUT}" | cut -f1)"
echo "Copy update_offline.sh and the ubuntu-update folder to the tablet."
