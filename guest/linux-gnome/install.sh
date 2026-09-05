#!/bin/bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
helper_source="$script_dir/guest/utm_dnd_guest.py"
extension_source="$script_dir/gnome-shell-extension/utm-dnd-target@utmapp.dev"
rule_source="$script_dir/install/70-utm-dnd.rules"
service_source="$script_dir/install/utm-dnd-guest.service"
extension_id=utm-dnd-target@utmapp.dev

fail() {
    echo "install.sh: $*" >&2
    exit 1
}
warn() {
    echo "install.sh: warning: $*" >&2
}

[[ "$(uname -s)" == "Linux" ]] || fail "Linux is required"
[[ "$(id -u)" -ne 0 ]] || fail "run this installer as the desktop user, not root"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v systemctl >/dev/null 2>&1 || fail "systemd is required"
systemctl --user --version >/dev/null 2>&1 || fail "a systemd user manager is unavailable"
command -v gnome-shell >/dev/null 2>&1 || fail "GNOME Shell is required"
command -v gnome-extensions >/dev/null 2>&1 || fail "gnome-extensions is required"
command -v spice-vdagent >/dev/null 2>&1 || warn "spice-vdagent is not installed; file delivery will not work until it is installed"

if [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
    warn "the active session is not Wayland; Wayland is preferred and other sessions are not broadly tested"
fi
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
    warn "XDG_RUNTIME_DIR is unset; run from the graphical user session"
fi

install -Dm755 "$helper_source" "$HOME/.local/libexec/utm-dnd-guest"
install -Dm644 "$service_source" "$HOME/.config/systemd/user/utm-dnd-guest.service"
mkdir -p "$HOME/.local/share/gnome-shell/extensions/$extension_id"
install -m644 "$extension_source/extension.js" "$HOME/.local/share/gnome-shell/extensions/$extension_id/extension.js"
install -m644 "$extension_source/metadata.json" "$HOME/.local/share/gnome-shell/extensions/$extension_id/metadata.json"

sudo install -o root -g root -m 0644 "$rule_source" /etc/udev/rules.d/70-utm-dnd.rules
sudo udevadm control --reload-rules

systemctl --user daemon-reload
systemctl --user enable --now utm-dnd-guest.service
gnome-extensions enable "$extension_id"

echo "Guest tools installed for user $USER."
echo "The helper runs as a user service; spice-vdagent remains unchanged."
echo "A reboot, or logout/login, may be required for the uaccess ACL and Shell extension."
