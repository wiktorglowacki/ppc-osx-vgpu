#!/usr/bin/env python3
"""Bounded ISO-only Radeon test; requires the default Ambient menu bar.

    python3 tools/morphos-smoke.py [--display gtk]

Uses the launcher's MORPHOS_ISO/PPCOSX_QEMU overrides. Saves serial/GPU
logs, PCI identity, framebuffer and draw log in vm/morphos/. This checks the
fresh ISO's default desktop appearance; TinyGL still needs a separate test.
"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from morphos_bench_lib import ppm  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--display', choices=('none', 'gtk'), default='none')
    args = parser.parse_args()
    out = REPO / 'vm/morphos'
    out.mkdir(parents=True, exist_ok=True)
    sock = out / f'smoke-{os.getpid()}.sock'
    drawlog = out / f'draw-{os.getpid()}.log'
    deadline = time.monotonic() + 90
    env = dict(os.environ, R300_DRAWLOG=str(drawlog))
    with (out / 'serial.log').open('w') as log:
        vm_args = [
            str(REPO / 'tools/morphos-run.sh'), '-display', args.display,
            '-monitor', 'none', '-qmp', f'unix:{sock},server=on,wait=off',
        ]
        vm = subprocess.Popen(vm_args, env=env, stdout=log, stderr=log)
        try:
            while not sock.exists():
                if vm.poll() is not None:
                    raise RuntimeError(f'QEMU exited {vm.returncode}; see {out / "serial.log"}')
                if time.monotonic() >= deadline:
                    raise RuntimeError('QMP startup timed out')
                time.sleep(.1)
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(5)
                conn.connect(str(sock))
                with conn.makefile('rw') as stream:
                    json.loads(stream.readline())

                    def qmp(command, **arguments):
                        stream.write(json.dumps({'execute': command, 'arguments': arguments}) + '\n')
                        stream.flush()
                        while True:
                            reply = json.loads(stream.readline())
                            if 'error' in reply:
                                raise RuntimeError(reply['error'])
                            if 'return' in reply:
                                return reply['return']

                    qmp('qmp_capabilities')
                    for _ in range(45):
                        time.sleep(1)
                        if vm.poll() is not None:
                            raise RuntimeError(f'QEMU exited {vm.returncode}; see {out / "serial.log"}')
                    pci = qmp('human-monitor-command', **{'command-line': 'info pci'})
                    (out / 'pci.txt').write_text(pci)
                    assert '1002:4e44' in pci, 'Radeon 9700 PCI identity missing'
                    image = out / 'screen.ppm'
                    qmp('screendump', filename=str(image))
                    width, height, pixels = ppm(image.read_bytes())
                    count = width * height
                    assert len(pixels) == count * 3
                    green = sum(pixels[i:i+3] == b'\x00\xff\x00' for i in range(0, len(pixels), 3))
                    print(f'Radeon PCI identity confirmed; firmware-green pixels: {green / count:.1%}')
                    print(f'Evidence: {out}; 3D draw log: {drawlog if drawlog.exists() else "no draws"}')
                    if green > count * .9:
                        raise RuntimeError('FAIL: MorphOS has not replaced the firmware screen')
                    row = int(width) * 8 * 3
                    bar = [pixels[row + x * 3:row + x * 3 + 3]
                           for x in range(40, int(width) - 40, 8)]
                    light = sum(min(rgb) > 160 and max(rgb) - min(rgb) < 40 for rgb in bar)
                    if light < len(bar) * .8:
                        raise RuntimeError('FAIL: default Ambient menu bar is not rendered')
                    if args.display == 'gtk':
                        # The ISO opens a modal language dialog: select another visible row.
                        mouse = next(m for m in qmp('query-mice') if m['current'])
                        if mouse['absolute']:
                            raise RuntimeError(f'FAIL: expected launcher relative mouse: {mouse}')
                        mmio = int(re.search(r'BAR2:.*memory at (0x[0-9a-f]+)', pci)[1], 16)
                        x, y = int(width) // 2, int(height) // 2 - 220
                        for _ in range(150):
                            position = qmp('human-monitor-command', **{'command-line': f'xp /1wx {mmio + 0x264:#x}'})
                            raw = bytes.fromhex(re.search(r': 0x([0-9a-f]{8})', position)[1])
                            value = int.from_bytes(raw, 'little')
                            px, py = (value >> 16) & 0x3fff, value & 0xfff
                            if abs(px - x) < 35 and abs(py - y) < 6:
                                break
                            events = []
                            for axis, error in (('x', x - px), ('y', y - py)):
                                delta = int(error / 16)
                                if error and not delta:
                                    delta = 1 if error > 0 else -1
                                events.append({'type': 'rel', 'data': {'axis': axis, 'value': max(-12, min(12, delta))}})
                            qmp('input-send-event', events=events)
                            time.sleep(.08)
                        else:
                            raise RuntimeError(f'FAIL: guest pointer missed language list: {(px, py)}')
                        qmp('input-send-event', events=[{
                            'type': 'btn', 'data': {'button': 'left', 'down': True}}])
                        time.sleep(.2)
                        qmp('input-send-event', events=[{
                            'type': 'btn', 'data': {'button': 'left', 'down': False}}])
                        time.sleep(.3)
                        interaction = out / 'screen-interaction.ppm'
                        qmp('screendump', filename=str(interaction))
                        changed = ppm(interaction.read_bytes())[2]
                        # Compare the dialog, excluding clocks and other transient UI.
                        left, right = x - 240, x + 240
                        # The original English highlight must disappear; exclude
                        # the cursor at the newly selected row from this comparison.
                        top, bottom = int(height) // 2 - 327, int(height) // 2 - 313
                        before = b''.join(pixels[(row * int(width) + left) * 3:(row * int(width) + right) * 3]
                                          for row in range(top, bottom))
                        after = b''.join(changed[(row * int(width) + left) * 3:(row * int(width) + right) * 3]
                                         for row in range(top, bottom))
                        if after == before:
                            raise RuntimeError('FAIL: language dialog did not redraw after input')
                        print(f'GTK survived 45 seconds and language selection: {interaction}')
                    log.flush()
                    if 'Validation Error:' in (out / 'serial.log').read_text():
                        raise RuntimeError('FAIL: Vulkan validation reported an API error')
                    print('Default Ambient menu bar rendered; TinyGL requires a separate test.')
        finally:
            if vm.poll() is None:
                vm.terminate()
                try:
                    vm.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    vm.kill()
                    vm.wait(timeout=5)
            sock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, AssertionError, OSError, ValueError) as error:
        raise SystemExit(str(error))
