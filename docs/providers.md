# Provider Interface

`ModelProvider` is the asynchronous boundary used by the future agent runtime.
It accepts normalized `ModelRequest` models and yields normalized
`ModelStreamEvent` objects. Streaming is the only generation interface; a
future caller may collect a stream when it needs a complete response.

Provider IDs identify adapter implementations (for example, `local.ollama`),
while model IDs identify models supplied by an adapter. Registry IDs are exact,
lowercase ASCII identifiers and are preserved rather than normalized.

`validate_configuration()` returns `ConfigurationValidationResult`. Ordinary
configuration problems become invalid structured results, potentially with a
serializable `ModelError`. In contrast, `ProviderError` subclasses are local
Python infrastructure/control-flow exceptions. A normalized remote/API failure
should appear as a terminal `StreamError` event.

Each `ModelProviderRegistry` is an independent lifecycle object—there is no
global registry or stored credential state. The registry stores implementations
and returns deterministic ID-sorted snapshots.

`MockModelProvider` supplies deep-copied, deterministic scripted events,
models, validation outcomes, and request history for unit tests and local
development. It is designed primarily for sequential use; concurrent streams
are independent but request-history ordering follows task scheduling.

Cancelling a task consuming a provider stream propagates `asyncio.CancelledError`
unchanged. No real provider integration, network transport, credential storage,
or model routing exists yet.
