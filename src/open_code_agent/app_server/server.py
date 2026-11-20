"""Application composition and JSON-RPC method dispatch for one connection."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from open_code_agent import __version__
from open_code_agent.agent import (
    AgentLimits,
    AgentMode,
    AgentRunRequest,
    AgentSession,
    build_agent_session,
)
from open_code_agent.agent.events import RunCompleted
from open_code_agent.agent.prompt import coding_agent_system_prompt
from open_code_agent.app_server.approvals import RpcApprovalHandler
from open_code_agent.app_server.exceptions import (
    ALREADY_INITIALIZED,
    INCOMPATIBLE_PROTOCOL,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    INVALID_WORKSPACE,
    METHOD_NOT_FOUND,
    NOT_INITIALIZED,
    PARSE_ERROR,
    PROFILE_NOT_FOUND,
    PROVIDER_FAILURE,
    RESOURCE_LIMIT,
    RUN_NOT_FOUND,
    SERVER_SHUTTING_DOWN,
    SESSION_BUSY,
    SESSION_NOT_FOUND,
    AppServerError,
)
from open_code_agent.app_server.limits import AppServerLimits
from open_code_agent.app_server.messages import (
    ErrorResponse,
    IncomingResponse,
    Notification,
    Request,
    RpcError,
    parse_incoming,
)
from open_code_agent.app_server.serialization import event_payload, safe_profile
from open_code_agent.app_server.sessions import (
    ActiveRun,
    SessionRecord,
    SessionRegistry,
    now,
)
from open_code_agent.app_server.transport import AsyncLineTransport, TransportError
from open_code_agent.execution import CommandExecutionPolicy
from open_code_agent.persistence import ProfileNotFoundError, ProviderProfileStore
from open_code_agent.providers import (
    ManagedModelProvider,
    OpenAICompatibleProfile,
    provider_for_profile,
)
from open_code_agent.workspace import (
    InvalidWorkspaceError,
    WorkspaceContext,
    WorkspacePolicy,
)

PROTOCOL_VERSION = "1.0"
SERVER_NAME = "open-code-agent"
_STATEFUL_METHODS = frozenset(
    {
        "profiles/list",
        "profiles/get",
        "profiles/test",
        "models/list",
        "session/create",
        "session/status",
        "session/clear",
        "session/close",
        "session/list",
        "turn/start",
        "turn/cancel",
        "shutdown",
    }
)

SessionBuilder = Callable[..., AgentSession]
ProviderBuilder = Callable[[OpenAICompatibleProfile], ManagedModelProvider]


class AppServer:
    """A bounded server instance for one IDE/editor stdio connection."""

    def __init__(
        self,
        transport: AsyncLineTransport,
        *,
        profile_store: ProviderProfileStore,
        limits: AppServerLimits | None = None,
        session_builder: SessionBuilder = build_agent_session,
        provider_builder: ProviderBuilder = provider_for_profile,
    ) -> None:
        self._transport = transport
        self._profile_store = profile_store
        self._limits = (limits or AppServerLimits()).model_copy(deep=True)
        self._session_builder = session_builder
        self._provider_builder = provider_builder
        self._sessions = SessionRegistry(self._limits.max_sessions)
        self._initialized = False
        self._shutting_down = False
        self._exited = False
        self._run_slots = asyncio.Semaphore(self._limits.max_concurrent_runs)
        self._pending: dict[str, asyncio.Future[dict[str, Any] | None]] = {}
        self._request_counter = 0

    async def serve(self) -> None:
        """Handle one connection until exit notification or EOF, then clean up."""
        try:
            while not self._exited:
                try:
                    raw = await self._transport.read_message()
                except TransportError as error:
                    await self._send_error(None, PARSE_ERROR, str(error))
                    continue
                if raw is None:
                    break
                try:
                    incoming = parse_incoming(raw)
                except ValidationError:
                    await self._send_error(
                        self._unsafe_id(raw),
                        INVALID_REQUEST,
                        "Invalid JSON-RPC request.",
                    )
                    continue
                if isinstance(incoming, IncomingResponse):
                    self._receive_response(incoming)
                    continue
                if isinstance(incoming, Notification):
                    await self._handle_notification(incoming)
                    continue
                await self._handle_request(incoming)
        finally:
            self._exited = True
            await self._cleanup()

    async def _handle_request(self, request: Request) -> None:
        try:
            result = await self._dispatch(request.method, request.params or {})
        except AppServerError as error:
            await self._send_error(request.id, error.code, error.message)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._send_error(request.id, INTERNAL_ERROR, "Internal server error.")
        else:
            await self._transport.send(
                {"jsonrpc": "2.0", "id": request.id, "result": result}
            )

    async def _handle_notification(self, notification: Notification) -> None:
        if notification.method == "exit":
            self._exited = True
            return
        # initialized is optional; other unknown notifications are ignored.

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return await self._initialize(params)
        if method in _STATEFUL_METHODS and not self._initialized:
            raise AppServerError(NOT_INITIALIZED, "Server has not been initialized.")
        if self._shutting_down and method != "shutdown":
            raise AppServerError(SERVER_SHUTTING_DOWN, "Server is shutting down.")
        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
            "profiles/list": self._profiles_list,
            "profiles/get": self._profiles_get,
            "profiles/test": self._profiles_test,
            "models/list": self._models_list,
            "session/create": self._session_create,
            "session/status": self._session_status,
            "session/clear": self._session_clear,
            "session/close": self._session_close,
            "session/list": self._session_list,
            "turn/start": self._turn_start,
            "turn/cancel": self._turn_cancel,
            "shutdown": self._shutdown,
        }
        try:
            return await handlers[method](params)
        except KeyError as error:
            raise AppServerError(METHOD_NOT_FOUND, "Method not found.") from error

    async def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._initialized:
            raise AppServerError(ALREADY_INITIALIZED, "Server is already initialized.")
        name = params.get("client_name")
        version = params.get("client_version")
        versions = params.get("supported_protocol_versions")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(version, str)
        ):
            raise AppServerError(INVALID_PARAMS, "Invalid initialize parameters.")
        if not isinstance(versions, list) or not all(
            isinstance(item, str) for item in versions
        ):
            raise AppServerError(INVALID_PARAMS, "Invalid initialize parameters.")
        if PROTOCOL_VERSION not in versions:
            raise AppServerError(
                INCOMPATIBLE_PROTOCOL, "No mutually supported protocol version."
            )
        self._initialized = True
        return {
            "server_name": SERVER_NAME,
            "server_version": __version__,
            "selected_protocol_version": PROTOCOL_VERSION,
            "capabilities": {
                "profiles": True,
                "sessions": True,
                "streaming_events": True,
                "bidirectional_approvals": True,
                "cancellation": True,
                "conversation_persistence": False,
                "mcp": False,
                "pty": False,
            },
            "limits": self._limits.model_dump(mode="json"),
            "supported_methods": sorted(_STATEFUL_METHODS | {"initialize"}),
        }

    async def _profiles_list(self, _: dict[str, Any]) -> dict[str, Any]:
        try:
            default = self._profile_store.default_profile_id()
            profiles = self._profile_store.list()
        except Exception as error:
            raise AppServerError(
                PROVIDER_FAILURE, "Profile store could not be read."
            ) from error
        return {
            "profiles": [
                safe_profile(profile, is_default=profile.profile_id == default)
                for profile in profiles
            ]
        }

    def _profile_param(
        self, params: dict[str, Any], *, optional: bool = False
    ) -> OpenAICompatibleProfile:
        profile_id = params.get("profile_id")
        if profile_id is None and optional:
            profile_id = self._profile_store.default_profile_id()
        if not isinstance(profile_id, str) or not profile_id:
            raise AppServerError(INVALID_PARAMS, "A profile_id is required.")
        try:
            return self._profile_store.get(profile_id)
        except ProfileNotFoundError as error:
            raise AppServerError(
                PROFILE_NOT_FOUND, "Provider profile was not found."
            ) from error

    async def _profiles_get(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = self._profile_param(params)
        return {
            "profile": safe_profile(
                profile,
                is_default=profile.profile_id
                == self._profile_store.default_profile_id(),
            )
        }

    async def _profiles_test(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = self._profile_param(params)
        provider = self._provider_builder(profile)
        try:
            result = await provider.validate_configuration()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise AppServerError(PROVIDER_FAILURE, "Provider test failed.") from error
        finally:
            await provider.aclose()
        return {
            "valid": result.valid,
            "diagnostic": result.diagnostic if not result.valid else None,
        }

    async def _models_list(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = self._profile_param(params, optional=True)
        provider = self._provider_builder(profile)
        try:
            models = await provider.list_models()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise AppServerError(PROVIDER_FAILURE, "Model listing failed.") from error
        finally:
            await provider.aclose()
        return {
            "models": [
                {"id": model.id, "display_name": model.display_name}
                for model in sorted(models, key=lambda model: model.id)
            ]
        }

    async def _session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = self._profile_param(params, optional=True)
        workspace_path = params.get("workspace_path", params.get("workspace"))
        if not isinstance(workspace_path, str) or not workspace_path:
            raise AppServerError(INVALID_PARAMS, "workspace_path is required.")
        mode_value = params.get("mode", "ask")
        mode_map = {
            "plan": AgentMode.PLAN,
            "ask": AgentMode.ASK,
            "auto_edit": AgentMode.AUTO_EDIT,
            "auto-edit": AgentMode.AUTO_EDIT,
        }
        mode = mode_map.get(mode_value) if isinstance(mode_value, str) else None
        if mode is None:
            raise AppServerError(
                INVALID_PARAMS, "mode must be plan, ask, or auto_edit."
            )
        model = params.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise AppServerError(INVALID_PARAMS, "model must be a non-blank string.")
        try:
            workspace = WorkspaceContext(
                root=Path(workspace_path), policy=self._workspace_policy(params)
            )
        except (InvalidWorkspaceError, ValueError, OSError) as error:
            raise AppServerError(INVALID_WORKSPACE, "Workspace is invalid.") from error
        try:
            limits = AgentLimits.model_validate(params.get("agent_limits", {}))
        except ValidationError as error:
            raise AppServerError(INVALID_PARAMS, "agent_limits are invalid.") from error
        context: dict[str, str] = {}
        approvals = RpcApprovalHandler(
            self._approval_request, lambda: (context["session_id"], context["run_id"])
        )
        try:
            session = self._session_builder(
                profile=profile,
                workspace=workspace,
                mode=mode,
                model=model,
                limits=limits,
                command_policy=CommandExecutionPolicy(execution_enabled=True),
                command_approvals=approvals,
                patch_approvals=approvals,
            )
            record = await self._sessions.add(session)
        except RuntimeError as error:
            raise AppServerError(RESOURCE_LIMIT, "Session limit reached.") from error
        # The closure is per-session and only one run can be active for it.
        record.approval_context = context
        return self._session_summary(record, detail=True)

    def _workspace_policy(self, params: dict[str, Any]) -> WorkspacePolicy:
        overrides = params.get("workspace_policy")
        if overrides is None:
            return WorkspacePolicy()
        if not isinstance(overrides, dict):
            raise AppServerError(INVALID_PARAMS, "workspace_policy must be an object.")
        allowed = {
            "max_file_size_bytes",
            "max_read_lines",
            "max_list_results",
            "max_search_results",
            "max_output_bytes",
        }
        if set(overrides).difference(allowed):
            raise AppServerError(
                INVALID_PARAMS, "Workspace policy override is not allowed."
            )
        base = WorkspacePolicy()
        for key, value in overrides.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > getattr(base, key)
            ):
                raise AppServerError(
                    INVALID_PARAMS, "Workspace policy override is invalid."
                )
        return WorkspacePolicy.model_validate({**base.model_dump(), **overrides})

    def _session_summary(
        self, record: SessionRecord, *, detail: bool = False
    ) -> dict[str, Any]:
        session = record.session
        data: dict[str, Any] = {
            "session_id": record.session_id,
            "mode": session.mode.value,
            "model": session.model,
            "profile": safe_profile(session.profile),
            "workspace_display_name": session.workspace.name
            or session.workspace.root.name,
            "turn_count": record.turn_count,
            "history_message_count": len(record.history),
            "active_run": record.active.run_id if record.active is not None else None,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        if detail:
            data["workspace_identifier"] = session.workspace.root.name
            data["available_tools"] = [tool.name for tool in session.tools.list_tools()]
            data["effective_limits"] = session.limits.model_dump(mode="json")
        return data

    async def _require_session(self, params: dict[str, Any]) -> SessionRecord:
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise AppServerError(INVALID_PARAMS, "session_id is required.")
        record = await self._sessions.get(session_id)
        if record is None:
            raise AppServerError(SESSION_NOT_FOUND, "Session was not found.")
        return record

    async def _session_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session_summary(await self._require_session(params))

    async def _session_list(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "sessions": [
                self._session_summary(record) for record in await self._sessions.list()
            ]
        }

    async def _session_clear(self, params: dict[str, Any]) -> dict[str, Any]:
        record = await self._require_session(params)
        if record.active is not None:
            raise AppServerError(SESSION_BUSY, "Session has an active run.")
        record.history.clear()
        record.turn_count = 0
        record.updated_at = now()
        return self._session_summary(record)

    async def _session_close(self, params: dict[str, Any]) -> dict[str, Any]:
        record = await self._require_session(params)
        removed = await self._sessions.remove(record.session_id)
        assert removed is not None
        if removed.active is not None:
            removed.active.task.cancel()
            await asyncio.gather(removed.active.task, return_exceptions=True)
        await removed.session.aclose()
        return {"session_id": record.session_id, "closed": True}

    async def _turn_start(self, params: dict[str, Any]) -> dict[str, Any]:
        record = await self._require_session(params)
        text = params.get("text", params.get("user_text"))
        if not isinstance(text, str) or not text.strip():
            raise AppServerError(INVALID_PARAMS, "user text must not be blank.")
        if record.active is not None:
            raise AppServerError(SESSION_BUSY, "Session already has an active run.")
        import secrets

        run_id = secrets.token_urlsafe(18)
        context = record.approval_context
        context.update({"session_id": record.session_id, "run_id": run_id})
        task = asyncio.create_task(
            self._run_turn(record, run_id, text), name=f"oca-run-{run_id}"
        )
        record.active = ActiveRun(run_id, task)
        record.updated_at = now()
        return {"run_id": run_id, "accepted": True}

    async def _turn_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        record = await self._require_session(params)
        run_id = params.get("run_id")
        if (
            not isinstance(run_id, str)
            or record.active is None
            or record.active.run_id != run_id
        ):
            raise AppServerError(RUN_NOT_FOUND, "Run was not found.")
        record.active.task.cancel()
        return {"run_id": run_id, "cancel_requested": True}

    async def _run_turn(self, record: SessionRecord, run_id: str, text: str) -> None:
        terminal_sent = False
        try:
            async with self._run_slots:
                request = AgentRunRequest(
                    model=record.session.model,
                    initial_messages=record.history,
                    task=text,
                    system_prompt=None
                    if record.history
                    else coding_agent_system_prompt(record.session.mode),
                    limits=record.session.limits,
                )
                async for event in record.session.runner.stream(request):
                    mapped = event_payload(event, self._limits.max_event_payload_bytes)
                    if mapped is None:
                        continue
                    event_type, payload = mapped
                    await self._run_event(record, run_id, event_type, payload)
                    if event_type in {
                        "run_completed",
                        "run_failed",
                        "run_limit_reached",
                    }:
                        terminal_sent = True
                    if isinstance(event, RunCompleted):
                        record.history = [
                            message.model_copy(deep=True)
                            for message in event.result.messages
                        ]
                        record.turn_count += 1
        except asyncio.CancelledError:
            if not terminal_sent:
                with contextlib.suppress(TransportError):
                    await self._run_event(record, run_id, "run_cancelled", {})
            raise
        except Exception:
            if not terminal_sent:
                with contextlib.suppress(TransportError):
                    await self._run_event(
                        record, run_id, "run_failed", {"message": "Agent run failed."}
                    )
        finally:
            if record.active is not None and record.active.run_id == run_id:
                record.active = None
            record.updated_at = now()

    async def _event(
        self,
        session_id: str,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self._transport.send(
            {
                "jsonrpc": "2.0",
                "method": "agent/event",
                "params": {
                    "session_id": session_id,
                    "run_id": run_id,
                    "sequence": sequence,
                    "type": event_type,
                    "payload": payload,
                    "timestamp": now(),
                },
            }
        )

    async def _run_event(
        self,
        record: SessionRecord,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        active = record.active
        if active is None or active.run_id != run_id:
            return
        active.sequence += 1
        await self._event(
            record.session_id, run_id, active.sequence, event_type, payload
        )

    async def _approval_request(self, params: dict[str, Any]) -> dict[str, Any] | None:
        if (
            self._shutting_down
            or len(self._pending) >= self._limits.max_pending_server_requests
        ):
            return None
        self._request_counter += 1
        request_id = f"server:approval:{self._request_counter}"
        params = {"approval_id": request_id, **params}
        record = await self._sessions.get(cast(str, params["session_id"]))
        if record is not None:
            await self._run_event(
                record,
                cast(str, params["run_id"]),
                "approval_requested",
                {"proposal_type": params.get("proposal_type")},
            )
        future: asyncio.Future[dict[str, Any] | None] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        try:
            await self._transport.send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "approval/request",
                    "params": params,
                }
            )
            result = await asyncio.wait_for(
                future, timeout=self._limits.approval_timeout_seconds
            )
            if not isinstance(result, dict) or result.get("approval_id") != request_id:
                return None
            await self._approval_observed(params, result)
            return result
        except asyncio.CancelledError:
            raise
        except (TimeoutError, TransportError):
            return None
        finally:
            self._pending.pop(request_id, None)

    async def _approval_observed(
        self, requested: dict[str, Any], result: dict[str, Any]
    ) -> None:
        # Event correlation is already contained in this request's immutable params.
        record = await self._sessions.get(cast(str, requested["session_id"]))
        if record is not None:
            await self._run_event(
                record,
                cast(str, requested["run_id"]),
                "approval_decision_observed",
                {"decision": result.get("decision")},
            )

    def _receive_response(self, response: IncomingResponse) -> None:
        if not isinstance(response.id, str):
            return
        future = self._pending.get(response.id)
        if future is None or future.done():
            return
        if response.error is not None or not isinstance(response.result, dict):
            future.set_result(None)
        else:
            future.set_result(cast(dict[str, Any], response.result))

    async def _shutdown(self, _: dict[str, Any]) -> dict[str, Any]:
        self._shutting_down = True
        await self._cleanup()
        return {"shutdown": True}

    async def _cleanup(self) -> None:
        self._shutting_down = True
        for future in self._pending.values():
            if not future.done():
                future.set_result(None)
        self._pending.clear()
        try:
            async with asyncio.timeout(self._limits.shutdown_timeout_seconds):
                await self._sessions.close_all()
        except TimeoutError:
            pass

    async def _send_error(
        self, identifier: str | int | None, code: int, message: str
    ) -> None:
        await self._transport.send(
            ErrorResponse(
                jsonrpc="2.0", id=identifier, error=RpcError(code=code, message=message)
            ).model_dump(mode="json", exclude_none=True)
        )

    @staticmethod
    def _unsafe_id(raw: object) -> str | int | None:
        if isinstance(raw, dict):
            value = raw.get("id")
            if isinstance(value, str) or (
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
            ):
                return value
        return None
