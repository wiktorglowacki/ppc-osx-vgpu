#!/usr/bin/env python3
"""Verify native GPU offscreen color/endian roundtrips without intermediate readback."""
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
parser.add_argument('binary', nargs='?', type=Path, default=repo / 'qemu/build/qemu-system-ppc')
parser.add_argument('--morphos-compat', action='store_true',
                    help='Check the opt-in MorphOS local-color compatibility convention')
args = parser.parse_args()
binary = args.binary.resolve()
device = 'ati-radeon-9700,addr=e.0,vgamem_mb=32,renderer=vulkan,romfile='
if args.morphos_compat:
    device += ',morphos-color-compat=on'
with tempfile.TemporaryDirectory(prefix='radeon-color-') as temp:
    sock = Path(temp) / 'qtest.sock'
    with (Path(temp) / 'qemu.log').open('w+') as log:
        env = dict(os.environ)
        env.pop('R300_SYNC', None)
        vm = subprocess.Popen([
            str(binary), '-machine', 'mac99,via=pmu', '-cpu', 'G4', '-m', '64',
            '-vga', 'none', '-device', device,
            '-bios', str(repo / 'firmware/radeon/openbios-ppc'),
            '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none', '-S',
            '-qtest', f'unix:{sock},server=on,wait=off', '-qtest-log', '/dev/null',
        ], stdout=log, stderr=log, env=env)
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
                        time.sleep(.05)
                with conn.makefile('rw') as stream:
                    def request(command):
                        stream.write(command + '\n'); stream.flush()
                        reply = stream.readline().strip().split()
                        assert reply and reply[0] == 'OK', reply
                        return reply[1:]

                    def write(addr, value):
                        request(f'write {addr:#x} 4 0x{value.to_bytes(4, "little").hex()}')

                    def read(addr):
                        return int.from_bytes(bytes.fromhex(request(f'read {addr:#x} 4')[0][2:]), 'little')

                    for offset, value in [(0x10, 0x90000000), (0x18, 0xa0000000), (4, 2)]:
                        write(0xf2800000, 0x80007000 | offset)
                        write(0xf2c00000, value)
                    base = 0xa0000000
                    write(base + 0x148, 0x91ff0000)
                    write(base + 0xb00, 0x100)
                    registers = json.loads((repo / 'tools/fixtures/morphos-ui-registers.json').read_text())
                    registers.update({'4E04': '00000000', '4E08': '00000000',
                                      '43E4': f'{(1440 + 31) | ((1440 + 31) << 13):08X}'})
                    for reg, value in registers.items():
                        write(base + int(reg, 16), int(value, 16))
                    request('write 0x91000000 32 0x' + 'c0' * 32)

                    def state_packet(values):
                        words = []
                        for reg, value in values.items():
                            words.extend((reg // 4, value))  # Type 0, one register
                        return words

                    draw = [0xc0203500, 0x0004003d]
                    for vertex in [(0, 0, 0, 1, 0, 0, 0, 1), (32, 0, 0, 1, 1, 0, 0, 1),
                                   (32, 32, 0, 1, 1, 1, 0, 1), (0, 32, 0, 1, 0, 1, 0, 1)]:
                        draw.extend(struct.unpack('<8I', struct.pack('<8f', *vertex)))

                    def submit(words):
                        # The FIFO aperture ends at 0x1400; restart there without idling.
                        for start in range(0, len(words), 256):
                            chunk = words[start:start + 256]
                            packet = struct.pack(f'<{len(chunk)}I', *chunk).hex()
                            request(f'write {base + 0x1000:#x} {len(chunk) * 4} 0x{packet}')

                    def idle():
                        deadline = time.monotonic() + 10
                        while read(base + 0xe40) & (1 << 31):
                            assert time.monotonic() < deadline, 'GPU did not become idle'

                    for endian in (0, 2):
                        # First draw renders distinct RGB and alpha through the captured glyph shader.
                        producer = state_packet({0x4E28: 0x90008000, 0x4E38: 0x00c00020 | endian << 19,
                                                 0x4480: 0x80000000, 0x44C0: 0x0016d000,
                                                 0x4500: 0x1f, 0x4540: 0x91000000,
                                                 0x4C00: 0x3c0000, 0x4C04: 0x3d0000,
                                                 0x4C08: 0x3e0000}) + draw
                        # Immediately sample that offscreen target. Swapped native textures require
                        # CPU conversion, so pending GPU writes must complete before texture upload.
                        consumer = state_packet({0x4E28: 0x90000000, 0x4E38: 0x00c00020,
                                                 0x4480: 0x80000000 | 31 | 31 << 11,
                                                 0x44C0: 0x0000a60c, 0x4500: 0x1f,
                                                 0x4540: 0x90008000 | (0 if args.morphos_compat else endian),
                                                 0x4C00: 0x3f0000, 0x4C04: 0x3f0000,
                                                 0x4C08: 0x3f0000}) + draw
                        # Warm both pipelines, then replay in one FIFO write with no intervening
                        # status/aperture read that could accidentally synchronize the producer.
                        submit(producer + consumer); idle()
                        request('write 0x90000000 4096 0x' + '55' * 4096)
                        request('write 0x90008000 4096 0x' + 'aa' * 4096)
                        submit(producer + consumer); idle()
                        pixel = bytes.fromhex(request('read 0x90000840 4')[0][2:])
                        expected = bytes((128, 64, 32, 192))
                        raw = bytes.fromhex(request('read 0x90008840 4')[0][2:])
                        stored = expected[::-1] if endian == 2 and not args.morphos_compat else expected
                        assert all(abs(a - b) <= 1 for a, b in zip(raw, stored)), (
                            f'endian {endian}: producer bytes {raw.hex()}, expected {stored.hex()}')
                        assert all(abs(a - b) <= 1 for a, b in zip(pixel, expected)), (
                            f'endian {endian}: offscreen RGB/alpha roundtrip {pixel.hex()}, '
                            f'expected {expected.hex()}')
                    if not args.morphos_compat:
                        # Switch shader/unit/format without idling, then replay cache hits.
                        # The sparse shader samples unit 3 to obtain unit 15's coordinates.
                        cases = [(0, 'sampled'), (15, 'sampled'), (3, 'sampled'),
                                 (15, 'raw'), (15, 'unbound'), (15, 'sampled'),
                                 (15, 'sparse'), (0, 'sampled')]
                        color = bytes((128, 64, 32, 192))
                        request('write 0x90008000 4096 0x' + (color * 1024).hex())
                        request('write 0x90010000 4096 0x' + '00' * 4096)
                        sequence = []
                        for index, (unit, kind) in enumerate(cases):
                            enabled = 0 if kind == 'unbound' else (1 << unit)
                            if kind == 'sparse':
                                enabled |= 1 << 3
                            values = {
                                0x4E28: 0x90020000 + index * 4096, 0x4E38: 0x00c00020,
                                0x4104: enabled, 0x461C: 0x00420000 if kind == 'sparse' else 0x00400000,
                                0x4620: 0x8040 | ((3 if kind == 'sparse' else unit) << 11),
                                0x4624: 0x8041 | (15 << 11),
                                0x4C00: 0x3f0000, 0x4C04: 0x3f0000, 0x4C08: 0x3f0000,
                            }
                            for active in {unit, 3}:
                                values.update({
                                    0x4400 + 4 * active: int(registers['4400'], 16),
                                    0x4440 + 4 * active: int(registers.get('4440', '0'), 16),
                                    0x4480 + 4 * active: 0x80000000 | 31 | (31 << 11),
                                    0x44C0 + 4 * active: 0x0000a61b if kind == 'raw' else 0x0000a60c,
                                    0x4500 + 4 * active: 31,
                                    0x4540 + 4 * active: 0x90010000 if kind == 'raw' else 0x90008000,
                                })
                            sequence.extend(state_packet(values) + draw)
                        for replay in range(2):
                            request('write 0x90020000 32768 0x' + '55' * 32768)
                            submit(sequence); idle()
                            for index, (unit, kind) in enumerate(cases):
                                pixel = bytes.fromhex(request(f'read {0x90020840 + index * 4096:#x} 4')[0][2:])
                                expected = bytes((0, 0, 0, 255)) if kind in ('raw', 'unbound') else color
                                assert all(abs(a - b) <= 1 for a, b in zip(pixel, expected)), (
                                    f'descriptor shader switch {replay=} {unit=} {kind=}: '
                                    f'{pixel.hex()}, expected {expected.hex()}')
                        print('PASS: sparse/high-unit, sampled/RAW/unbound sampler shader switches '
                              'and cached replays preserve pixels')
                    if args.morphos_compat:
                        # The captured font uses US_OUT_FMT=0x1b00, constant black,
                        # COLOR_ENDIAN=2, then a byte-preserving 2D copy to scanout.
                        gray = bytes((128, 128, 128, 255))
                        black = state_packet({0x4E28: 0x90008000, 0x4E38: 0x00d00020,
                                              0x4480: 0x80000000, 0x44C0: 0x0016d000,
                                              0x4500: 0x1f, 0x4540: 0x91000000,
                                              0x46A4: 0x1b00, 0x4C00: 0, 0x4C04: 0,
                                              0x4C08: 0, 0x4C0C: 0x3f0000}) + draw
                        for mask, blend, expected in ((255, False, bytes((0, 0, 0, 255))),
                                                      (128, True, bytes((64, 64, 64, 255)))):
                            request('write 0x90000000 4096 0x' + (gray * 1024).hex())
                            request('write 0x90008000 4096 0x' + (gray * 1024).hex())
                            request('write 0x91000000 32 0x' + (bytes((mask,)) * 32).hex())
                            submit(state_packet({0x4E04: 0x27260007 if blend else 0,
                                                 0x4E08: 0x27210000 if blend else 0}) + black)
                            # No explicit idle/readback before the 2D source access.
                            for reg, value in ((0x16c0, 3), (0x146c, 0x52cc36d3),
                                               (0x1428, (128 << 16) | (0x90008000 >> 10)),
                                               (0x142c, (128 << 16) | (0x90000000 >> 10)),
                                               (0x1434, 0), (0x1438, (8 << 16) | 8),
                                               (0x16c8, 0x00cc0200), (0x1598, (16 << 16) | 16)):
                                write(base + reg, value)
                            idle()
                            pixels = bytes.fromhex(request('read 0x90000000 4096')[0][2:])
                            for y in range(32):
                                for x in range(32):
                                    wanted = expected if 8 <= x < 24 and 8 <= y < 24 else gray
                                    actual = pixels[(y * 32 + x) * 4:(y * 32 + x + 1) * 4]
                                    assert all(abs(a - b) <= 1 for a, b in zip(actual, wanted)), (
                                        f'Glyph copy blend={blend} pixel {x},{y}: '
                                        f'{actual.hex()}, expected {wanted.hex()}')
                        print('PASS: MorphOS compatibility preserves local RGB/alpha with TXO_ENDIAN=0; '
                              'captured black glyph and partial-alpha 2D copies preserve the grey background')
                    else:
                        print('PASS: native GPU offscreen draw→texture→draw preserves distinct RGB/alpha '
                              'with matching COLOR_ENDIAN/TXO_ENDIAN 0 and 2')
        except BaseException:
            log.seek(0)
            print(log.read())
            raise
        finally:
            vm.terminate()
            try:
                vm.wait(timeout=10)
            except subprocess.TimeoutExpired:
                vm.kill(); vm.wait(timeout=5)
