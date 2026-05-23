#!/usr/bin/env bash
# Scoop-watch uninstaller.
#
#   curl -fsSL https://raw.githubusercontent.com/MauriceDHanisch/scoop-watch/main/uninstall.sh | bash
#
# Removes the program, the command and any scheduled timers. Your projects and
# briefings are kept unless you confirm their deletion. You can also run
# `scoop-watch uninstall` if the command still works.
set -euo pipefail

APP_DIR="$HOME/.local/share/scoop-watch"
SHIM="$HOME/.local/bin/scoop-watch"
CONFIG_DIR="$HOME/.config/scoop-watch"
UNIT_DIR="$HOME/.config/systemd/user"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B=$'\e[1m'; D=$'\e[2m'; G=$'\e[38;5;114m'; C=$'\e[38;5;110m'; X=$'\e[0m'
else
    B= D= G= C= X=
fi
step() { printf '%s→%s %s\n' "$C" "$X" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$X" "$*"; }

printf '\n%sscoop-watch%s %suninstaller%s\n' "$B" "$X" "$D" "$X"
printf '%s─────────────────────────%s\n\n' "$D" "$X"

# 1. Stop and remove any scheduled timers/services.
if command -v systemctl >/dev/null 2>&1; then
    step "Removing scheduled timers..."
    shopt -s nullglob
    names=()
    for unit in "$UNIT_DIR"/scoop-watch-*.timer "$UNIT_DIR"/scoop-watch-*.service; do
        name="$(basename "$unit")"
        names+=("$name")
        if [ "$(systemctl --user is-active "$name" 2>/dev/null)" = "active" ]; then
            systemctl --user stop "$name" 2>/dev/null || true
            ok "stopped $name (timer was running) and removed it"
        else
            systemctl --user stop "$name" 2>/dev/null || true
            ok "removed $name"
        fi
        rm -f "$unit"
    done
    shopt -u nullglob
    if [ "${#names[@]}" -gt 0 ]; then
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user reset-failed "${names[@]}" 2>/dev/null || true
    else
        ok "no scheduled timers found"
    fi
fi

# 2. Remove the program and command.
step "Removing the program..."
rm -rf "$APP_DIR"
rm -f "$SHIM"
ok "program and command removed"

# 3. Resolve the data directory.
if [ -f "$CONFIG_DIR/datadir" ]; then
    data_dir="$(cat "$CONFIG_DIR/datadir")"
elif command -v xdg-user-dir >/dev/null 2>&1; then
    data_dir="$(xdg-user-dir DOCUMENTS)/scoop-watch"
else
    data_dir="$HOME/scoop-watch"
fi

# 4. Offer to delete the data (briefings + project descriptions).
if [ -d "$data_dir" ]; then
    printf '\n%sYour projects and briefings:%s %s\n' "$D" "$X" "$data_dir"
    reply="n"
    if [ -e /dev/tty ]; then
        printf '%sDelete this data too?%s [y/N]: ' "$C" "$X"
        read -r reply < /dev/tty || reply="n"
    fi
    case "$reply" in
        [yY]*) rm -rf "$data_dir" "$CONFIG_DIR"; ok "deleted $data_dir" ;;
        *)     ok "kept $data_dir" ;;
    esac
fi

printf '\n%s%s✓%s %sscoop-watch uninstalled%s\n\n' "$B" "$G" "$X" "$B" "$X"
