#!/bin/bash
set -euo pipefail

extension_id=utm-dnd-target@utmapp.dev
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
rule_source="$script_dir/install/70-utm-dnd.rules"
rule_target=/etc/udev/rules.d/70-utm-dnd.rules
extension_dir="$HOME/.local/share/gnome-shell/extensions/$extension_id"

warn() {
    echo "uninstall.sh: warning: $*" >&2
}

remove_project_udev_rule() {
    local rule_snapshot sentinel
    sentinel=$'\037'
    rule_snapshot=$(cat -- "$rule_source" && printf '%s' "$sentinel") || {
        warn "could not read udev rule source; preserving $rule_target"
        return 0
    }
    if [[ "$rule_snapshot" != *"$sentinel" ]]; then
        warn "could not snapshot udev rule source; preserving $rule_target"
        return 0
    fi
    rule_snapshot=${rule_snapshot%"$sentinel"}

    printf '%s' "$rule_snapshot" | \
    sudo bash -eu -o pipefail -c '
        rule_target=$1
        rule_directory=${rule_target%/*}
        quarantine_directory=$(mktemp -d -- "$rule_directory/.70-utm-dnd-uninstall.XXXXXX")
        quarantine="$quarantine_directory/70-utm-dnd.rules"
        snapshot=$(mktemp -- "$quarantine_directory/expected.XXXXXX")
        cat > "$snapshot"
        chmod 0644 -- "$snapshot"
        restore_needed=false

        restore_quarantine() {
            restore_needed=false
            if mv -T -n -- "$quarantine" "$rule_target" && [[ ! -e "$quarantine" ]] && [[ ! -L "$quarantine" ]]; then
                rm -f -- "$snapshot"
                rmdir -- "$quarantine_directory"
                return
            fi
            echo "uninstall.sh: warning: $rule_target changed concurrently; preserved prior file at $quarantine" >&2
        }
        # shellcheck disable=SC2317,SC2329
        cleanup() {
            if [[ "$restore_needed" == true ]]; then
                if [[ -e "$quarantine" ]] || [[ -L "$quarantine" ]]; then
                    restore_quarantine
                else
                    restore_needed=false
                    rm -f -- "$snapshot"
                    rmdir -- "$quarantine_directory" 2>/dev/null || true
                fi
            fi
        }
        trap cleanup EXIT
        trap '\''exit 129'\'' HUP
        trap '\''exit 130'\'' INT
        trap '\''exit 143'\'' TERM

        restore_needed=true
        if ! mv -- "$rule_target" "$quarantine" 2>/dev/null; then
            restore_needed=false
            rm -f -- "$snapshot"
            rmdir -- "$quarantine_directory"
            if [[ ! -e "$rule_target" ]] && [[ ! -L "$rule_target" ]]; then
                exit 0
            fi
            echo "uninstall.sh: could not quarantine $rule_target; preserving it" >&2
            exit 74
        fi
        if [[ -L "$quarantine" ]]; then
            rm -f -- "$snapshot"
            echo "uninstall.sh: warning: not removing symbolic link $rule_target" >&2
            restore_quarantine
            exit 0
        fi
        if [[ ! -f "$quarantine" ]] || ! cmp -s -- "$snapshot" "$quarantine"; then
            rm -f -- "$snapshot"
            echo "uninstall.sh: warning: not removing $rule_target; existing file has different content" >&2
            restore_quarantine
            exit 0
        fi
        metadata=$(stat -Lc "%u:%g:%a" -- "$quarantine")
        if [[ "$metadata" != "0:0:644" ]]; then
            rm -f -- "$snapshot"
            echo "uninstall.sh: warning: not removing $rule_target; untrusted ownership or mode $metadata" >&2
            restore_quarantine
            exit 0
        fi
        rm -f -- "$snapshot" "$quarantine"
        restore_needed=false
        rmdir -- "$quarantine_directory"
    ' uninstall-utm-dnd-rule "$rule_target"
}

remove_project_extension() {
    (
        extension_parent=${extension_dir%/*}
        if [[ ! -d "$extension_parent" ]]; then
            return
        fi

        quarantine_directory=$(mktemp -d -- "$extension_parent/.utm-dnd-uninstall.XXXXXX")
        quarantine="$quarantine_directory/$extension_id"
        restore_needed=false

        restore_extension() {
            restore_needed=false
            if mv -T -n -- "$quarantine" "$extension_dir" \
                    && [[ ! -e "$quarantine" ]] && [[ ! -L "$quarantine" ]]; then
                rmdir -- "$quarantine_directory"
                return
            fi
            warn "$extension_dir changed concurrently; preserved prior extension at $quarantine"
        }
        # shellcheck disable=SC2317,SC2329
        cleanup() {
            if [[ "$restore_needed" == true ]]; then
                if [[ -e "$quarantine" ]] || [[ -L "$quarantine" ]]; then
                    restore_extension
                else
                    restore_needed=false
                    rmdir -- "$quarantine_directory" 2>/dev/null || true
                fi
            fi
        }
        trap cleanup EXIT
        trap 'exit 129' HUP
        trap 'exit 130' INT
        trap 'exit 143' TERM

        restore_needed=true
        if ! mv -- "$extension_dir" "$quarantine" 2>/dev/null; then
            restore_needed=false
            rmdir -- "$quarantine_directory"
            if [[ ! -e "$extension_dir" ]] && [[ ! -L "$extension_dir" ]]; then
                return
            fi
            warn "cannot quarantine extension path (possible mount boundary); preserving $extension_dir"
            return
        fi
        if [[ -L "$quarantine" ]]; then
            warn "not removing symbolic-link extension directory $extension_dir"
            restore_extension
            return
        fi
        if [[ ! -d "$quarantine" ]]; then
            warn "not removing non-directory extension path $extension_dir"
            restore_extension
            return
        fi

        rm -f -- "$quarantine/extension.js" "$quarantine/metadata.json"
        if ! rmdir -- "$quarantine" 2>/dev/null; then
            warn "extension directory contains unexpected files; preserving $extension_dir"
            restore_extension
            return
        fi
        restore_needed=false
        rmdir -- "$quarantine_directory"
    )
}

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
remove_project_extension

remove_project_udev_rule
sudo udevadm control --reload-rules

echo "UTM DnD guest tools removed. spice-vdagent and unrelated user files were left untouched."
