#!/usr/bin/env python3
"""Time the desktop paint: click OK on the language dialog, then sample the
screen until it stops changing.

    python3 tools/morphos-desktop-timing.py --mode fork|official --label NAME

Reports when the screen last changed after the click (the desktop is done),
how many distinct frames were seen, and leaves screenshots in vm/morphos/.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from morphos_bench_lib import launch, Qmp  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / 'vm/morphos'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('fork', 'official'), required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--display', default='gtk,gl=on')
    parser.add_argument('--seconds', type=float, default=40)
    parser.add_argument('--net', action='store_true', help='user networking with a sungem NIC, as start.sh')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    extra = ['-device', 'usb-tablet'] if args.mode == 'official' else []
    if args.net:
        extra += ['-netdev', 'user,id=n0', '-device', 'sungem,netdev=n0']
    vm, qmp, log = launch(args.mode, args.label, args.display, extra)
    try:
        width, height, title_y = qmp.wait_for_dialog()
        t_dialog = time.monotonic()
        mmio = qmp.mmio_base()

        def pointer():
            return qmp.hw_cursor(mmio)

        def abs_move(x, y):
            qmp.cmd('input-send-event', events=[
                {'type': 'abs', 'data': {'axis': 'x', 'value': x * 32767 // width}},
                {'type': 'abs', 'data': {'axis': 'y', 'value': y * 32767 // height}}])

        # OK button: bottom centre of the dialog (feedback from the hardware cursor)
        tx, ty = width // 2, title_y + 766
        sx, sy = tx, ty - 40
        for _ in range(10):
            abs_move(sx, sy)
            time.sleep(0.3)
            px, py = pointer()
            if abs(px - tx) < 3 and abs(py - ty) < 3:
                break
            sx += int((tx - px) * 0.9)
            sy += int((ty - py) * 0.9)
        time.sleep(1)
        qmp.click()
        t0 = time.monotonic()
        last_hash, last_change, frames = None, 0.0, 0
        shots = []
        deadline = t0 + args.seconds
        while time.monotonic() < deadline:
            path = OUT / f'desktop-{args.label}-sample.ppm'
            qmp.cmd('screendump', filename=str(path))
            data = path.read_bytes()
            # skip the menu bar (its clock changes every second)
            header_end = data.index(b'255\n') + 4
            h = hashlib.md5(data[header_end + 24 * width * 3:]).hexdigest()
            now = time.monotonic() - t0
            if h != last_hash:
                frames += 1
                last_change = now
                last_hash = h
                if len(shots) < 12:
                    keep = OUT / f'desktop-{args.label}-{now:05.2f}.ppm'
                    keep.write_bytes(data)
                    shots.append(keep.name)
            time.sleep(0.25)
        result = {'label': args.label, 'mode': args.mode, 'dialog_after_boot_s': None,
                  'last_change_s': round(last_change, 2), 'distinct_frames': frames,
                  'shots': shots}
        (OUT / f'desktop-{args.label}.json').write_text(json.dumps(result, indent=1))
        print(f'{args.label}: desktop last changed {last_change:.2f} s after the OK click, '
              f'{frames} distinct frames in {args.seconds:.0f} s')
    finally:
        qmp.close()
        vm.terminate()
        try:
            vm.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vm.kill()


if __name__ == '__main__':
    main()
