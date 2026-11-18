#!/usr/bin/env python3
"""Check OV0 formats, pitches, scaling, locking, keys and bounds without a guest."""
from pathlib import Path
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parent.parent
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / 'qemu/build/qemu-system-ppc'
renderer = ',renderer=vulkan' if '--vulkan' in sys.argv[2:] else ''
no_overlay_queue = '--no-overlay-queue' in sys.argv[2:]
gpu_overlay = bool(renderer) and not os.environ.get('PPCGPU_GPU_OVERLAY', '').startswith('0') and not no_overlay_queue
timeout_regression = '--timeout-regression' in sys.argv[2:]
queue_regression = '--queue-regression' in sys.argv[2:]
submit_regression = '--submit-failure-regression' in sys.argv[2:]
wait_loss_regression = '--wait-loss-regression' in sys.argv[2:]
pending_exit_regression = '--pending-exit-regression' in sys.argv[2:]
lazy_depth_regression = '--lazy-depth-regression' in sys.argv[2:]
fault_regression = timeout_regression or queue_regression or submit_regression or wait_loss_regression or pending_exit_regression
if fault_regression:
    assert gpu_overlay, 'Fault regressions require --vulkan with GPU overlay enabled'
with tempfile.TemporaryDirectory(prefix='radeon-overlay-') as temporary:
    temp = Path(temporary)
    with (temp / 'qemu.log').open('w+') as log:
        vm = subprocess.Popen([
            str(binary), '-machine', 'mac99,via=pmu', '-cpu', 'G4', '-m', '64',
            '-vga', 'none', '-device', 'ati-radeon-9700,addr=e.0,vgamem_mb=32,romfile=' + renderer,
            '-bios', str(repo / 'firmware/radeon/openbios-ppc'),
            '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none', '-S',
            '-qtest', f'unix:{temp}/qtest.sock,server=on,wait=off', '-qtest-log', '/dev/null',
            '-qmp', f'unix:{temp}/qmp.sock,server=on,wait=off',
            '-D', str(temp / 'gpu.log'),
        ], stdout=log, stderr=log)
        try:
            with socket.socket(socket.AF_UNIX) as qt, socket.socket(socket.AF_UNIX) as qm:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        qt.connect(str(temp / 'qtest.sock'))
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if vm.poll() is not None or time.monotonic() > deadline:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        time.sleep(.05)
                qt.settimeout(5); qm.settimeout(30)
                qm.connect(str(temp / 'qmp.sock'))
                with qt.makefile('rw') as stream, qm.makefile('rw') as monitor:
                    def request(command):
                        stream.write(command + '\n'); stream.flush()
                        reply = stream.readline().strip().split()
                        assert reply and reply[0] == 'OK', reply
                        return reply[1:]

                    def raw(address, data):
                        request(f'write {address:#x} {len(data)} 0x{data.hex()}')

                    def write(address, value, size=4):
                        raw(address, value.to_bytes(size, 'little'))

                    def read(address):
                        return int.from_bytes(bytes.fromhex(request(f'read {address:#x} 4')[0][2:]), 'little')

                    def qmp(command):
                        monitor.write(json.dumps(command) + '\n'); monitor.flush()
                        while True:
                            reply = json.loads(monitor.readline())
                            assert 'error' not in reply, reply
                            if 'return' in reply:
                                return

                    json.loads(monitor.readline())
                    qmp({'execute': 'qmp_capabilities'})
                    qm.settimeout(5)
                    for offset, value in ((0x10, 0x90000000), (0x18, 0xa0000000), (4, 2)):
                        write(0xf2800000, 0x80007000 | offset)
                        write(0xf2c00000, value)
                    base = 0xa0000000
                    read(base + 0x158)  # native register view, rather than NDRV palette aliases
                    write(base + 0x148, 0x91ff0000)
                    write(base + 0x50, 0x03000600)
                    write(base + 0x200, 31)
                    write(base + 0x208, 31)
                    write(base + 0x22c, 1)
                    write(base + 0x410, 1)
                    assert read(base + 0x410) & 8, 'Overlay lock never acknowledged'
                    for register, value in ((0x400, 0), (0x404, (2 << 16) | 4),
                                            (0x430, 1 << 16), (0x494, 1),
                                            (0x460, 8), (0x464, 8), (0x43c, 0x90000000),
                                            (0x440, 0x1000), (0x444, 0x2001), (0x448, 0x3001),
                                            (0x4f4, 0x10), (0x420, 0x40000400)):
                        write(base + register, value)
                    raw(0x90001000, bytes.fromhex('00f8e007000000001f00ffff00000000'))
                    shot = temp / 'overlay.ppm'

                    def pixels(rows=2):
                        qmp({'execute': 'screendump', 'arguments': {'filename': str(shot)}})
                        with shot.open('rb') as image:
                            assert image.readline().strip() == b'P6'
                            line = image.readline()
                            while line.startswith(b'#'):
                                line = image.readline()
                            width, height = map(int, line.split())
                            assert image.readline().strip() == b'255'
                            data = image.read()
                            return [tuple(data[y * width * 3 + x * 3:y * width * 3 + x * 3 + 3])
                                    for y in range(rows) for x in range(4)]

                    assert pixels() == [(0, 0, 0)] * 8, 'Locked overlay changed scanout'
                    if queue_regression:
                        assert os.environ.get('PPCGPU_VK_SUBMIT_MIN') == '0'
                        assert int(os.environ.get('OVERLAY_REPRO_DELAY_MS', '0')) >= 5000
                        state = json.loads((repo / 'tools/fixtures/morphos-ui-registers.json').read_text())
                        state.update({'4E28': '90400000', '4E38': '00d00020',
                                      '4E04': '00000000', '4E08': '00000000',
                                      '4480': '80000000', '44C0': '0016d000',
                                      '4500': '0000001f', '4540': '91000000',
                                      '43E4': f'{(1440 + 31) | ((1440 + 31) << 13):08X}'})
                        probe = 0x90400000 + 8 * 128 + 8 * 4
                        if '--queue-first-scanout' in sys.argv[2:]:
                            state.update({'4E28': '90000000', '4E38': '00d00280'})
                            probe = 0x90000000 + 8 * 2560 + 8 * 4
                        for reg, value in state.items():
                            write(base + int(reg, 16), int(value, 16))
                        raw(0x91000000, bytes([255]) * 32)
                        words = [0xc0203500, 0x0004003d]
                        for vertex in ((0, 0, 0, 1, 0, 0, 0, 1), (32, 0, 0, 1, 1, 0, 0, 1),
                                       (32, 32, 0, 1, 1, 1, 0, 1), (0, 32, 0, 1, 0, 1, 0, 1)):
                            words.extend(struct.unpack('<8I', struct.pack('<8f', *vertex)))
                        packet = struct.pack(f'<{len(words)}I', *words)
                        raw(base + 0x1000, packet)
                        write(base + 0x1720, 1 << 17)
                        deadline = time.monotonic() + 5
                        while read(base + 0x7f8) or read(base + 0xe40) & (1 << 31):
                            assert time.monotonic() < deadline, '3D warmup never completed'
                        warm_pixel = read(probe)
                        assert warm_pixel == 0x000000ff, f'3D warmup did not render: {warm_pixel:#x}'
                        write(probe, 0)  # Later draws must produce a fresh pixel.
                    write(base + 0x410, 0)
                    assert read(base + 0x410) & 16, 'Overlay flip never completed'
                    expected = [(255, 0, 0)] * 2 + [(0, 255, 0)] * 2 + [(0, 0, 255)] * 2 + [(255, 255, 255)] * 2
                    if fault_regression:
                        # The interposer holds real work, or rejects submission before queuing it.
                        assert pixels() == expected, 'Failed GPU job did not use CPU fallback'
                        if queue_regression:
                            times = []
                            for batch in range(7):
                                started = time.monotonic()
                                raw(base + 0x1000, packet)
                                write(base + 0x1720, 1 << 17)
                                times.append(time.monotonic() - started)
                                if batch == 0 and '--queue-first-scanout' in sys.argv[2:]:
                                    started = time.monotonic()
                                    assert pixels() == expected
                                    times.append(time.monotonic() - started)
                            started = time.monotonic()
                            assert pixels() == expected
                            times.append(time.monotonic() - started)
                            print('3D batch and scanout seconds:', times, flush=True)
                            fixture_log = Path(os.environ['OVERLAY_REPRO_LOG']).read_text()
                            assert max(times) < .5, 'Overlay delay blocked 3D batches or display'
                            assert 'other-submit-while-overlay-pending 7' in fixture_log, fixture_log
                            assert 'other-submit-shares-overlay-queue 1' not in fixture_log, fixture_log
                            assert 'pending-command-pool-destroyed' not in fixture_log, fixture_log
                            assert 'release-event 0' not in fixture_log, 'Test outlasted the real pending job'
                            started = time.monotonic()
                            assert read(probe) == warm_pixel, '3D rendering did not complete'
                            assert time.monotonic() - started < .5, '3D readback waited on overlay'
                            assert 'release-event 0' not in Path(os.environ['OVERLAY_REPRO_LOG']).read_text()
                            print('PASS: seven 3D batches and scanout progress while overlay remains pending')
                            sys.exit(0)
                        if pending_exit_regression:
                            qmp({'execute': 'quit'})
                            vm.wait(timeout=12)
                            fixture_log = Path(os.environ['OVERLAY_REPRO_LOG']).read_text()
                            assert 'release-event 0' in fixture_log and 'exit-release-confirmed 1' in fixture_log, fixture_log
                            assert 'pending-fence-destroyed' not in fixture_log, fixture_log
                            assert 'pending-command-pool-destroyed' not in fixture_log, fixture_log
                            assert 'unbounded-overlay-wait' not in fixture_log, fixture_log
                            print('PASS: process exit while GPU pending; fixture released before OS cleanup')
                            sys.exit(0)
                        raw(0x90001000, bytes(16))
                        if timeout_regression:
                            time.sleep(.35)  # Let the fixture inspect fallback writes before refresh.
                        started = time.monotonic()
                        assert pixels() == [(0, 0, 0)] * 8, 'CPU fallback retained old source'
                        elapsed = time.monotonic() - started
                        assert elapsed < .5, f'Refresh waited on failed GPU job: {elapsed:.3f}s'
                        # Grow and replace the public surface while the old job is still pending.
                        started = time.monotonic()
                        write(base + 0x200, (99 << 16) | 127)
                        write(base + 0x208, (599 << 16) | 627)
                        write(base + 0x22c, 100)
                        assert pixels() == [(0, 0, 0)] * 8
                        assert time.monotonic() - started < .5, 'Mode change waited on failed GPU job'
                        if timeout_regression:
                            deadline = time.monotonic() + 11
                            passes = 0
                            while 'release-event 0' not in Path(os.environ['OVERLAY_REPRO_LOG']).read_text():
                                assert time.monotonic() < deadline, 'Injected event never released'
                                started = time.monotonic()
                                w, h = (640, 480) if passes % 2 else (min(1024 + 8 * (passes // 2), 1920), 768)
                                write(base + 0x200, ((w // 8 - 1) << 16) | 127)
                                write(base + 0x208, ((h - 1) << 16) | 800)
                                write(base + 0x22c, w // 8)
                                assert pixels() == [(0, 0, 0)] * 8
                                assert time.monotonic() - started < .5, 'Repeated refresh/mode change blocked'
                                passes += 1
                                time.sleep(.05)
                            if int(os.environ.get('OVERLAY_REPRO_DELAY_MS', '1500')) >= 5000:
                                assert passes >= 18, 'Sustained test did not exhaust retired shadow slots'
                            print(f'PASS: {passes} refresh/mode changes with {(passes + 1) // 2} growing allocations while GPU job remained pending')
                        assert pixels() == [(0, 0, 0)] * 8, 'Late GPU output changed the new surface'
                        fixture_log = Path(os.environ['OVERLAY_REPRO_LOG']).read_text()
                        if timeout_regression:
                            assert 'pending-fence-destroyed' not in fixture_log, fixture_log
                            assert 'pending-command-pool-destroyed' not in fixture_log, fixture_log
                            assert 'unbounded-overlay-wait' not in fixture_log, fixture_log
                            for evidence in ('overlay-wait-result 2', 'fence-status-at-return 1',
                                             'input-retained 1', 'output-retained 1', 'release-event 0'):
                                assert evidence in fixture_log, (evidence, fixture_log)
                        elif wait_loss_regression:
                            assert 'completed-wait-device-lost 1' in fixture_log, fixture_log
                            assert 'completed-loss-fence-destroyed 1' in fixture_log, fixture_log
                        else:
                            failure = os.environ.get('OVERLAY_REPRO_SUBMIT_FAIL', '')
                            result = {'device-lost': -4, 'unknown': -13}.get(failure, -1)
                            assert f'submit-rejected {result}' in fixture_log, fixture_log
                            if failure == 'unknown':
                                assert 'rejected-fence-destroyed' not in fixture_log, fixture_log
                            else:
                                assert f'rejected-fence-destroyed {result}' in fixture_log, fixture_log
                        print('PASS: bounded GPU failure fallback, source isolation, refresh and mode change')
                        sys.exit(0)
                    if gpu_overlay:
                        # Pixel centers sample 0, 1/4, 3/4, 1: GPU filtering is bilinear.
                        expected = [(255, 0, 0), (191, 64, 0), (64, 191, 0), (0, 255, 0),
                                    (0, 0, 255), (64, 64, 255), (191, 191, 255), (255, 255, 255)]
                    assert pixels() == expected, ('565/pitch/scaling', pixels())
                    write(base + 0x404, (4 << 16) | 4)
                    scaled = expected[:4] * 2 + expected[4:] * 2
                    if gpu_overlay:
                        scaled = (expected[:4] +
                                  [(191, 0, 64), (159, 64, 64), (96, 191, 64), (64, 255, 64)] +
                                  [(64, 0, 191), (96, 64, 191), (159, 191, 191), (191, 255, 191)] +
                                  expected[4:])
                    assert pixels(4) == scaled, ('two-axis scaling', pixels(4))
                    write(base + 0x404, (2 << 16) | 4)
                    # Buffer 1 is selected from BUF3, without changing the CPU framebuffer.
                    raw(0x90004000, bytes.fromhex('ffff1f0000000000e00700f800000000'))
                    write(base + 0x44c, 0x4000)
                    write(base + 0x470, 1)
                    flipped = [(255, 255, 255)] * 2 + [(0, 0, 255)] * 2 + [(0, 255, 0)] * 2 + [(255, 0, 0)] * 2
                    if gpu_overlay:
                        flipped = list(reversed(expected))
                    assert pixels() == flipped, 'Overlay buffer selection lost'
                    write(base + 0x410, 1)
                    write(base + 0x470, 0)
                    assert pixels() == flipped, 'Locked buffer flip changed active video'
                    write(base + 0x410, 0)
                    assert pixels() == expected, 'Buffer flip did not latch at unlock'
                    assert read(0x90000000) == 0, 'Overlay altered CPU framebuffer'
                    write(base + 0x420, 0x40000300)
                    raw(0x90001000, bytes.fromhex('007ce003000000001f00ff7f00000000'))
                    assert pixels() == expected, ('555', pixels())
                    # BT.601 matrix programmed by the legacy Xorg driver on R300.
                    for index, value in enumerate((0x12a00000, 0x1990190e, 0x12a0f9c0, 0xf3000442, 0x12a02040, 0x175f)):
                        write(base + 0xd20 + index * 4, value)
                    raw(0x90001000, bytes.fromhex('1080eb80000000001080eb8000000000'))
                    write(base + 0x420, 0x40000b00)
                    grey = pixels()
                    levels = [0, 64, 191, 255] if gpu_overlay else [0, 0, 255, 255]
                    assert all(abs(c - level) <= 2 for rgb, level in zip(grey, levels * 2)
                               for c in rgb), grey
                    raw(0x90001000, bytes.fromhex('801080eb00000000801080eb00000000'))
                    write(base + 0x420, 0x40000c00)
                    assert pixels() == grey, 'UYVY component order differs'
                    raw(0x90001000, bytes.fromhex('10eb00000000000010eb000000000000'))
                    raw(0x90002000, b'\x80'); raw(0x90003000, b'\x80')
                    write(base + 0x420, 0x40000a00)
                    assert pixels() == grey, 'Planar420 component order differs'
                    # Non-neutral chroma catches accidental U/V reversal hidden by grey tests.
                    raw(0x90001000, bytes.fromhex('51510000000000005151000000000000'))
                    raw(0x90002000, b'\x5a'); raw(0x90003000, b'\xf0')
                    assert pixels() == [(255, 0, 0)] * 8, 'Planar420 chroma order lost'
                    raw(0x90001000, bytes([0x51, 0x5a, 0x51, 0xf0, 0, 0, 0, 0] * 2))
                    write(base + 0x420, 0x40000b00)
                    assert pixels() == [(255, 0, 0)] * 8, 'YUYV chroma order lost'
                    raw(0x90001000, bytes([0x5a, 0x51, 0xf0, 0x51, 0, 0, 0, 0] * 2))
                    write(base + 0x420, 0x40000c00)
                    assert pixels() == [(255, 0, 0)] * 8, 'UYVY chroma order lost'
                    raw(0x90001000, bytes.fromhex('801080eb00000000801080eb00000000'))
                    write(base + 0x4ec, 0x010101); write(base + 0x4f0, 0x010101)
                    write(base + 0x4f4, 0x20)
                    assert pixels() == [(0, 0, 0)] * 8, 'Graphics key ignored'
                    write(base + 0x4ec, 0); write(base + 0x4f0, 0)
                    assert pixels() == grey, 'Graphics key comparison lost'
                    write(base + 0x440, 0x02000000)
                    assert pixels() == [(0, 0, 0)] * 8, 'Out-of-bounds buffer displayed'
                    write(base + 0x440, 0x1000)
                    write(base + 0x460, 1)
                    assert pixels() == [(0, 0, 0)] * 8, 'Invalid pitch accepted'
                    write(base + 0x420, 0)
                    assert pixels() == [(0, 0, 0)] * 8, 'Disabled overlay left old video behind'
                    if lazy_depth_regression:
                        assert gpu_overlay, 'Lazy-depth case requires Vulkan GPU overlay'
                        raw(0x90200000, bytes([255]) * 2048)
                        for reg, value in ((0x440, 0x200000), (0x460, 64),
                                           (0x4f4, 0x10), (0x420, 0x40000400)):
                            write(base + reg, value)
                        assert pixels() == [(255, 255, 255)] * 8
                        state = json.loads((repo / 'tools/fixtures/morphos-ui-registers.json').read_text())
                        state.update({'4E28': '90400000', '4E38': '00c00020',
                                      '4E04': '00000000', '4E08': '00000000',
                                      '4480': '80000000', '44C0': '0016d000',
                                      '4500': '0000001f', '4540': '91000000',
                                      '4F00': '00000006', '4F04': '00000007',
                                      '4F10': '00000000', '4F20': '90200000',
                                      '4F24': '00000020',  # Depth pitch is pixels; OV0 pitch is bytes.
                                      '43E4': f'{(1440 + 31) | ((1440 + 31) << 13):08X}'})
                        for reg, value in state.items():
                            write(base + int(reg, 16), int(value, 16))
                        raw(0x91000000, bytes([255]) * 32)
                        words = [0xc0203500, 0x0004003d]
                        for vertex in ((0, 0, 0, 1, 0, 0, 0, 1), (32, 0, 0, 1, 1, 0, 0, 1),
                                       (32, 32, 0, 1, 1, 1, 0, 1), (0, 32, 0, 1, 0, 1, 0, 1)):
                            words.extend(struct.unpack('<8I', struct.pack('<8f', *vertex)))
                        raw(base + 0x1000, struct.pack(f'<{len(words)}I', *words))
                        # No idle/status/VRAM read may force depth writeback before display does.
                        deadline = time.monotonic() + 3
                        while pixels() != [(0, 0, 0)] * 8:
                            assert time.monotonic() < deadline, 'Overlay deferred forever on lazy depth'
                            time.sleep(.01)
                        assert 'depth/stencil tested in hardware' in (temp / 'gpu.log').read_text()
                        print('PASS: overlay source aliases a freshly written lazy depth surface')
                    if gpu_overlay:
                        assert 'overlay composed on the GPU'  in (temp / 'gpu.log').read_text(), 'GPU composer not exercised'
                    if no_overlay_queue:
                        assert 'no independent compute queue; CPU overlay fallback' in (temp / 'gpu.log').read_text()
                        assert 'delayed-compute' not in Path(os.environ['OVERLAY_REPRO_LOG']).read_text()
                    print('PASS:', 'GPU bilinear' if gpu_overlay else 'CPU nearest',
                          'OV0 lock, RGB555/565, YUYV/UYVY/420, padded pitches, scaling, graphics keys and bounds')
        except Exception:
            log.seek(0)
            sys.stderr.write(log.read())
            if (temp / 'gpu.log').exists():
                sys.stderr.write((temp / 'gpu.log').read_text())
            raise
        finally:
            if timeout_regression or queue_regression or pending_exit_regression:
                deadline = time.monotonic() + 11
                fixture = Path(os.environ['OVERLAY_REPRO_LOG'])
                while vm.poll() is None and 'delayed-compute' in fixture.read_text() and 'release-event 0' not in fixture.read_text():
                    if time.monotonic() >= deadline:
                        break  # The VM must still be terminated on fixture failure.
                    time.sleep(.05)
            vm.terminate()
            try:
                vm.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vm.kill(); vm.wait()
