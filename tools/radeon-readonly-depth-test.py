#!/usr/bin/env python3
"""Check read-only hardware depth contents and actual Vulkan copybacks.

Build the Linux trace shim with the Vulkan development headers:
    cc -shared -fPIC -O2 $(pkg-config --cflags vulkan) \
        tools/fixtures/depth-copy-trace.c -o /tmp/depth-copy-trace.so -pthread -ldl

Use a fresh TRACE_FILE for every run. A fixed binary must pass with
--expect-readonly-skip; omit that flag when checking an older baseline:
    LD_PRELOAD=/tmp/depth-copy-trace.so TRACE_FILE=/tmp/depth-copies-new.log \
        python3 tools/radeon-readonly-depth-test.py QEMU \
        --expect-readonly-skip --output /tmp/depth-result-new.json

Seven draws, diskless paused mac99/G4, no MorphOS boot or timing benchmark.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time

RIG = Path(__file__).resolve().parent.parent
RT, ZBUF, TEX = 0x90008000, 0x90020000, 0x91000000
WIDTH, HEIGHT, COLOR_BYTES, DEPTH_BYTES = 32, 32, 4096, 2048
BACKGROUND = bytes([0x55]) * COLOR_BYTES
DRAWN = bytes.fromhex("7f4020c0")
Z_SAMPLE = (16 * WIDTH + 16) * 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--expect-readonly-skip", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    assert os.environ.get("LD_PRELOAD"), "Root must supply the Vulkan trace shim"
    trace = Path(os.environ["TRACE_FILE"]).resolve()
    trace.parent.mkdir(parents=True, exist_ok=True)
    with trace.open("x"):
        pass
    gpu_log = output.with_suffix(".gpu.log")
    stdio_log = output.with_suffix(".qemu.log")
    result = {"binary": str(binary), "expect_readonly_skip": args.expect_readonly_skip,
              "trace": str(trace), "gpu_log": str(gpu_log), "phases": [],
              "draws": 7, "VM_starts": 0, "disks": [], "passed": False}

    def copies():
        records = []
        for line in trace.read_text().splitlines():
            if line.startswith("COPY "):
                records.append({key: int(value, 0) for key, value in
                                (field.split("=", 1) for field in line.split()[1:])})
        return {"calls": len(records),
                "depth": sum(item["DEPTH"] for item in records),
                "stencil": sum(item["STENCIL"] for item in records)}

    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PPCGPU_", "MORPHOS_", "PPCOSX_"))}
    env.pop("R300_SYNC", None)
    env.update(PPCGPU_VK_HWZ="1", PPCGPU_VK_LAZY_Z="1", PPCGPU_VK_SPIN_US="200")
    result["controls"] = {key: env[key] for key in
                          ("PPCGPU_VK_HWZ", "PPCGPU_VK_LAZY_Z", "PPCGPU_VK_SPIN_US")}
    vm = None
    try:
        with tempfile.TemporaryDirectory(prefix="readonly-depth-") as temp:
            sock = Path(temp) / "qtest.sock"
            with stdio_log.open("w") as log:
                command = [str(binary), "-machine", "mac99,via=pmu", "-cpu", "G4",
                           "-m", "64", "-vga", "none", "-device",
                           "ati-radeon-9700,addr=e.0,vgamem_mb=32,renderer=vulkan,romfile=",
                           "-bios", str(RIG / "firmware/radeon/openbios-ppc"),
                           "-display", "none", "-serial", "none", "-monitor", "none",
                           "-net", "none", "-S", "-qtest",
                           f"unix:{sock},server=on,wait=off", "-qtest-log", "/dev/null",
                           "-D", str(gpu_log), "-d", "guest_errors"]
                result["command"] = command
                vm = subprocess.Popen(command, stdout=log, stderr=log, env=env)
                result["VM_starts"] = 1
                with socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(10)
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            conn.connect(str(sock))
                            break
                        except (FileNotFoundError, ConnectionRefusedError):
                            if vm.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError(stdio_log.read_text())
                            time.sleep(0.05)
                    with conn.makefile("rw") as stream:
                        def request(command):
                            stream.write(command + "\n")
                            stream.flush()
                            reply = stream.readline().strip().split()
                            assert reply and reply[0] == "OK", reply
                            return reply[1:]

                        def blit(addr, data):
                            request(f"write {addr:#x} {len(data)} 0x{data.hex()}")

                        def write(addr, value):
                            blit(addr, value.to_bytes(4, "little"))

                        def read(addr, size=4):
                            data = bytes.fromhex(request(f"read {addr:#x} {size}")[0][2:])
                            assert len(data) == size, (addr, size, len(data))
                            return data

                        def state_packet(values):
                            words = []
                            for reg, value in values.items():
                                words.extend((reg // 4, value))
                            return words

                        def draw(z, cntl, func=7):
                            words = state_packet({0x4F00: cntl, 0x4F04: func})
                            words += [0xC0203500, 0x0004003D]
                            for vertex in ((0, 0, z, 1, 0, 0, 0, 1),
                                           (32, 0, z, 1, 1, 0, 0, 1),
                                           (32, 32, z, 1, 1, 1, 0, 1),
                                           (0, 32, z, 1, 0, 1, 0, 1)):
                                words.extend(struct.unpack("<8I", struct.pack("<8f", *vertex)))
                            return words

                        def phase(name, words, real_write):
                            blit(RT, BACKGROUND)
                            before = copies()
                            blit(base + 0x1000, struct.pack(f"<{len(words)}I", *words))
                            deadline = time.monotonic() + 15
                            while read(base + 0xE40)[3] & 0x80:
                                assert time.monotonic() < deadline, "GPU did not become idle"
                            depth = read(ZBUF, DEPTH_BYTES)
                            color = read(RT, COLOR_BYTES)
                            after = copies()
                            delta = {key: after[key] - before[key] for key in after}
                            item = {"name": name, "copies": delta,
                                    "depth_sha256": hashlib.sha256(depth).hexdigest(),
                                    "depth_center": depth[Z_SAMPLE:Z_SAMPLE + 2].hex(),
                                    "color_center": color[0x840:0x844].hex()}
                            result["phases"].append(item)
                            for offset in (0, 0x840):
                                pixel = color[offset:offset + 4]
                                assert all(abs(a - b) <= 1 for a, b in zip(pixel, DRAWN)), (
                                    name, offset, pixel.hex(), DRAWN.hex())
                            if real_write or not args.expect_readonly_skip:
                                assert delta["depth"] > 0, (name, "no actual depth copy", delta)
                            else:
                                assert delta["depth"] == 0, (name, "read-only depth copied", delta)
                            assert delta["stencil"] == 0, (name, "D16 copied stencil", delta)
                            return depth

                        for offset, value in ((0x10, 0x90000000), (0x18, 0xA0000000), (4, 2)):
                            write(0xF2800000, 0x80007000 | offset)
                            write(0xF2C00000, value)
                        base = 0xA0000000
                        write(base + 0x148, 0x91FF0000)
                        write(base + 0xB00, 0x100)
                        state = json.loads((RIG / "tools/fixtures/morphos-ui-registers.json").read_text())
                        state.update({"4E04": "00000000", "4E08": "00000000", "4E18": "00000000",
                                      "4E28": f"{RT:08X}", "4E38": "00C00020",
                                      "4480": "80000000", "44C0": "0016D000", "4500": "0000001F",
                                      "4540": f"{TEX:08X}", "2140": "00000100",
                                      "4C00": "003C0000", "4C04": "003D0000", "4C08": "003E0000",
                                      "4C0C": "003F0000", "4F00": "00000006", "4F04": "00000007",
                                      "4F10": "00000000", "4F20": f"{ZBUF:08X}", "4F24": "00000020",
                                      "43E4": f"{(1440 + 31) | ((1440 + 31) << 13):08X}"})
                        for reg, value in state.items():
                            write(base + int(reg, 16), int(value, 16))
                        blit(TEX, b"\xc0" * 32)
                        blit(ZBUF, b"\xff" * DEPTH_BYTES)

                        initial = phase("real-depth-write", draw(0.25, 6), True)
                        assert initial[Z_SAMPLE:Z_SAMPLE + 2] != b"\xff\xff", "Depth writer did not change Z"
                        readonly = phase("read-only-depth", draw(0.75, 2), False)
                        assert readonly == initial, "Read-only depth changed raw VRAM"
                        retained = phase("retained-depth-less", draw(0.75, 2, 0) + draw(0.75, 0, 0), False)
                        assert retained == initial, "Retained depth-less pass changed raw Z"
                        later = phase("later-real-depth-write", draw(0.75, 6), True)
                        assert later[Z_SAMPLE:Z_SAMPLE + 2] != initial[Z_SAMPLE:Z_SAMPLE + 2], "Later writer did not change Z"
                        pending = phase("pending-real-write-then-read-only", draw(0.25, 6) + draw(0.75, 2), True)
                        assert pending == initial, "Read-only pass lost an earlier pending real depth write"
                        gpu_text = gpu_log.read_text()
                        assert "depth/stencil tested in hardware" in gpu_text, "Hardware depth not exercised"
                        failures = [line for line in gpu_text.splitlines() if re.search(
                            r"\bVkResult\b|\bVK_ERROR_|\b(?:ERROR|Error|error):|"
                            r"vulkan(?: CHECK)?:.*(?:\bBUG\b|\bfailed\b|depth lost)", line)]
                        assert not failures, ("Vulkan errors in GPU log", failures)
                        result["passed"] = True
    except Exception as error:
        result["error"] = repr(error)
        raise
    finally:
        if vm is not None:
            vm.terminate()
            try:
                vm.wait(timeout=10)
            except subprocess.TimeoutExpired:
                vm.kill()
                vm.wait(timeout=5)
            result["QEMU_exit"] = vm.returncode
        output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"PASS: seven draws, raw depth/color correctness and actual depth-copy deltas ({output})")


if __name__ == "__main__":
    main()
