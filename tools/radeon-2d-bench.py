#!/usr/bin/env python3
"""Compare 100 paused, diskless 1920x1080 fills/copies; this is not desktop FPS.

Pass a QEMU executable and optionally ati-vga to compare the two device paths.
"""
import socket, subprocess, sys, tempfile, time
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / 'qemu/build/qemu-system-ppc'
device = sys.argv[2] if len(sys.argv) > 2 else 'ati-radeon-9700'
with tempfile.TemporaryDirectory(prefix='morphos-2d-') as tmp:
    sock = Path(tmp) / 'qtest'
    with open(Path(tmp) / 'log', 'w') as log:
        vm = subprocess.Popen([str(binary), '-machine', 'mac99,via=pmu', '-cpu', 'G4', '-m', '64',
                               '-vga', 'none', '-device', device + ',addr=e.0,vgamem_mb=32,romfile=',
                               '-bios', str(repo / 'firmware/radeon/openbios-ppc'),
                               '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none', '-S',
                               '-qtest', f'unix:{sock},server=on,wait=off', '-qtest-log', '/dev/null'],
                              stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            with socket.socket(socket.AF_UNIX) as conn:
                while True:
                    try:
                        conn.connect(str(sock)); break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if vm.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(Path(tmp, 'log').read_text())
                        time.sleep(.05)
                conn.settimeout(30)
                stream = conn.makefile('rw')
                def command(cmd):
                    stream.write(cmd + '\n'); stream.flush()
                    line = stream.readline().strip()
                    assert line.startswith('OK'), line
                    return line.split()[1:]
                def write(addr, val):
                    command(f'write {addr:#x} 4 0x{val.to_bytes(4, "little").hex()}')
                def pci(reg, val):
                    write(0xf2800000, 0x80007000 | reg); write(0xf2c00000, val)
                pci(0x10, 0x90000000); pci(0x18, 0xa0000000); pci(0x04, 2)
                base = 0xa0000000
                def reg(off, val): write(base + off, val)
                reg(0x16c0, 3)
                if device == 'ati-vga':
                    reg(0x1404, 0); reg(0x1408, 240)
                    reg(0x15ac, 0x90000000); reg(0x15b0, 240)
                else:
                    reg(0xb00, 0x100)  # native Radeon ownership, unswapped aperture
                    reg(0x148, 0x91ff0000)
                    reg(0x142c, (7680 << 16) | (0x90000000 >> 10))
                reg(0x146c, 0x52f036d3); reg(0x147c, 0x12345678)
                wh = (1920 << 16) | 1080
                def fill(): reg(0x1438, 0); reg(0x1598, wh)
                fill()
                pixel = command('read 0x90000000 4')[0]
                assert pixel != '0x00000000', pixel
                assert command('read 0x907e8ffc 4')[0] == pixel, 'Fill did not reach final pixel'
                print(device, 'first pixel', pixel)
                for trial in range(3):
                    start = time.perf_counter()
                    for i in range(100): fill()
                    print(device, '100 fullscreen fills seconds', round(time.perf_counter() - start, 6))
                if device == 'ati-vga':
                    reg(0x15ac, 0); reg(0x15b0, 240)
                    reg(0x1404, 0x800000); reg(0x1408, 240)
                else:
                    reg(0x1428, (7680 << 16) | (0x90000000 >> 10))
                    reg(0x142c, (7680 << 16) | (0x90800000 >> 10))
                reg(0x1434, 0); reg(0x146c, 0x52cc36d3)
                for trial in range(3):
                    start = time.perf_counter()
                    for i in range(100): reg(0x1438, 0); reg(0x1598, wh)
                    print(device, '100 fullscreen copies seconds', round(time.perf_counter() - start, 6))
                    assert command('read 0x90800000 4')[0] == pixel, 'Copy did not reach destination'
                    assert command('read 0x90fe8ffc 4')[0] == pixel, 'Copy did not reach final pixel'
        finally:
            vm.terminate()
            try: vm.wait(timeout=5)
            except subprocess.TimeoutExpired: vm.kill(); vm.wait()
