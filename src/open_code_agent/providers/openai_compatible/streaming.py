"""Incremental SSE framing for OpenAI-compatible streaming responses."""

import codecs

from open_code_agent.providers.openai_compatible.errors import (
    OpenAICompatibleStreamError,
)


class SSEEventParser:
    """Frame UTF-8 SSE ``data`` payloads without assuming network chunk boundaries.

    ``max_event_size`` limits a single framed event in decoded UTF-8 bytes to
    prevent unbounded buffering. Comments and unrecognized SSE fields are
    ignored, while multiple data lines are joined with a newline as SSE defines.
    """

    def __init__(self, max_event_size: int = 1_048_576) -> None:
        """Create an incremental parser with a positive per-event size limit."""
        if max_event_size <= 0:
            raise ValueError("max_event_size must be positive.")
        self._max_event_size = max_event_size
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._line_buffer = ""
        self._data_lines: list[str] = []

    def feed(self, chunk: bytes) -> list[str]:
        """Consume arbitrary bytes and return fully framed data payloads."""
        try:
            self._line_buffer += self._decoder.decode(chunk)
        except UnicodeDecodeError as error:
            raise OpenAICompatibleStreamError(
                "Response stream is not valid UTF-8."
            ) from error
        self._ensure_buffer_limit()
        return self._drain_lines()

    def finalize(self) -> list[str]:
        """Flush a final decoder fragment and a final event without a blank line."""
        try:
            self._line_buffer += self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise OpenAICompatibleStreamError(
                "Response stream is not valid UTF-8."
            ) from error
        self._ensure_buffer_limit()
        events = self._drain_lines()
        if self._line_buffer:
            events.extend(self._process_line(self._line_buffer.removesuffix("\r")))
            self._line_buffer = ""
        events.extend(self._emit_event())
        return events

    def _drain_lines(self) -> list[str]:
        events: list[str] = []
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", maxsplit=1)
            events.extend(self._process_line(line.removesuffix("\r")))
        return events

    def _process_line(self, line: str) -> list[str]:
        if not line:
            return self._emit_event()
        if line.startswith(":"):
            return []
        field, separator, value = line.partition(":")
        if field != "data" or not separator:
            return []
        self._data_lines.append(value.removeprefix(" "))
        self._ensure_event_limit()
        return []

    def _emit_event(self) -> list[str]:
        if not self._data_lines:
            return []
        event = "\n".join(self._data_lines)
        self._data_lines.clear()
        return [event]

    def _ensure_buffer_limit(self) -> None:
        if len(self._line_buffer.encode("utf-8")) > self._max_event_size:
            raise OpenAICompatibleStreamError(
                "SSE event exceeds the configured size limit."
            )

    def _ensure_event_limit(self) -> None:
        if len("\n".join(self._data_lines).encode("utf-8")) > self._max_event_size:
            raise OpenAICompatibleStreamError(
                "SSE event exceeds the configured size limit."
            )
