#!/usr/bin/env bash
# workspace-guard installer: install/update/uninstall skill+plugin per Hermes profile.
# Interactive menu (no args, TTY) or flag-driven non-interactive mode.
set -u

DEFAULT_REPO_URL="https://github.com/shawVV1992/workspace-guard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # repo root (src/ lives there; script lives at the root)

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

# Extract owner/repo from a full repo URL. hermes skills install expects the
# short "owner/repo/path" identifier (GitHubSource splits on "/" and treats
# parts[0]/parts[1] as the repo) -- a full URL with scheme + ".git" suffix
# breaks that parsing. The plugin installer, by contrast, expects the full
# URL (plugins_cmd._resolve_git_url). So skill commands use the slug and
# plugin commands keep the full URL.
repo_slug() {
    local url="$1" slug
    slug="${url#*://}"          # strip scheme (https://, git://, ssh://)
    slug="${slug%%#*}"          # strip any #fragment
    case "$slug" in
        git@*) slug="${slug#*:}" ;;  # scp-like: git@host:owner/repo.git -> owner/repo.git (host already stripped)
        *)    slug="${slug#*/}" ;;   # https-like: host/owner/repo -> owner/repo
    esac
    slug="${slug%.git}"         # strip .git suffix
    printf '%s' "$slug"
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
show_menu() {
    cat <<'EOF'
🔧 workspace-guard installer
👥 1) Install/Update (skill + plugin)
🗑️ 2) Uninstall
📊 3) Status
🚪 4) Quit
EOF
}

interactive_menu() {
    local choice
    while true; do
        show_menu
        read -r -p "Choose [1-4]: " choice
        case "$choice" in
            1) cmd_install;;
            2) cmd_uninstall;;
            3) cmd_status;;
            4) return 0;;
            *) err "invalid choice: $choice";;
        esac
    done
}

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

plan_profile() {
    # $1 = profile name, $2 = profile home; prints plan lines; sets NEED_SKILL/NEED_PLUGIN/NEED_FORCE
    local prof="$1" home="$2"
    local repo_skill repo_plugin inst_skill inst_plugin
    repo_skill=$(read_repo_version workspace-organization SKILL.md)
    repo_plugin=$(read_repo_version workspace-guard plugin.yaml)
    inst_skill=$(read_installed_version "$home" skill)
    inst_plugin=$(read_installed_version "$home" plugin)

    NEED_SKILL=0; NEED_PLUGIN=0; NEED_FORCE=0
    if [ -z "$inst_skill" ]; then
        NEED_SKILL=1; log "  [$prof] skill: not installed -> install"
    elif [ "$FORCE" = 1 ] || ver_gt "$repo_skill" "$inst_skill"; then
        NEED_SKILL=1; NEED_FORCE=1
        log "  [$prof] skill: $inst_skill -> $repo_skill (update)"
    else
        log "  [$prof] skill: $inst_skill (up to date)"
    fi
    if [ -z "$inst_plugin" ]; then
        NEED_PLUGIN=1; log "  [$prof] plugin: not installed -> install"
    elif [ "$FORCE" = 1 ] || ver_gt "$repo_plugin" "$inst_plugin"; then
        NEED_PLUGIN=1; NEED_FORCE=1
        log "  [$prof] plugin: $inst_plugin -> $repo_plugin (update)"
    else
        log "  [$prof] plugin: $inst_plugin (up to date)"
    fi
}

cmd_install() {
    parse_install_flags "$@"
    preflight
    local root; root=$(hermes_root)
    local url slug; url=$(resolve_repo_url); slug=$(repo_slug "$url")
    local slug; slug=$(repo_slug "$url")
    log "repo URL: $url"
    local p home
    for p in $(discover_profiles "$root"); do
        if [ "$ALL_PROFILES" = 1 ]; then :; elif [ ${#TARGET_PROFILES[@]} -gt 0 ]; then
            # filter: only listed profiles
            local keep=0 q
            for q in "${TARGET_PROFILES[@]}"; do [ "$q" = "$p" ] && keep=1; done
            [ "$keep" = 1 ] || continue
        else
            is_tty || die "no TTY and no --profile/--all-profiles"
            err "interactive selection not implemented yet; use --profile or --all-profiles"
            return 1
        fi
        home="$root"; [ "$p" != "default" ] && home="$root/profiles/$p"
        [ -d "$home" ] || { err "  [$p] profile home missing: $home"; continue; }
        plan_profile "$p" "$home"
        local hargs=""; [ "$p" != "default" ] && hargs="-p $p"
        if [ "$DRY_RUN" = 1 ]; then
            if [ "$NEED_SKILL" = 1 ]; then
                log "  would run: hermes ${hargs:+$hargs }skills install $slug/src/workspace-organization $([ "$NEED_FORCE" = 1 ] && echo --force)"
            fi
            if [ "$NEED_PLUGIN" = 1 ]; then
                log "  would run: hermes ${hargs:+$hargs }plugins install $url#src/workspace-guard --enable $([ "$NEED_FORCE" = 1 ] && echo --force)"
            fi
            if [ "$NEED_SKILL" = 1 ] || [ "$NEED_PLUGIN" = 1 ]; then
                log "  would copy: src/workspace-guard/guard-config.yaml -> $home/workspace-guard/guard-config.yaml"
                log "  would delete memo: $home/workspace-guard/profile-workspaces.json"
            fi
            continue
        fi
        # real execution
        if [ "$NEED_SKILL" = 1 ]; then
            hermes $hargs skills install "$slug/src/workspace-organization" $([ "$NEED_FORCE" = 1 ] && echo --force) || { err "skill install failed for $p"; return 2; }
        fi
        if [ "$NEED_PLUGIN" = 1 ]; then
            hermes $hargs plugins install "$url#src/workspace-guard" --enable $([ "$NEED_FORCE" = 1 ] && echo --force) || { err "plugin install failed for $p"; return 2; }
        fi
        if [ "$NEED_SKILL" = 1 ] || [ "$NEED_PLUGIN" = 1 ]; then
            mkdir -p "$home/workspace-guard"
            cp "$SCRIPT_DIR/src/workspace-guard/guard-config.yaml" "$home/workspace-guard/guard-config.yaml"
            rm -f "$home/workspace-guard/profile-workspaces.json"
        fi
    done
    log "Restart Hermes for changes to take effect."
    return 0
}
parse_uninstall_flags() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --all-profiles) ALL_PROFILES=1 ;;
            --profile) [ $# -ge 2 ] || die "--profile needs a value"; TARGET_PROFILES+=("$2"); shift ;;
            --keep-config) KEEP_CONFIG=1 ;;
            --dry-run) DRY_RUN=1 ;;
            *) die "unknown uninstall flag: $1" ;;
        esac
        shift
    done
}

cmd_uninstall() {
    parse_uninstall_flags "$@"
    local root; root=$(hermes_root)
    local p home hargs
    for p in $(discover_profiles "$root"); do
        if [ "$ALL_PROFILES" = 1 ]; then :; elif [ ${#TARGET_PROFILES[@]} -gt 0 ]; then
            local keep=0 q
            for q in "${TARGET_PROFILES[@]}"; do [ "$q" = "$p" ] && keep=1; done
            [ "$keep" = 1 ] || continue
        else
            is_tty || die "no TTY and no --profile/--all-profiles"
            err "interactive selection not implemented yet; use --profile or --all-profiles"
            return 1
        fi
        home="$root"; [ "$p" != "default" ] && home="$root/profiles/$p"
        hargs=""; [ "$p" != "default" ] && hargs="-p $p"
        if [ "$DRY_RUN" = 1 ]; then
            if [ -n "$(find "$home/skills" -path '*/.archive' -prune -o -type f -name SKILL.md -path '*/workspace-organization/SKILL.md' -print 2>/dev/null | head -n1)" ]; then
                log "  would run: hermes ${hargs:+$hargs }skills uninstall workspace-organization"
            else
                log "  [$p] skill not installed, skip"
            fi
            [ -d "$home/plugins/workspace-guard" ] && log "  would run: hermes ${hargs:+$hargs }plugins remove workspace-guard" || log "  [$p] plugin not installed, skip"
            if [ "$KEEP_CONFIG" = 1 ]; then
                log "  keep config (--keep-config)"
            else
                log "  would delete guard-config.yaml: $home/workspace-guard/guard-config.yaml"
                log "  would delete memo: $home/workspace-guard/profile-workspaces.json"
            fi
            continue
        fi
        if [ -n "$(find "$home/skills" -path '*/.archive' -prune -o -type f -name SKILL.md -path '*/workspace-organization/SKILL.md' -print 2>/dev/null | head -n1)" ]; then
            # warn and continue on failure: tolerate missing/removed skills
            hermes $hargs skills uninstall workspace-organization || err "warning: skill uninstall failed for $p (continuing)"
        else
            log "  [$p] skill not installed, skip"
        fi
        if [ -d "$home/plugins/workspace-guard" ]; then
            hermes $hargs plugins remove workspace-guard || { err "plugin remove failed for $p"; return 2; }
        else
            log "  [$p] plugin not installed, skip"
        fi
        if [ "$KEEP_CONFIG" != 1 ] && [ -d "$home/workspace-guard" ]; then
            rm -f "$home/workspace-guard/guard-config.yaml" "$home/workspace-guard/profile-workspaces.json"
            rmdir "$home/workspace-guard" 2>/dev/null || true
        fi
    done
    return 0
}
cmd_status() {
    local root; root=$(hermes_root)
    local repo_skill repo_plugin
    repo_skill=$(read_repo_version workspace-organization SKILL.md)
    repo_plugin=$(read_repo_version workspace-guard plugin.yaml)
    log "HERMES root: $root"
    log "repo skill: $repo_skill | repo plugin: $repo_plugin"
    local p home is ip
    for p in $(discover_profiles "$root"); do
        home="$root"; [ "$p" != "default" ] && home="$root/profiles/$p"
        is=$(read_installed_version "$home" skill); [ -z "$is" ] && is="-"
        ip=$(read_installed_version "$home" plugin); [ -z "$ip" ] && ip="-"
        log "  $p: skill $is | plugin $ip"
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
    if [ "$sub" = "--show-menu" ]; then show_menu; return 0; fi
    if [ -z "$sub" ]; then
        if ! is_tty; then
            die "no TTY and no --profile/--all-profiles; refusing to enter the menu"
        fi
        interactive_menu
        return 0
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
