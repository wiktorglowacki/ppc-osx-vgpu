#!/usr/bin/env python3
"""Check Radeon bring-up registers and byte lanes without booting a guest.

    python3 tools/radeon-register-test.py [qemu-system-ppc]
"""
from pathlib import Path
import socket
import json
import subprocess
import struct
import sys
import tempfile
import time

repo = Path(__file__).resolve().parent.parent
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / 'qemu/build/qemu-system-ppc'
with tempfile.TemporaryDirectory(prefix='radeon-registers-') as temp:
    sock = Path(temp) / 'qtest.sock'
    qmp_sock = Path(temp) / 'qmp.sock'
    with (Path(temp) / 'qemu.log').open('w+') as log:
        vm = subprocess.Popen([
            str(binary), '-machine', 'mac99,via=pmu', '-cpu', 'G4', '-m', '64',
            '-vga', 'none', '-device', 'ati-radeon-9700,addr=e.0,vgamem_mb=32,romfile=',
            '-bios', str(repo / 'firmware/radeon/openbios-ppc'),
            '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none', '-S',
            '-qtest', f'unix:{sock},server=on,wait=off', '-qtest-log', '/dev/null',
            '-qmp', f'unix:{qmp_sock},server=on,wait=off',
        ], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(5)
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

                    def write(addr, value, size=4):
                        data = value.to_bytes(size, 'little').hex()
                        request(f'write {addr:#x} {size} 0x{data}')

                    def read(addr, size=4):
                        data = request(f'read {addr:#x} {size}')[0]
                        return int.from_bytes(bytes.fromhex(data[2:]), 'little')

                    def pci_write(offset, value):
                        write(0xf2800000, 0x80007000 | offset)
                        write(0xf2c00000, value)

                    def check_gart_colors(native):
                        write(base + 0x148, 0x91ff0000)
                        write(base + 0x16c0, 3)
                        pci_write(4, 6)  # Enable PCI bus mastering for system-memory reads.
                        rgba = bytes.fromhex('112233445566778899aabbcc')
                        request('write 0x200000 128 0x' + (b'\0' * 4 + rgba + b'\0' * 48).hex() * 2)
                        write(0x300000, 0x200000)  # One little-endian GART PTE.
                        for reg, value in ((0x1d0, 1), (0x1d8, 0x300000),
                                           (0x1dc, 0x40000000), (0x1e0, 0x40000fff)):
                            write(base + reg, value)
                        src_po = (64 << 16) | (0x40000000 >> 10)
                        dst_po = (64 << 16) | (0x90004000 >> 10)
                        for swap, order in enumerate(((0, 1, 2, 3), (1, 0, 3, 2),
                                                      (3, 2, 1, 0), (2, 3, 0, 1))):
                            if not native:
                                order = tuple(reversed(order))
                            row = bytes(rgba[p + c] for p in range(0, len(rgba), 4) for c in order)
                            expected = bytearray(b'\xaa' * 192)
                            expected[72:84] = row
                            expected[136:148] = row
                            write(base + 0x15d4, swap)
                            for path in ('combined', 'separate', 'pm4'):
                                request('write 0x90004000 192 0x' + 'aa' * 192)
                                write(base + 0x146c, 0x52cc36d3)
                                write(base + 0x1434, 1)
                                write(base + 0x1438, (1 << 16) | 2)
                                if path == 'combined':
                                    write(base + 0x1428, src_po)
                                    write(base + 0x142c, dst_po)
                                    write(base + 0x1598, (3 << 16) | 2)
                                elif path == 'separate':
                                    write(base + 0x15ac, 0x40000000)
                                    write(base + 0x15b0, 64)
                                    write(base + 0x1404, 0x90004000)
                                    write(base + 0x1408, 64)
                                    write(base + 0x143c, (2 << 16) | 3)
                                else:
                                    body = [0x52cc36d3, src_po, dst_po, 1 << 16,
                                            (2 << 16) | 1, (3 << 16) | 2]
                                    packet = [(3 << 30) | ((len(body) - 1) << 16) | (0x9b << 8)] + body
                                    raw = struct.pack(f'<{len(packet)}I', *packet)
                                    request(f'write {base + 0x1000:#x} {len(raw)} 0x' + raw.hex())
                                actual = bytes.fromhex(request('read 0x90004000 192')[0][2:])
                                assert actual == expected, f'{path} GART colors differ for source swap {swap}, native={native}'
                        write(base + 0x1d0, 0)
                        write(base + 0x15d4, 0)
                        pci_write(4, 2)

                    write(0xf2800000, 0x80007000)
                    identity = read(0xf2c00000)
                    assert identity == 0x4e441002, f'PCI identity: {identity:#x}'
                    pci_write(0x10, 0x90000000)
                    pci_write(0x18, 0xa0000000)
                    pci_write(0x04, 2)
                    base = 0xa0000000
                    assert read(base + 0x158) & (1 << 28), 'SDRAM not initialized'
                    assert read(base + 0x7b8) & 0x3ff >= 12, 'PIO queue cannot accept a packet'
                    check_gart_colors(False)
                    for reg in (0x50, 0x54, 0x58, 0x8, 0xc, 0xb00, 0x260, 0x264, 0x268, 0x26c, 0x270):
                        if reg == 0xc:
                            write(base + 0x8, 0x84)  # PLL write enable + register 4
                        write(base + reg, 0x11223344)
                        write(base + reg + 1, 0xaa, 1)
                        write(base + reg + 2, 0x55bb, 2)
                        assert read(base + reg) == 0x55bbaa44, hex(reg)
                        assert read(base + reg + 1, 1) == 0xaa, hex(reg)
                    write(base + 0xb00, 0x100)  # native driver, no aperture byte swap
                    # A DIV3 value must never leak its bit15 into REF_DIV polling.
                    write(base + 0x8, 0x87, 1)
                    write(base + 0xc, 0x0707829f)
                    write(base + 0x8, 0x83, 1)
                    write(base + 0xc, 0x0028803c)
                    write(base + 0x8, 3, 1)
                    assert read(base + 0xc) == 0x0028003c, 'PPLL atomic update did not finish'
                    write(base + 0xc, 0xdeadbeef)
                    assert read(base + 0xc) == 0x0028003c, 'PLL write accepted without WR_EN'
                    write(base + 0x8, 7, 1)
                    assert read(base + 0xc) == 0x0707829f, 'PLL indices share a data latch'
                    write(base + 0x8, 0x307)
                    assert read(base + 0xc) == 0x0707829f, 'Clock divider-select bits changed PLL index'
                    write(base + 0x8, 0x87, 1)
                    write(base + 0xd, 0x55, 1)
                    write(base + 0xe, 0x1234, 2)
                    assert read(base + 0xc) == 0x1234559f, 'PLL byte/word lanes lost'
                    write(base + 0x8, 0xab, 1)
                    write(base + 0xc, 0x003c803c)
                    write(base + 0x8, 0x2b, 1)
                    assert read(base + 0xc) == 0x003c003c, 'P2PLL atomic update did not finish'
                    write(base, 0x50)
                    write(base + 6, 0x1234, 2)
                    assert read(base + 0x50) == 0x1234aa44, 'Indexed register lane lost'
                    write(base, 0x80000100)
                    write(base + 4, 0x11223344)
                    write(base + 5, 0xaa, 1)
                    write(base + 6, 0x55bb, 2)
                    assert read(0x90000100) == 0x55bbaa44, 'Indexed VRAM lanes lost'
                    assert read(base + 5, 1) == 0xaa
                    write(base, 0x82000000)
                    assert read(base + 4) == 0, 'Out-of-bounds VRAM read accepted'
                    write(base + 4, 0xdeadbeef)
                    assert read(0x90000100) == 0x55bbaa44, 'Out-of-bounds write altered VRAM'
                    write(base + 0x16c0, 3)
                    # The MC base is zero; MorphOS supplies PCI addresses to 2D.
                    write(base + 0x148, 0x91ff0000)
                    write(base + 0x142c, (64 << 16) | (0x90001000 >> 10))
                    write(base + 0x1438, 0)
                    write(base + 0x146c, 0x52f036d3)
                    write(base + 0x147c, 0x11223344)
                    write(base + 0x1598, (2 << 16) | 1)
                    assert read(0x90001000) == 0x11223344, 'PCI-addressed fill lost'
                    write(base + 0x1428, (64 << 16) | (0x90001000 >> 10))
                    write(base + 0x142c, (64 << 16) | (0x90002000 >> 10))
                    write(base + 0x1434, 0)
                    write(base + 0x16c8, 0x00cc0200)
                    write(base + 0x1598, (2 << 16) | 1)
                    assert read(0x90002000) == 0x11223344, 'PCI-addressed copy lost'
                    write(0x90002000, 0)
                    write(base + 0x16c0, 0)
                    write(base + 0x1434, 1)
                    write(base + 0x1438, 1)
                    write(base + 0x1598, (2 << 16) | 1)
                    assert read(0x90002000) == 0x11223344, 'Reverse copy origin lost'
                    # Aliased views overlap even when their base offsets differ.
                    rows = b''.join(bytes((n,)) * 1024 for n in (0x11, 0x22, 0x33, 0x44))
                    for reverse in (True, False):
                        request('write 0x90008000 4096 0x' + rows.hex())
                        source, destination = ((0x90008000, 0x90008400) if reverse else
                                               (0x90008400, 0x90008000))
                        write(base + 0x1428, (1024 << 16) | (source >> 10))
                        write(base + 0x142c, (1024 << 16) | (destination >> 10))
                        write(base + 0x146c, 0x52cc36d3)
                        write(base + 0x16c0, 0 if reverse else 3)
                        write(base + 0x1434, (2 << 16) | 1 if reverse else 0)
                        write(base + 0x1438, (2 << 16) | 1 if reverse else 0)
                        write(base + 0x1598, (2 << 16) | 3)
                        expected_alias = bytearray(rows)
                        for row in range(3):
                            src = (row + (not reverse)) * 1024
                            dst = (row + reverse) * 1024
                            expected_alias[dst:dst + 8] = rows[src:src + 8]
                        raw_alias = bytes.fromhex(request('read 0x90008000 4096')[0][2:])
                        assert raw_alias == expected_alias, 'Overlapping surface aliases corrupted rows/padding'
                    write(base + 0x16c0, 3)
                    write(base + 0x1438, 0)
                    write(base + 0x142c, (64 << 16) | (0x90002000 >> 10))
                    write(base + 0x146c, 0x53cc36d3)
                    write(base + 0x1598, (1 << 16) | 1)
                    write(base + 0x17c0, 0x55667788)
                    assert read(0x90002000) == 0x55667788, 'PCI-addressed upload lost'
                    check_gart_colors(True)
                    # A copy from VRAM into AGP memory lands in the RAM the GART maps.
                    write(base + 0x14c, 0x4fff4000)
                    write(base + 0x1d8, 0x300000)
                    write(0x300000, 0x200001)
                    request('write 0x200000 192 0x' + 'aa' * 192)
                    request('write 0x9000a000 192 0x' + bytes(range(192)).hex())
                    write(base + 0x146c, 0x52cc36d3)
                    write(base + 0x16c0, 3)
                    write(base + 0x1428, (64 << 16) | (0x9000a000 >> 10))
                    write(base + 0x142c, (64 << 16) | (0x40000000 >> 10))
                    write(base + 0x1434, (1 << 16) | 1)
                    write(base + 0x1438, (1 << 16) | 2)
                    write(base + 0x1598, (3 << 16) | 2)
                    expected = bytearray(b'\xaa' * 192)
                    for row in (1, 2):
                        expected[row * 64 + 8:row * 64 + 20] = bytes(range(row * 64 + 4, row * 64 + 16))
                    assert bytes.fromhex(request('read 0x200000 192')[0][2:]) == expected, 'VRAM to AGP copy lost'
                    write(base + 0x14c, 0)
                    write(base + 0x1d8, 0)
                    # All fill entry points preserve byte order and row padding.
                    for datatype, bpp in ((2, 1), (3, 2), (6, 4)):
                        gmc = 0x52f030d3 | (datatype << 8)
                        color = 0x11223344
                        pixel = (color & ((1 << (8 * bpp)) - 1)).to_bytes(bpp, 'little')
                        po = (64 << 16) | (0x90006000 >> 10)
                        expected = bytearray(b'\xaa' * 192)
                        for row in (1, 2):
                            start = row * 64 + bpp
                            expected[start:start + 3 * bpp] = pixel * 3
                        for path in ('combined', 'separate', 'paint'):
                            request('write 0x90006000 192 0x' + ('aa' * 192))
                            write(base + 0x146c, gmc)
                            write(base + 0x147c, color)
                            write(base + 0x1438, (1 << 16) | 1)
                            if path == 'combined':
                                write(base + 0x142c, po)
                                write(base + 0x1598, (3 << 16) | 2)
                            elif path == 'separate':
                                write(base + 0x1404, 0x90006000)
                                write(base + 0x1408, 64)
                                write(base + 0x143c, (2 << 16) | 3)
                            else:
                                write(base + 0x1000, 0xc0049a00)
                                for word in (gmc, po, color, (1 << 16) | 1, (3 << 16) | 2):
                                    write(base + 0x1004, word)
                            raw = bytes.fromhex(request('read 0x90006000 192')[0][2:])
                            assert raw == expected, f'{path} {bpp * 8}bpp fill changed padding or byte order'
                    # The bounded fallback still writes the valid pixels of a partial row.
                    write(base + 0x146c, 0x52f036d3)
                    write(base + 0x1404, 0x91fffff0)
                    write(base + 0x1408, 64)
                    write(base + 0x1438, 0)
                    write(base + 0x143c, (2 << 16) | 8)
                    raw = bytes.fromhex(request('read 0x91fffff0 16')[0][2:])
                    assert raw == bytes.fromhex('44332211') * 4, 'Partial VRAM fill lost valid pixels'
                    # Combined fills/copies must not mark an invalid dirty bitmap range.
                    write(base + 0x142c, (64 << 16) | (0x91fffc00 >> 10))
                    write(base + 0x1438, 252)
                    write(base + 0x1598, (8 << 16) | 2)
                    assert read(0x91fffff0) == 0x11223344, 'Combined partial fill lost valid pixels'
                    write(base + 0x142c, (64 << 16) | (0xdead0000 >> 10))
                    write(base + 0x1438, 0)
                    write(base + 0x1598, (1 << 16) | 1)
                    write(base + 0x146c, 0x52cc36d3)
                    write(base + 0x1598, (1 << 16) | 1)
                    assert read(0x90001000) == 0x11223344, 'Invalid 2D destination altered VRAM'
                    # A PPC CPU sees the programmed aperture swap, independently of scanout.
                    seed = bytes.fromhex('001cdfff')  # captured red Gears fragment as card BGRA
                    lanes = (0, 1, 3, 2)
                    for mode, lane in enumerate(lanes):
                        write(base + 0xb00, 0x100 | (mode << 20))
                        request('write 0x92008000 4 0x' + seed.hex())  # aperture 1, mode zero
                        view = bytes.fromhex(request('read 0x90008000 4')[0][2:])
                        assert view == bytes(seed[i ^ lane] for i in range(4)), f'AP0 read swap {mode}'
                        request('write 0x90008000 4 0x11223344')
                        physical = bytes.fromhex(request('read 0x92008000 4')[0][2:])
                        assert physical == bytes(bytes.fromhex('11223344')[i ^ lane] for i in range(4)), f'AP0 write swap {mode}'
                        write(base + 0xb00, 0x100 | (mode << 22))
                        request('write 0x90008000 4 0x' + seed.hex())
                        view = bytes.fromhex(request('read 0x92008000 4')[0][2:])
                        assert view == bytes(seed[i ^ lane] for i in range(4)), f'AP1 read swap {mode}'
                        request('write 0x92008000 4 0x11223344')
                        physical = bytes.fromhex(request('read 0x90008000 4')[0][2:])
                        assert physical == bytes(bytes.fromhex('11223344')[i ^ lane] for i in range(4)), f'AP1 write swap {mode}'
                    # PPC BE stores plus swap2 and explicit LE stores plus swap0 agree.
                    write(base + 0xb00, 0x100 | (2 << 20))
                    request('write 0x90008000 4 0x3f800000')
                    assert request('read 0x92008000 4')[0] == '0x0000803f', 'PPC float upload through swap2'
                    write(base + 0xb00, 0x100)
                    request('write 0x90008000 4 0x0000803f')
                    assert request('read 0x92008000 4')[0] == '0x0000803f', 'Explicit LE float upload through swap0'
                    write(base + 0xb00, 0)
                    write(base + 0xb04, 0x8000)
                    write(base + 0xb08, 0x8fff)
                    write(base + 0xb0c, 2 << 20)
                    request('write 0x92008000 4 0x' + seed.hex())
                    assert request('read 0x90008000 4')[0] == '0x' + seed[::-1].hex(), 'Surface AP0 swap lost'
                    write(base + 0xb00, 0x100)
                    assert request('read 0x90008000 4')[0] == '0x' + seed.hex(), 'Translation disable ignored'
                    # Fast clears must accept PCI addresses and reject unmapped targets.
                    write(base + 0x43e4, ((1440 + 3) << 13) | (1440 + 63))
                    write(base + 0x4f20, 0x90003000)
                    write(base + 0x4f24, 64)
                    write(base + 0x4f10, 2)
                    write(base + 0x4f28, 0x00ffffff)
                    write(base + 0x1000, 0xc0003200)
                    write(base + 0x1004, 0)
                    assert read(0x90003000) == 0x00ffffff, 'PCI depth clear lost'
                    write(base + 0x4e28, 0x90004000)
                    write(base + 0x4e38, (6 << 21) | 64)
                    write(base + 0x4e14, 0x11223344)
                    write(base + 0x1000, 0xc0003800)
                    write(base + 0x1004, 0)
                    assert read(0x90004000) == 0x11223344, 'PCI color clear lost'
                    write(base + 0x4f20, 0xdead0000)
                    write(base + 0x1000, 0xc0003200)
                    write(base + 0x1004, 0)
                    assert read(0x90003000) == 0x00ffffff, 'Invalid depth clear altered VRAM'
                    write(base + 0x50, 0x03000600)
                    write(base + 0x200, 31)       # eight pixels wide
                    write(base + 0x208, 31)       # one scanline
                    write(base + 0x22c, 1)
                    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (17, 34, 51)]
                    write(base + 0x142c, (64 << 16) | (0x90000000 >> 10))
                    write(base + 0x146c, 0x53cc36d3)
                    write(base + 0x1598, (4 << 16) | 1)
                    for rgb in colors:
                        write(base + 0x17c0, (rgb[0] << 16) | (rgb[1] << 8) | rgb[2])
                    with socket.socket(socket.AF_UNIX) as monitor:
                        monitor.settimeout(5)
                        monitor.connect(str(qmp_sock))
                        with monitor.makefile('rw') as qmp:
                            json.loads(qmp.readline())
                            shot = Path(temp) / 'colors.ppm'
                            def command(value):
                                qmp.write(json.dumps(value) + '\n'); qmp.flush()
                                while True:
                                    reply = json.loads(qmp.readline())
                                    assert 'error' not in reply, reply
                                    if 'return' in reply:
                                        return
                            command({'execute': 'qmp_capabilities'})
                            command({'execute': 'screendump', 'arguments': {'filename': str(shot)}})
                            pixels = shot.read_bytes().split(maxsplit=4)[4]
                            assert pixels[:12] == bytes(c for rgb in colors for c in rgb), pixels[:12].hex()
                            write(base + 0x50, 0x03000200)
                            write(base + 0xb0, 0)
                            for rgb in colors:
                                write(base + 0xb4, (rgb[0] << 16) | (rgb[1] << 8) | rgb[2])
                            for index in range(4):
                                write(0x90000000 + index, index, 1)
                            command({'execute': 'screendump', 'arguments': {'filename': str(shot)}})
                            pixels = shot.read_bytes().split(maxsplit=4)[4]
                            assert pixels[:12] == bytes(c for rgb in colors for c in rgb), '8bpp palette lost'

                    # A ring interrupt is a latched event, not a read-to-clear register.
                    def setup_irq_ring():
                        pci_write(4, 6)
                        write(0x300000, 0x200000)
                        for reg, value in ((0x1d0, 1), (0x1d8, 0x300000),
                                           (0x1dc, 0x40000000), (0x1e0, 0x40000fff),
                                           (0x700, 0x40000000), (0x704, 9), (0x710, 0),
                                           (0x40, 0), (0x44, 0x03ffffff)):
                            write(base + reg, value)
                    def queue_irq(sequence):
                        words = [0x15e0 >> 2, sequence, 0x44 >> 2, 1 << 26]
                        data = struct.pack('>4I', *words)
                        request(f'write {0x200000 + sequence * 16 - 16:#x} 16 0x' + data.hex())
                        write(base + 0x714, sequence * 4)

                    setup_irq_ring()
                    for sequence in range(1, 17):
                        queue_irq(sequence)
                        deadline = time.monotonic() + 2
                        while not read(base + 0x44) & (1 << 25):
                            assert time.monotonic() < deadline, 'Queued software interrupt never arrived'
                            time.sleep(.001)
                        assert read(base + 0x15e0) == sequence, 'Interrupt overtook its ring marker'
                        assert read(base + 0x44) & (1 << 25), 'Status read cleared the interrupt'
                        write(base + 0x44, 1)  # Acknowledge another source only.
                        assert read(base + 0x44) & (1 << 25), 'Unrelated acknowledge cleared software IRQ'
                        write(base + 0x44, 1 << 25)
                        assert not read(base + 0x44) & (1 << 25), 'Software interrupt acknowledge failed'
                        write(base + 0x40, (sequence & 1) << 25)
                    # Coalesced events may share one status bit; their ring writes must survive.
                    write(base + 0x44, 1 << 26)
                    queue_irq(17)
                    write(base + 0x44, 1 << 25)
                    assert read(base + 0x15e0) == 17, 'Acknowledge lost a queued ring write'
                    assert not read(base + 0x44) & (1 << 25), 'Ordered acknowledge left an old IRQ pending'
                    queue_irq(18)
                    deadline = time.monotonic() + 2
                    while not read(base + 0x44) & (1 << 25):
                        assert time.monotonic() < deadline, 'IRQ did not recover after coalesced acknowledge'
                        time.sleep(.001)
                    assert read(base + 0x15e0) == 18, 'Final ring marker lost'
                    # Reset must clear a pending IRQ and let the same worker process a new ring.
                    with socket.socket(socket.AF_UNIX) as monitor:
                        monitor.settimeout(5)
                        monitor.connect(str(qmp_sock))
                        with monitor.makefile('rw') as qmp:
                            json.loads(qmp.readline())
                            command({'execute': 'qmp_capabilities'})
                            qmp.write(json.dumps({'execute': 'system_reset'}) + '\n'); qmp.flush()
                            reset = replied = False
                            while not (reset and replied):
                                reply = json.loads(qmp.readline())
                                assert 'error' not in reply, reply
                                reset |= reply.get('event') == 'RESET'
                                replied |= 'return' in reply
                    pci_write(0x10, 0x90000000)
                    pci_write(0x18, base)
                    pci_write(4, 6)
                    assert not read(base + 0x44) & (1 << 25), 'Reset kept software IRQ pending'
                    assert read(base + 0x710) == read(base + 0x714) == 0, 'Reset kept ring pointers'
                    setup_irq_ring()
                    queue_irq(1)
                    deadline = time.monotonic() + 2
                    while not read(base + 0x44) & (1 << 25):
                        assert time.monotonic() < deadline, 'IRQ worker did not recover after reset'
                        time.sleep(.001)
                    assert read(base + 0x15e0) == 1, 'Post-reset ring marker lost'
                    write(base + 0x44, 1 << 25)
                    print('PASS: SDRAM, PIO capacity, byte lanes, VRAM bounds, 2D aperture addressing, RGB scanout, 8bpp palette, queued IRQ latch/ack ordering and reset')
        finally:
            vm.terminate()
            try:
                vm.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vm.kill(); vm.wait(timeout=5)
