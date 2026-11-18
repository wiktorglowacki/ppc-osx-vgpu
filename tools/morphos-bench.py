#!/usr/bin/env python3
"""Reproducible MorphOS desktop workload: idle, then drag the language dialog.

    python3 tools/morphos-bench.py --mode fork|official --label NAME [--seconds 10]

Boots the ISO, waits for the language/keymap dialog, measures 5 s idle, then
drags the dialog's title bar left and right for --seconds with relative mouse
events through QMP.  Reports QEMU process and vCPU-thread CPU time, `info jit`
deltas (TB count, invalidations, IO recompiles when the build counts them) and,
for the fork, the PPCGPU_TRAFFIC MMIO rates.  Evidence lands in vm/morphos/.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from morphos_bench_lib import Qmp  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO.parent
OUT = REPO / 'vm/morphos'
CLK = os.sysconf('SC_CLK_TCK')


def official_argv(sock, iso):
    return ['/usr/bin/qemu-system-ppc', '-machine', 'mac99,via=pmu', '-m', '2048',
            '-vga', 'none', '-device', 'ati-vga,guest_hwcursor=true', '-cpu', 'G4',
            '-net', 'none', '-cdrom', str(iso), '-boot', 'd',
            '-prom-env', 'boot-device=cd:,\\mac_ppc32\\boot.img',
            '-bios', str(BUNDLE / 'openbios-qemu.elf'), '-serial', 'none',
            '-display', 'gtk', '-monitor', 'none',
            '-qmp', f'unix:{sock},server=on,wait=off']


def ps_threads(pid):
    """macOS (no /proc): {thread index: cpu seconds} from `ps -M` (threads are unnamed there)."""
    out = subprocess.run(['ps', '-M', '-p', str(pid)], capture_output=True, text=True).stdout
    secs = lambda t: sum(float(v) * 60 ** i for i, v in enumerate(reversed(t.split(':'))))
    return {f't{i}': secs(m[1]) + secs(m[2])
            for i, m in enumerate(re.finditer(r'(\d+(?::\d+)+\.\d+)\s+(\d+(?::\d+)+\.\d+)', out))}


def cpu_times(pid):
    """(process cpu seconds, vcpu thread cpu seconds, {thread: cpu seconds}, vcpu sched)."""
    if not Path('/proc').exists():
        # ponytail: macOS reports the busiest thread as the vCPU and no scheduler stats
        threads = ps_threads(pid)
        return sum(threads.values()), max(threads.values(), default=0.0), threads, {}
    def ticks(path):
        fields = Path(path).read_text().rsplit(')', 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / CLK
    proc = ticks(f'/proc/{pid}/stat')
    vcpu, threads, sched = 0.0, {}, {}
    for task in Path(f'/proc/{pid}/task').iterdir():
        try:
            comm = (task / 'comm').read_text().strip()
            t = ticks(task / 'stat')
            threads[comm] = threads.get(comm, 0.0) + t
            if comm.split('/')[-1].startswith('TCG'):
                vcpu = t
                # schedstat: running ns, runqueue-wait ns, timeslices
                run_ns, wait_ns, slices = (task / 'schedstat').read_text().split()
                status = (task / 'status').read_text()
                sched = {'run_s': int(run_ns) / 1e9, 'runqueue_wait_s': int(wait_ns) / 1e9,
                         'timeslices': int(slices),
                         'voluntary_switches': int(re.search(r'voluntary_ctxt_switches:\s+(\d+)', status)[1]),
                         'involuntary_switches': int(re.search(r'nonvoluntary_ctxt_switches:\s+(\d+)', status)[1])}
        except OSError:
            pass
    return proc, vcpu, threads, sched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('fork', 'official'), required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--idle', type=float, default=5)
    parser.add_argument('--settle', type=float, default=25,
                        help='seconds to wait after the dialog appears before measuring')
    parser.add_argument('--rate', type=float, default=25, help='mouse events per second')
    parser.add_argument('--perf', action='store_true', help='perf record the VM (all threads, call graphs) during the drag: vm/morphos/bench-LABEL.perf')
    parser.add_argument('--abs', action='store_true',
                        help='drag with absolute (USB tablet) pointer events; relative events need a relative mouse (ADB on mac99)')
    parser.add_argument('--trace-seconds', type=float, default=1,
                        help='trace memory_region_ops_* for this long during idle (0 = off)')
    parser.add_argument('--display', default='none', help='QEMU -display argument (none by default: the GTK display scales the frame with cairo on the main thread and dominates the profile; sdl,gl=on measures the real present path and opens a window)')
    parser.add_argument('--ramlog', action='store_true',
                        help='boot with EDEBUGFLAGS=init ramdebug and extract the RAM debug log '
                             'from guest memory after settling')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sock = OUT / f'bench-{args.label}.sock'
    sock.unlink(missing_ok=True)
    stderr_log = OUT / f'bench-{args.label}-stderr.log'
    iso = Path(os.environ.get('MORPHOS_ISO', BUNDLE / 'morphos-3.21.iso'))
    env = dict(os.environ, PPCGPU_TRAFFIC='1')
    env['MORPHOS_ISO'] = str(iso)    # fork and official boot the same ISO
    env.pop('MORPHOS_BOOT', None)    # the drag needs the CD's language dialog
    env.setdefault('MORPHOS_BOOT_ARGS', 'EDEBUGFLAGS=init ramdebug' if args.ramlog else '')
    qemu_log = OUT / f'bench-{args.label}-gpu.log'
    qemu_log.unlink(missing_ok=True)
    if args.mode == 'fork':
        argv = [str(REPO / 'tools/morphos-run.sh'), '-display', args.display, '-monitor', 'none',
                '-qmp', f'unix:{sock},server=on,wait=off']
    else:
        argv = official_argv(sock, iso)
        argv[argv.index('-display') + 1] = args.display
    argv += ['-name', f'bench,debug-threads=on', '-D', str(qemu_log)]
    result = {'label': args.label, 'mode': args.mode, 'argv': argv,
              'vram_mb': env.get('MORPHOS_VRAM_MB'), 'boot_args': env.get('MORPHOS_BOOT_ARGS')}
    t_start = time.monotonic()
    with stderr_log.open('w') as log:
        vm = subprocess.Popen(argv, env=env, stdout=log, stderr=log)
        monitor = None
        try:
            while not sock.exists():
                if vm.poll() is not None:
                    raise RuntimeError(f'QEMU exited {vm.returncode}; see {stderr_log}')
                if time.monotonic() - t_start > 60:
                    raise RuntimeError('QMP startup timed out')
                time.sleep(.1)
            monitor = Qmp(sock)
            qmp = monitor.cmd
            hmp = monitor.hmp

            def screen(name):
                path = OUT / f'bench-{args.label}-{name}.ppm'
                return monitor.screen(path)

            # Wait for the language dialog.
            while True:
                if vm.poll() is not None:
                    raise RuntimeError(f'QEMU exited {vm.returncode}; see {stderr_log}')
                if time.monotonic() - t_start > 180:
                    raise RuntimeError('language dialog did not appear')
                time.sleep(2)
                width, height, pixels = screen('boot')
                title_y = Qmp.dialog_title_y(width, height, pixels)
                if title_y is not None:
                    break
            result['boot_seconds'] = round(time.monotonic() - t_start, 2)
            result['resolution'] = [width, height]
            mtree = hmp('info mtree')
            (OUT / f'bench-{args.label}-mtree.txt').write_text(mtree)
            ap0 = [l.strip() for l in mtree.splitlines() if 'vram-ap0' in l]
            result['vram_ap0'] = ap0[:1]

            def trace_regions(seconds):
                """Count MMIO accesses per memory region over a short window."""
                try:
                    for event in ('memory_region_ops_read', 'memory_region_ops_write'):
                        qmp('trace-event-set-state', name=event, enable=True)
                except RuntimeError as error:
                    result['mmio_regions_per_s'] = f'unavailable: {error}'
                    return
                time.sleep(seconds)
                for event in ('memory_region_ops_read', 'memory_region_ops_write'):
                    qmp('trace-event-set-state', name=event, enable=False)
                counts = {}
                for line in qemu_log.read_text(errors='replace').splitlines():
                    if line.startswith('memory_region_ops_'):
                        kind = 'read' if 'ops_read' in line else 'write'
                        name = line.rsplit('name ', 1)[-1].strip()
                        size = re.search(r'size (\d+)', line)
                        key = f'{name} {kind}{size[1] if size else ""}'
                        counts[key] = counts.get(key, 0) + 1
                top = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
                result['mmio_regions_per_s'] = {k: round(v / seconds) for k, v in top}
                print('mmio regions/s:', json.dumps(result['mmio_regions_per_s']), flush=True)

            def guest_errors(seconds):
                """Count invalid/unassigned guest accesses per address over a window."""
                mark = qemu_log.stat().st_size if qemu_log.exists() else 0
                hmp('log invalid_mem')
                time.sleep(seconds)
                hmp('log none')
                counts = {}
                with qemu_log.open('rb') as f:
                    f.seek(mark)
                    for line in f.read().decode(errors='replace').splitlines():
                        m = re.match(r'Invalid (\w+) at addr (0x[0-9a-fA-F]+), size (\d+), region \'?([^\',]*)', line)
                        if m:
                            key = f'{m[1]} {int(m[2], 16) & ~0xfff:#x} size{m[3]} {m[4]}'
                            counts[key] = counts.get(key, 0) + 1
                top = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
                result['guest_errors_per_s'] = {k: round(v / seconds) for k, v in top}
                print('guest errors/s:', json.dumps(result['guest_errors_per_s']), flush=True)

            result['threads'] = (sorted({(t / 'comm').read_text().strip()
                                         for t in Path(f'/proc/{vm.pid}/task').iterdir()})
                                 if Path('/proc').exists() else sorted(ps_threads(vm.pid)))
            time.sleep(args.settle)
            screen('settled')
            if args.ramlog:
                # MorphOS's RAM debug buffer: dump guest RAM and keep its "T.TTT| text" lines.
                dump = OUT / f'bench-{args.label}-ram.bin'
                qmp('pmemsave', val=0, size=2048 << 20, filename=str(dump))
                data = dump.read_bytes()
                dump.unlink()
                lines = re.findall(rb'\d+\.\d{3}\| [^\n\0]{1,300}', data)
                text = b'\n'.join(lines).decode('latin-1')
                (OUT / f'bench-{args.label}-init.log').write_text(text)
                result['ramlog'] = {
                    'lines': len(lines),
                    'int_timeouts': text.count('timeout waiting for int'),
                    'dma_failed': text.count('Failed to initialise Radeon DMA'),
                    'cce_failed': text.count('Failed to allocate CCE DMA memory'),
                }
                print('ramlog:', json.dumps(result['ramlog']), flush=True)
            if args.trace_seconds > 0:
                trace_regions(args.trace_seconds)
                guest_errors(args.trace_seconds)
            mmio = monitor.mmio_base()

            def pointer():
                raw = hmp(f'xp /1wx {mmio + 0x264:#x}')
                value = int.from_bytes(bytes.fromhex(re.search(r': 0x([0-9a-f]{8})', raw)[1]), 'little')
                return (value >> 16) & 0x3fff, value & 0xfff

            def jit():
                text = hmp('info jit')
                stats = {}
                for key, pat in (('tb_count', r'TB count\s+(\d+)'),
                                 ('tb_invalidate', r'TB invalidate count\s+(\d+)'),
                                 ('io_recompile', r'IO recompile count\s+(\d+)'),
                                 ('tb_flush', r'TB flush count\s+(\d+)')):
                    m = re.search(pat, text)
                    stats[key] = int(m[1]) if m else None
                return stats

            def traffic_lines():
                log.flush()
                return stderr_log.read_text(errors='replace').count('[TRAFFIC]')

            def phase(name, seconds, action=None):
                before = {'cpu': cpu_times(vm.pid), 'jit': jit(), 'traffic_index': traffic_lines(),
                          'wall': time.monotonic()}
                perf = None
                if action and args.perf:
                    perf = subprocess.Popen(['perf', 'record', '--all-user', '--user-callchains', '-g', '-F', '999', '-p', str(vm.pid),
                                             '-o', str(OUT / f'bench-{args.label}.perf'), '--', 'sleep', str(seconds)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if action:
                    action(seconds)
                else:
                    time.sleep(seconds)
                if perf:
                    perf.wait()
                after = {'cpu': cpu_times(vm.pid), 'jit': jit(), 'traffic_index': traffic_lines(),
                         'wall': time.monotonic()}
                wall = after['wall'] - before['wall']
                lines = stderr_log.read_text(errors='replace').splitlines()
                traffic = [l for l in lines if '[TRAFFIC]' in l][before['traffic_index']:after['traffic_index']]
                rates = {}
                for line in traffic:
                    for value, key in re.findall(r'(\d+) ([a-z0-9-]+)/s', line):
                        rates.setdefault(key, []).append(int(value))
                thread_delta = {name: round(after['cpu'][2].get(name, 0) - before['cpu'][2].get(name, 0), 2)
                                for name in after['cpu'][2]}
                thread_delta = dict(sorted(((k, v) for k, v in thread_delta.items() if v >= 0.05),
                                           key=lambda kv: -kv[1]))
                sched_delta = {k: round(after['cpu'][3][k] - before['cpu'][3][k], 2)
                               for k in after['cpu'][3]} if after['cpu'][3] else {}
                summary = {
                    'wall_s': round(wall, 2),
                    'proc_cpu_s': round(after['cpu'][0] - before['cpu'][0], 2),
                    'vcpu_cpu_s': round(after['cpu'][1] - before['cpu'][1], 2),
                    'threads_cpu_s': thread_delta,
                    'vcpu_sched': sched_delta,
                    'jit_delta': {k: (after['jit'][k] - before['jit'][k])
                                  if before['jit'][k] is not None else None for k in before['jit']},
                    'traffic_avg_per_s': {k: round(sum(v) / len(v)) for k, v in rates.items()},
                }
                summary['io_recompile_per_s'] = (round(summary['jit_delta']['io_recompile'] / wall)
                                                 if summary['jit_delta']['io_recompile'] is not None else None)
                result[name] = summary
                print(f'{name}: {json.dumps(summary)}', flush=True)

            tablet_offset = [0, 0]

            def abs_move(x, y):
                qmp('input-send-event', events=[
                    {'type': 'abs', 'data': {'axis': 'x', 'value': max(0, min(width - 1, x)) * 32767 // width}},
                    {'type': 'abs', 'data': {'axis': 'y', 'value': max(0, min(height - 1, y)) * 32767 // height}}])

            def move_to(x, y):
                if args.abs:
                    sx, sy = x, y
                    for _ in range(20):
                        abs_move(sx, sy)
                        time.sleep(.1)
                        px, py = pointer()
                        if abs(px - x) < 3 and abs(py - y) < 3:
                            tablet_offset[:] = [sx - x, sy - y]
                            return True
                        sx += int((x - px) * .9)
                        sy += int((y - py) * .9)
                    return False
                for _ in range(200):
                    px, py = pointer()
                    if abs(px - x) < 3 and abs(py - y) < 3:
                        return True
                    events = []
                    for axis, error in (('x', x - px), ('y', y - py)):
                        delta = max(-12, min(12, int(error / 12) or (1 if error > 0 else -1 if error else 0)))
                        if delta:
                            events.append({'type': 'rel', 'data': {'axis': axis, 'value': delta}})
                    qmp('input-send-event', events=events)
                    time.sleep(.03)
                return False

            def drag(seconds):
                title_x = width // 2
                if not move_to(title_x, title_y):
                    raise RuntimeError(f'pointer did not reach title bar: {pointer()}')
                qmp('input-send-event', events=[{'type': 'btn', 'data': {'button': 'left', 'down': True}}])
                time.sleep(.2)
                period = 1 / args.rate
                step, direction, travelled, end = 6, 1, 0, time.monotonic() + seconds
                while time.monotonic() < end:
                    if args.abs:
                        abs_move(title_x + travelled + step * direction + tablet_offset[0], title_y + tablet_offset[1])
                    else:
                        qmp('input-send-event', events=[{'type': 'rel', 'data': {'axis': 'x', 'value': step * direction}}])
                    travelled += step * direction
                    if abs(travelled) >= 240:
                        direction = -direction
                    time.sleep(period)
                qmp('input-send-event', events=[{'type': 'btn', 'data': {'button': 'left', 'down': False}}])
                time.sleep(.3)

            phase('idle', args.idle)
            phase('drag', args.seconds, drag)
            w2, h2, after_pixels = screen('after-drag')
            moved = Qmp.dialog_title_y(w2, h2, after_pixels)
            # The title row stays; check the dialog's left edge shifted by sampling the title row.
            def title_span(pix):
                xs = [x for x in range(0, width, 2)
                      if (lambda i: pix[i + 2] > pix[i] + 40 and pix[i + 2] > pix[i + 1] + 10)((title_y * width + x) * 3)]
                return (xs[0], xs[-1]) if xs else None
            result['title_span_before'] = title_span(pixels)
            result['title_span_after'] = title_span(after_pixels)
            result['window_moved'] = result['title_span_before'] != result['title_span_after']
            result['jit_final'] = jit()
            (OUT / f'bench-{args.label}.json').write_text(json.dumps(result, indent=1))
            print(f"window moved: {result['window_moved']} {result['title_span_before']} -> {result['title_span_after']}")
            print(f"boot {result['boot_seconds']} s, ap0: {result['vram_ap0']}, saved {OUT / f'bench-{args.label}.json'}")
        finally:
            if vm.poll() is None:
                vm.terminate()
                try:
                    vm.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    vm.kill()
                    vm.wait(timeout=5)
            if monitor is not None:
                monitor.close()
            sock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, AssertionError, OSError, ValueError) as error:
        raise SystemExit(str(error))
