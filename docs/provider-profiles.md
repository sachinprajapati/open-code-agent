# Provider Profiles

`ProviderProfileStore` persists OpenAI-compatible profiles in versioned,
human-readable JSON at `<data_dir>/profiles.json`. Profiles contain endpoint
configuration and an `env:VARIABLE_NAME` secret reference only; resolved API
keys are never written or displayed.

Profile IDs identify saved configurations. Provider IDs identify adapter
instances and are derived as `openai-compatible.<profile_id>`. Model IDs are
the server-provided model names. These three identities must not be confused.

Writes serialize profile IDs deterministically, create parents only when a
write is requested, use a temporary file followed by replacement, and use
owner-only permissions where the platform supports them. A store path may not
be a symlink. The store serializes callers within one process; it deliberately
does not claim cross-process locking, so separate processes must not edit one
store concurrently.

Use `open-code-agent provider --help` for add, list, show, remove,
set-default, test, and models commands. `test` and `models` resolve a reference
only for their request and always close the provider afterward.
