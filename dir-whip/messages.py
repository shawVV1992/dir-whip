"""Central message template store for dir-whip (spec 5.20, SCR-047 R1,
ADR-0014).

Core leaf module: the static runtime message constants/templates of the
A/B/C/D families (session injections, block messages, tool schemas, tool
returns) live here, moved byte-identical from their original modules
(verdict / session_dirs / audit / allow_path / config). ZERO intra-package
imports -- pure string constants, no imports at all -- so every core
module (config.py included, ADR-0007 dependency direction) can import it
safely, with no cycle.

Dynamic builders (script_invocation_line, _block_message,
_remediation_instruction, _orphan_notice, _confirmation_payload) stay in
their owning modules and draw their static text from these constants.
Original modules keep same-name constants as import aliases; the
__init__.py re-export surface is unchanged (test import paths intact).
Message texts are byte-identical to the pre-v0.6.7 sources (the wording
rewrite is a later phase, 47.C.1).
"""

# ---------------------------------------------------------------- verdict.py (session injections + block skeleton)

# Spec 5.12 (term-updated): injected once per session when the guard is
# disabled because working_dir_root could not be resolved.
FAIL_OPEN_WARNING_MESSAGE = (
    "[dir-whip] WARNING: The guard is DISABLED because the Working "
    "Directory\n"
    "could not be resolved. File writes are NOT being enforced.\n"
    "Check dir-whip-config.yaml (working_dir_root) or your profile's config.yaml\n"
    "(terminal.cwd) and restart the session."
)

# Spec 5.4 (v2.8 R9): session-start discipline reminder (top-level
# sessions only). Placement parenthetical de-ambiguated (R9, realhost
# incident 20260827_222411_245249: the arrow shorthand was misread as a
# literal path template); verbatim-locked + len<=280 chars cap
# (tokenizer-independent; new length 251).
REMINDER_MESSAGE = (
    "[dir-whip] Active. WD writes need a session dir first: python "
    "scripts/create_session_dir.py <task> --workspace <root> "
    "(write the deliverable to Outputs/<filename>, or scratch to "
    ".tmp/<filename>). Root forbidden. "
    "User path -> dir_whip_allow_path first."
)

# _block_message static skeleton fragments (spec 5.3): the BLOCKED header
# line, the fix-line templates (top-level + subagent variants), the
# uniqueness line, the allowlist hint and the [Reason]/[Next] cue. The
# dynamic assembly (Target line, shared script invocation line, orphan
# rename line, fragment joining) stays in verdict._block_message.
BLOCK_MESSAGE_HEADER_LINE = (
    "BLOCKED: File writes in the Working Directory require a Session "
    "Directory or an allowed root file."
)

# One %s slot: session_dirs.script_invocation_line output.
BLOCK_MESSAGE_FIX_LINE_TEMPLATE = (
    "Fix: Create a session directory first:\n"
    "  %s\n"
    "Then write the deliverable to Outputs/<filename> "
    "(or scratch to .tmp/<filename>).\n"
    "User-specified path -> dir_whip_allow_path first."
)

# Subagent variant fix line (subagents never create session directories).
BLOCK_MESSAGE_SUBAGENT_FIX_LINE = (
    "Fix: write to the target directory passed by the parent agent."
)

# Uniqueness line (leading \n is part of the fragment: it concatenates
# directly after the fix line in the builder).
BLOCK_MESSAGE_UNIQUENESS_LINE = (
    "\nOne session directory per conversation."
)

BLOCK_MESSAGE_ALLOWLIST_HINT_LINE = (
    "If this is a project directory, add it to the allowlist dirs in "
    "HERMES_HOME/dir-whip/dir-whip-config.yaml (relative to the Working "
    "Directory root, e.g. projects/foo)"
)

# Shared [Reason]/[Next] cue line: also the L3 gate message tail
# (audit._audit_gate_block_message).
BLOCK_MESSAGE_TEMPLATE_CUE_LINE = (
    "Reply using the [Reason]/[Next] template."
)

# ---------------------------------------------------------------- session_dirs.py (session-dir limit + orphan notice)

# Spec 5.19 verbatim locks. <root>/<claim> are substituted at build
# time (forward-slash rendering, same message convention as verdict).
SESSION_DIR_LIMIT_BLOCK_MESSAGE = (
    "BLOCKED: One session directory per conversation.\n"
    "This conversation already uses: %(root)s/%(claim)s\n"
    "Write deliverables to %(claim)s/Outputs/ (scratch: %(claim)s/.tmp/).\n"
    "User-specified path -> dir_whip_allow_path first."
)

# Subagent variant (verdict subagent block-message convention): the
# escape lines are replaced by the parent-target guidance -- subagents
# never create session directories nor hold allow_path sanctions.
SESSION_DIR_LIMIT_SUBAGENT_MESSAGE = (
    "BLOCKED: One session directory per conversation.\n"
    "This conversation already uses: %(root)s/%(claim)s\n"
    "Write deliverables to %(claim)s/Outputs/ (scratch: %(claim)s/.tmp/).\n"
    "Fix: write to the target directory passed by the parent agent."
)

# R7 advisory notice (testing-standards 7.14.7 O-1: header/tail pinned).
# ADVISE-ONLY: the notice is plain TEXT -- it never blocks, never
# deletes, and lands at most once per top-level session start.
ORPHAN_NOTICE_HEADER = (
    "NOTICE: Working Directory root has entries outside a session directory:"
)
ORPHAN_NOTICE_TAIL = (
    "If a project directory, add it to the allowlist dirs in "
    "HERMES_HOME/dir-whip/dir-whip-config.yaml (relative to the Working "
    "Directory root)."
)

# Orphan-notice mid-section lines (create + relocate guidance; the
# listed-entry bullet and the shared invocation line stay in
# session_dirs._orphan_notice).
ORPHAN_NOTICE_CREATE_RELOCATE_LINE = (
    "Create a session directory, then relocate them:"
)
ORPHAN_NOTICE_MV_LINE = (
    '  mv "<root>/<entry>" "<session_dir>/Outputs/"'
)

# ---------------------------------------------------------------- audit.py (L1 notice / remediation / L3 gate / nudge / settle schema)

# L1 notice header line (one notice per result, context hygiene).
AUDIT_NOTICE_HEADER_LINE = (
    "[dir-whip] Write audit: the following file(s) were written to the "
    "Working Directory root outside any Session Directory:"
)

# Shared remediation sentence (5.18 v2.8 R1, single source of truth):
# used by BOTH the L1 notice and the continuation nudge. Two %s slots:
# the quoted path list and the quarantine location. The short settle
# call line below (L3 gate only) is deliberately separate.
REMEDIATION_INSTRUCTION_TEMPLATE = (
    "Remediate now: call dir_whip_settle(paths=[%s]) to move the "
    "file(s) into quarantine (%s), or move them manually into a "
    "Session Directory"
)

# L1 notice tail line (leading space is part of the fragment: it
# concatenates directly after the remediation sentence in the builder).
AUDIT_NOTICE_TAIL_LINE = (
    " (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/). To keep the "
    "file(s) at the root, ask the user to add them to the allowlist "
    "files entries in dir-whip-config.yaml (files: [notes.txt]) — "
    "give them the exact command to run: /dir-whip allow <path> — "
    "while the block is active all writes are frozen (config edits "
    "included). Further writes to the Working Directory are blocked "
    "until then."
)

# L3 gate message static line fragments (spec 5.18); the dynamic
# assembly (path list, settle-call join, fragment joining) stays in
# audit._audit_gate_block_message.
GATE_BLOCK_HEADER_LINE = (
    "BLOCKED: earlier command(s) wrote file(s) to the Working Directory "
    "root that still need remediation:"
)

# Subagent variant fix line (report to the parent agent).
GATE_BLOCK_SUBAGENT_FIX_LINE = (
    "Fix: report the pending path(s) to the parent agent "
    "for remediation (do not create a session directory)."
)

GATE_BLOCK_FIX_LINE = (
    "Fix: move the file(s) into a Session Directory "
    "(YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/), or ask the user "
    "to add them to the allowlist files entries in "
    "dir-whip-config.yaml (files: [notes.txt]) — give them the "
    "exact command: /dir-whip allow <path> — while the block is "
    "active all writes are frozen (config edits included)."
)

# One %s slot: the quoted unresolved-path list.
GATE_BLOCK_SETTLE_LINE = (
    "Remediate now: call dir_whip_settle(paths=[%s])."
)

# Continuation nudge message template (5.18 v2.8 R1/R2). Three %s slots:
# the unresolved count, the shared remediation sentence and the
# keep-at-root command.
NUDGE_MESSAGE_TEMPLATE = (
    "[dir-whip] %d unresolved root write(s) remain at the "
    "Working Directory root. %s. Present the resolution "
    "choice to the user: move the file(s) (settle), or keep "
    "them at the root — for the keep-at-root choice, give "
    "the user the exact command to run: %s. Finish only "
    "after settlement or the user's decision."
)

# dir_whip_settle tool schema description texts (the schema dict itself
# stays in audit.py, same contract as ALLOW_PATH_TOOL_SCHEMA).
SETTLE_TOOL_DESCRIPTION = (
    "Move files that the dir-whip write audit flagged in the Working "
    "Directory root into the audit quarantine "
    "(<dir-whip home>/audit-quarantine/), "
    "settling the write block. Hard-constrained to paths currently "
    "listed as unresolved by the write-audit notice/gate."
)
SETTLE_TOOL_PATHS_DESCRIPTION = (
    "Paths to settle (absolute, forward slashes, as listed "
    "by the write-audit notice; relative to the Working "
    "Directory root tolerated)"
)

# ---------------------------------------------------------------- allow_path.py (tool schema + entry-gating messages)

# dir_whip_allow_path tool schema description texts (the schema dict
# itself stays in allow_path.py, OpenAI function-call format).
ALLOW_PATH_TOOL_DESCRIPTION = (
    "Add an absolute path to the dir-whip runtime allowlist so "
    "file operations under that path are exempt for this session (Tier 0). "
    "Use when the user explicitly specifies a path to write to. "
    "Two-step confirmation: call WITHOUT confirm to obtain the "
    "user-confirmation briefing, relay it to the user, then re-call with "
    "confirm=true ONLY after the user explicitly approves."
)
ALLOW_PATH_TOOL_PATH_DESCRIPTION = (
    "Absolute path to allow (forward slashes)"
)
ALLOW_PATH_TOOL_CONFIRM_DESCRIPTION = (
    "true ONLY after the user explicitly approved the "
    "briefing payload from the first call (default false)"
)

# Spec 5.11 v2.9 (SCR-041 R2a): subagent rejection, parent-guidance variant.
ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: dir_whip_allow_path is not available to subagents.\n"
    "Exemptions are granted by the user via the main agent. Write to the target\n"
    "directory passed by the parent agent, or report back so the parent can ask\n"
    "the user."
)

# Spec 5.11 v2.9 (SCR-041 R2b): Working Directory root rejection.
ALLOW_PATH_ROOT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the Working Directory root itself cannot be allowlisted.\n"
    "Allow a specific file or subdirectory path instead; workspace-wide\n"
    "exemptions belong in dir-whip-config.yaml (allowlist dirs) authored by the user."
)

# Spec 5.11 v2.9 (SCR-041 R3): two-step confirmation payload ("<path>"
# substituted with the forward-slash form of the requested path).
ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE = (
    "[dir-whip] CONFIRMATION REQUIRED: adding \"%s\" to the runtime allowlist\n"
    "exempts ALL file operations under it from the guard for the rest of this\n"
    "session. Previously recorded root writes under it are NOT remediated by\n"
    "this exemption (they stay pending until settled). The entry expires\n"
    "automatically when the session ends; persistent exemptions belong in\n"
    "dir-whip-config.yaml\n"
    "(allowlist files/dirs, removable via /dir-whip remove).\n"
    "Present this to the user and ask for explicit approval. Re-call\n"
    "dir_whip_allow_path(path=..., confirm=true) ONLY after the user approves."
)

# Spec 5.11 v2.9 (SCR-041 R3): latch-context conditional line, appended to
# the payload only when the pending set is non-empty (latch active).
ALLOW_PATH_LATCH_CONTEXT_LINE = (
    "NOTE: a settlement block is currently active \u2014 present the resolution "
    "choice to the user: move the file(s) (settle), or keep them at the root "
    "(give the user the exact command: /dir-whip allow <path>). Writes stay "
    "frozen until then."
)

# ---------------------------------------------------------------- config.py (add-layer rejections + add feedback)

# SCR-043 R3 (spec 5.11 v2.11) add-layer rejection messages.
# SINGLE SOURCE (SCR-047 R1, ADR-0007 direction respected): config.py and
# allow_path.py both import this constant from here -- the former
# verbatim duplicate pair is gone.
ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the path is outside the Working Directory; no allowlist\n"
    "entry is needed. Writes there are allowed and logged (external-write).\n"
    "Retry the write directly at the requested path."
)
ALLOW_PATH_EMPTY_REJECTED_MESSAGE = (
    "[dir-whip] Rejected: empty path. dir_whip_allow_path requires an explicit\n"
    "path inside the Working Directory."
)

# Confirmed-add feedback ("Added to runtime allowlist: <path>"); one %s
# slot: the normalized forward-slash path.
RUNTIME_ALLOWLIST_ADDED_TEMPLATE = (
    "[dir-whip] Added to runtime allowlist: %s"
)
