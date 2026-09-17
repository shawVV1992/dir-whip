"""Central static runtime message-template store (session injections / block messages / tool schemas / tool returns).

Core leaf module (spec 5.20, SCR-047 R1): the static runtime message
constants/templates of the A/B/C/D families live here, moved
byte-identical from their original modules (verdict / session_dirs /
audit / allow_path / config); ZERO intra-package imports (no imports at
all), so every core module (config.py included, ADR-0007 dependency
direction) can import it safely, with no cycle. Dynamic builders
(script_invocation_line, _block_message, _settle_instruction,
_orphan_notice, _confirmation_payload) stay in their owning modules and
draw their static text from these constants; original modules keep
same-name constants as import aliases and the __init__.py re-export
surface is unchanged (test import paths intact); copy compliance
(SCR-047 47.C.1): unified block skeleton carries the INLINED
[Reason]/[Next] tail (ADR-0014 D3, pointer cue retired), style rules
T1-T9 applied, structural contracts locked by testing-standards 7.17.

Layer: core
Refs: spec 5.3, spec 5.4, spec 5.11, spec 5.12, spec 5.18, spec 5.19, spec 5.20, SCR-041, SCR-043, SCR-047, SCR-048, ADR-0007, ADR-0014
Key exports:
  - FAIL_OPEN_WARNING_MESSAGE, DISCIPLINE_BLOCK_MESSAGE -- guard-disabled warning (spec 5.12) + session-start discipline block (spec 5.4; SCR-052 G7 renamed from REMINDER_MESSAGE, text verbatim).
  - BLOCK_MESSAGE_* -- unified block skeleton fragments (header / fix lines / uniqueness / allowlist hint / [Reason]+[Next] tail).
  - SESSION_DIR_LIMIT_* -- one-Session-Directory-per-conversation limit messages (top-level + subagent).
  - ORPHAN_NOTICE_* -- advisory orphan-notice lines (header / tail / create-relocate); never blocks.
  - AUDIT_NOTICE_* / GATE_BLOCK_* / NUDGE_MESSAGE_TEMPLATE / SETTLE_INSTRUCTION_TEMPLATE -- write-audit L1 notice, L3 gate, continuation nudge, shared remediation sentence.
  - SETTLE_TOOL_* -- dir_whip_settle tool schema description texts.
  - ALLOW_PATH_* / RUNTIME_ALLOWLIST_ADDED_TEMPLATE -- dir_whip_allow_path tool schema, entry-gating messages (spec 5.11), add feedback.
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

# Spec 5.4 (v2.15 R3 rewrite; v2.16 SCR-048 R3 reuse rule): session-start
# discipline block (top-level sessions only). Full "Session Directory"
# term (T2, the "WD" abbreviation retired), angle-bracket placeholders
# matching the script argument names (R9 disambiguation), ASCII arrow
# (T5), and the reuse rule: ONE Session Directory per conversation --
# write into the existing directory's Outputs/ and .tmp/ rather than
# creating a second one. SCR-052 G7: renamed from REMINDER_MESSAGE to
# DISCIPLINE_BLOCK_MESSAGE (the injected entity is the Discipline Block);
# text verbatim.
# Content requirements (CR-1): <=280 chars cap (tokenizer-independent,
# v2.7 ruling; current text 276) + the key command substrings below +
# `reuse`; no standalone "WD", no em-dash.
DISCIPLINE_BLOCK_MESSAGE = (
    "[dir-whip] Active. Root writes need a Session Directory: "
    "python scripts/create_session_dir.py <task_name> "
    "--workspace <root> (deliverables to Outputs/, scratch to "
    ".tmp/). One Session Directory per conversation: reuse it. "
    "Root forbidden. User path -> dir_whip_allow_path first."
)

# _block_message static skeleton fragments (spec 5.3 + 5.20 unified
# skeleton): the BLOCKED header line, the fix-line templates (top-level +
# subagent variants), the uniqueness line, the allowlist hint and the
# INLINED [Reason]/[Next] template tail (ADR-0014 D3 -- the former
# "Reply using the [Reason]/[Next] template." pointer cue is retired: the
# template definition lived only in the opt-in SKILL.md, so models that
# had not loaded the skill never saw it). The dynamic assembly (Target
# line, shared script invocation line, orphan move line, fragment
# joining) stays in verdict._block_message.
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

# Uniqueness line (leading \n is part of the fragment: it concatenates
# directly after the fix line in the builder).
BLOCK_MESSAGE_UNIQUENESS_LINE = (
    "\nOne Session Directory per conversation."
)

# Allowlist hint (T3 "allowlist dirs entries" term; T8 the config entry is
# user-authored, so the action is attributed to the user).
BLOCK_MESSAGE_ALLOWLIST_HINT_LINE = (
    "If this is a project directory, ask the user to add it to the "
    "allowlist dirs entries in HERMES_HOME/dir-whip/dir-whip-config.yaml "
    "(relative to the Working Directory root, e.g. projects/foo)"
)

# INLINED [Reason]/[Next] template tail (5.20 D-inline): the final two
# lines of every block message -- one line for what was blocked and why,
# one line for the fix you will run and where you will write.
BLOCK_MESSAGE_REASON_LINE = (
    "[Reason] The Working Directory root requires a Session Directory "
    "(or an allowlisted root file); this write target is unprotected."
)
BLOCK_MESSAGE_NEXT_LINE = (
    "[Next] Create the Session Directory with the command above, then "
    "write the deliverable to Outputs/<filename> (or scratch to "
    ".tmp/<filename>)."
)

# Subagent variant tail (ADR-0014 D3: the subagent [Next] reads
# report-to-parent / write-to-parent-target).
BLOCK_MESSAGE_SUBAGENT_REASON_LINE = (
    "[Reason] The Working Directory root requires a Session Directory "
    "(or an allowlisted root file); this write target is unprotected."
)
BLOCK_MESSAGE_SUBAGENT_NEXT_LINE = (
    "[Next] Report the blocked target to the parent agent, or write to "
    "the target directory the parent passed."
)

# ---------------------------------------------------------------- session_dirs.py (session-dir limit + orphan notice)

# Spec 5.19 limit messages (v2.15 rewrite: unified block skeleton with
# the INLINED [Reason]/[Next] tail, spec 5.20). <root>/<claim> are
# substituted at build time (forward-slash rendering, same message
# convention as verdict).
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

# Subagent variant (verdict subagent block-message convention): the
# escape lines are replaced by the parent-target guidance -- subagents
# never create session directories nor hold allow_path sanctions; the
# inline tail keeps the report-to-parent [Next].
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

# R7 advisory notice (testing-standards 7.14.7 O-1: NOTICE header shape).
# ADVISE-ONLY: the notice is plain TEXT -- it never blocks, never
# deletes, and lands at most once per top-level session start.
ORPHAN_NOTICE_HEADER = (
    "NOTICE: Working Directory root has entries outside a Session Directory:"
)
ORPHAN_NOTICE_TAIL = (
    "If a project directory, ask the user to add it to the allowlist "
    "dirs entries in HERMES_HOME/dir-whip/dir-whip-config.yaml (relative "
    "to the Working Directory root)."
)

# Orphan-notice mid-section lines (create + relocate guidance; the
# listed-entry bullet and the shared invocation line stay in
# session_dirs._orphan_notice).
ORPHAN_NOTICE_CREATE_RELOCATE_LINE = (
    "Create a Session Directory, then relocate them:"
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
SETTLE_INSTRUCTION_TEMPLATE = (
    "Remediate now: call dir_whip_settle(paths=[%s]) to move the "
    "file(s) into quarantine (%s), or move them manually into a "
    "Session Directory"
)

# L1 notice tail line (leading space is part of the fragment: it
# concatenates directly after the remediation sentence in the builder).
# v2.15 R3 rewrite: the pipe notation (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/)
# is plainified into the concrete placement form (CR-3: no pipe notation);
# em-dashes replaced with plain punctuation (T5).
AUDIT_NOTICE_TAIL_LINE = (
    " (deliverables to <session_dir>/Outputs/, scratch to "
    "<session_dir>/.tmp/). To keep the "
    "file(s) at the root, ask the user to add them to the allowlist "
    "files entries in dir-whip-config.yaml; give them the exact "
    "command to run: /dir-whip allow <path>. While the block is active "
    "all writes are frozen (config edits included). Further writes to "
    "the Working Directory are blocked until then."
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
    "for remediation (do not create a Session Directory)."
)

# v2.15 R3 rewrite: the pipe notation is plainified into the concrete
# placement form (CR-4); em-dashes replaced with plain punctuation (T5);
# the user-attributed allowlist option and the latch-period freeze kept.
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

# INLINED [Reason]/[Next] template tail (5.20 D-inline) for the L3 gate
# message: replaces the former pointer cue line.
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

# Continuation nudge message template (5.18 v2.8 R1/R2). Three %s slots:
# the unresolved count, the shared remediation sentence and the
# keep-at-root command. v2.15 R3: em-dash replaced with a semicolon (T5).
NUDGE_MESSAGE_TEMPLATE = (
    "[dir-whip] %d unresolved root write(s) remain at the "
    "Working Directory root. %s. Present the resolution "
    "choice to the user: move the file(s) (settle), or keep "
    "them at the root; for the keep-at-root choice, give "
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
    "exemptions belong in dir-whip-config.yaml (allowlist dirs entries) authored by the user."
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
# v2.15 R4: em-dash replaced with a semicolon (T5).
ALLOW_PATH_LATCH_CONTEXT_LINE = (
    "NOTE: a settlement block is currently active; present the resolution "
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
