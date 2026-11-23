# Task history and restore safety

The VS Code client supplies its user-global storage directory to the app server.
OpenCodeAgent stores task metadata and the visible user/assistant transcript in
`tasks.sqlite3` there, scoped by a SHA-256 identity of the resolved workspace
root. It stores profile IDs, model and mode, never provider API keys,
SecretStorage values, environment values, or raw secret-resolution responses.

The task RPCs create, list, search, resume, rename, and delete tasks for the
current workspace. Resume creates a fresh app-server session with the saved
profile; it does not replay tools or other side effects.
If its provider is missing, resume asks for provider setup instead of switching
providers. Deleting a task removes its metadata and transcript for that
workspace.

History is intentionally bounded: individual persisted messages are truncated
at 128 KiB, event data at 32 KiB, and a task holds at most 2,000 entries. Start
a new task when the transcript limit is reached.

Approved patch creations are checkpointed with their path, absent before-state,
and created content hash. Restoring one removes the created file only when its
current hash still matches; otherwise it reports a conflict and leaves the file
unchanged. Task history must never be used to undo shell commands, MCP calls,
browser actions, installs, database changes, or arbitrary manual file edits.
