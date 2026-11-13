"""Shared types and base models for the provider-neutral protocol."""

from pydantic import BaseModel, ConfigDict

type JSONPrimitive = None | bool | int | float | str
type JSONValue = JSONPrimitive | list[JSONValue] | dict[str, JSONValue]
type JSONArray = list[JSONValue]
type JSONObject = dict[str, JSONValue]


class ProtocolModel(BaseModel):
    """Strict base model for serializable protocol data.

    Protocol data intentionally accepts only JSON-compatible values, rejects
    undeclared fields, and rejects non-finite floating-point values.
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
