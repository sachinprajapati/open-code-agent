# Bounded Agent Loop

`open_code_agent.agent.AgentRunner` is the provider-independent orchestration
layer.  A model step is one complete `ModelProvider.stream()` response; tool
execution happens after that step and before a possible next model step.  The
runner calls registered tools sequentially and preserves the provider's tool
call order.

## Input and streaming

`AgentRunRequest` builds a new conversation in one unambiguous order: optional
system prompt, copied initial messages, then optional task as the final user
message.  Empty conversations are rejected.  The caller's messages and all
returned result snapshots are copied, so runs on the same runner do not share
history or counters.

`AgentRunner.stream()` emits a provider-neutral `AgentRunEvent` stream:
`RunStarted`, model-step lifecycle events (including every normalized provider
event as it arrives), tool execution events, and exactly one terminal completed,
failed, or limit-reached event.  `collect_agent_run()` consumes this stream once
and returns the terminal structured result.

The response assembler accepts one strict normalized stream.  It requires one
start event and one terminal event, keeps text and completed tool-call ordering,
and rejects partial calls, duplicate IDs, unknown argument fragments, and events
after a terminal event.  A `StreamError` is a failed step, never a successful
assistant response.  A normal final response must have non-empty text; an empty
response or `tool_calls` finish without completed calls fails the run.

## Tools and safety boundary

Tool definitions are generated only from a `ToolRegistry`; unregistered tools
cannot run.  Arguments are model-generated, untrusted input.  This phase uses a
small documented JSON Schema validation boundary (`object`, `required`,
`properties`, `additionalProperties`, and the primitive property types used by
the mock tools), rather than claiming full JSON Schema support.  Invalid input,
unknown tools, error results, and ordinary tool exceptions become sanitized
normalized tool results and let the model take a later step.  Cancellation is
never converted to a tool or run error: `asyncio.CancelledError` propagates.

`EchoTool`, `AddNumbersTool`, and `ScriptedTool` are in-memory development and
testing utilities.  They intentionally have no filesystem, shell, Git,
network, credential, or workspace access.  `ToolRisk` metadata is present for
future approval policy, but does not authorize additional capabilities today.

Read-only repository inspection tools are explicitly bound to one
`WorkspaceContext` when registered. They do not receive a workspace path from
the run context, so model input cannot switch workspace authority. See
[workspace tools](workspace-tools.md) for containment and policy rules.

## Bounds and accounting

`AgentLimits` bounds model steps, calls per model step, total calls, and an
optional whole-run timeout.  Calls beyond a reached bound are not executed.  No
retries, backoff, routing, planning subsystem, parallel execution, or LangGraph
dependency is used: the control flow is deliberately visible in `runner.py`.

Usage is aggregated independently for each `UsageInfo` numeric field across
steps.  In particular, total tokens are summed only from provider-reported
`total_tokens`; cached and reasoning tokens are never added into that total,
because their provider semantics may overlap.
