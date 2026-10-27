from typing import Callable, Protocol

class AssetMissing(FileNotFoundError):
    """The remote asset is absent, a normal optional result."""
    pass

class Response(Protocol):
    """The transport contract supplies byte chunks and an owned handle."""

    def read(self, size: int) -> bytes:
        """Return the next chunk; empty bytes denotes end of stream."""
        ...

    def close(self) -> None:
        """Release the response once; transport errors may propagate."""
        ...

Opener = Callable[[], Response]
