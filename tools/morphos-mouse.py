#!/usr/bin/env python3
"""Switch a running MorphOS VM between the absolute USB tablet and the relative mouse.

    tools/morphos-mouse.py rel     games with mouse-look (Quake III, RTCW): click into
                                   the window to capture the mouse, Ctrl+Alt+G (macOS:
                                   Ctrl+Option+G) releases it
    tools/morphos-mouse.py abs     back to the tablet (desktop, no capture)
    tools/morphos-mouse.py         show the mice

Talks to every VM started by morphos-run.sh (/tmp/morphos-qmp-<pid>.sock).
"""
import glob, json, re, socket, sys


def hmp(path, line):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(path)
    f = s.makefile('rw')
    f.readline()
    for cmd in ({'execute': 'qmp_capabilities'},
                {'execute': 'human-monitor-command', 'arguments': {'command-line': line}}):
        f.write(json.dumps(cmd) + '\n')
        f.flush()
        while True:
            r = json.loads(f.readline())
            if 'return' in r or 'error' in r:
                break
    return r.get('return', '')


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else None
    socks = glob.glob('/tmp/morphos-qmp-*.sock')
    if not socks:
        sys.exit('no running VM (no /tmp/morphos-qmp-*.sock)')
    for path in socks:
        try:
            mice = hmp(path, 'info mice')
        except OSError:
            continue                      # stale socket of a VM that is gone
        # "* Mouse #2: QEMU HID Tablet (absolute)" - '*' marks the active one
        found = re.findall(r'^\s*(\*?)\s*Mouse #(\d+): (.*)$', mice, re.M)
        if want in ('rel', 'abs'):
            pick = [n for _, n, name in found if ('(absolute)' in name) == (want == 'abs')]
            if not pick:
                print(f'{path}: no {want} mouse in:\n{mice}')
                continue
            hmp(path, f'mouse_set {pick[0]}')
            mice = hmp(path, 'info mice')
        print(f'{path}:\n{mice.rstrip()}')


if __name__ == '__main__':
    main()
