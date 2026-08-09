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
# step-level progress line with a spinner (TTY) or a plain status line
# (non-TTY). Command stderr is never shown unless it fails.
#   $1 = step index, $2 = total steps, $3 = label
#   remaining args = the command to run
progress_run() {
    local i="$1" total="$2" label="$3"; shift 3
    logfile "== [${i}/${total}] ${label}: $*"
    if [ -t 1 ]; then
        local spin=('|' '/' '-' '\') s=0 pid rc
        printf '\r  [%d/%d] %s ...' "$i" "$total" "$label"
        "$@" >> "$LOG_FILE" 2>&1 &
        pid=$!
        while kill -0 "$pid" 2>/dev/null; do
            printf '\r  [%d/%d] %s %s' "$i" "$total" "$label" "${spin[$((s % 4))]}"
            s=$((s + 1))
            sleep 0.1
        done
        wait "$pid"; rc=$?
        if [ "$rc" -eq 0 ]; then
            printf '\r  [%d/%d] %s 完成\n' "$i" "$total" "$label"
        else
            printf '\r  [%d/%d] %s 失败 (rc=%d)\n' "$i" "$total" "$label" "$rc"
            tail -n 3 "$LOG_FILE" >&2
        fi
        logfile "== [${i}/${total}] ${label} exit=$rc"
        return "$rc"
    fi
    # Non-TTY: plain status line, output only to the log
    log "  [${i}/${total}] ${label} ..."
    if "$@" >> "$LOG_FILE" 2>&1; then
        log "  [${i}/${total}] ${label} 完成"
        return 0
    else
        local rc=$?
        log "  [${i}/${total}] ${label} 失败 (rc=$rc)"
        tail -n 3 "$LOG_FILE" >&2
        return "$rc"
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

# WSL has no Hermes: re-execute this script under Git Bash so it runs against
# the Windows-side Hermes (PowerShell/CMD "bash" is the WSL launcher and cannot
# see Windows env vars like LOCALAPPDATA). WG_RELAUNCHED guards against loops.
relaunch_via_git_bash() {
    [ -n "${WG_RELAUNCHED:-}" ] && return 1
    local gb script_win
    for gb in "/mnt/c/Program Files/Git/bin/bash.exe" "/mnt/c/Program Files (x86)/Git/bin/bash.exe"; do
        [ -x "$gb" ] || continue
        script_win=$(cd "$(dirname "$0")" && pwd | sed 's|^/mnt/\([a-z]\)|\U\1:|' | tr '/' '\\')
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
🔧 workspace-guard installer
👥 1) Install/Update (skill + plugin)
🗑️ 2) Uninstall
📊 3) Status
🚪 4) Quit
EOF
    sep
}

interactive_menu() {
    local choice
    while true; do
        show_menu
        read -r -p "Choose [1-4]: " choice
        case "$choice" in
            1) ASK_CONFIRM=1
               if select_profiles; then cmd_install; fi
               ASK_CONFIRM=0
               sep ;;
            2) if select_profiles; then cmd_uninstall; fi
               sep ;;
            3) cmd_status
               sep ;;
            4) return 0;;
            *) err "invalid choice: $choice";;
        esac
    done
}

# Interactive profile picker (number-based). Fills the ALL_PROFILES /
# TARGET_PROFILES globals for cmd_install / cmd_uninstall. Returns 0 on a
# valid selection, 1 on cancel/error.
select_profiles() {
    local root; root=$(hermes_root)
    local -a names=()
    local name n=0 sel num
    sep
    while IFS= read -r name; do
        n=$((n + 1))
        names+=("$name")
        log "  $n) $name"
    done < <(discover_profiles "$root")
    [ "$n" -gt 0 ] || { err "no profiles found under $root"; sep; return 1; }
    ALL_PROFILES=0
    TARGET_PROFILES=()
    while true; do
        read -r -p "Select profile numbers (comma-separated, 0 = all, Enter = cancel): " sel
        [ -z "$sel" ] && { err "cancelled"; sep; return 1; }
        if [ "$sel" = "0" ]; then ALL_PROFILES=1; sep; return 0; fi
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
            return 0
        fi
        err "invalid selection: $sel (use 1-$n, comma-separated; 0 = all)"
    done
}

confirm_yn() {
    # $1 = prompt; returns 0 on y/Y, 1 otherwise
    local ans
    sep
    read -r -p "$1 [y/N] " ans
    sep
    [ "$ans" = "y" ] || [ "$ans" = "Y" ]
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
    # before overwriting an outdated install.
    local prof="$1" home="$2"
    local repo_skill repo_plugin inst_skill inst_plugin
    repo_skill=$(read_repo_version workspace-organization SKILL.md)
    repo_plugin=$(read_repo_version workspace-guard plugin.yaml)
    inst_skill=$(read_installed_version "$home" skill)
    inst_plugin=$(read_installed_version "$home" plugin)

    NEED_SKILL=0; NEED_PLUGIN=0; NEED_FORCE=0
    NEED_SKILL_UPDATE=0; NEED_PLUGIN_UPDATE=0
    if [ -z "$inst_skill" ]; then
        NEED_SKILL=1; log "  [$prof] skill: not installed -> install"
    elif [ "$FORCE" = 1 ] || ver_gt "$repo_skill" "$inst_skill"; then
        NEED_SKILL=1; NEED_SKILL_UPDATE=1; NEED_FORCE=1
        log "  [$prof] skill: $inst_skill -> $repo_skill (update)"
    else
        log "  [$prof] skill: $inst_skill (up to date)"
    fi
    if [ -z "$inst_plugin" ]; then
        NEED_PLUGIN=1; log "  [$prof] plugin: not installed -> install"
    elif [ "$FORCE" = 1 ] || ver_gt "$repo_plugin" "$inst_plugin"; then
        NEED_PLUGIN=1; NEED_PLUGIN_UPDATE=1; NEED_FORCE=1
        log "  [$prof] plugin: $inst_plugin -> $repo_plugin (update)"
    else
        log "  [$prof] plugin: $inst_plugin (up to date)"
    fi
}

cmd_install() {
    parse_install_flags "$@"
    preflight
    require_hermes_cli
    local root; root=$(require_hermes_root) || return 1
    local url slug; url=$(resolve_repo_url); slug=$(repo_slug "$url")
    logfile "install: repo=$url slug=$slug mode=$([ "$DRY_RUN" = 1 ] && echo dry-run || echo live)"
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
        local hargs=""; [ "$p" != "default" ] && hargs="-p $p"
        local step=0 total=2
        if [ "$DRY_RUN" = 1 ]; then
            if [ "$NEED_SKILL" = 1 ]; then
                step=$((step + 1))
                log "  [${step}/${total}] 将安装 skill"
                logfile "  would run: hermes ${hargs:+$hargs }skills install --yes $slug/src/workspace-organization $([ "$NEED_FORCE" = 1 ] && echo --force)"
            fi
            if [ "$NEED_PLUGIN" = 1 ]; then
                step=$((step + 1))
                log "  [${step}/${total}] 将安装 plugin"
                logfile "  would run: hermes ${hargs:+$hargs }plugins install $url#src/workspace-guard --enable $([ "$NEED_FORCE" = 1 ] && echo --force)"
            fi
            if [ "$NEED_SKILL" = 1 ] || [ "$NEED_PLUGIN" = 1 ]; then
                logfile "  would copy guard-config template to $home/workspace-guard/"
                logfile "  would delete memo: $home/workspace-guard/profile-workspaces.json"
            fi
            continue
        fi
        # real execution
        if [ "$NEED_SKILL" = 1 ]; then
            step=$((step + 1))
            progress_run "$step" "$total" "安装 skill" hermes $hargs skills install --yes "$slug/src/workspace-organization" $([ "$NEED_FORCE" = 1 ] && echo --force) || { err "skill install failed for $p"; return 2; }
        fi
        if [ "$NEED_PLUGIN" = 1 ]; then
            step=$((step + 1))
            progress_run "$step" "$total" "安装 plugin" hermes $hargs plugins install "$url#src/workspace-guard" --enable $([ "$NEED_FORCE" = 1 ] && echo --force) || { err "plugin install failed for $p"; return 2; }
        fi
        if [ "$NEED_SKILL" = 1 ] || [ "$NEED_PLUGIN" = 1 ]; then
            mkdir -p "$home/workspace-guard"
            cp "$SCRIPT_DIR/src/workspace-guard/guard-config.yaml" "$home/workspace-guard/guard-config.yaml"
            rm -f "$home/workspace-guard/profile-workspaces.json"
            logfile "  config template copied; memo deleted for $p"
        fi
    done
    log "Restart Hermes for changes to take effect."
    logfile "install finished (exit 0)"
    sep
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
    require_hermes_cli
    local root; root=$(require_hermes_root) || return 1
    local p home hargs
    logfile "uninstall: mode=$([ "$DRY_RUN" = 1 ] && echo dry-run || echo live) keep_config=$KEEP_CONFIG"
    sep
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
        logfile "== profile: $p home=$home"
        local step=0 total=2
        if [ "$DRY_RUN" = 1 ]; then
            if [ -n "$(find "$home/skills" -path '*/.archive' -prune -o -type f -name SKILL.md -path '*/workspace-organization/SKILL.md' -print 2>/dev/null | head -n1)" ]; then
                step=$((step + 1))
                log "  [${step}/${total}] 将卸载 skill"
                logfile "  would run: hermes ${hargs:+$hargs }skills uninstall workspace-organization"
            else
                log "  [$p] skill not installed, skip"
            fi
            if [ -d "$home/plugins/workspace-guard" ]; then
                step=$((step + 1))
                log "  [${step}/${total}] 将卸载 plugin"
                logfile "  would run: hermes ${hargs:+$hargs }plugins remove workspace-guard"
            else
                log "  [$p] plugin not installed, skip"
            fi
            if [ "$KEEP_CONFIG" = 1 ]; then
                log "  保留配置 (--keep-config)"
                logfile "  keep config"
            else
                logfile "  would delete config + memo under $home/workspace-guard/"
            fi
            continue
        fi
        if [ -n "$(find "$home/skills" -path '*/.archive' -prune -o -type f -name SKILL.md -path '*/workspace-organization/SKILL.md' -print 2>/dev/null | head -n1)" ]; then
            # warn and continue on failure: tolerate missing/removed skills
            step=$((step + 1))
            # `hermes skills uninstall` has no --yes flag (skills_hub.py
            # hardcodes skip_confirm=False) and prompts "Confirm [y/N]"; feed
            # "y" via stdin so the progress step cannot hang on the prompt.
            # The uninstall intent was already confirmed by the menu/profile
            # selection or an explicit --all-profiles invocation.
            progress_run "$step" "$total" "卸载 skill" sh -c "printf 'y\n' | hermes $hargs skills uninstall workspace-organization" || err "warning: skill uninstall failed for $p (continuing)"
        else
            log "  [$p] skill not installed, skip"
        fi
        if [ -d "$home/plugins/workspace-guard" ]; then
            step=$((step + 1))
            progress_run "$step" "$total" "卸载 plugin" hermes $hargs plugins remove workspace-guard || { err "plugin remove failed for $p"; return 2; }
        else
            log "  [$p] plugin not installed, skip"
        fi
        if [ "$KEEP_CONFIG" != 1 ] && [ -d "$home/workspace-guard" ]; then
            rm -f "$home/workspace-guard/guard-config.yaml" "$home/workspace-guard/profile-workspaces.json"
            rmdir "$home/workspace-guard" 2>/dev/null || true
            logfile "  config + memo deleted for $p"
        fi
    done
    logfile "uninstall finished (exit 0)"
    sep
    return 0
}
cmd_status() {
    local root; root=$(require_hermes_root) || return 1
    local repo_skill repo_plugin
    repo_skill=$(read_repo_version workspace-organization SKILL.md)
    repo_plugin=$(read_repo_version workspace-guard plugin.yaml)
    sep
    log "HERMES root: $root"
    log "repo skill: $repo_skill | repo plugin: $repo_plugin"
    local p home is ip
    for p in $(discover_profiles "$root"); do
        home="$root"; [ "$p" != "default" ] && home="$root/profiles/$p"
        is=$(read_installed_version "$home" skill); [ -z "$is" ] && is="-"
        ip=$(read_installed_version "$home" plugin); [ -z "$ip" ] && ip="-"
        log "  $p: skill $is | plugin $ip"
    done
    sep
    return 0
}

main() {
    [ $# -eq 1 ] && [ "$1" = "--help" ] && { usage; return 0; }
    if detect_wsl; then
        if wsl_has_hermes; then
            # WSL-side Hermes exists: install into IT (never the Windows one)
            :
        elif relaunch_via_git_bash "$@"; then
            return 0
        else
            die "WSL detected with no Hermes inside, and Git Bash was not found.
Install Git for Windows (https://gitforwindows.org), or install Hermes inside WSL."
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
