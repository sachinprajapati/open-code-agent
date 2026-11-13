# OpenAI-Compatible Provider

`OpenAICompatibleProvider` implements the OpenAI-compatible **Chat
Completions** streaming API only. It does not support the OpenAI Responses API.
It can target a compatible cloud service, OpenRouter, Ollama, LM Studio, vLLM,
or another server exposing `/models` and `/chat/completions` below a configured
base URL.

## Profiles and secrets

Profiles contain an `api_key_secret_ref`, never the API-key value. The built-in
environment resolver accepts explicit references such as `env:OPENAI_API_KEY`.
Future hosts, including a VS Code extension, should provide a `SecretResolver`
backed by their own SecretStorage/IPC boundary. OS-keyring persistence is not
implemented.

```python
OpenAICompatibleProfile(
    profile_id="openai",
    display_name="OpenAI-compatible HTTPS",
    base_url="https://api.example.invalid/v1",
    default_model="example-model",
    api_key_secret_ref="env:OPENAI_API_KEY",
)
```

```python
# OpenRouter-compatible endpoint
base_url = "https://openrouter.ai/api/v1"

# Ollama
base_url = "http://localhost:11434/v1"

# LM Studio
base_url = "http://127.0.0.1:1234/v1"
```

HTTPS is expected for remote endpoints. Plain HTTP is automatically allowed
only for explicit loopback hosts (`localhost`, `127.0.0.1`, and `::1`). Setting
`allow_insecure_http=True` for another host can expose prompts and API keys in
transit. Extra headers are non-secret only; authorization and provider-managed
headers are rejected.

## Behavior

Provider IDs are namespaced as `openai-compatible.<profile_id>`, so multiple
profiles can coexist in a registry. Model listing uses `GET /models`, sorts by
model ID, and reports conservative capabilities—there is no automatic
capability detection. Servers without `/models` currently produce an invalid
configuration result with a diagnostic.

Requests use `max_tokens` for broad Chat Completions compatibility and ask for
streaming usage with `stream_options.include_usage`. Provider-neutral metadata
is not sent. Text, supported image URL/data-URL parts, function tools,
assistant tool calls, and tool results are mapped to the compatible wire format.

The incremental SSE parser handles split bytes, UTF-8, CRLF/LF, comments,
multiple data lines, `[DONE]`, and bounded event size. Only choice index zero
is processed. Tool argument fragments are retained verbatim until they form a
valid JSON object, then become a normalized `ToolCallCompleted` event.

Remote HTTP and compatible-stream failures become terminal `StreamError` events
with sanitized, bounded data. Local mapping, secret-resolution, and lifecycle
failures remain local exceptions. Cancelling the consuming task propagates
`asyncio.CancelledError` and closes the response stream.

The provider owns a client it constructs from a transport or default settings;
an injected `httpx.AsyncClient` stays caller-owned. Use `async with` or
`await provider.aclose()` to end the provider lifecycle. No agent loop,
automatic tool execution, credential persistence, retries, routing, or native
Anthropic/Gemini integration is included.
