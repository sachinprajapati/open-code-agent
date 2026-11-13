# Provider-Neutral Protocol

The protocol package is the boundary between the future agent runtime and model
providers. Provider adapters will translate native provider payloads into these
strict Pydantic models; they must not expose SDK objects beyond the adapter.

Messages use normalized roles and discriminated content parts. Tool definitions,
calls, and results only carry JSON-compatible values. Model requests express the
portable subset of request controls, while provider-specific information belongs
only in explicitly declared JSON metadata when needed.

Streaming uses a discriminated `ModelStreamEvent` union. Tool argument deltas
remain raw text because JSON may be incomplete until the provider reports a
complete `ToolCall`. `UsageInfo.total_tokens` is provider-reported rather than
derived: cached and reasoning token counts may overlap other counts.

This package has no provider integrations, execution tools, filesystem access,
or agent loop.
