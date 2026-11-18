#!/usr/bin/env python3
"""Replay a captured MorphOS UI draw; verify Vulkan FIFO/busy/idle ordering."""
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parent.parent
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / 'qemu/build/qemu-system-ppc'
check_copies = '--check-copy-counter' in sys.argv[2:]
check_surface = '--check-surface' in sys.argv[2:]
check_snapshot = '--check-dirty-snapshot' in sys.argv[2:]
with tempfile.TemporaryDirectory(prefix='radeon-status-') as temp:
    sock = Path(temp) / 'qtest.sock'
    qmp_sock = Path(temp) / 'qmp.sock'
    with (Path(temp) / 'qemu.log').open('w+') as log:
        env = dict(os.environ)
        env.pop('R300_SYNC', None)
        vm = subprocess.Popen([
            str(binary), '-machine', 'mac99,via=pmu', '-cpu', 'G4', '-m', '64',
            '-vga', 'none', '-device', 'ati-radeon-9700,addr=e.0,vgamem_mb=32,renderer=vulkan,romfile=',
            '-bios', str(repo / 'firmware/radeon/openbios-ppc'),
            '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none', '-S',
            '-qtest', f'unix:{sock},server=on,wait=off', '-qtest-log', '/dev/null',
            '-qmp', f'unix:{qmp_sock},server=on,wait=off',
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
                    registers = json.loads((repo / 'tools/fixtures/morphos-ui-registers.json').read_text())
                    registers.update({'4E28': '90000000', '4E38': '00D00800',
                                      '4540': '91000000', '4480': '8000001F', '4500': '0000001F',
                                      '4E04': '00000000', '4E08': '00000000',
                                      '43E4': f'{(1440 + 2047) | ((1440 + 4095) << 13):08X}'})
                    for reg, value in registers.items():
                        write(base + int(reg, 16), int(value, 16))
                    request('write 0x91000000 32 0x' + 'ff' * 32)
                    words = [0xc0203500, 0x0004003d]
                    for vertex in [(0, 0, 0, 1, 0, 0, 0, 1), (2048, 0, 0, 1, 1, 0, 0, 1),
                                   (2048, 4096, 0, 1, 1, 1, 0, 1), (0, 4096, 0, 1, 0, 1, 0, 1)]:
                        words.extend(struct.unpack('<8I', struct.pack('<8f', *vertex)))
                    packet = struct.pack(f'<{len(words)}I', *words).hex()
                    busy = False
                    for port in (0xe40, 0x1740):
                        assert read(base + port) == 64, 'Empty GPU must be idle with FIFO capacity'
                        write(0x90000808, 0x78563412)
                        assert read(0x90000808) == 0x78563412, 'Probe pixel was not reset'
                        request(f'write {base + 0x1000:#x} {len(words) * 4} 0x{packet}')
                        deadline = time.monotonic() + 10
                        while True:
                            status = read(base + port)
                            assert status & 0x7f == 64, 'GPU busy must not consume command FIFO capacity'
                            busy |= bool(status & (1 << 31))
                            if not status & (1 << 31):
                                break
                            assert time.monotonic() < deadline, 'GPU did not become idle'
                        pixel = request('read 0x90000808 4')[0]
                        assert pixel == '0xff000000', f'Idle reported before rendered pixels reached VRAM: {pixel}'
                    print('PASS: both status ports reach idle after VRAM writeback; '
                          f'FIFO capacity preserved (busy observed: {busy})')
                    for issue in ('mmio', 'indexed-byte', 'pm4'):
                        request(f'write {base + 0x1000:#x} {len(words) * 4} 0x{packet}')
                        if issue == 'mmio':
                            write(base + 0x1720, 1 << 17)  # WAIT_3D_IDLECLEAN
                        elif issue == 'indexed-byte':
                            write(base, 0x1720)
                            request(f'write {base + 6:#x} 1 0x02')
                        else:
                            barrier = struct.pack('<2I', 0x1720 // 4, 1 << 17).hex()
                            request(f'write {base + 0x1000:#x} 8 0x{barrier}')
                        # A WAIT_UNTIL is queued, not executed by the CPU's write
                        # (on the card too: 0x1400-0x1eff go through the command
                        # FIFO).  The driver drains the FIFO (CP_CSQ_STAT) or polls
                        # idle (RBBM_STATUS) before it touches VRAM, as MorphOS does.
                        deadline = time.monotonic() + 10
                        while read(base + 0x7f8) or read(base + 0xe40) & (1 << 31):
                            assert time.monotonic() < deadline, 'GPU wait barrier did not complete'
                        pixel = request('read 0x90000808 4')[0]
                        assert pixel == '0xff000000', f'{issue} WAIT: drained before rendering reached VRAM: {pixel}'
                        write(0x90000808, 0x78563412)
                        time.sleep(0.05)
                        assert read(0x90000808) == 0x78563412, f'{issue} WAIT lost a following CPU write'
                    print('PASS: MMIO, indexed byte and PM4 write-only idle-clean barriers drain before CPU updates')
                    with socket.socket(socket.AF_UNIX) as monitor:
                        monitor.settimeout(10)
                        monitor.connect(str(qmp_sock))
                        with monitor.makefile('rw') as qmp_stream:
                            json.loads(qmp_stream.readline())

                            def qmp(command, **arguments):
                                qmp_stream.write(json.dumps({'execute': command, 'arguments': arguments}) + '\n')
                                qmp_stream.flush()
                                while True:
                                    reply = json.loads(qmp_stream.readline())
                                    assert 'error' not in reply, reply
                                    if 'return' in reply:
                                        return reply['return']

                            qmp('qmp_capabilities')

                            def screen_pixel():
                                shot = Path(temp) / 'screen.ppm'
                                qmp('screendump', filename=str(shot))
                                return shot.read_bytes().split(maxsplit=4)[4][:3]

                            def copy_counter():
                                if not check_copies:
                                    return None
                                result = subprocess.check_output([
                                    'gdb', '-q', '-nx', '-batch', str(binary), '-p', str(vm.pid),
                                    '-ex', 'set debuginfod enabled off',
                                    '-ex', 'p r200_rate.since', '-ex', 'p r200_rate.co_copies',
                                    '-ex', 'detach',
                                ], text=True, stderr=subprocess.DEVNULL)
                                values = re.findall(r'\$\d+ = (\d+)', result)
                                assert len(values) == 2, result
                                return tuple(map(int, values))

                            def surface_identity():
                                if not check_surface:
                                    return None
                                result = subprocess.check_output([
                                    'gdb', '-q', '-nx', '-batch', str(binary), '-p', str(vm.pid),
                                    '-ex', 'set debuginfod enabled off',
                                    '-ex', 'p/x (uintptr_t)consoles.tqh_first->surface',
                                    '-ex', 'p/x (uintptr_t)consoles.tqh_first->surface->image',
                                    '-ex', 'p/x (uintptr_t)((PPCMacGPUState*)V.dirty_arg)->surface_data',
                                    '-ex', 'detach',
                                ], text=True, stderr=subprocess.DEVNULL)
                                values = re.findall(r'\$\d+ = (0x[0-9a-f]+)', result)
                                assert len(values) == 3, result
                                return tuple(values)

                            def snapshot_boundaries():
                                # Exercise the actual QEMU helper, not a copied implementation.
                                pg = 4096
                                cases = [
                                    (0, 1, True), (pg, 62 * pg, False),
                                    (63 * pg - 1, 1, False), (63 * pg - 1, 2, True),
                                    (63 * pg, pg, True), (64 * pg, 1, True),
                                    (65 * pg, 1, True), (66 * pg, 61 * pg, False),
                                    (127 * pg, 1, True), (128 * pg, 1, True),
                                    (129 * pg, 126 * pg, False), (255 * pg, pg, True),
                                    (0, 0, False), (255 * pg, 0, False),
                                    # Keep existing page-rounded zero-length behavior.
                                    (1, 0, True), (pg + 1, 0, False),
                                ]
                                commands = [
                                    'set debuginfod enabled off',
                                    'set confirm off',
                                    "python if int(gdb.parse_and_eval('sizeof(unsigned long)')) != 8: "
                                    "print('Dirty-snapshot fixture requires a 64-bit host'); gdb.execute('quit 1')",
                                    'set $snap = (DirtyBitmapSnapshot*)calloc(1, sizeof(DirtyBitmapSnapshot) + 4 * sizeof(unsigned long))',
                                    'set $snap->start = 0x12340000',
                                    f'set $snap->end = $snap->start + {256 * pg}',
                                ]
                                for page in (0, 63, 64, 65, 127, 128, 255):
                                    commands.append(f'set $snap->dirty[{page // 64}] |= 1UL << {page % 64}')
                                for offset, length, _ in cases:
                                    commands.append('p cpu_physical_memory_snapshot_get_dirty('
                                                    f'$snap, $snap->start + {offset}, {length})')
                                commands.extend(['call (void)free($snap)', 'detach'])
                                arguments = ['gdb', '-q', '-nx', '-batch', str(binary), '-p', str(vm.pid)]
                                for command in commands:
                                    arguments.extend(['-ex', command])
                                result = subprocess.check_output(arguments, text=True, stderr=subprocess.STDOUT,
                                                                 timeout=15)
                                answers = re.findall(r'\$\d+ = (true|false)\b', result)
                                assert answers == ['true' if expected else 'false' for _, _, expected in cases], result
                                print('PASS: actual dirty-snapshot helper preserves page/word boundaries, '
                                      'excluded end bits and zero-length behavior')

                            def no_new_copies(before):
                                after = copy_counter()
                                if before is not None:
                                    # The diagnostic counters reset once per second.
                                    expected = before[1] if before[0] == after[0] else 0
                                    assert after[1] == expected, 'Offscreen changes recopied the front buffer'
                                return after

                            assert screen_pixel() == b'\0\0\0'
                            write(0x90000000, 0xffffffff)
                            assert screen_pixel() == b'\xff\xff\xff', 'Direct CPU write was not displayed'
                            assert screen_pixel() == b'\xff\xff\xff', 'Unchanged scanout lost its pixels'
                            request(f'write {base + 0x1000:#x} {len(words) * 4} 0x{packet}')
                            deadline = time.monotonic() + 10
                            while read(base + 0xe40) & (1 << 31):
                                assert time.monotonic() < deadline
                            assert screen_pixel() == b'\0\0\0', 'GPU write left a stale cached scanout'
                            surface = surface_identity()
                            write(base + 0x50, 0x03000400)
                            assert screen_pixel() != b'\0\0\0', '16-bit mode was not applied'
                            if check_surface:
                                assert surface_identity() != surface, 'Pixel mode did not replace the surface'
                            write(base + 0x50, 0x03000600)
                            assert screen_pixel() == b'\0\0\0', '32-bit mode reused a stale surface'
                            write(0x90000000, 0xffffffff)
                            assert screen_pixel() == b'\xff\xff\xff'
                            surface = surface_identity()
                            write(base + 0x224, 4096)
                            assert screen_pixel() == b'\0\0\0', 'Page flip reused the previous buffer'
                            assert surface_identity() == surface, 'Same-extent page flip recreated the surface'
                            write(base + 0x224, 0)
                            assert screen_pixel() == b'\xff\xff\xff'
                            assert surface_identity() == surface, 'Return page flip recreated the surface'
                            print('PASS: cached Vulkan scanout refreshes for CPU/GPU writes, mode changes and page flips')
                            # Repeat after the native driver's unswapped-aperture handoff.
                            write(base + 0xb00, 0x100)
                            write(0x90000000, 0xffffffff)
                            assert screen_pixel() == b'\xff\xff\xff'
                            request(f'write {base + 0x1000:#x} {len(words) * 4} 0x{packet}')
                            deadline = time.monotonic() + 10
                            while read(base + 0xe40) & (1 << 31):
                                assert time.monotonic() < deadline
                            word = read(0x90000000)
                            expected = bytes((word >> 16 & 255, word >> 8 & 255, word & 255))
                            assert expected != b'\xff\xff\xff', 'Native draw did not replace the sentinel'
                            assert screen_pixel() == expected, 'Native GPU write left a stale scanout'
                            assert screen_pixel() == expected
                            write(base + 0xb0, 0)
                            for _ in range(256):
                                write(base + 0xb4, 0x00ff00)
                            write(base + 0x50, 0x03000200)
                            assert screen_pixel() == b'\0\xff\0', 'Native palette mode was not applied'
                            surface = surface_identity()
                            write(base + 0x224, 4096)
                            assert screen_pixel() == b'\0\xff\0'
                            assert surface_identity() == surface, 'Palette page flip recreated the shadow surface'
                            write(base + 0x224, 0)
                            assert screen_pixel() == b'\0\xff\0'
                            assert surface_identity() == surface, 'Palette return flip recreated the shadow surface'
                            write(base + 0x50, 0x03000600)
                            assert screen_pixel() == expected, 'Native mode change reused the palette surface'
                            write(0x90000000, 0xffffffff)
                            assert screen_pixel() == b'\xff\xff\xff', 'Native CPU write was not displayed'
                            surface = surface_identity()
                            write(base + 0x224, 4096)
                            assert screen_pixel() == expected, 'Native page flip reused the previous buffer'
                            assert surface_identity() == surface, 'Native same-extent flip recreated the surface'
                            write(base + 0x224, 0)
                            assert screen_pixel() == b'\xff\xff\xff'
                            assert surface_identity() == surface, 'Native return flip recreated the surface'
                            print('PASS: native Radeon handoff preserves CPU/GPU, palette and page-flip scanout freshness')
                            assert screen_pixel() == b'\xff\xff\xff'  # Cache the settled flip range.
                            copies = copy_counter()
                            write(0x91e00000, 0x78563412)
                            assert screen_pixel() == b'\xff\xff\xff'
                            copies = no_new_copies(copies)
                            # Draw a small offscreen target while the front buffer stays white.
                            write(0x90800000, 0xffffffff)
                            write(base + 0x4e28, 0x90800000)
                            write(base + 0x43e4, (1440 + 31) | ((1440 + 31) << 13))
                            request(f'write {base + 0x1000:#x} {len(words) * 4} 0x{packet}')
                            deadline = time.monotonic() + 10
                            while read(base + 0xe40) & (1 << 31):
                                assert time.monotonic() < deadline
                            assert read(0x90800000) == word, 'Offscreen GPU draw did not reach VRAM'
                            assert screen_pixel() == b'\xff\xff\xff'
                            copies = no_new_copies(copies)
                            write(0x90000000, 0x44332211)
                            assert screen_pixel() == b'\x33\x22\x11', 'Front write was lost after offscreen updates'
                            after = copy_counter()
                            if copies is not None:
                                previous = copies[1] if copies[0] == after[0] else 0
                                assert after[1] > previous, 'Front write did not refresh the display'
                            print('PASS: offscreen CPU/GPU updates preserve front-buffer freshness' +
                                  (' without extra scanout copies' if check_copies else ''))
                            if check_surface:
                                # Changing actual geometry still replaces the shadow surface.
                                surface = surface_identity()
                                horizontal = read(base + 0x200)
                                write(base + 0x200, horizontal ^ (1 << 16))
                                assert screen_pixel() == b'\x33\x22\x11'
                                assert surface_identity() != surface, 'Width change reused the old surface'
                                # Rare 24-bpp direct scanout must rebind its VRAM pointer on flips.
                                write(base + 0x50, 0x03000500)
                                screen_pixel()
                                surface = surface_identity()
                                write(base + 0x224, 4096)
                                screen_pixel()
                                assert surface_identity() != surface, 'Direct scanout flip kept its old VRAM pointer'
                                print('PASS: shadow surfaces survive same-extent flips; mode/width and direct scanout still rebind')
                            if check_snapshot:
                                snapshot_boundaries()
        finally:
            vm.terminate()
            try:
                vm.wait(timeout=10)
            except subprocess.TimeoutExpired:
                vm.kill(); vm.wait(timeout=5)
