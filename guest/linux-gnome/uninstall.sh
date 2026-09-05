#!/bin/bash
set -euo pipefail

extension_id=utm-dnd-target@utmapp.dev

[[ "$(uname -s)" == "Linux" ]] || { echo "uninstall.sh: Linux is required" >&2; exit 1; }
[[ "$(id -u)" -ne 0 ]] || { echo "uninstall.sh: run as the desktop user, not root" >&2; exit 1; }

if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions disable "$extension_id" >/dev/null 2>&1 || true
fi
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now utm-dnd-guest.service >/dev/null 2>&1 || true
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

rm -f "$HOME/.local/libexec/utm-dnd-guest"
rm -f "$HOME/.config/systemd/user/utm-dnd-guest.service"
rm -rf "$HOME/.local/share/gnome-shell/extensions/$extension_id"

sudo rm -f /etc/udev/rules.d/70-utm-dnd.rules
sudo udevadm control --reload-rules

echo "UTM DnD guest tools removed. spice-vdagent and unrelated user files were left untouched."
