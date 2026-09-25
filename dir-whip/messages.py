"""Central static runtime message-template store (session injections / block messages / tool schemas / tool returns).

Core leaf module: the static message constants/templates live here with
zero imports, so every core module can import it safely with no cycle.
Dynamic builders stay in their owning modules and draw their static text
from these constants; the original modules keep same-name import aliases,
so all test import paths stay intact. Message texts are frozen.

Layer: core
Refs: spec 5.11, spec 5.20, ADR-0014
Key exports:
  - FAIL_OPEN_WARNING_MESSAGE, DISCIPLINE_BLOCK_MESSAGE -- guard-disabled warning + session-start discipline block (text frozen).
  - BLOCK_MESSAGE_* -- unified block skeleton fragments (header / fix lines / uniqueness / allowlist hint / [Reason]+[Next] tail).
  - SESSION_DIR_LIMIT_* -- one-Session-Directory-per-conversation limit messages (top-level + subagent).
  - ORPHAN_NOTICE_* -- advisory orphan-notice lines (header / tail / create-relocate); never blocks.
  - AUDIT_NOTICE_* / GATE_BLOCK_* / NUDGE_MESSAGE_TEMPLATE / SETTLE_INSTRUCTION_TEMPLATE -- write-audit L1 notice, L3 gate, continuation nudge, shared remediation sentence.
  - SETTLE_TOOL_* -- dir_whip_settle tool schema description texts.
  - ALLOW_PATH_* / RUNTIME_ALLOWLIST_ADDED_TEMPLATE -- dir_whip_allow_path tool schema, entry-gating messages, add feedback.
"""

# ---------------------------------------------------------------- guard module (session injections + block skeleton)

# spec 5.12: injected once per session while the guard is disabled
# (working_dir_root unresolved).
FAIL_OPEN_WARNING_MESSAGE = (
    "[dir-whip] WARNING: The guard is DISABLED because the Working "
    "Directory\n"
    "could not be resolved. File writes are NOT being enforced.\n"
    "Check dir-whip-config.yaml (working_dir_root) or your profile's config.yaml\n"
    "(terminal.cwd) and restart the session."
)

# spec 5.4: session-start discipline block (top-level only); <=280 chars,
# one Session Directory per conversation, no standalone "WD", no em-dash.
DISCIPLINE_BLOCK_MESSAGE = (
    "[dir-whip] Active. Root writes need a Session Directory: "
    "python scripts/create_session_dir.py <task_name> "
    "--workspace <root> (deliverables to Outputs/, scratch to "
    ".tmp/). One Session Directory per conversation: reuse it. "
    "Root forbidden. User path -> dir_whip_allow_path first."
)

# spec 5.3 unified block skeleton fragments; the dynamic assembly (Target
# line, script invocation line, orphan move line, fragment joining) stays
# in classify._block_message.
BLOCK_MESSAGE_HEADER_LINE = (
    "BLOCKED: File writes in the Working Directory require a Session "
    "Directory or an allowed root file."
)

# One %s slot: session_dirs.script_invocation_line output.
BLOCK_MESSAGE_FIX_LINE_TEMPLATE = (
    "Fix: Create a Session Directory first:\n"
    "  %s\n"
    "Then write the deliverable to Outputs/<filename> "
    "(or scratch to .tmp/<filename>).\n"
    "User-specified path -> dir_whip_allow_path first."
)

# Subagent variant fix line (subagents never create session directories).
BLOCK_MESSAGE_SUBAGENT_FIX_LINE = (
    "Fix: write to the target directory passed by the parent agent."
)

# Uniqueness line; the leading \n concatenates directly after the fix line.
BLOCK_MESSAGE_UNIQUENESS_LINE = (
    "\nOne Session Directory per conversation."
)

# Allowlist hint (the config entry is user-authored).
BLOCK_MESSAGE_ALLOWLIST_HINT_LINE = (
    "If this is a project directory, ask the user to add it to the "
    "allowlist dirs entries in HERMES_HOME/dir-whip/dir-whip-config.yaml "
    "(relative to the Working Directory root, e.g. projects/foo)"
)

# INLINED [Reason]/[Next] tail: the final two lines of every block
# message (what was blocked / the fix and target).
BLOCK_MESSAGE_REASON_LINE = (
    "[Reason] The Working Directory root requires a Session Directory "
    "(or an allowlisted root file); this write target is unprotected."
)
BLOCK_MESSAGE_NEXT_LINE = (
    "[Next] Create the Session Directory with the command above, then "
    "write the deliverable to Outputs/<filename> (or scratch to "
    ".tmp/<filename>)."
)

# Subagent variant tail ([Next] reports to parent / writes to parent target).
BLOCK_MESSAGE_SUBAGENT_REASON_LINE = (
    "[Reason] The Working Directory root requires a Session Directory "
    "(or an allowlisted root file); this write target is unprotected."
)
BLOCK_MESSAGE_SUBAGENT_NEXT_LINE = (
    "[Next] Report the blocked target to the parent agent, or write to "
    "the target directory the parent passed."
)

# ---------------------------------------------------------------- session_dirs.py (session-dir limit + orphan notice)

# spec 5.19 limit messages; <root>/<claim> substituted at build time
# (forward-slash rendering).
SESSION_DIR_LIMIT_BLOCK_MESSAGE = (
    "BLOCKED: One Session Directory per conversation.\n"
    "This conversation already uses: %(root)s/%(claim)s\n"
    "Write deliverables to %(claim)s/Outputs/ (scratch: %(claim)s/.tmp/).\n"
    "User-specified path -> dir_whip_allow_path first.\n"
    "[Reason] This conversation's Session Directory slot is already "
    "bound; a second Session Directory is blocked.\n"
    "[Next] Write the deliverable into %(claim)s/Outputs/ (or scratch "
    "to %(claim)s/.tmp/) instead of creating another Session Directory."
)

# Subagent variant: parent-target guidance replaces the escape lines
# (subagents never create session directories nor hold allow_path sanctions).
SESSION_DIR_LIMIT_SUBAGENT_MESSAGE = (
    "BLOCKED: One Session Directory per conversation.\n"
    "This conversation already uses: %(root)s/%(claim)s\n"
    "Write deliverables to %(claim)s/Outputs/ (scratch: %(claim)s/.tmp/).\n"
    "Fix: write to the target directory passed by the parent agent.\n"
    "[Reason] This conversation's Session Directory slot is already "
    "bound; subagents never create Session Directories.\n"
    "[Next] Report the blocked target to the parent agent, or write to "
    "the target directory the parent passed."
)

# Advisory notice: plain text, never blocks, at most once per top-level
# session start.
ORPHAN_NOTICE_HEADER = (
    "NOTICE: Working Directory root has entries outside a Session Directory:"
)
ORPHAN_NOTICE_TAIL = (
    "If a project directory, ask the user to add it to the allowlist "
    "dirs entries in HERMES_HOME/dir-whip/dir-whip-config.yaml (relative "
    "to the Working Directory root)."
)

# Orphan-notice mid-section lines (bullet + invocation line stay in
# session_dirs._orphan_notice).
ORPHAN_NOTICE_CREATE_RELOCATE_LINE = (
    "Create a Session Directory, then relocate them:"
)
ORPHAN_NOTICE_MV_LINE = (
    '  mv "<root>/<entry>" "<session_dir>/Outputs/"'
)

# ---------------------------------------------------------------- audit family (L1 notice / remediation / L3 gate / nudge / settle schema)

# L1 notice header line (one notice per result, context hygiene).
AUDIT_NOTICE_HEADER_LINE = (
    "[dir-whip] Write audit: the following file(s) were written to the "
    "Working Directory root outside any Session Directory:"
)

# Shared remediation sentence (single source): used by BOTH the L1 notice
# and the continuation nudge; two %s slots (quoted path list, quarantine
# location). The short settle call line below is L3-gate only.
SETTLE_INSTRUCTION_TEMPLATE = (
    "Remediate now: call dir_whip_settle(paths=[%s]) to move the "
    "file(s) into quarantine (%s), or move them manually into a "
    "Session Directory"
)

# L1 notice tail line; the leading space concatenates directly after the
# remediation sentence in the builder.
AUDIT_NOTICE_TAIL_LINE = (
    " (deliverables to <session_dir>/Outputs/, scratch to "
    "<session_dir>/.tmp/). To keep the "
    "file(s) at the root, ask the user to add them to the allowlist "
    "files entries in dir-whip-config.yaml; give them the exact "
    "command to run: /dir-whip allow <path>. While the block is active "
    "all writes are frozen (config edits included). Further writes to "
    "the Working Directory are blocked until then."
)

# L3 gate line fragments (spec 5.18); the dynamic assembly (path list,
# settle-call join, fragment joining) stays in
# audit_prompts._audit_gate_block_message.
GATE_BLOCK_HEADER_LINE = (
    "BLOCKED: earlier command(s) wrote file(s) to the Working Directory "
    "root that still need remediation:"
)

# Subagent variant fix line (report to the parent agent).
GATE_BLOCK_SUBAGENT_FIX_LINE = (
    "Fix: report the pending path(s) to the parent agent "
    "for remediation (do not create a Session Directory)."
)

# Gate fix line: concrete placement form, user-attributed allowlist
# option, latch-period freeze.
GATE_BLOCK_FIX_LINE = (
    "Fix: move the file(s) into a Session Directory "
    "(deliverables to <session_dir>/Outputs/, scratch to "
    "<session_dir>/.tmp/), or ask the user "
    "to add them to the allowlist files entries in "
    "dir-whip-config.yaml; give them the "
    "exact command: /dir-whip allow <path>. While the block is "
    "active all writes are frozen (config edits included)."
)

# One %s slot: the quoted unresolved-path list.
GATE_BLOCK_SETTLE_LINE = (
    "Remediate now: call dir_whip_settle(paths=[%s])."
)

# INLINED [Reason]/[Next] tail for the L3 gate message.
GATE_BLOCK_REASON_LINE = (
    "[Reason] Earlier command(s) wrote unprotected root file(s) that "
    "still need remediation; the settlement latch is active."
)
GATE_BLOCK_NEXT_LINE = (
    "[Next] Run the dir_whip_settle call above to quarantine the "
    "file(s), or move them into a Session Directory; the latch opens "
    "once every pending path is settled."
)
GATE_BLOCK_SUBAGENT_REASON_LINE = (
    "[Reason] Earlier command(s) wrote unprotected root file(s) that "
    "still need remediation; the settlement latch is inherited from "
    "the parent session."
)
GATE_BLOCK_SUBAGENT_NEXT_LINE = (
    "[Next] Report the pending path(s) to the parent agent so it can "
    "settle or relocate them."
)

# Continuation nudge; three %s slots: unresolved count, shared
# remediation sentence, keep-at-root command.
NUDGE_MESSAGE_TEMPLATE = (
    "[dir-whip] %d unresolved root write(s) remain at the "
    "Working Directory root. %s. Present the resolution "
    "choice to the user: move the file(s) (settle), or keep "
    "them at the root; for the keep-at-root choice, give "
    "the user the exact command to run: %s. Finish only "
    "after settlement or the user's decision."
)

# dir_whip_settle schema description texts (schema dict stays in audit.py).
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

# ---------------------------------------------------------------- runtime_allowlist.py (tool schema + entry-gating messages)

# dir_whip_allow_path schema description texts (schema dict stays in
# runtime_allowlist.py).
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

# spec 5.11: subagent rejection, parent-guidance variant.
ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: dir_whip_allow_path is not available to subagents.\n"
    "Exemptions are granted by the user via the main agent. Write to the target\n"
    "directory passed by the parent agent, or report back so the parent can ask\n"
    "the user."
)

# spec 5.11: Working Directory root rejection.
ALLOW_PATH_ROOT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the Working Directory root itself cannot be allowlisted.\n"
    "Allow a specific file or subdirectory path instead; workspace-wide\n"
    "exemptions belong in dir-whip-config.yaml (allowlist dirs entries) authored by the user."
)

# spec 5.11: two-step confirmation payload ("<path>" substituted with the
# forward-slash form of the requested path).
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

# spec 5.11: latch-context line, appended only when the pending set is
# non-empty (latch active).
ALLOW_PATH_LATCH_CONTEXT_LINE = (
    "NOTE: a settlement block is currently active; present the resolution "
    "choice to the user: move the file(s) (settle), or keep them at the root "
    "(give the user the exact command: /dir-whip allow <path>). Writes stay "
    "frozen until then."
)

# ---------------------------------------------------------------- config.py (add-layer rejections + add feedback)

# spec 5.11 add-layer rejection messages (single source: config.py and
# runtime_allowlist.py both import these).
ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the path is outside the Working Directory; no allowlist\n"
    "entry is needed. Writes there are allowed and logged (external-write).\n"
    "Retry the write directly at the requested path."
)
ALLOW_PATH_EMPTY_REJECTED_MESSAGE = (
    "[dir-whip] Rejected: empty path. dir_whip_allow_path requires an explicit\n"
    "path inside the Working Directory."
)

# Confirmed-add feedback; one %s slot: the normalized forward-slash path.
RUNTIME_ALLOWLIST_ADDED_TEMPLATE = (
    "[dir-whip] Added to runtime allowlist: %s"
)
