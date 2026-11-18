"""Shared bits for the MorphOS benchmark scripts: launching, QMP, dialog detection."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO.parent
OUT = REPO / 'vm/morphos'


def official_argv(sock, iso, display):
    """The distribution QEMU with ati-vga; MORPHOS_MACHINE=pegasos2 boots the
    ISO's boot.img on the Pegasos II machine instead of mac99."""
    common = ['-vga', 'none', '-device', 'ati-vga,guest_hwcursor=true', '-net', 'none',
              '-cdrom', str(iso), '-serial', 'none', '-display', display, '-monitor', 'none',
              '-qmp', f'unix:{sock},server=on,wait=off']
    if os.environ.get('MORPHOS_MACHINE', 'mac99') == 'pegasos2':
        boot_img = OUT / f'boot-{iso.stem}.img'
        if not boot_img.exists() or boot_img.stat().st_mtime < iso.stat().st_mtime:
            subprocess.run(['xorriso', '-osirrox', 'on', '-indev', str(iso), '-extract', '/boot.img',
                            str(boot_img)], check=True, capture_output=True)
        return ['/usr/bin/qemu-system-ppc', '-machine', 'pegasos2', '-m', '1024',
                '-kernel', str(boot_img)] + common
    return ['/usr/bin/qemu-system-ppc', '-machine', 'mac99,via=pmu', '-m', '2048', '-cpu', 'G4',
            '-boot', 'd', '-prom-env', 'boot-device=cd:,\\mac_ppc32\\boot.img',
            '-bios', str(BUNDLE / 'openbios-qemu.elf')] + common


def ppm(data):
    m = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', data)
    return int(m[1]), int(m[2]), data[m.end():]


class Qmp:
    def __init__(self, sock):
        self.conn = socket.socket(socket.AF_UNIX)
        self.conn.settimeout(30)
        self.conn.connect(str(sock))
        self.probe = Path(sock).with_suffix('.ppm')
        self.stream = self.conn.makefile('rw')
        json.loads(self.stream.readline())
        self.cmd('qmp_capabilities')

    def cmd(self, command, **arguments):
        self.stream.write(json.dumps({'execute': command, 'arguments': arguments}) + '\n')
        self.stream.flush()
        while True:
            reply = json.loads(self.stream.readline())
            if 'error' in reply:
                raise RuntimeError(reply['error'])
            if 'return' in reply:
                return reply['return']

    def hmp(self, line):
        return self.cmd('human-monitor-command', **{'command-line': line})

    def close(self):
        self.stream.close()
        self.conn.close()

    def screen(self, path):
        self.cmd('screendump', filename=str(path))
        return ppm(Path(path).read_bytes())

    @staticmethod
    def dialog_title_y(width, height, pixels):
        """Row of the language dialog's blue title bar, or None."""
        for y in range(height // 2 - 420, height // 2 - 300):
            blue = 0
            for x in range(width // 2 - 200, width // 2 + 200, 4):
                i = (y * width + x) * 3
                r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
                blue += b > r + 40 and b > g + 10
            if blue > 80:
                return y + 6
        return None

    def wait_for_dialog(self, timeout=180):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            time.sleep(2)
            width, height, pixels = self.screen(self.probe)
            title_y = self.dialog_title_y(width, height, pixels)
            if title_y is not None:
                return width, height, title_y
        raise RuntimeError('language dialog did not appear')

    def wait_for_desktop(self, timeout=180, settle=20):
        """Hard-disk boot (no language dialog): the desktop is up once two
        screendumps 3 s apart are identical below the menu bar (its clock
        ticks every second), at least `settle` s after start."""
        start = time.monotonic()
        last = None
        while time.monotonic() - start < timeout:
            time.sleep(3)
            width, height, pixels = self.screen(self.probe)
            body = pixels[width * 3 * 40:]
            if time.monotonic() - start >= settle and body == last:
                return width, height
            last = body
        raise RuntimeError('desktop did not settle')

    def mmio_base(self):
        pci = self.hmp('info pci')
        return int(re.search(r'BAR2:.*memory at (0x[0-9a-f]+)', pci)[1], 16)

    def hw_cursor(self, mmio):
        self.cmd('stop')
        raw = self.hmp(f'xp /1wx {mmio + 0x264:#x}')
        self.cmd('cont')
        value = int.from_bytes(bytes.fromhex(re.search(r': 0x([0-9a-f]{8})', raw)[1]), 'little')
        return (value >> 16) & 0x3fff, value & 0xfff

    def click(self, button='left', times=1):
        for _ in range(times):
            for down in (True, False):
                self.cmd('input-send-event', events=[{'type': 'btn', 'data': {'button': button, 'down': down}}])
                time.sleep(0.15)


def hd_in_use(hd):
    if not Path('/proc/self').exists():
        pids = subprocess.run(['pgrep', '-f', 'qemu-system-ppc'], capture_output=True, text=True).stdout.split()
        return any(hd in subprocess.run(['ps', '-o', 'args=', '-p', p], capture_output=True, text=True).stdout for p in pids)
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            c = p.read_bytes()
        except OSError:
            continue
        if b'qemu-system-ppc' in c.split(b'\0')[0] and hd.encode() in c:
            return True
    return False


def wait_for_hd_free(timeout=30):
    """A previous VM still exiting holds the disk's write lock."""
    hd = os.environ.get('MORPHOS_HD')
    end = time.monotonic() + timeout
    while hd and time.monotonic() < end and hd_in_use(hd):
        time.sleep(1)


def launch(mode, label, display, extra=()):
    """Start the VM; returns (process, Qmp, stderr log path)."""
    OUT.mkdir(parents=True, exist_ok=True)
    wait_for_hd_free()
    sock = Path('/tmp') / f'morphos-bench-{os.getpid()}.sock'
    sock.unlink(missing_ok=True)
    log = OUT / f'{label}-stderr.log'
    iso = Path(os.environ.get('MORPHOS_ISO', BUNDLE / 'morphos-3.21.iso'))
    env = dict(os.environ)
    env.setdefault('MORPHOS_BOOT_ARGS', '')
    # both modes boot the same ISO (the launcher would pick its own default);
    # a hard-disk boot leaves the install CD out
    if env.get('MORPHOS_BOOT') != 'hd':
        env.setdefault('MORPHOS_ISO', str(iso))
    env.setdefault('MORPHOS_WINDOW_POS', '3900,100')   # the test windows go to the second monitor
    if mode == 'fork':
        argv = [str(REPO / 'tools/morphos-run.sh'), '-display', display, '-monitor', 'none',
                '-qmp', f'unix:{sock},server=on,wait=off']
    else:
        argv = official_argv(sock, iso, display)
    argv += ['-name', 'bench,debug-threads=on', '-D', str(OUT / f'{label}-gpu.log')] + list(extra)
    argv += os.environ.get('MORPHOS_QEMU_EXTRA', '').split()
    vm = subprocess.Popen(argv, env=env, stdout=log.open('w'), stderr=subprocess.STDOUT)
    print(f'QEMU pid {vm.pid}' + (f', QMP /tmp/morphos-qmp-{vm.pid}.sock' if mode == 'fork' else ''))
    start = time.monotonic()
    try:
        runtime = Path(env.get('MORPHOS_RUNTIME_DIR', OUT))
        while not sock.exists() or (mode == 'fork' and not any(
                p.stat().st_size for p in runtime.glob('boot-serial-*.log'))):
            if vm.poll() is not None:
                raise RuntimeError(f'QEMU exited {vm.returncode}; see {log}')
            if time.monotonic() - start > 60:
                raise RuntimeError('QMP startup timed out')
            time.sleep(0.1)
        return vm, Qmp(sock), log
    except BaseException:
        # a VM whose main thread never answers QMP (e.g. stuck creating its
        # X11 window) must not outlive the tool and block the next run
        vm.kill()
        raise
