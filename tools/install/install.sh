#!/usr/bin/env bash
# workspace-guard installer: install/update/uninstall skill+plugin per Hermes profile.
# Interactive menu (no args, TTY) or flag-driven non-interactive mode.
set -u

DEFAULT_REPO_URL="https://github.com/shawVV1992/workspace-guard"
SKILL_NAME="workspace-organization"
PLUGIN_NAME="workspace-guard"
SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root (src/ lives there)

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

# --- install/uninstall flag state -------------------------------------------
REPO_URL=""
DRY_RUN=0
FORCE=0
ALL_PROFILES=0
KEEP_CONFIG=0
declare -a TARGET_PROFILES=()

parse_install_flags() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --all-profiles) ALL_PROFILES=1 ;;
            --profile) [ $# -ge 2 ] || die "--profile needs a value"; TARGET_PROFILES+=("$2"); shift ;;
            --dry-run) DRY_RUN=1 ;;
            --repo) [ $# -ge 2 ] || die "--repo needs a value"; REPO_URL="$2"; shift ;;
            --force) FORCE=1 ;;
            *) die "unknown install flag: $1" ;;
        esac
        shift
    done
}

resolve_repo_url() {
    if [ -n "$REPO_URL" ]; then printf '%s' "$REPO_URL"; return 0; fi
    local url
    url=$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null) && [ -n "$url" ] && { printf '%s' "$url"; return 0; }
    printf '%s' "$DEFAULT_REPO_URL"
}

preflight() {
    # warn-and-continue on local/remote drift; skip unpushed check without upstream
    git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null | grep -q . && \
        err "warning: uncommitted changes in the repo; install uses the GitHub remote as source"
    if git -C "$SCRIPT_DIR" rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
        git -C "$SCRIPT_DIR" log @{u}.. 2>/dev/null | grep -q . && \
            err "warning: unpushed commits; install uses the GitHub remote as source"
    fi
    return 0
}

# --- version helpers --------------------------------------------------------
read_repo_version() {
    # $1 = src subdir (workspace-organization|workspace-guard), $2 = file (SKILL.md|plugin.yaml)
    local src_dir="$SCRIPT_DIR/src/$1/$2" v
    [ -f "$src_dir" ] || return 1
    v=$(grep -m1 -E '^version:' "$src_dir" | sed 's/^version:[[:space:]]*//; s/[\"'"'"'].*//')
    printf '%s' "$v"
}

read_installed_version() {
    # $1 = profile home, $2 = skill|plugin
    local home="$1" kind="$2" f v=""
    if [ "$kind" = "skill" ]; then
        f=$(find "$home/skills" -path '*/.archive' -prune -o -type f -name SKILL.md -path '*/workspace-organization/SKILL.md' -print 2>/dev/null | head -n1)
        [ -n "$f" ] && v=$(grep -m1 -E '^version:' "$f" | sed 's/^version:[[:space:]]*//; s/[\"'"'"'].*//')
    else
        f="$home/plugins/workspace-guard/plugin.yaml"
        [ -f "$f" ] && v=$(grep -m1 -E '^version:' "$f" | sed 's/^version:[[:space:]]*//; s/[\"'"'"'].*//')
    fi
    printf '%s' "$v"
}

ver_gt() {
    # $1 > $2 semver numeric per segment; returns 0 true
    local a="$1" b="$2" ia ib
    IFS=. read -ra ia <<< "$a"
    IFS=. read -ra ib <<< "$b"
    local i n m max
    n=${#ia[@]}; m=${#ib[@]}
    max=$(( n > m ? n : m ))
    for (( i=0; i<max; i++ )); do
        local x=0 y=0
        [ $i -lt $n ] && x=${ia[$i]}
        [ $i -lt $m ] && y=${ib[$i]}
        x=$((10#$x + 0)); y=$((10#$y + 0))
        if [ $x -gt $y ]; then return 0; fi
        if [ $x -lt $y ]; then return 1; fi
    done
    return 1
}

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

cmd_install() { parse_install_flags "$@"; preflight; err "plan not implemented"; return 2; }
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
    if [ "$sub" = "--selftest" ]; then
        if ver_gt 1.10.0 1.9.0 && ! ver_gt 1.0.0 1.0.0 && ! ver_gt 0.9.0 1.0.0; then
            echo "1.10.0 > 1.9.0: ok"
            echo "1.0.0 > 1.0.0: no"
            return 0
        fi
        die "semver comparison broken"
    fi
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
