#!/usr/bin/env python3
"""Check that the Radeon's frame clock follows the programmed refresh rate.

    python3 tools/radeon-refresh-test.py [qemu-system-ppc]

Without a guest (qtest, virtual time only advances on clock_step): program a
1920x1080 CRTC with the pixel PLL at 60 Hz, then at 120 Hz at the same
resolution, and count CRTC_CRNT_FRAME ticks per virtual second.  The rate
change alone (no resolution change) must be picked up, and the counter must
never run backwards across it.
"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parent.parent
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / 'qemu/build/qemu-system-ppc'
BASE = 0xa0000000
CRTC_CRNT_FRAME = 0x214

with tempfile.TemporaryDirectory(prefix='radeon-refresh-') as temp:
    sock, qmp_sock = Path(temp) / 'qtest.sock', Path(temp) / 'qmp.sock'
    with (Path(temp) / 'qemu.log').open('w+') as log:
        vm = subprocess.Popen([
            str(binary), '-machine', 'mac99,via=pmu', '-cpu', 'G4', '-m', '64',
            '-accel', 'qtest',                    # virtual time moves only on clock_step
            '-vga', 'none', '-device', 'ati-radeon-9700,addr=e.0,vgamem_mb=32,romfile=',
            '-bios', str(repo / 'firmware/radeon/openbios-ppc'),
            '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none', '-S',
            '-qtest', f'unix:{sock},server=on,wait=off', '-qtest-log', '/dev/null',
            '-qmp', f'unix:{qmp_sock},server=on,wait=off', '-D', str(Path(temp) / 'gpu.log'),
        ], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            conn = socket.socket(socket.AF_UNIX)
            while True:
                try:
                    conn.connect(str(sock))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if vm.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    time.sleep(.05)
            stream = conn.makefile('rw')

            def request(command):
                stream.write(command + '\n'); stream.flush()
                reply = stream.readline().strip().split()
                assert reply and reply[0] == 'OK', reply
                return reply[1:]

            def write(addr, value, size=4):
                request(f'write {addr:#x} {size} 0x{value.to_bytes(size, "little").hex()}')

            def read(addr, size=4):
                return int.from_bytes(bytes.fromhex(request(f'read {addr:#x} {size}')[0][2:]), 'little')

            def pci_write(offset, value):
                write(0xf2800000, 0x80007000 | offset)
                write(0xf2c00000, value)

            qmp = socket.socket(socket.AF_UNIX)
            qmp.connect(str(qmp_sock))
            qf = qmp.makefile('rw')
            qf.readline()

            def qmp_cmd(command, **arguments):
                qf.write(json.dumps({'execute': command, 'arguments': arguments}) + '\n'); qf.flush()
                while True:
                    reply = json.loads(qf.readline())
                    if 'return' in reply or 'error' in reply:
                        return reply

            qmp_cmd('qmp_capabilities')

            def refresh():
                """A screendump runs the display update, which reads the CRTC and PLL."""
                qmp_cmd('screendump', filename=str(Path(temp) / 'shot.ppm'))

            def pll(index, value):
                write(BASE + 0x8, 0x80 | index, 1)    # CLOCK_CNTL_INDEX: write enable + index
                write(BASE + 0xc, value)

            def frames_per_second():
                f0 = read(BASE + CRTC_CRNT_FRAME)
                request('clock_step 1000000000')
                f1 = read(BASE + CRTC_CRNT_FRAME)
                return f0, f1

            pci_write(0x10, 0x90000000)
            pci_write(0x18, 0xa0000000)
            pci_write(0x04, 2)
            write(BASE + 0xb00, 0x100)                 # native driver, no aperture swap
            write(BASE + 0x200, (239 << 16) | 274)    # H: 1920 shown, 2200 total
            write(BASE + 0x208, (1079 << 16) | 1124)  # V: 1080 shown, 1125 total
            write(BASE + 0x22c, 0xf0)                 # pitch 1920
            write(BASE + 0x224, 0)
            write(BASE + 0x50, 0x03000600)            # CRTC on, 32 bpp
            pll(0x03, 0x8002)                         # PPLL_REF_DIV 2 (atomic update)
            pll(0x07, 11)                             # PPLL_DIV_3: fb 11, post /1 -> 148.5 MHz
            write(BASE + 0x8, 0x300)                  # select PPLL_DIV_3
            refresh()
            f0, f1 = frames_per_second()
            rate60 = f1 - f0
            pll(0x07, 22)                             # 297 MHz: 120 Hz, same resolution
            write(BASE + 0x8, 0x300)
            refresh()
            f2, f3 = frames_per_second()
            rate120 = f3 - f2
            print(f'60 Hz mode: {rate60} frames/s; 120 Hz mode: {rate120} frames/s; '
                  f'counter {f1} -> {f2} across the change')
            assert 59 <= rate60 <= 61, f'60 Hz mode counted {rate60} frames/s'
            assert 119 <= rate120 <= 121, f'refresh change at the same resolution ignored ({rate120} frames/s)'
            assert f2 >= f1, 'frame counter ran backwards across the rate change'
            print('PASS: frame clock follows a refresh change at the same resolution')
        finally:
            vm.kill()
            vm.wait()
