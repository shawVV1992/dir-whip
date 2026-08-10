#!/usr/bin/env bash
# workspace-guard installer: install/update/uninstall skill+plugin per Hermes profile.
# Interactive menu (no args, TTY) or flag-driven non-interactive mode.
set -u

DEFAULT_REPO_URL="https://github.com/shawVV1992/workspace-guard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # repo root (packages live there; script lives at the root)

# --- helpers ---------------------------------------------------------------
log()  { echo "$*"; }
err()  { printf '%s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }
sep()  { printf '%s\n' '----------------------------------------'; }

# --- logging ---------------------------------------------------------------
LOG_FILE=""

# Resolve the log file path: <HERMES_HOME>/workspace-guard/install.log
# (--log <path> overrides). The workspace-guard dir is created if needed.
resolve_log_file() {
    if [ -n "$LOG_FILE" ]; then
        mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
        return 0
    fi
    local root; root=$(hermes_root)
    LOG_FILE="$root/workspace-guard/install.log"
    mkdir -p "$root/workspace-guard" 2>/dev/null || true
}

# Timestamped append to the log file (never to the terminal).
logfile() {
    [ -n "$LOG_FILE" ] || resolve_log_file
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
    printf '[%s] %s\n' "$ts" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

# Run a hermes command with full output captured to the log. Terminal shows a
# per-profile progress line with a spinner (TTY) or a plain status line
# (non-TTY). Command stderr is never shown unless it fails.
#   $1 = profile, $2 = component (skill|plugin)
#   $3 = actioning (installing|updating|uninstalling)
#   $4 = actioned  (installed|updated|uninstalled)
#   $5 = failed    (install failed|update failed|uninstall failed)
#   remaining args = the command to run
progress_run() {
    local prof="$1" comp="$2" actioning="$3" actioned="$4" failed="$5"; shift 5
    logfile "== [${prof}] ${comp} ${actioning}: $*"
    if [ -t 1 ]; then
        local spin=('|' '/' '-' '\') s=0 pid rc
        printf '\r[%s] %s %s \033[33m%s\033[0m' "$prof" "$comp" "$actioning" "${spin[0]}"
        "$@" >> "$LOG_FILE" 2>&1 &
        pid=$!
        while kill -0 "$pid" 2>/dev/null; do
            printf '\r[%s] %s %s \033[33m%s\033[0m' "$prof" "$comp" "$actioning" "${spin[$((s % 4))]}"
            s=$((s + 1))
            sleep 0.1
        done
        wait "$pid"; rc=$?
        if [ "$rc" -eq 0 ]; then
            printf '\r\033[K[%s] %s %s \033[32m✓\033[0m\n' "$prof" "$comp" "$actioned"
        else
            printf '\r\033[K[%s] %s %s \033[31m✗\033[0m\n' "$prof" "$comp" "$failed"
        fi
        logfile "== [${prof}] ${comp} ${actioning} exit=$rc"
        return "$rc"
    fi
    # Non-TTY: plain status line, output only to the log
    log "  [${prof}] ${comp} ${actioning} ..."
    if "$@" >> "$LOG_FILE" 2>&1; then
        log "  [${prof}] ${comp} ${actioned} OK"
        return 0
    else
        local rc=$?
        log "  [${prof}] ${comp} ${failed}"
        return "$rc"
    fi
}

# Print a one-line failure cause plus the absolute log path (stderr).
# Call after a progress_run failure; $LOG_FILE must already be resolved.
fail_report() {
    local cause
    cause=$(tail -n 1 "$LOG_FILE" 2>/dev/null)
    [ -n "$cause" ] && err "  reason: $cause"
    err "  log: $LOG_FILE"
}

# Installed/absent marker for a component status line. $1 = 1 (installed) or 0.
# TTY: green ✓ / red ✗; non-TTY: plain ASCII "ok" / "-".
# Note: echo is used (not printf) because bash's printf re-encodes multibyte
# chars through the locale and degrades ✓/✗ to "?" under a C locale (Windows
# Git Bash default); echo passes bytes through verbatim.
mark() {
    if [ -t 1 ]; then
        if [ "$1" = 1 ]; then echo -e '\033[32m✓\033[0m'; else echo -e '\033[31m✗\033[0m'; fi
    else
        if [ "$1" = 1 ]; then printf 'ok'; else printf '-'; fi
    fi
}

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
      --log <path>               log file (default: <HERMES_HOME>/workspace-guard/install.log)
  install.sh uninstall           uninstall skill+plugin
      --all-profiles             all profiles
      --profile <name>           target profile (repeatable)
      --keep-config              preserve guard-config.yaml + memo
      --log <path>               log file (default: <HERMES_HOME>/workspace-guard/install.log)
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

detect_wsl() {
    [ -n "${WSL_DISTRO_NAME:-}" ] && return 0
    uname -r 2>/dev/null | grep -qi microsoft && return 0
    return 1
}

# True when the WSL side has its own Hermes installation (its own HERMES_HOME
# ~/.hermes and a hermes CLI on the WSL PATH). When true we install INTO the
# WSL Hermes and never fall back to the Windows one.
wsl_has_hermes() {
    [ -d "$HOME/.hermes" ] || return 1
    command -v hermes >/dev/null 2>&1 || return 1
    return 0
}

# True when this WSL process was launched FROM Windows (PowerShell/CMD/Windows
# Terminal typing `bash`): the System32 bash launcher maps the Windows cwd to
# /mnt/<drive>/..., so pwd (or the script's absolute path) starts with /mnt/<l>.
# A real WSL session (user entered WSL) usually runs from the WSL filesystem
# (/home/..., /root/..., /...) and is NOT detected as a Windows launch.
launched_from_windows() {
    case "$(pwd 2>/dev/null)" in
        /mnt/[a-z]/*) return 0 ;;
    esac
    local sp
    sp=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
    case "$sp" in
        /mnt/[a-z]/*) return 0 ;;
    esac
    return 1
}

# Launched from Windows but running in WSL: re-execute this script under Git
# Bash so it runs against the Windows-side Hermes (the WSL bash launcher cannot
# see Windows env vars like LOCALAPPDATA). WG_RELAUNCHED guards against loops.
# The script must live on /mnt/<drive>/... for the path conversion to work.
relaunch_via_git_bash() {
    [ -n "${WG_RELAUNCHED:-}" ] && return 1
    local gb script_win
    for gb in "/mnt/c/Program Files/Git/bin/bash.exe" "/mnt/c/Program Files (x86)/Git/bin/bash.exe"; do
        [ -x "$gb" ] || continue
        script_win=$(cd "$(dirname "$0")" && pwd | sed 's|^/mnt/\([a-z]\)|\U\1:|' | tr '/' '\\')
        [ -n "$script_win" ] || continue
        WG_RELAUNCHED=1 exec "$gb" "$script_win\\install.sh" "$@"
    done
    return 1
}

# install/uninstall need a real hermes CLI on PATH; fail loudly otherwise.
require_hermes_cli() {
    command -v hermes >/dev/null 2>&1 || die "hermes CLI not found in PATH
(install Hermes first, or run inside the environment that has the hermes command)"
}

# Every subcommand needs a real HERMES root; fail loudly instead of silently
# looping over zero discovered profiles (which used to print a fake "Restart"
# success line).
require_hermes_root() {
    local root; root=$(hermes_root)
    [ -d "$root" ] || die "HERMES root not found: $root
(on Windows run with Git Bash: & 'C:\Program Files\Git\bin\bash.exe' install.sh)"
    printf '%s' "$root"
}

# --- install/uninstall flag state -------------------------------------------
REPO_URL=""
DRY_RUN=0
FORCE=0
ALL_PROFILES=0
KEEP_CONFIG=0
ASK_CONFIRM=0
declare -a TARGET_PROFILES=()

parse_install_flags() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --all-profiles) ALL_PROFILES=1 ;;
            --profile) [ $# -ge 2 ] || die "--profile needs a value"; TARGET_PROFILES+=("$2"); shift ;;
            --dry-run) DRY_RUN=1 ;;
            --repo) [ $# -ge 2 ] || die "--repo needs a value"; REPO_URL="$2"; shift ;;
            --force) FORCE=1 ;;
            --log) [ $# -ge 2 ] || die "--log needs a value"; LOG_FILE="$2"; shift ;;
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

# --- repo remote fetch (SCR-020) -------------------------------------------
# Repo versions and the config template come from the GitHub remote ONLY --
# install.sh works standalone (no repo checkout next to it). WG_CURL overrides
# the curl command so tests can inject a fake (WG_FAKE_REPO feeds it files).

# Convert a github.com repository URL into the raw.githubusercontent.com base
# (https/git@ forms; strips .git). Non-GitHub sources return 1.
github_raw_base() {
    local url="$1" base
    case "$url" in
        https://github.com/*|http://github.com/*)
            base=$(printf '%s' "$url" | sed -E 's#^(https?://)github\.com/#\1raw.githubusercontent.com/#')
            ;;
        git@github.com:*)
            base=$(printf '%s' "${url#git@github.com:}" | sed 's#^#https://raw.githubusercontent.com/#')
            ;;
        *) return 1 ;;
    esac
    printf '%s' "${base%.git}"
}

# Fetch a repo file from the GitHub remote (HEAD = default branch). Prints the
# file content; returns non-zero on any failure.
fetch_repo_file() {
    local base
    base=$(github_raw_base "$(resolve_repo_url)") || return 1
    "${WG_CURL:-curl}" -fsSL --max-time 15 "$base/HEAD/$1" 2>/dev/null
}

# Latest `version:` of a package file on the GitHub remote (empty on failure).
repo_file_version() {
    local v
    v=$(fetch_repo_file "$1/$2" | grep -m1 -E '^version:' | sed 's/^version:[[:space:]]*//; s/[\"'"'"'].*//')
    printf '%s' "$v"
}

# Fetch skill+plugin versions once into REPO_SKILL/REPO_PLUGIN globals. install
# (incl. --dry-run) depends on them for its plan: fail loudly with a retry hint
# instead of silently treating everything as up to date.
fetch_repo_versions() {
    REPO_SKILL=$(repo_file_version workspace-organization SKILL.md)
    REPO_PLUGIN=$(repo_file_version workspace-guard plugin.yaml)
    if [ -z "$REPO_SKILL" ] || [ -z "$REPO_PLUGIN" ]; then
        err "cannot fetch workspace-guard versions from GitHub (network unreachable, or --repo source is not GitHub)."
        die "please connect to the network and retry"
    fi
}

read_installed_version() {
    # $1 = profile home, $2 = skill|plugin
    # Skill lookup scans EVERY workspace-organization SKILL.md (excluding
    # .archive) and prefers one that carries a `version:` field: stale copies
    # from pre-version installs exist alongside the current one and must not
    # shadow the real version (find|head -1 can pick the old copy).
    local home="$1" kind="$2" f v=""
    if [ "$kind" = "skill" ]; then
        local f2
        while IFS= read -r f2; do
            [ -n "$f2" ] || continue
            v=$(grep -m1 -E '^version:' "$f2" | sed 's/^version:[[:space:]]*//; s/[\"'"'"'].*//')
            [ -n "$v" ] && { printf '%s' "$v"; return 0; }
        done < <(find "$home/skills" -path '*/.archive' -prune -o -type f -name SKILL.md -path '*/workspace-organization/SKILL.md' -print 2>/dev/null)
        # No versioned copy: report the bare install (empty -> treated as not installed)
        printf '%s' ""
    else
        f="$home/plugins/workspace-guard/plugin.yaml"
        [ -f "$f" ] && v=$(grep -m1 -E '^version:' "$f" | sed 's/^version:[[:space:]]*//; s/[\"'"'"'].*//')
        printf '%s' "$v"
    fi
}

stale_plugin_archive_exists() {
    # $1 = profile home; returns 0 when a stale archived plugin copy exists
    [ -d "$1/plugins/.archive/workspace-guard" ]
}

clean_stale_plugin_archive() {
    # $1 = profile home, $2 = profile name
    # Move a stale plugins/.archive/workspace-guard copy out of the plugins
    # tree (Hermes' plugin scanner does not skip dot-directories and matches
    # enabled by bare manifest name, so an archived copy would be loaded
    # alongside the active one) and drop the leftover `.archive/workspace-guard`
    # entry from plugins.enabled in config.yaml. Best-effort: failures are
    # logged but never abort install/uninstall.
    local home="$1" prof="$2" bkdir
    if [ -d "$home/plugins/.archive/workspace-guard" ]; then
        bkdir=$(mktemp -d 2>/dev/null) || bkdir="$home/workspace-guard/stale-plugin-backup"
        mkdir -p "$bkdir" 2>/dev/null || true
        if mv "$home/plugins/.archive/workspace-guard" "$bkdir/workspace-guard" 2>/dev/null; then
            log "  [$prof] stale plugin archive moved to $bkdir/workspace-guard"
            logfile "[$prof] stale plugin archive moved to $bkdir/workspace-guard"
        else
            err "  [$prof] could not move stale plugin archive: $home/plugins/.archive/workspace-guard"
            logfile "[$prof] could not move stale plugin archive"
        fi
        # Drop the empty .archive dir it came from (harmless if not empty).
        rmdir "$home/plugins/.archive" 2>/dev/null || true
    fi
    if [ -f "$home/config.yaml" ]; then
        sed -i '/\.archive\/workspace-guard/d' "$home/config.yaml"
        logfile "[$prof] removed stale '.archive/workspace-guard' entry from config.yaml"
    fi
    return 0
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
    sep
    cat <<'EOF'
workspace-guard installer
👥 1) Install/Update (skill + plugin)
🗑️ 2) Uninstall
📊 3) Status
🚪 4) Quit
EOF
    sep
    printf '\n'
}

interactive_menu() {
    local choice
    while true; do
        show_menu
        read -r -p "Choose [1-4]: " choice || return 0   # EOF: quit
        choice="${choice%$'\r'}"   # strip CR from Windows-piped input
        case "$choice" in
            1) ASK_CONFIRM=1
               if select_profiles; then cmd_install; fi
               ASK_CONFIRM=0 ;;
            2) if select_profiles; then cmd_uninstall; fi ;;
            3) cmd_status;;
            4) return 0;;
            *) err "invalid choice: $choice";;
        esac
        printf '\n'
    done
}

# Interactive profile picker (number-based). Fills the ALL_PROFILES /
# TARGET_PROFILES globals for cmd_install / cmd_uninstall. Returns 0 on a
# valid selection, 1 on cancel/error.
select_profiles() {
    local root; root=$(hermes_root)
    local -a names=()
    local name n=0 sel num home skill_v plugin_v
    sep
    while IFS= read -r name; do
        n=$((n + 1))
        names+=("$name")
        home="$root"; [ "$name" != "default" ] && home="$root/profiles/$name"
        skill_v=$(read_installed_version "$home" skill); [ -z "$skill_v" ] && skill_v="-"
        plugin_v=$(read_installed_version "$home" plugin); [ -z "$plugin_v" ] && plugin_v="-"
        log "  $n) $name: skill $skill_v | plugin $plugin_v"
    done < <(discover_profiles "$root")
    [ "$n" -gt 0 ] || { err "no profiles found under $root"; sep; printf '\n'; return 1; }
    ALL_PROFILES=0
    TARGET_PROFILES=()
    while true; do
        read -r -p "Select profile numbers (comma-separated; A/a = all, X/x = cancel): " sel || { err "cancelled"; sep; printf '\n'; return 1; }
        sel="${sel%$'\r'}"   # strip CR from Windows-piped input
        case "$sel" in
            a|A) ALL_PROFILES=1; sep; printf '\n'; return 0 ;;
            x|X) err "cancelled"; sep; printf '\n'; return 1 ;;
            "") err "invalid selection: $sel (use 1-$n, comma-separated; A/a = all, X/x = cancel)"; continue ;;
        esac
        local ok=1 nums
        IFS=, read -ra nums <<< "$sel"
        for num in "${nums[@]}"; do
            case "$num" in
                *[!0-9]*) ok=0 ;;
                *) [ "$num" -ge 1 ] && [ "$num" -le "$n" ] || ok=0 ;;
            esac
            [ "$ok" = 1 ] || break
        done
        if [ "$ok" = 1 ]; then
            for num in "${nums[@]}"; do
                TARGET_PROFILES+=("${names[$((num - 1))]}")
            done
            sep
            printf '\n'
            return 0
        fi
        err "invalid selection: $sel (use 1-$n, comma-separated; A/a = all, X/x = cancel)"
    done
}

confirm_yn() {
    # $1 = prompt; returns 0 on y/Y or Enter (default yes), 1 on n/N
    local ans
    while true; do
        read -r -p "$1 [Y/n] " ans || return 1   # EOF: treat as no
        ans="${ans%$'\r'}"   # strip CR from Windows-piped input
        case "$ans" in
            ""|y|Y) return 0 ;;
            n|N) return 1 ;;
            *) err "invalid answer: $ans (y/n)" ;;
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
    # $1 = profile name, $2 = profile home; prints plan lines; sets
    # NEED_SKILL/NEED_PLUGIN/NEED_FORCE plus per-component update flags
    # (NEED_SKILL_UPDATE / NEED_PLUGIN_UPDATE) so the interactive menu can ask
    # before overwriting an outdated install. Repo versions come from the
    # REPO_SKILL/REPO_PLUGIN globals (filled once by fetch_repo_versions).
    local prof="$1" home="$2"
    local inst_skill inst_plugin
    inst_skill=$(read_installed_version "$home" skill)
    inst_plugin=$(read_installed_version "$home" plugin)

    NEED_SKILL=0; NEED_PLUGIN=0; NEED_FORCE=0
    NEED_SKILL_UPDATE=0; NEED_PLUGIN_UPDATE=0
    if [ -z "$inst_skill" ]; then
        NEED_SKILL=1
    elif [ "$FORCE" = 1 ] || ver_gt "$REPO_SKILL" "$inst_skill"; then
        NEED_SKILL=1; NEED_SKILL_UPDATE=1; NEED_FORCE=1
        log "  [$prof] skill: $inst_skill -> $REPO_SKILL (update)"
    else
        log "  [$prof] skill: $inst_skill (up to date)"
    fi
    if [ -z "$inst_plugin" ]; then
        NEED_PLUGIN=1
    elif [ "$FORCE" = 1 ] || ver_gt "$REPO_PLUGIN" "$inst_plugin"; then
        NEED_PLUGIN=1; NEED_PLUGIN_UPDATE=1; NEED_FORCE=1
        log "  [$prof] plugin: $inst_plugin -> $REPO_PLUGIN (update)"
    else
        log "  [$prof] plugin: $inst_plugin (up to date)"
    fi
}

cmd_install() {
    parse_install_flags "$@"
    preflight
    [ "$DRY_RUN" = 1 ] || require_hermes_cli
    local root; root=$(require_hermes_root) || return 1
    local url slug; url=$(resolve_repo_url); slug=$(repo_slug "$url")
    logfile "install: repo=$url slug=$slug mode=$([ "$DRY_RUN" = 1 ] && echo dry-run || echo live)"
    fetch_repo_versions   # exits with a connect-to-the-network hint when offline
    local p home
    sep
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
        logfile "== profile: $p home=$home"
        plan_profile "$p" "$home"
        local stale_archive=0
        if stale_plugin_archive_exists "$home"; then
            stale_archive=1
            log "  [$p] stale plugin archive: found (will clean)"
            logfile "  [$p] stale plugin archive: found (will clean)"
        fi
        # Interactive confirmation: ask before overwriting an outdated install.
        # Fresh installs and --all-profiles one-shot runs do not ask.
        if [ "$ASK_CONFIRM" = 1 ] && [ "$DRY_RUN" = 0 ]; then
            if [ "$NEED_SKILL_UPDATE" = 1 ] && ! confirm_yn "  [$p] update skill?"; then
                NEED_SKILL=0; NEED_SKILL_UPDATE=0
                log "  [$p] skill update skipped"
            fi
            if [ "$NEED_PLUGIN_UPDATE" = 1 ] && ! confirm_yn "  [$p] update plugin?"; then
                NEED_PLUGIN=0; NEED_PLUGIN_UPDATE=0
                log "  [$p] plugin update skipped"
            fi
        fi
        # Always pass -p: without it hermes targets the CURRENTLY ACTIVE
        # profile, not the default/root profile (cross-profile installs).
        local hargs="-p $p"
        if [ "$DRY_RUN" = 1 ]; then
            if [ "$NEED_SKILL" = 1 ]; then
                log "  [$p] skill will $([ "$NEED_SKILL_UPDATE" = 1 ] && echo update || echo install)"
                logfile "  would run: hermes ${hargs:+$hargs }skills install --yes $slug/workspace-organization $([ "$NEED_FORCE" = 1 ] && echo --force)"
            fi
            if [ "$NEED_PLUGIN" = 1 ]; then
                log "  [$p] plugin will $([ "$NEED_PLUGIN_UPDATE" = 1 ] && echo update || echo install)"
                logfile "  would run: hermes ${hargs:+$hargs }plugins install $url#workspace-guard --enable $([ "$NEED_FORCE" = 1 ] && echo --force)"
            fi
            if [ "$NEED_SKILL" = 1 ] || [ "$NEED_PLUGIN" = 1 ]; then
                logfile "  would fetch guard-config template from GitHub to $home/workspace-guard/"
                logfile "  would delete memo: $home/workspace-guard/profile-workspaces.json"
            fi
            if [ "$stale_archive" = 1 ]; then
                logfile "  would move stale archive: $home/plugins/.archive/workspace-guard"
            fi
            continue
        fi
        # real execution
        if [ "$NEED_SKILL" = 1 ]; then
            if [ "$NEED_SKILL_UPDATE" = 1 ]; then
                progress_run "$p" skill updating updated "update failed" hermes $hargs skills install --yes "$slug/workspace-organization" --force || { err "skill update failed for $p"; fail_report; return 2; }
            else
                progress_run "$p" skill installing installed "install failed" hermes $hargs skills install --yes "$slug/workspace-organization" || { err "skill install failed for $p"; fail_report; return 2; }
            fi
        fi
        if [ "$NEED_PLUGIN" = 1 ]; then
            if [ "$NEED_PLUGIN_UPDATE" = 1 ]; then
                progress_run "$p" plugin updating updated "update failed" hermes $hargs plugins install "$url#workspace-guard" --enable --force || { err "plugin update failed for $p"; fail_report; return 2; }
            else
                progress_run "$p" plugin installing installed "install failed" hermes $hargs plugins install "$url#workspace-guard" --enable || { err "plugin install failed for $p"; fail_report; return 2; }
            fi
        fi
        if [ "$NEED_SKILL" = 1 ] || [ "$NEED_PLUGIN" = 1 ]; then
            mkdir -p "$home/workspace-guard"
            if ! fetch_repo_file workspace-guard/guard-config.yaml > "$home/workspace-guard/guard-config.yaml"; then
                rm -f "$home/workspace-guard/guard-config.yaml"
                err "  [$p] could not download the guard-config template from GitHub"
                err "  [$p] please connect to the network and retry"
                logfile "  [$p] guard-config template download failed"
                return 2
            fi
            rm -f "$home/workspace-guard/profile-workspaces.json"
            logfile "  config template fetched from GitHub; memo deleted for $p"
        fi
        [ "$stale_archive" = 1 ] && clean_stale_plugin_archive "$home" "$p"
    done
    log "Restart Hermes for changes to take effect."
    logfile "install finished (exit 0)"
    sep
    printf '\n'
    return 0
}
parse_uninstall_flags() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --all-profiles) ALL_PROFILES=1 ;;
            --profile) [ $# -ge 2 ] || die "--profile needs a value"; TARGET_PROFILES+=("$2"); shift ;;
            --keep-config) KEEP_CONFIG=1 ;;
            --dry-run) DRY_RUN=1 ;;
            --log) [ $# -ge 2 ] || die "--log needs a value"; LOG_FILE="$2"; shift ;;
            *) die "unknown uninstall flag: $1" ;;
        esac
        shift
    done
}

cmd_uninstall() {
    parse_uninstall_flags "$@"
    [ "$DRY_RUN" = 1 ] || require_hermes_cli
    local root; root=$(require_hermes_root) || return 1
    local p home hargs n=0
    logfile "uninstall: mode=$([ "$DRY_RUN" = 1 ] && echo dry-run || echo live) keep_config=$KEEP_CONFIG"
    sep
    for p in $(discover_profiles "$root"); do
        n=$((n + 1))
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
        hargs="-p $p"   # always explicit: no -p targets the ACTIVE profile, not default
        logfile "== profile: $p home=$home"
        local has_skill=0 has_plugin=0 stale_archive=0
        [ -n "$(find "$home/skills" -path '*/.archive' -prune -o -type f -name SKILL.md -path '*/workspace-organization/SKILL.md' -print 2>/dev/null | head -n1)" ] && has_skill=1
        [ -d "$home/plugins/workspace-guard" ] && has_plugin=1
        if stale_plugin_archive_exists "$home"; then
            stale_archive=1
            log "  [$p] stale plugin archive: found (will clean)"
            logfile "  [$p] stale plugin archive: found (will clean)"
        fi
        if [ "$DRY_RUN" = 1 ]; then
            if [ "$has_skill" = 1 ]; then
                logfile "  would run: hermes ${hargs:+$hargs }skills uninstall workspace-organization"
            fi
            if [ "$has_plugin" = 1 ]; then
                logfile "  would run: hermes ${hargs:+$hargs }plugins remove workspace-guard"
            fi
            if [ "$KEEP_CONFIG" = 1 ]; then
                log "  [$p] keep config (--keep-config)"
                logfile "  keep config"
            else
                logfile "  would delete config + memo under $home/workspace-guard/"
            fi
            if [ "$stale_archive" = 1 ]; then
                logfile "  would move stale archive: $home/plugins/.archive/workspace-guard"
            fi
            continue
        fi
        if [ "$has_skill" = 1 ]; then
            # warn and continue on failure: tolerate missing/removed skills
            # `hermes skills uninstall` has no --yes flag (skills_hub.py
            # hardcodes skip_confirm=False) and prompts "Confirm [y/N]"; feed
            # "y" via stdin so the progress step cannot hang on the prompt.
            # The uninstall intent was already confirmed by the menu/profile
            # selection or an explicit --all-profiles invocation.
            progress_run "$p" skill uninstalling uninstalled "uninstall failed" sh -c "printf 'y\n' | hermes $hargs skills uninstall workspace-organization" || { err "warning: skill uninstall failed for $p (continuing)"; fail_report; }
        fi
        if [ "$has_plugin" = 1 ]; then
            progress_run "$p" plugin uninstalling uninstalled "uninstall failed" hermes $hargs plugins remove workspace-guard || { err "plugin remove failed for $p"; fail_report; return 2; }
        fi
        if [ "$KEEP_CONFIG" != 1 ] && [ -d "$home/workspace-guard" ]; then
            rm -f "$home/workspace-guard/guard-config.yaml" "$home/workspace-guard/profile-workspaces.json"
            rmdir "$home/workspace-guard" 2>/dev/null || true
            logfile "  config + memo deleted for $p"
        fi
        [ "$stale_archive" = 1 ] && clean_stale_plugin_archive "$home" "$p"
    done
    logfile "uninstall finished (exit 0)"
    sep
    printf '\n'
    return 0
}
cmd_status() {
    local root; root=$(require_hermes_root) || return 1
    local repo_skill repo_plugin
    repo_skill=$(repo_file_version workspace-organization SKILL.md)
    repo_plugin=$(repo_file_version workspace-guard plugin.yaml)
    sep
    log "HERMES root: $root"
    if [ -n "$repo_skill" ] && [ -n "$repo_plugin" ]; then
        log "latest version skill: $repo_skill | plugin: $repo_plugin"
    else
        log "latest version skill: - | plugin: -"
        err "warning: cannot fetch repo versions from GitHub; connect to the network to see the latest versions"
    fi
    local p home is ip
    for p in $(discover_profiles "$root"); do
        home="$root"; [ "$p" != "default" ] && home="$root/profiles/$p"
        is=$(read_installed_version "$home" skill); [ -z "$is" ] && is="-"
        ip=$(read_installed_version "$home" plugin); [ -z "$ip" ] && ip="-"
        log "  $p: skill $is | plugin $ip"
    done
    sep
    printf '\n'
    return 0
}

main() {
    [ $# -eq 1 ] && [ "$1" = "--help" ] && { usage; return 0; }
    if detect_wsl; then
        # Environment-entry routing (SCR-021): figure out which Hermes the user
        # actually means, not just which kernel the process runs on.
        #   WG_TARGET=wsl     -> force the WSL side (SCR-019 behavior)
        #   WG_TARGET=windows -> force the Windows side
        #   unset -> auto: launched from a Windows terminal (PowerShell/CMD via
        #            the System32 bash launcher, cwd under /mnt/<drive>) means
        #            Windows; a real WSL session means WSL-only.
        local target="${WG_TARGET:-}"
        if [ "$target" = windows ] || { [ -z "$target" ] && launched_from_windows; }; then
            # Silent relaunch (SCR-022): no hints on success; the die below is
            # the brief reason when the Windows side cannot be reached.
            relaunch_via_git_bash "$@" || die "Git Bash was not found (or this script does not live on /mnt/<drive>).
Run install.sh from Git Bash directly, or set WG_TARGET=wsl to configure the WSL-side Hermes."
        else
            # Real WSL session: configure the WSL-side Hermes and NEVER fall
            # back to the Windows one. Without a WSL Hermes we fail with
            # guidance (PowerShell/CMD "bash" is the WSL launcher and cannot
            # see Windows env vars like LOCALAPPDATA).
            wsl_has_hermes || die "WSL detected: this script configures only the Hermes inside WSL, and no Hermes installation was found there (~/.hermes + hermes CLI).
Install Hermes inside WSL, or set WG_TARGET=windows to configure the Windows-side Hermes via Git Bash."
        fi
    fi
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
    if [ "$sub" = "--force-menu" ]; then interactive_menu; return 0; fi
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
