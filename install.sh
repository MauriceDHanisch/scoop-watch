#!/usr/bin/env bash
# Scoop-watch installer.
#
#   curl -fsSL https://raw.githubusercontent.com/MauriceDHanisch/scoop-watch/main/install.sh | bash
#
# Installs the program into ~/.local/share/scoop-watch (a uv-managed isolated
# environment), puts a `scoop-watch` command on PATH, and runs first-time setup.
# Re-run any time to update, or use `scoop-watch update`.
set -euo pipefail

REPO="${SCOOP_WATCH_REPO:-MauriceDHanisch/scoop-watch}"
BRANCH="${SCOOP_WATCH_BRANCH:-main}"
APP_DIR="$HOME/.local/share/scoop-watch"
BIN_DIR="$HOME/.local/bin"
SHIM="$BIN_DIR/scoop-watch"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B=$'\e[1m'; D=$'\e[2m'; G=$'\e[38;5;114m'; C=$'\e[38;5;110m'; R=$'\e[31m'; X=$'\e[0m'
else
    B= D= G= C= R= X=
fi
step() { printf '%s→%s %s\n' "$C" "$X" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$X" "$*"; }
warn() { printf '  %s•%s %s\n' "$C" "$X" "$*"; }
die()  { printf '  %s✗%s %s\n' "$R" "$X" "$*" >&2; exit 1; }

printf '\n%sscoop-watch%s %sinstaller%s\n' "$B" "$X" "$D" "$X"
printf '%s───────────────────────%s\n\n' "$D" "$X"

# 1. Platform checks.
UNAME="$(uname -s)"
case "$UNAME" in
    Linux)  PLATFORM="linux"  ; SCHEDULER="systemd user timers" ;;
    Darwin) PLATFORM="macos"  ; SCHEDULER="launchd LaunchAgents" ;;
    *)      die "scoop-watch supports Linux and macOS only (got: $UNAME)" ;;
esac
ok "platform: $PLATFORM (scheduler: $SCHEDULER)"
for tool in curl tar; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done

# 2. Ensure uv is available (it manages the environment and dependencies).
UV="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV" ]; then
    step "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        [ -x "$candidate" ] && UV="$candidate" && break
    done
fi
[ -n "$UV" ] || die "uv is not available after installation"
ok "uv ready"

# 3. Download the program into a clean APP_DIR (each run reinstalls fresh).
prev_sha=""
[ -f "$APP_DIR/.installed-sha" ] && prev_sha="$(cat "$APP_DIR/.installed-sha")"
step "Downloading scoop-watch ($REPO@$BRANCH)..."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$tmp"
rm -rf "$APP_DIR"
mkdir -p "$(dirname "$APP_DIR")"
mv "$tmp/scoop-watch-$BRANCH" "$APP_DIR"

# Record the installed commit so `scoop-watch update` can detect new versions.
sha="$(curl -fsSL "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null \
    | grep -m1 '"sha"' | cut -d'"' -f4 || true)"
[ -n "$sha" ] && printf '%s\n' "$sha" > "$APP_DIR/.installed-sha"

if [ -z "$prev_sha" ]; then
    final_word="installed"
    ok "installed${sha:+ — ${sha:0:7}}"
elif [ -n "$sha" ] && [ "$prev_sha" = "$sha" ]; then
    final_word="up to date"
    ok "already the latest version — ${sha:0:7}"
else
    final_word="updated"
    ok "updated${prev_sha:+ — ${prev_sha:0:7}}${sha:+ → ${sha:0:7}}"
fi

# 4. Isolated environment + dependencies, via uv.
step "Installing dependencies..."
"$UV" venv --quiet "$APP_DIR/.venv"
"$UV" pip install --quiet --python "$APP_DIR/.venv/bin/python" "$APP_DIR"
ok "environment ready"

# 5. Command shim on PATH.
mkdir -p "$BIN_DIR"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
exec "$APP_DIR/.venv/bin/scoop-watch" "\$@"
EOF
chmod +x "$SHIM"
ok "command installed: $SHIM"

# 6. First-time setup.
#
# The installer used to auto-launch `scoop-watch setup < /dev/tty`, which works
# on Linux but breaks on macOS: prompt_toolkit's kqueue selector rejects the
# redirected stdin fd with EINVAL. Skipping the auto-launch removes the
# TTY-reattach hack and matches what most CLI installers do — the user runs
# setup themselves once the command is on PATH.
needs_setup=true
if [ -f "$HOME/Documents/scoop-watch/.env" ] || [ -f "$HOME/scoop-watch/.env" ]; then
    needs_setup=false
fi

printf '\n%s%s✓%s %sscoop-watch %s%s\n\n' "$B" "$G" "$X" "$B" "$final_word" "$X"

if ! command -v claude >/dev/null 2>&1 && ! command -v codex >/dev/null 2>&1; then
    warn "no agent CLI found — install 'claude' or 'codex' before running briefings"
    printf '\n'
fi
if ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
    warn "$BIN_DIR is not on your PATH — add to your shell rc:"
    printf '      %sexport PATH="$HOME/.local/bin:$PATH"%s\n\n' "$D" "$X"
fi

printf '%sNext%s\n' "$B" "$X"
if [ "$needs_setup" = "true" ]; then
    printf '  %sscoop-watch setup%s    configure agent, model, schedule, search window\n' "$C" "$X"
fi
printf '  %sscoop-watch author%s   create your first project (start here)\n' "$C" "$X"
printf '  %sscoop-watch run%s      generate a daily briefing\n' "$C" "$X"
printf '  %sscoop-watch deep%s     5-year survey of work overlapping your project\n' "$C" "$X"
printf '  %sscoop-watch arm%s      schedule the daily briefing (stops at reboot)\n' "$C" "$X"
printf '  %sscoop-watch read%s     tick papers you have read so they defer next run\n\n' "$C" "$X"
