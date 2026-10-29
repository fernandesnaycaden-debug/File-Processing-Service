import asyncio
import struct
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None


class ScanError(Exception):
    """The scanner could not give a verdict (unreachable, size limit, protocol error)."""


class Scanner(Protocol):
    async def scan(self, data: bytes) -> ScanResult: ...


class NoopScanner:
    async def scan(self, data: bytes) -> ScanResult:
        return ScanResult(clean=True)


class ClamAVScanner:
    """Streams bytes to clamd using the INSTREAM command over TCP."""

    CHUNK_SIZE = 64 * 1024

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    async def scan(self, data: bytes) -> ScanResult:
        try:
            return await asyncio.wait_for(self._scan(data), self.timeout)
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            raise ScanError(f"clamd unavailable: {exc!r}") from exc

    async def _scan(self, data: bytes) -> ScanResult:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(b"zINSTREAM\0")
            for i in range(0, len(data), self.CHUNK_SIZE):
                chunk = data[i : i + self.CHUNK_SIZE]
                writer.write(struct.pack("!L", len(chunk)) + chunk)
                await writer.drain()
            writer.write(struct.pack("!L", 0))
            await writer.drain()
            reply = (await reader.readuntil(b"\0")).rstrip(b"\0").decode(errors="replace")
        finally:
            writer.close()
            await writer.wait_closed()

        # Replies look like "stream: OK" or "stream: Eicar-Test-Signature FOUND".
        if reply.endswith(" OK"):
            return ScanResult(clean=True)
        if reply.endswith(" FOUND"):
            signature = reply.removeprefix("stream: ").removesuffix(" FOUND")
            return ScanResult(clean=False, signature=signature)
        raise ScanError(f"Unexpected clamd reply: {reply}")


def get_scanner(settings: Settings) -> Scanner:
    if settings.scanner == "clamav":
        return ClamAVScanner(settings.clamav_host, settings.clamav_port, settings.clamav_timeout)
    return NoopScanner()
