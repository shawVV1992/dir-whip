#!/usr/bin/env bash
# workspace-guard installer: install/update/uninstall skill+plugin per Hermes profile.
# Interactive menu (no args, TTY) or flag-driven non-interactive mode.
set -u

DEFAULT_REPO_URL="https://github.com/shawVV1992/workspace-guard"
SKILL_NAME="workspace-organization"
PLUGIN_NAME="workspace-guard"

# --- helpers ---------------------------------------------------------------
log()  { printf '%s\n' "$*"; }
err()  { printf '%s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    cat <<'EOF'
workspace-guard installer
Usage:
  install.sh                     interactive menu (TTY)
  install.sh install             install/update skill+plugin
      --all-profiles             all profiles, no prompts
      --profile <name>           target profile (repeatable)
      --dry-run                  show actions only
      --repo <url>               override repository URL
      --force                    reinstall even when versions match
  install.sh uninstall           uninstall skill+plugin
      --all-profiles             all profiles
      --profile <name>           target profile (repeatable)
      --keep-config              preserve guard-config.yaml + memo
  install.sh status              per-profile installed versions
EOF
}

hermes_root() {
    local hh="${HERMES_HOME:-}"
    if [ -n "$hh" ]; then printf '%s' "$hh"; return 0; fi
    if [ -n "${LOCALAPPDATA:-}" ]; then printf '%s/hermes' "$LOCALAPPDATA"; return 0; fi
    printf '%s/.hermes' "$HOME"
}

is_tty() { [ -t 0 ]; }

# --- subcommands -----------------------------------------------------------
discover_profiles() {
    local root="$1" name
    [ -d "$root" ] || return 0
    printf 'default\n'
    for dir in "$root"/profiles/*/; do
        [ -d "$dir" ] || continue
        name=$(basename "$dir")
        case "$name" in
            [a-z0-9]*[a-z0-9_-]*) printf '%s\n' "$name" ;;
        esac
    done
}

cmd_install() { err "not implemented yet"; return 2; }
cmd_uninstall() { err "not implemented yet"; return 2; }
cmd_status() {
    local root; root=$(hermes_root)
    local p
    log "HERMES root: $root"
    for p in $(discover_profiles "$root"); do
        log "  profile: $p"
    done
    return 0
}

main() {
    [ $# -eq 1 ] && [ "$1" = "--help" ] && { usage; return 0; }
    local sub="${1:-}"
    if [ -z "$sub" ]; then
        if ! is_tty; then
            die "no TTY and no --profile/--all-profiles; refusing to enter the menu"
        fi
        # interactive menu body lands in Task 9; for now fall through to usage
        usage
        return 1
    fi
    shift
    case "$sub" in
        install)   cmd_install "$@" ;;
        uninstall) cmd_uninstall "$@" ;;
        status)    cmd_status "$@" ;;
        *)         die "unknown subcommand: $sub (see --help)" ;;
    esac
}

main "$@"
