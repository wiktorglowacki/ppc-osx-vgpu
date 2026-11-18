#!/usr/bin/env python3
"""Boot to the Ambient desktop, run a guest command through Ambient's
"Execute command" requester and screenshot the result.

    python3 tools/morphos-guest-run.py --mode fork|official --label NAME \
        --command 'NewShell' --seconds 30

Screenshots land in vm/morphos/guest-NAME-*.ppm (every --every seconds).
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import time

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from morphos_bench_lib import launch  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / 'vm/morphos'
SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8',
           '(': '9', ')': '0', '_': 'minus', '+': 'equal', ':': 'semicolon', '"': 'apostrophe',
           '<': 'comma', '>': 'dot', '?': 'slash', '{': 'bracket_left', '}': 'bracket_right',
           '|': 'backslash', '~': 'grave_accent'}
PLAIN = {' ': 'spc', '-': 'minus', '=': 'equal', ';': 'semicolon', "'": 'apostrophe', ',': 'comma',
         '.': 'dot', '/': 'slash', '[': 'bracket_left', ']': 'bracket_right', '\\': 'backslash',
         '`': 'grave_accent'}


def ram_strings(qmp, label, text):
    dump = OUT / f'guest-{label}-ram.bin'
    qmp.conn.settimeout(300)
    qmp.cmd('pmemsave', val=0, size=2048 << 20, filename=str(dump))
    qmp.conn.settimeout(30)
    data = dump.read_bytes()
    dump.unlink()
    needle = text.encode()
    return sorted(set(m.group().decode('latin-1')
                      for m in re.finditer(rb'[\x20-\x7e]{4,200}', data) if needle in m.group()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('fork', 'official'), required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--command', required=True)
    parser.add_argument('--display', default='none')
    parser.add_argument('--seconds', type=float, default=90)
    parser.add_argument('--every', type=float, default=5)
    parser.add_argument('--click', action='append', default=None, help='x,y[r|d][@delay] to click after the app opened (r: right button, d: double click); repeatable, each delay counts from the previous click; a screenshot follows each click')
    parser.add_argument('--click-delay', type=float, default=12)
    parser.add_argument('--key', default=None, help='QEMU key name (qcode, e.g. spc, ret) to press after --key-delay seconds; a comma list (ret,down,ret) presses them 3 s apart, key@N waits N s before that key')
    parser.add_argument('--key-delay', type=float, default=14)
    parser.add_argument('--vram-dump', default=None, metavar='FILE',
                        help='at the end, save the Radeon VRAM aperture (BAR 0) to FILE')
    parser.add_argument('--log-window', default=None, metavar='START,SECONDS,MASK',
                        help="turn on QEMU's -d MASK logging (HMP 'log') START seconds after the command "
                             "was typed, for SECONDS (e.g. 6,3,int); output goes to the -D log")
    parser.add_argument('--hmp', action='append', default=[], metavar='CMD',
                        help='monitor command to run at the end (repeatable); output in vm/morphos/guest-NAME-hmp.txt')
    parser.add_argument('--ram-dump', default=None, metavar='FILE',
                        help='at the end, save the whole guest RAM (2 GB) to FILE')
    parser.add_argument('--dump-strings', default=None,
                        help='at the end, dump guest RAM and save printable strings containing this text')
    parser.add_argument('--deadline', type=float, default=0, metavar='SECONDS',
                        help='needs --dump-strings: poll the RAM every 15 s after the command and finish once the text '
                             'shows up; else after SECONDS save guest-NAME-hang.txt (registers) and exit 3')
    parser.add_argument('--perf-delay', type=float, default=0, metavar='SECONDS',
                        help='start the perf recording this long after the command (default: at once)')
    parser.add_argument('--perf', type=float, default=0, metavar='SECONDS',
                        help='perf record (dwarf call graphs) the VM for this long, starting after the command is typed; '
                             'written to vm/morphos/guest-NAME.perf')
    parser.add_argument('--perf-stat', type=float, default=0, metavar='SECONDS',
                        help='count host instructions and cycles of the VM per thread (perf stat, user space) '
                             'for this long from when the command is typed; the vCPU thread "ALL CPUs/TCG" '
                             'is the translated code. Whole-process counts carry ~7%% noise from spin-waits '
                             'and timer work that scale with wall time. Written to '
                             'vm/morphos/guest-NAME-perfstat.txt')
    parser.add_argument('--execute-key', default='meta_r',
                        help='modifier of the Execute Command hotkey (meta_r = right Amiga/Command)')
    args = parser.parse_args()
    if args.deadline and not args.dump_strings:
        parser.error('--deadline needs --dump-strings')
    extra = ['-device', 'usb-tablet'] if args.mode == 'official' else []
    vm, qmp, log = launch(args.mode, args.label, args.display, extra)

    def key(name, mods=()):
        for m in mods:
            qmp.cmd('input-send-event', events=[{'type': 'key', 'data': {'down': True, 'key': {'type': 'qcode', 'data': m}}}])
        qmp.cmd('input-send-event', events=[{'type': 'key', 'data': {'down': True, 'key': {'type': 'qcode', 'data': name}}}])
        time.sleep(0.05)
        qmp.cmd('input-send-event', events=[{'type': 'key', 'data': {'down': False, 'key': {'type': 'qcode', 'data': name}}}])
        for m in reversed(mods):
            qmp.cmd('input-send-event', events=[{'type': 'key', 'data': {'down': False, 'key': {'type': 'qcode', 'data': m}}}])
        time.sleep(0.06)

    def type_text(text):
        for ch in text:
            if ch.isalpha():
                key(ch.lower(), ('shift',) if ch.isupper() else ())
            elif ch.isdigit():
                key(ch)
            elif ch in SHIFTED:
                key(SHIFTED[ch], ('shift',))
            elif ch in PLAIN:
                key(PLAIN[ch])
            else:
                raise ValueError(f'cannot type {ch!r}')

    try:
        # MORPHOS_BOOT=hd: the installed system comes straight to the desktop
        hd = os.environ.get('MORPHOS_BOOT') == 'hd'
        if hd:
            width, height = qmp.wait_for_desktop()
        else:
            width, height, title_y = qmp.wait_for_dialog()
        mmio = qmp.mmio_base()

        def abs_move(x, y):
            qmp.cmd('input-send-event', events=[
                {'type': 'abs', 'data': {'axis': 'x', 'value': x * 32767 // width}},
                {'type': 'abs', 'data': {'axis': 'y', 'value': y * 32767 // height}}])

        if not hd:
            tx, ty = width // 2, title_y + 766
            sx, sy = tx, ty - 40
            for _ in range(10):
                abs_move(sx, sy)
                time.sleep(0.3)
                px, py = qmp.hw_cursor(mmio)
                if abs(px - tx) < 3 and abs(py - ty) < 3:
                    break
                sx += int((tx - px) * 0.9)
                sy += int((ty - py) * 0.9)
            time.sleep(0.5)
            qmp.click()
            time.sleep(8)                  # the desktop and FirstFlight
        abs_move(width // 2, height - 60)  # over the desktop, not a window
        time.sleep(0.3)
        qmp.click()                        # focus the desktop
        time.sleep(0.5)
        if args.command:                   # '' skips the requester (clicks only)
            key('e', (args.execute_key,))  # Ambient: Execute command...
            time.sleep(2)
            key('x', (args.execute_key,))  # clear the remembered last command
            qmp.screen(OUT / f'guest-{args.label}-requester.ppm')
            type_text(args.command)
            time.sleep(0.5)
            key('ret')
        # --click X,Y[@DELAY] may repeat: each waits DELAY seconds (default
        # --click-delay) after the previous one, then moves there and clicks
        for i, spec in enumerate(args.click or []):
            pos, _, delay = spec.partition('@')
            time.sleep(float(delay) if delay else args.click_delay)
            button, times = 'left', 1
            if pos.endswith('r'):
                button, pos = 'right', pos[:-1]
            elif pos.endswith('d'):
                times, pos = 2, pos[:-1]
            cx, cy = (int(v) for v in pos.split(','))
            sx, sy = cx, cy
            for _ in range(10):
                abs_move(sx, sy)
                time.sleep(0.3)
                px, py = qmp.hw_cursor(mmio)
                if abs(px - cx) < 3 and abs(py - cy) < 3:
                    break
                sx += int((cx - px) * 0.9)
                sy += int((cy - py) * 0.9)
            qmp.screen(OUT / f'guest-{args.label}-before-click{i if i else ""}.ppm')
            qmp.click(button, times)
            time.sleep(2)
            qmp.screen(OUT / f'guest-{args.label}-after-click{i}.ppm')
        stat = None
        if args.perf_stat:
            # --per-thread: the vCPU thread ("ALL CPUs/TCG" with debug-threads)
            # is the translated code; GPU spin-waits and timer work sit in others
            stat = subprocess.Popen(['perf', 'stat', '--per-thread', '-x', ',',
                                     '-e', 'instructions:u,cycles:u,task-clock',
                                     '-p', str(vm.pid), '-o', str(OUT / f'guest-{args.label}-perfstat.txt'),
                                     '--', 'sleep', str(args.perf_stat)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        perf = None
        if args.perf:
            cg = '' if os.environ.get('MORPHOS_PERF_CALLGRAPH') == '0' else '--call-graph dwarf,4096 '
            perf = subprocess.Popen(['bash', '-c', f'sleep {args.perf_delay}; exec perf record --user-callchains {cg}-e cycles:u -F 999 -p {vm.pid} '
                                     f'-o {OUT / f"guest-{args.label}.perf"} -- sleep {args.perf}'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.monotonic()
        n = 0
        found = None
        polled = t0
        while time.monotonic() - t0 < (args.deadline or args.seconds):
            if args.key and args.key_delay >= 0 and time.monotonic() - t0 >= args.key_delay:
                for i, k in enumerate(args.key.split(',')):
                    k, _, gap = k.partition('@')
                    if i or gap:
                        time.sleep(float(gap or 3))
                    key(k)
                qmp.screen(OUT / f'guest-{args.label}-after-key.ppm')
                args.key_delay = -1
            if args.log_window:
                lw_start, lw_len, lw_mask = args.log_window.split(',')
                lw_start, lw_len = float(lw_start), float(lw_len)
                wait = lw_start - (time.monotonic() - t0)
                if 0 <= wait < args.every:
                    time.sleep(wait)
                    qmp.hmp(f'log {lw_mask}')
                    time.sleep(lw_len)
                    qmp.hmp('log none')
                    args.log_window = None
                    continue
            time.sleep(args.every)
            n += 1
            qmp.screen(OUT / f'guest-{args.label}-{int(time.monotonic() - t0):03d}s.ppm')
            if args.deadline and time.monotonic() - polled >= 15:
                found = ram_strings(qmp, args.label, args.dump_strings)
                polled = time.monotonic()
                if found:
                    break
        if args.deadline and not found:
            found = ram_strings(qmp, args.label, args.dump_strings)
        hang = args.deadline and not found
        if hang:
            with (OUT / f'guest-{args.label}-hang.txt').open('w') as f:
                for i in range(3):
                    f.write(f'### info registers ({i})\n{qmp.hmp("info registers")}\n')
                    time.sleep(3)
                f.write(f'### info cpus\n{qmp.hmp("info cpus")}\n')
            qmp.screen(OUT / f'guest-{args.label}-hang.ppm')
            print(f'{args.label}: no {args.dump_strings!r} after {args.deadline:g} s, registers in guest-{args.label}-hang.txt')
        print(f'{args.label}: {n} screenshots in vm/morphos/guest-{args.label}-*.ppm')
        if stat:
            stat.wait()
            print(f'perf stat: vm/morphos/guest-{args.label}-perfstat.txt')
        if perf:
            perf.wait()
            print(f'perf data: vm/morphos/guest-{args.label}.perf')
        if args.hmp:
            out = OUT / f'guest-{args.label}-hmp.txt'
            with out.open('w') as f:
                for cmd in args.hmp:
                    f.write(f'### {cmd}\n{qmp.hmp(cmd)}\n')
            print(f'monitor output in {out}')
        if args.vram_dump:
            bar = None
            for bus in qmp.cmd('query-pci'):
                for dev in bus['devices']:
                    if dev['id']['vendor'] == 0x1002:
                        for region in dev['regions']:
                            if region['bar'] == 0 and region['type'] == 'memory':
                                bar = (region['address'], region['size'])
            assert bar, 'Radeon BAR 0 not found'
            qmp.cmd('pmemsave', val=bar[0], size=bar[1], filename=str(Path(args.vram_dump).resolve()))
            print(f'VRAM ({bar[1] >> 20} MB at 0x{bar[0]:x}) saved to {args.vram_dump}')
        if args.ram_dump:
            qmp.cmd('pmemsave', val=0, size=2048 << 20, filename=str(Path(args.ram_dump).resolve()))
            print(f'guest RAM saved to {args.ram_dump}')
        if args.dump_strings:
            found = found or ram_strings(qmp, args.label, args.dump_strings)
            (OUT / f'guest-{args.label}-strings.txt').write_text('\n'.join(found))
            print(f'{len(found)} strings containing {args.dump_strings!r} saved')
        if hang:
            sys.exit(3)
    finally:
        qmp.close()
        vm.terminate()
        try:
            vm.wait(timeout=30)
        except subprocess.TimeoutExpired:
            vm.kill()


if __name__ == '__main__':
    main()
