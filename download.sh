#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash download.sh"
    exit 1
fi

APT_PACKAGES="unclutter evtest"
SNAPS="snapd chromium"

OUT="$(cd "$(dirname "$0")" && pwd)/kiosk-offline"
DEB_DIR="${OUT}/debs"
SNAP_DIR="${OUT}/snaps"
ORDER="${SNAP_DIR}/order.txt"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

for cmd in snap apt-ftparchive unsquashfs; do
    if ! command -v "${cmd}" >/dev/null; then
        echo "Missing command: ${cmd}"
        exit 1
    fi
done

rm -rf "${OUT}"
mkdir -p "${DEB_DIR}" "${SNAP_DIR}"
: > "${ORDER}"

echo "[1/2] Downloading apt packages"
apt-get -o Acquire::Retries=5 update
cd "${DEB_DIR}"
apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances ${APT_PACKAGES} \
    | grep -E '^[a-z0-9]' | sort -u | while read -r pkg; do
    apt-get -o Acquire::Retries=5 download "${pkg}" >/dev/null 2>&1 || true
done
apt-ftparchive packages . > Packages
for pkg in ${APT_PACKAGES}; do
    if ! grep -q "^Package: ${pkg}$" Packages; then
        echo "Failed to download: ${pkg}"
        exit 1
    fi
done

echo "[2/2] Downloading snaps"
snap_yaml() {
    rm -rf "${WORK}/x"
    unsquashfs -n -d "${WORK}/x" "$1" meta/snap.yaml >/dev/null
    cat "${WORK}/x/meta/snap.yaml"
}

download_snap() {
    local name="$1"
    local attempt
    for attempt in 1 2 3 4 5; do
        rm -f "${SNAP_DIR}/${name}"_*.snap "${SNAP_DIR}/${name}"_*.assert
        if (cd "${SNAP_DIR}" && snap download "${name}" >/dev/null); then
            return 0
        fi
        echo "  retry ${attempt}/5: ${name}"
        sleep 5
    done
    echo "Failed to download snap: ${name}"
    exit 1
}

declare -A SEEN

fetch_snap() {
    local name="$1"
    local file yaml type base deps dep
    if [ -n "${SEEN[$name]:-}" ]; then
        return 0
    fi
    SEEN[$name]=1
    echo "  ${name}"
    download_snap "${name}"
    file="$(ls "${SNAP_DIR}/${name}"_*.snap | head -n 1)"
    yaml="$(snap_yaml "${file}")"
    type="$(sed -n 's/^type:[[:space:]]*//p' <<< "${yaml}" | tr -d "\"'")"
    base="$(sed -n 's/^base:[[:space:]]*//p' <<< "${yaml}" | tr -d "\"'")"
    if [ -z "${base}" ] && { [ -z "${type}" ] || [ "${type}" = "app" ]; }; then
        base="core"
    fi
    deps="$(sed -n 's/^[[:space:]]*default-provider:[[:space:]]*//p' <<< "${yaml}" | tr -d "\"'" | cut -d: -f1 | sort -u)"
    for dep in ${base} ${deps}; do
        fetch_snap "${dep}"
    done
    basename "${file}" .snap >> "${ORDER}"
}

for s in ${SNAPS}; do
    fetch_snap "${s}"
done

touch "${OUT}/complete"

echo
echo "Done. Files saved to: ${OUT}"
echo "Copy setup.sh and the kiosk-offline folder to the tablet."
