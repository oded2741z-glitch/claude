#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash update_offline.sh"
    exit 1
fi

exec > >(tee -a /var/log/ubuntu-update.log) 2>&1

PACKAGES="ubuntu-desktop gdm3"
OFFLINE="$(cd "$(dirname "$0")" && pwd)/ubuntu-update"
LOCAL_REPO="/var/tmp/ubuntu-update-debs"

if [ ! -f "${OFFLINE}/complete" ]; then
    echo "Offline files missing or incomplete: ${OFFLINE}"
    echo "Run download_update.sh again on a computer with internet."
    exit 1
fi

echo "[1/4] Preparing local package source"
dpkg --configure -a
rm -rf "${LOCAL_REPO}"
cp -r "${OFFLINE}/debs" "${LOCAL_REPO}"
chmod -R a+rX "${LOCAL_REPO}"
echo "deb [trusted=yes] file:${LOCAL_REPO} ./" > "${LOCAL_REPO}/local.list"
APT_LOCAL=(-o "Dir::Etc::sourcelist=${LOCAL_REPO}/local.list" -o "Dir::Etc::sourceparts=-" -o "APT::Get::List-Cleanup=0")
APT_SAFE=(-o "Dpkg::Options::=--force-confdef" -o "Dpkg::Options::=--force-confold" --no-remove -y)
export DEBIAN_FRONTEND=noninteractive
apt-get "${APT_LOCAL[@]}" update

echo "[2/4] Upgrading installed packages"
apt-get "${APT_LOCAL[@]}" "${APT_SAFE[@]}" --with-new-pkgs upgrade

echo "[3/4] Installing Ubuntu Desktop"
apt-get "${APT_LOCAL[@]}" "${APT_SAFE[@]}" install ${PACKAGES}

echo "[4/4] Enabling graphical login"
systemctl set-default graphical.target
rm -rf "${LOCAL_REPO}"

echo
echo "Done. Log: /var/log/ubuntu-update.log"
echo "Reboot now: sudo reboot"
