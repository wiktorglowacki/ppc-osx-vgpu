#!/usr/bin/env python3
"""A draw's vertex arrays in VRAM are read after the GPU work that wrote them.

DRAW_IMMD_2 carries its vertices in the packet, DRAW_VBUF_2 fetches them
from the 3D_LOAD_VBPNTR arrays, which for MorphOS TinyGL live in VRAM.
Two things follow and are checked here:

  * the array path decodes to the same vertices as the immediate path, so
    the bytes read out of VRAM are the right ones (endian swap included);
  * a GPU draw that writes those bytes is in VRAM before the CPU reads
    them, or the next draw would build from the previous contents (the
    CroMagRally white/green fades).  PPCGPU_READ_SYNC=0 must make that
    check fail, which is what --expect-stale asserts.

    python3 tools/radeon-vertex-read-test.py [qemu-system-ppc] [--expect-stale]
"""

import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time

repo = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "binary", nargs="?", type=Path, default=repo / "qemu/build/qemu-system-ppc"
)
parser.add_argument(
    "--expect-stale",
    action="store_true",
    help="Assert the stale-vertex failure (run with PPCGPU_READ_SYNC=0)",
)
args = parser.parse_args()
binary = args.binary.resolve()

RT = 0x90008000  # 32 rows of 128 bytes: the drawn frame
ARRAY = 0x90010000  # where the vertex array lives, in VRAM
PITCH = 0x00C00020  # RB3D_COLORPITCH0: 128 bytes
BACKGROUND = 0x55
DRAWN = bytes.fromhex("7f4020c0")

with tempfile.TemporaryDirectory(prefix="radeon-vertex-") as temp:
    sock = Path(temp) / "qtest.sock"
    with (Path(temp) / "qemu.log").open("w+") as log:
        env = dict(os.environ)
        env.pop("R300_SYNC", None)
        vm = subprocess.Popen(
            [
                str(binary),
                "-machine",
                "mac99,via=pmu",
                "-cpu",
                "G4",
                "-m",
                "64",
                "-vga",
                "none",
                "-device",
                "ati-radeon-9700,addr=e.0,vgamem_mb=32,renderer=vulkan,romfile=",
                "-bios",
                str(repo / "firmware/radeon/openbios-ppc"),
                "-display",
                "none",
                "-serial",
                "none",
                "-monitor",
                "none",
                "-net",
                "none",
                "-S",
                "-qtest",
                f"unix:{sock},server=on,wait=off",
                "-qtest-log",
                "/dev/null",
            ],
            stdout=log,
            stderr=log,
            env=env,
        )
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(10)
                deadline = time.monotonic() + 10
                while True:
                    try:
                        conn.connect(str(sock))
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if vm.poll() is not None or time.monotonic() > deadline:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        time.sleep(0.05)
                with conn.makefile("rw") as stream:

                    def request(command):
                        stream.write(command + "\n")
                        stream.flush()
                        reply = stream.readline().strip().split()
                        assert reply and reply[0] == "OK", reply
                        return reply[1:]

                    def write(addr, value):
                        request(
                            f"write {addr:#x} 4 0x{value.to_bytes(4, 'little').hex()}"
                        )

                    def blit(addr, data):
                        request(f"write {addr:#x} {len(data)} 0x{data.hex()}")

                    def read(addr, size=4):
                        return bytes.fromhex(request(f"read {addr:#x} {size}")[0][2:])

                    for offset, value in [
                        (0x10, 0x90000000),
                        (0x18, 0xA0000000),
                        (4, 2),
                    ]:
                        write(0xF2800000, 0x80007000 | offset)
                        write(0xF2C00000, value)
                    base = 0xA0000000
                    write(base + 0x148, 0x91FF0000)
                    write(base + 0xB00, 0x100)
                    registers = json.loads(
                        (repo / "tools/fixtures/morphos-ui-registers.json").read_text()
                    )
                    registers.update(
                        {
                            "4E04": "00000000",
                            "4E08": "00000000",
                            "43E4": f"{(1440 + 31) | ((1440 + 31) << 13):08X}",
                        }
                    )
                    for reg, value in registers.items():
                        write(base + int(reg, 16), int(value, 16))
                    blit(0x91000000, b"\xc0" * 32)

                    def state_packet(values):
                        words = []
                        for reg, value in values.items():
                            words.extend((reg // 4, value))  # Type 0, one register
                        return words

                    def submit(words):
                        blit(base + 0x1000, struct.pack(f"<{len(words)}I", *words))

                    def idle():
                        deadline = time.monotonic() + 10
                        while read(base + 0xE40)[3] & 0x80:
                            assert time.monotonic() < deadline, (
                                "GPU did not become idle"
                            )

                    # The captured draw's state, but reading the vertex array
                    # in VRAM as the CPU wrote it (VAP_CNTL_STATUS.VC_SWAP 0:
                    # the fixture's 2 byte-reverses what the driver uploaded).
                    state = {
                        0x4E28: RT,
                        0x4E38: PITCH,
                        0x4480: 0x80000000,
                        0x44C0: 0x0016D000,
                        0x4500: 0x1F,
                        0x4540: 0x91000000,
                        0x2140: 0x100,
                        0x4C00: 0x3C0000,
                        0x4C04: 0x3D0000,
                        0x4C08: 0x3E0000,
                        0x4C0C: 0x3F0000,
                    }
                    quad = [
                        (0, 0, 0, 1, 0, 0, 0, 1),
                        (32, 0, 0, 1, 1, 0, 0, 1),
                        (32, 32, 0, 1, 1, 1, 0, 1),
                        (0, 32, 0, 1, 0, 1, 0, 1),
                    ]

                    def vram_array(vertices):
                        return b"".join(struct.pack("<8f", *v) for v in vertices)

                    immediate = state_packet(state) + [0xC0203500, 0x0004003D]
                    for v in quad:
                        immediate.extend(struct.unpack("<8I", struct.pack("<8f", *v)))
                    blit(RT, bytes((BACKGROUND,)) * 4096)
                    submit(immediate)
                    idle()
                    reference = read(RT, 4096)
                    # UNORM conversion can round a channel by one, as in radeon-color-test.
                    for offset in (0x840, 0):
                        pixel = reference[offset:offset + 4]
                        assert len(pixel) == 4 and all(abs(a - b) <= 1 for a, b in zip(pixel, DRAWN)), (
                            f"immediate draw at {offset:#x}: {pixel.hex()}, expected {DRAWN.hex()}"
                        )

                    blit(RT, bytes((BACKGROUND,)) * 4096)
                    blit(ARRAY, vram_array(quad))
                    write(base + 0x20C0, 1)  # one array
                    write(base + 0x20C4, 0x0808)  # 8 dwords, stride 8
                    write(base + 0x20C8, ARRAY)
                    submit(state_packet(state) + [0xC0003400, 0x0004003D])
                    idle()
                    fetched = read(RT, 4096)
                    if fetched != reference:
                        varied = [
                            i
                            for i in range(0, 4096, 4)
                            if fetched[i : i + 4] != reference[i : i + 4]
                        ]
                        raise AssertionError(
                            f"array draw differs from the immediate draw at "
                            f"{len(varied)} dwords, first {varied[:4]}"
                        )

                    # A GPU draw into the array's first vertex must reach VRAM
                    # before the next draw reads it: give vertex 0 the fragment
                    # colour as all four coordinates, which puts the quad's
                    # corner on (1,1) and leaves pixel 0,0 alone.
                    producer = state_packet(
                        {
                            0x4E28: ARRAY,
                            0x4E38: PITCH,
                            0x4480: 0x80000000,
                            0x44C0: 0x0016D000,
                            0x4500: 0x1F,
                            0x4540: 0x91000000,
                            0x2140: 0x100,
                            0x4E04: 0,
                            0x4E08: 0,
                            0x4C00: 0,
                            0x4C04: 0,
                            0x4C08: 0,
                            0x4C0C: 0x3F000000,
                        }
                    )
                    producer += [0xC0203500, 0x0004003D]
                    for v in [
                        (0, 0, 0, 1, 0, 0, 0, 1),
                        (4, 0, 0, 1, 0, 0, 0, 1),
                        (4, 1, 0, 1, 0, 0, 0, 1),
                        (0, 1, 0, 1, 0, 0, 0, 1),
                    ]:
                        producer.extend(struct.unpack("<8I", struct.pack("<8f", *v)))
                    blit(RT, bytes((BACKGROUND,)) * 4096)
                    blit(ARRAY, vram_array(quad))
                    write(base + 0x20C8, ARRAY)
                    submit(producer + state_packet(state) + [0xC0003400, 0x0004003D])
                    idle()
                    first = read(RT, 4)
                    vertex0 = read(ARRAY, 8)
                    stale = first != bytes((BACKGROUND,)) * 4
                    if args.expect_stale:
                        assert stale, (
                            "PPCGPU_READ_SYNC=0: expected the draw to build "
                            "from the vertices the GPU was still to write"
                        )
                        print(
                            "PASS: without the read synchronization the draw reads the "
                            "previous vertex data (pixel 0,0 drawn), as it must"
                        )
                    else:
                        assert not stale, (
                            f"vertex array read before the GPU wrote it: pixel 0,0 is "
                            f"{first.hex()}, expected the background; vertex 0 {vertex0.hex()}"
                        )
                        print(
                            "PASS: DRAW_VBUF_2 from a VRAM array renders exactly like "
                            "DRAW_IMMD_2, and a GPU write to that array is in VRAM "
                            "before the next draw reads it"
                        )
        finally:
            vm.terminate()
            try:
                vm.wait(timeout=10)
            except subprocess.TimeoutExpired:
                vm.kill()
                vm.wait(timeout=5)
