#!/usr/bin/env python3
"""Make a minimal GPT-6 Luna request to verify an OpenAI API key.

Set OPENAI_API_KEY before running this program. It intentionally never prints
the key or sends it anywhere except OpenAI's API endpoint.
"""

import json
import os
import sys

import httpx

MODEL = "gpt-6-luna"
API_URL = "https://api.openai.com/v1/responses"


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set.", file=sys.stderr)
        print("Example: export OPENAI_API_KEY='your_api_key'", file=sys.stderr)
        return 2

    payload = {
        "model": MODEL,
        "input": "Reply with exactly: ok",
        "max_output_tokens": 16,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.post(API_URL, headers=headers, json=payload, timeout=20.0)
    except httpx.RequestError as exc:
        print(f"Could not reach the OpenAI API: {exc}", file=sys.stderr)
        return 3

    if response.is_success:
        response_id = response.json().get("id", "(not supplied)")
        print(f"Success: the API key works with {MODEL}. Response ID: {response_id}")
        return 0

    try:
        error = response.json().get("error", {})
        message = error.get("message", response.text)
        error_type = error.get("type", "unknown_error")
    except (json.JSONDecodeError, AttributeError):
        message = response.text
        error_type = "unknown_error"

    print(
        f"OpenAI API check failed ({response.status_code}, {error_type}): {message}",
        file=sys.stderr,
    )
    if response.status_code == 401:
        print("The key is invalid, revoked, or malformed.", file=sys.stderr)
    elif response.status_code in {403, 404}:
        print(
            f"The key may be valid but does not have access to {MODEL}.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
