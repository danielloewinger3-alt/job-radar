"""Assist-domain limits that logically belong in backend.config but cannot live
there (config.py is read-only for this workstream)."""

# Combined cap across ALL project-file text placed into one pack's AI context,
# on top of the per-file 200 KB extraction cap (config.PROJECT_FILE_TEXT_EXTRACT_MAX_BYTES).
PROJECT_CONTEXT_TOTAL_MAX_BYTES = 512 * 1024

# Upper bound on a single user-supplied pack answer value.
ANSWER_MAX_CHARS = 4000
