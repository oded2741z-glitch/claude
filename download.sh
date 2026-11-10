#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash download.sh"
    exit 1
fi

APT_PACKAGES="unclutter evtest ffmpeg x11vnc"
SNAPS="snapd chromium"
ATTEMPTS=5

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

if [ "${1:-}" = "--clean" ]; then
    echo "Removing previous downloads"
    rm -rf "${OUT}"
fi

mkdir -p "${DEB_DIR}" "${SNAP_DIR}"
rm -f "${OUT}/complete"
: > "${ORDER}"
MISSING=()

echo "[1/2] Downloading apt packages"
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
    echo "Failed to update package lists. Check the internet connection and run download.sh again."
    exit 1
fi

cd "${DEB_DIR}"
mapfile -t PKGS < <(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances ${APT_PACKAGES} \
    | grep -E '^[a-z0-9]' | sort -u)

total="${#PKGS[@]}"
count=0
for pkg in "${PKGS[@]}"; do
    count=$((count + 1))
    info="$(apt-cache show --no-all-versions "${pkg}" 2>/dev/null || true)"
    ver="$(sed -n 's/^Version: //p' <<< "${info}" | head -n 1)"
    arch="$(sed -n 's/^Architecture: //p' <<< "${info}" | head -n 1)"
    if [ -z "${ver}" ] || [ -z "${arch}" ]; then
        continue
    fi
    file="${pkg}_${ver//:/%3a}_${arch}.deb"
    if [ -s "${file}" ]; then
        continue
    fi
    echo "  [${count}/${total}] ${pkg}"
    ok=0
    for attempt in $(seq 1 "${ATTEMPTS}"); do
        rm -f "${file}"
        if apt-get -o Acquire::Retries=3 download "${pkg}" >/dev/null 2>&1 && [ -s "${file}" ]; then
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

apt-ftparchive packages . > Packages
for pkg in ${APT_PACKAGES}; do
    if ! grep -q "^Package: ${pkg}$" Packages; then
        MISSING+=("${pkg}")
    fi
done

echo "[2/2] Downloading snaps"
snap_yaml() {
    rm -rf "${WORK}/x"
    unsquashfs -n -d "${WORK}/x" "$1" meta/snap.yaml >/dev/null
    cat "${WORK}/x/meta/snap.yaml"
}

snap_ready() {
    local name="$1"
    local snaps asserts
    snaps="$(ls "${SNAP_DIR}/${name}"_*.snap 2>/dev/null | wc -l)"
    asserts="$(ls "${SNAP_DIR}/${name}"_*.assert 2>/dev/null | wc -l)"
    [ "${snaps}" -eq 1 ] && [ "${asserts}" -eq 1 ]
}

download_snap() {
    local name="$1"
    local attempt
    if snap_ready "${name}"; then
        return 0
    fi
    for attempt in $(seq 1 "${ATTEMPTS}"); do
        rm -f "${SNAP_DIR}/${name}"_*.snap "${SNAP_DIR}/${name}"_*.assert "${SNAP_DIR}/${name}"_*.partial
        if (cd "${SNAP_DIR}" && snap download "${name}" >/dev/null 2>&1) && snap_ready "${name}"; then
            return 0
        fi
        echo "    retry ${attempt}/${ATTEMPTS}: ${name}"
        sleep 5
    done
    rm -f "${SNAP_DIR}/${name}"_*.snap "${SNAP_DIR}/${name}"_*.assert "${SNAP_DIR}/${name}"_*.partial
    return 1
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
    if ! download_snap "${name}"; then
        MISSING+=("snap:${name}")
        return 0
    fi
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

if [ "${#MISSING[@]}" -gt 0 ]; then
    echo
    echo "Not downloaded (${#MISSING[@]}):"
    for m in "${MISSING[@]}"; do
        echo "  ${m}"
    done
    echo
    echo "Run download.sh again to continue. Files already downloaded are kept."
    exit 1
fi

touch "${OUT}/complete"

echo
echo "Done. Files saved to: ${OUT}"
echo "Copy setup.sh and the kiosk-offline folder to the tablet."
