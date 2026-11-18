# Firmware inputs and initial package contents

The portable Linux artifact contains QEMU, qemu-img, keymaps, license notices, exact source references, its build recipe and checksums. It contains no firmware, operating system, guest disk or optional application. The bundle's source archives also omit inherited firmware binaries and the optional vm tree through export-ignore rules; a full Git clone and its history still contain those inherited files.

## MorphOS 2018 OpenBIOS

[BALATON Zoltan's project page](https://zero.eik.bme.hu/~balaton/qemu/amiga/) publishes the patched mac99 firmware used by this launcher. The download was checked against the launcher's required input:

| Item | SHA-256 |
| --- | --- |
| [openbios-qemu.elf.zip](https://zero.eik.bme.hu/~balaton/qemu/amiga/openbios-qemu.elf.zip) | `4b215f5cd95c706c769552d60b5888652912eee525a77727140dcc961ed30d10` |
| ZIP member openbios-qemu.elf, 763128 bytes | `df3d53348721b9c4f85504b44e230e8a9097f393ec2a31e418e44ccf84dc69a7` |

Download and extract that single member with Python's standard library:

```sh
python3 - "$HOME/.local/share/morphos-qemu/openbios-qemu.elf" <<'PY'
import hashlib, io, pathlib, sys, urllib.request, zipfile
url = "https://zero.eik.bme.hu/~balaton/qemu/amiga/openbios-qemu.elf.zip"
with urllib.request.urlopen(url, timeout=30) as response:
    archive = response.read()
if hashlib.sha256(archive).hexdigest() != "4b215f5cd95c706c769552d60b5888652912eee525a77727140dcc961ed30d10":
    sys.exit("Firmware ZIP checksum mismatch")
with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
    image = zipped.read("openbios-qemu.elf")
if hashlib.sha256(image).hexdigest() != "df3d53348721b9c4f85504b44e230e8a9097f393ec2a31e418e44ccf84dc69a7":
    sys.exit("Firmware image checksum mismatch")
destination = pathlib.Path(sys.argv[1])
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_bytes(image)
PY
```

Set `MORPHOS_FIRMWARE` to that file. The launcher patches a runtime copy and preserves the downloaded input.

The author's [PCI workaround source link](https://mail.coreboot.org/pipermail/openbios/2018-June/010392.html) returned HTTP 404 during this review. The upstream [OpenBIOS source](https://github.com/openbios/openbios) contains its license and build instructions, but an exact source/configuration/toolchain record for this published binary has not been established. QEMU 3.0.0 pins OpenBIOS commit `8fe6f5f96f6ca39f1f62200be7fa130e929f13f2`; its bundled image has a different hash and build date. Treat it as a reference, not a verified replacement or a reproducible build of the required input.

## NDRV

The inherited base `firmware/src/ndrv/qemu_vga.orig.ndrv` matches [QemuMacDrivers commit 90c488d](https://github.com/ozbenh/QemuMacDrivers/tree/90c488d5f4a407342247b9ea869df1c2d9c8e266) byte for byte: 18752 bytes, SHA-256 `d8697354bde8a7ac9e6fe3a5fbca03698de623e95433a59c248e8e09b882c6b8`. That source distribution includes [COPYING](https://github.com/ozbenh/QemuMacDrivers/blob/90c488d5f4a407342247b9ea869df1c2d9c8e266/COPYING).

The cursor patch source is `firmware/src/ndrv/build.py`. Its documented regeneration command is:

```sh
python3 firmware/src/ndrv/build.py /path/to/qemu_vga.ndrv /path/to/qemu_vga_hwc.ndrv
```

The inherited patched file has SHA-256 `b817675d45268624d2f075fd76c71abd0381a2ec49b771c3e08b1e1e25c88b80`. Regenerating it from the pinned base with the bundled patcher reproduced this hash. Set `MORPHOS_NDRV` to your supplied file when using a firmware-free archive. A full clone retains the inherited default.

The UTM-derived images and ppc-ndrvloader remain outside the initial artifacts until their exact source revisions/build records are qualified.
