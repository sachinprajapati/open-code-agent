import pytest
from pydantic import ValidationError

from open_code_agent.app_server.messages import (
    ErrorResponse,
    IncomingResponse,
    Notification,
    Request,
    SuccessResponse,
    parse_incoming,
)


def test_valid_envelopes() -> None:
    request = Request(jsonrpc="2.0", id=1, method="initialize", params={})
    notification = Notification(jsonrpc="2.0", method="exit")
    success = SuccessResponse(jsonrpc="2.0", id="server:1", result=None)
    error = ErrorResponse.model_validate(
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "bad"}}
    )

    assert request.id == 1
    assert notification.method == "exit"
    assert success.result is None
    assert error.error.code == -32600


@pytest.mark.parametrize("identifier", [True, -1])
def test_invalid_ids_are_rejected(identifier: object) -> None:
    with pytest.raises(ValidationError):
        Request.model_validate({"jsonrpc": "2.0", "id": identifier, "method": "x"})


def test_response_with_null_result_is_valid() -> None:
    response = parse_incoming({"jsonrpc": "2.0", "id": "server:1", "result": None})
    assert isinstance(response, IncomingResponse)
    assert response.result is None


def test_response_with_both_result_and_error_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_incoming(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {},
                "error": {"code": 1, "message": "bad"},
            }
        )
