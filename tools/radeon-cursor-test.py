#!/usr/bin/env python3
"""Check native cursor compositing, clipping and scanout restoration without a guest."""
from pathlib import Path
import json
import socket
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parent.parent
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / 'qemu/build/qemu-system-ppc'
with tempfile.TemporaryDirectory(prefix='radeon-cursor-') as temporary:
    temp = Path(temporary)
    with (temp / 'qemu.log').open('w+') as log:
        vm = subprocess.Popen([
            str(binary), '-machine', 'mac99,via=pmu', '-cpu', 'G4', '-m', '64',
            '-vga', 'none', '-device', 'ati-radeon-9700,addr=e.0,vgamem_mb=32,renderer=vulkan,romfile=',
            '-bios', str(repo / 'firmware/radeon/openbios-ppc'),
            '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none', '-S',
            '-qtest', f'unix:{temp}/qtest.sock,server=on,wait=off', '-qtest-log', '/dev/null',
            '-qmp', f'unix:{temp}/qmp.sock,server=on,wait=off',
        ], stdout=log, stderr=log)
        try:
            with socket.socket(socket.AF_UNIX) as qt, socket.socket(socket.AF_UNIX) as qm:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        qt.connect(str(temp / 'qtest.sock'))
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if vm.poll() is not None or time.monotonic() > deadline:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        time.sleep(.05)
                qt.settimeout(5); qm.settimeout(5)
                qm.connect(str(temp / 'qmp.sock'))
                with qt.makefile('rw') as stream, qm.makefile('rw') as monitor:
                    def request(command):
                        stream.write(command + '\n'); stream.flush()
                        reply = stream.readline().strip().split()
                        assert reply and reply[0] == 'OK', reply
                        return reply[1:]

                    def raw(address, data):
                        request(f'write {address:#x} {len(data)} 0x{data.hex()}')

                    def write(address, value, size=4):
                        raw(address, value.to_bytes(size, 'little'))

                    def read(address):
                        return int.from_bytes(bytes.fromhex(request(f'read {address:#x} 4')[0][2:]), 'little')

                    def qmp(command):
                        monitor.write(json.dumps(command) + '\n'); monitor.flush()
                        while True:
                            reply = json.loads(monitor.readline())
                            assert 'error' not in reply, reply
                            if 'return' in reply:
                                return

                    json.loads(monitor.readline())
                    qmp({'execute': 'qmp_capabilities'})
                    for offset, value in ((0x10, 0x90000000), (0x18, 0xa0000000), (4, 2)):
                        write(0xf2800000, 0x80007000 | offset)
                        write(0xf2c00000, value)
                    base = 0xa0000000
                    read(base + 0x158)  # native register view, rather than NDRV palette aliases
                    write(base + 0x148, 0x91ff0000)
                    write(base + 0xb00, 0x100)
                    write(base + 0x50, 0x03000600)
                    write(base + 0x200, (79 << 16) | 799)
                    write(base + 0x208, (479 << 16) | 524)
                    write(base + 0x22c, 80)
                    background = bytes((60, 40, 20, 255)) * (640 * 480)
                    raw(0x90000000, background)
                    shot = temp / 'cursor.ppm'

                    def pixel(x, y):
                        qmp({'execute': 'screendump', 'arguments': {'filename': str(shot)}})
                        header, width, height, maximum, data = shot.read_bytes().split(maxsplit=4)
                        assert (header, int(width), int(height), maximum) == (b'P6', 640, 480, b'255'), (header,width,height,maximum)
                        off = (y * 640 + x) * 3
                        return tuple(data[off:off + 3])

                    assert pixel(2, 4) == (20, 40, 60), 'Native background byte order changed'
                    cursor = bytearray(64 * 64 * 4)
                    # ATI CRTC_CUR_MODE alpha formats use aRGB8888 words.
                    # Physical VRAM is little endian: B,G,R,A bytes.
                    cursor[:8] = bytes((0, 0, 255, 255, 0, 255, 0, 128))
                    cursor[256:260] = bytes((255, 0, 0, 255))
                    raw(0x90200000, cursor)
                    write(base + 0x264, (2 << 16) | 4)
                    write(base + 0x260, 0x90200000)
                    write(base + 0x50, 0x03310600)
                    assert pixel(2, 4) == (255, 0, 0), 'Native cursor not visible in scanout'
                    assert pixel(3, 4) == (10, 148, 30), 'Cursor alpha blend differs'
                    # The captured MorphOS pointer pixel 00008cff is dark red,
                    # rather than blue; this distinguishes BGRA from RGBA.
                    cursor[:4] = bytes((0, 0, 140, 255))
                    raw(0x90200000, cursor)
                    write(base + 0x260, 0x90200000)
                    assert pixel(2, 4) == (140, 0, 0), 'MorphOS pointer color differs'
                    # Mode 2 alpha is premultiplied: green64 is already weighted.
                    cursor[:4] = bytes((0, 64, 0, 128))
                    raw(0x90200000, cursor)
                    write(base + 0x50, 0x03210600)
                    assert pixel(2, 4) == (10, 84, 30), 'Premultiplied cursor alpha differs'
                    cursor[:4] = bytes((0, 0, 255, 255))
                    raw(0x90200000, cursor)
                    write(base + 0x50, 0x03310600)
                    write(base + 0x264, (6 << 16) | 20)
                    assert pixel(2, 4) == (20, 40, 60), 'Old cursor row was not restored'
                    assert pixel(6, 20) == (255, 0, 0), 'Cursor-only refresh skipped new rows'
                    write(base + 0x264, 0x80000000 | (8 << 16) | 10)
                    assert pixel(6, 20) == (255, 0, 0), 'Locked cursor position became visible'
                    write(base + 0x264, (8 << 16) | 10)
                    assert pixel(6, 20) == (20, 40, 60), 'Unlock did not restore old rows'
                    write(base + 0x268, 1 << 16)
                    assert pixel(8, 10) == (10, 148, 30), 'Left-edge cursor clipping differs'
                    write(base + 0x264, 0x80000000 | (8 << 16) | 10)
                    write(base + 0x268, 1)
                    write(base + 0x260, 0x90200100)
                    write(base + 0x264, (8 << 16) | 10)
                    assert pixel(8, 10) == (0, 0, 255), 'Adjusted cursor address was vertically clipped twice'
                    write(base + 0x50, 0x03300600)
                    assert pixel(8, 10) == (20, 40, 60), 'Hidden cursor remained on screen'
                    mono = bytearray([255] * 1024)
                    for row in range(64):
                        mono[row * 16 + 8:row * 16 + 16] = bytes(8)
                    mono[0] = 0x3f; mono[8] = 0x60
                    raw(0x90200000, mono)
                    write(base + 0x260, 0x90200000)
                    write(base + 0x268, 0)
                    write(base + 0x26c, 0x112233)
                    write(base + 0x270, 0xaabbcc)
                    write(base + 0x50, 0x03010600)
                    assert pixel(8, 10) == (17, 34, 51), 'Monochrome background color differs'
                    assert pixel(9, 10) == (170, 187, 204), 'Monochrome foreground color differs'
                    assert pixel(10, 10) == (235, 215, 195), 'Monochrome inversion differs'
                    assert pixel(11, 10) == (20, 40, 60), 'Monochrome transparent pixel changed'
                    write(base + 0x50, 0x03000600)
                    assert bytes.fromhex(request('read 0x90000000 1228800')[0][2:]) == background, 'Cursor modified guest VRAM'
                    print('PASS: native ARGB cursor, straight/premultiplied alpha, movement, lock, clipping, hide, monochrome inversion and unchanged VRAM')
        finally:
            vm.terminate()
            try:
                vm.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vm.kill(); vm.wait()
