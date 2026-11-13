"""Incremental byte-bounded output retention for subprocess streams."""

from dataclasses import dataclass


@dataclass
class CombinedOutputBudget:
    limit: int
    retained: int = 0
    truncated: bool = False


class BoundedOutput:
    """Retain valid UTF-8 replacement-decoded output without unbounded lines."""

    def __init__(
        self, limit: int, max_line_length: int, combined: CombinedOutputBudget
    ) -> None:
        self._limit = limit
        self._max_line_length = max_line_length
        self._combined = combined
        self._data = bytearray()
        self._line_length = 0
        self.truncated = False

    def feed(self, chunk: bytes) -> None:
        for item in chunk:
            if item == 10:
                self._line_length = 0
            else:
                self._line_length += 1
            if self._line_length > self._max_line_length:
                self.truncated = True
                continue
            if len(self._data) >= self._limit:
                self.truncated = True
                continue
            if self._combined.retained >= self._combined.limit:
                self._combined.truncated = True
                continue
            self._data.append(item)
            self._combined.retained += 1

    @property
    def text(self) -> str:
        return bytes(self._data).decode("utf-8", errors="replace")
