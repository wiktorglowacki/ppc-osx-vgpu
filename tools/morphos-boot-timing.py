#!/usr/bin/env python3
"""Time the boot phases: QEMU start -> first picture (boot logo) -> language
dialog, polling the screen every 0.2 s.

    python3 tools/morphos-boot-timing.py --mode fork|official --label NAME [--vram 64]

Prints when the screen first shows something, when the logo goes away and
when the dialog is up; keeps the first frame of each phase in vm/morphos/.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
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
    parser.add_argument('--vram', type=int, default=None, help='fork only: VRAM MB (launcher MORPHOS_VRAM_MB)')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--ramlog', action='store_true',
                        help="boot with 'EDEBUGFLAGS=init ramdebug' and save the guest's timestamped log "
                             "(vm/morphos/boot-NAME-init.log) once the dialog is up")
    args = parser.parse_args()
    if args.ramlog:
        os.environ['MORPHOS_BOOT_ARGS'] = 'EDEBUGFLAGS=init ramdebug'
    if args.vram:
        os.environ['MORPHOS_VRAM_MB'] = str(args.vram)
    extra = ['-device', 'usb-tablet'] if args.mode == 'official' else []
    t_start = time.monotonic()
    vm, qmp, log = launch(args.mode, args.label, args.display, extra)
    phases = {}
    shots = []
    last = None
    changes = 0
    try:
        while time.monotonic() - t_start < args.timeout:
            path = OUT / f'boot-{args.label}-sample.ppm'
            try:
                width, height, pixels = qmp.screen(path)
            except Exception:
                time.sleep(0.2)
                continue
            now = round(time.monotonic() - t_start, 2)
            h = hashlib.md5(pixels).hexdigest()
            if h != last:
                changes += 1
                last = h
            # black / uniform screen = nothing shown yet
            sample = pixels[::4096]
            lit = sum(1 for b in sample if b > 32) / max(len(sample), 1)
            if 'picture' not in phases and lit > 0.02:
                phases['picture'] = now
                (OUT / f'boot-{args.label}-picture.ppm').write_bytes(path.read_bytes())
            if 'picture' in phases and 'dialog' not in phases:
                if Qmp.dialog_title_y(width, height, pixels) is not None:
                    phases['dialog'] = now
                    (OUT / f'boot-{args.label}-dialog.ppm').write_bytes(path.read_bytes())
                    break
            time.sleep(0.2)
        if args.ramlog and 'dialog' in phases:
            import re
            ram_mb = qmp.cmd('query-memory-size-summary')['base-memory'] >> 20
            dump = OUT / f'boot-{args.label}-ram.bin'
            qmp.cmd('pmemsave', val=0, size=ram_mb << 20, filename=str(dump))
            lines = re.findall(rb'\d+\.\d{3}\| [^\n\0]{1,300}', dump.read_bytes())
            dump.unlink()
            (OUT / f'boot-{args.label}-init.log').write_text(b'\n'.join(lines).decode('latin-1'))
            phases['ramlog_lines'] = len(lines)
        phases['resolution'] = f'{width}x{height}'
        phases['screen_changes'] = changes
        print(f'{args.label}: picture at {phases.get("picture")} s, dialog at {phases.get("dialog")} s '
              f'(logo shown {round(phases["dialog"] - phases["picture"], 2) if "dialog" in phases and "picture" in phases else "?"} s), '
              f'{changes} screen changes, {phases["resolution"]}')
        (OUT / f'boot-{args.label}.json').write_text(json.dumps(phases, indent=1))
    finally:
        qmp.close()
        vm.terminate()
        try:
            vm.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vm.kill()


if __name__ == '__main__':
    main()
