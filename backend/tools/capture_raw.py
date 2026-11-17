"""Print the raw bytes of control server responses, to confirm message framing.

Usage:
    python tools/capture_raw.py HOST PORT ["COMMAND" ...]

With no commands it sends only read-only requests that move nothing:
    SAMPLE GET_STATUS, SCAN GET_MONO_ENERGY, SAMPLE GET_SAMPLE_STORAGE_SLOTS,
    and one deliberately unknown command to see an ERROR response.
"""

import asyncio
import sys

from robotweb.control import ControlClient

DEFAULT_COMMANDS = [
    "SAMPLE GET_STATUS",
    "SCAN GET_MONO_ENERGY",
    "SAMPLE GET_SAMPLE_STORAGE_SLOTS",
    "SAMPLE NO_SUCH_COMMAND",
]


async def main(host: str, port: int, commands: list[str]) -> None:
    client = ControlClient(host, port, name="capture")
    try:
        for command in commands:
            raw = await client.raw_exchange(command, listen_s=1.5)
            print(f"> {command}")
            print(f"< {len(raw)} bytes: {raw!r}")
            print(f"  hex tail: {raw[-8:].hex(' ')}")
            print()
    finally:
        await client.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), sys.argv[3:] or DEFAULT_COMMANDS))
