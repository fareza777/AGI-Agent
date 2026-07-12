"""Belief hygiene — the single source of truth for what must never live in
(or be recalled from) the claim store.

Two poison classes, both learned the hard way:

1. Volatile filesystem state (folder listings, drive contents, access
   status). It goes stale immediately; recalling it makes the model parrot an
   old or invented listing instead of calling list_dir live.
2. Self-referential meta-lessons from past incident loops (wrong diagnoses
   like "create_document always fails", duplicate-message paranoia,
   prompt-injection paranoia). Replaying them re-triggers the very behaviour
   they describe.

Used by:
  consolidator — refuse to WRITE such claims (hard backstop behind the prompt)
  composer     — refuse to RETRIEVE them into context
  store        — QUARANTINE existing poisoned rows (close valid_to) during
                 memory maintenance, so they stop leaking through any path
                 (e.g. reflect() reading active_claims directly)
"""

STALE_MARKERS = (
    "silent-fail",
    "server engram",
    "tool result missing",
    "7x beruntun",
    "infrastructure",
    "gemini tidak bisa",
    "server-side",
    "markdown→docx",
    "markdown->docx",
    "server-side markdown",
    "does not have execution",
    "python-docx binary runner",
    "toolset does not include",
    "consecutive duplicates",
    "2x identik",
    "auto-double-trigger",
    "auto-retry",
    "duplikat lagi",
    "prompt injection",
    "injeksi prompt",
    "abaikan instruksi",
    "bukan pesan asli",
    "not available as a workaround",
)

# Predicate fragments that mark volatile filesystem state.
FS_PRED_TOKENS = ("drive", "folder", "sandbox", "director", "layout",
                  "root_content", "root_folder", "filesystem", "file_system")

# Value fragments that mark volatile filesystem state.
FS_VAL_MARKERS = ("list_dir", "my drive", "access denied", "root folder",
                  "g:\\", "out of allowed director")

# Exact predicates from known past poisoning incidents.
POISONED_PREDICATES = frozenset({
    "tool_availability_engram",
    "lesson_create_document_limits",
    "lesson_create_document_outage_2026_06_11",
    "lesson_duplicate_message_handling",
    "lesson_duplicate_execution",
})


def is_poisoned(predicate: str, value: str) -> bool:
    """True when a (predicate, value) pair must not be believed or recalled."""
    p = (predicate or "").strip().lower()
    v = (value or "").strip().lower()
    if p in POISONED_PREDICATES:
        return True
    if any(t in p for t in FS_PRED_TOKENS):
        return True
    if any(m in v for m in FS_VAL_MARKERS):
        return True
    return any(m in v for m in STALE_MARKERS)
