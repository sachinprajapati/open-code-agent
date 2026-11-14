"""Strict JSON-RPC 2.0 envelope models and inbound classification."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from open_code_agent.protocol.common import JSONObject, JSONValue, ProtocolModel

JSONRPCId = str | int


class _Envelope(ProtocolModel):
    jsonrpc: Literal["2.0"]


class Request(_Envelope):
    id: JSONRPCId
    method: str = Field(min_length=1)
    params: JSONObject | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: JSONRPCId) -> JSONRPCId:
        if isinstance(value, bool) or (isinstance(value, int) and value < 0):
            raise ValueError("Request ID must be a string or non-negative integer.")
        return value

    @field_validator("method")
    @classmethod
    def non_blank_method(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Method must not be blank.")
        return value


class Notification(_Envelope):
    method: str = Field(min_length=1)
    params: JSONObject | None = None

    @field_validator("method")
    @classmethod
    def non_blank_method(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Method must not be blank.")
        return value


class RpcError(ProtocolModel):
    code: int
    message: str = Field(min_length=1)
    data: JSONObject | None = None


class SuccessResponse(_Envelope):
    id: JSONRPCId | None
    result: JSONValue

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: JSONRPCId | None) -> JSONRPCId | None:
        if isinstance(value, bool) or (isinstance(value, int) and value < 0):
            raise ValueError("Response ID must be a string or non-negative integer.")
        return value


class ErrorResponse(_Envelope):
    id: JSONRPCId | None
    error: RpcError

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: JSONRPCId | None) -> JSONRPCId | None:
        if isinstance(value, bool) or (isinstance(value, int) and value < 0):
            raise ValueError("Response ID must be a string or non-negative integer.")
        return value


class IncomingResponse(_Envelope):
    id: JSONRPCId | None
    result: JSONValue | None = None
    error: RpcError | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: JSONRPCId | None) -> JSONRPCId | None:
        if isinstance(value, bool) or (isinstance(value, int) and value < 0):
            raise ValueError("Response ID must be a string or non-negative integer.")
        return value

    @model_validator(mode="before")
    @classmethod
    def exactly_one_result(cls, value: object) -> object:
        if not isinstance(value, dict) or (("result" in value) == ("error" in value)):
            raise ValueError("Response must contain exactly one of result or error.")
        return value


IncomingMessage = Request | Notification | IncomingResponse


def parse_incoming(value: object) -> IncomingMessage:
    """Classify a decoded object without treating responses as requests."""
    if not isinstance(value, dict):
        raise ValueError("JSON-RPC messages must be objects.")
    if "method" in value:
        return (
            Request.model_validate(value)
            if "id" in value
            else Notification.model_validate(value)
        )
    if ("result" in value) == ("error" in value):
        raise ValueError("Response must contain exactly one of result or error.")
    return IncomingResponse.model_validate(value)
