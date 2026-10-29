import asyncio
import struct

import pytest

from app.scanning import ClamAVScanner, NoopScanner, ScanError
from tests.helpers import EICAR


async def test_noop_scanner_passes_everything():
    assert (await NoopScanner().scan(EICAR)).clean


@pytest.fixture
async def fake_clamd():
    """A tiny clamd stand-in that speaks INSTREAM and flags the EICAR string."""
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        assert await reader.readuntil(b"\0") == b"zINSTREAM\0"
        data = b""
        while True:
            (length,) = struct.unpack("!L", await reader.readexactly(4))
            if length == 0:
                break
            data += await reader.readexactly(length)
        received.append(data)
        reply = b"stream: Eicar-Test-Signature FOUND\0" if EICAR in data else b"stream: OK\0"
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield ClamAVScanner("127.0.0.1", port, timeout=5), received
    server.close()
    await server.wait_closed()


async def test_clamav_clean(fake_clamd):
    scanner, received = fake_clamd
    data = b"x" * (ClamAVScanner.CHUNK_SIZE * 2 + 10)  # spans several chunks

    result = await scanner.scan(data)

    assert result.clean
    assert received == [data]


async def test_clamav_infected(fake_clamd):
    scanner, _ = fake_clamd
    result = await scanner.scan(EICAR)
    assert not result.clean
    assert result.signature == "Eicar-Test-Signature"


async def test_clamav_unreachable_raises_scan_error():
    # Grab a free port, then close it so nothing is listening there.
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    with pytest.raises(ScanError):
        await ClamAVScanner("127.0.0.1", port, timeout=2).scan(b"data")
