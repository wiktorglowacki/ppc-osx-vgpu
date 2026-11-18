#!/bin/bash
# MorphOS boot on mac99. See docs/MORPHOS-QUICKSTART.md.
set -eo pipefail
# bash 3.2 (macOS) calls an empty "${arr[@]}" unbound under -u
[ "${BASH_VERSINFO[0]}" -ge 5 ] && set -u
# macOS: Cocoa window, Vulkan through MoltenVK (Metal lacks the texture de-tiling;
# MORPHOS_RENDERER=metal still selects it), CoreAudio (no SDL or PipeWire there)
if [ "$(uname)" = Darwin ]; then
    : "${MORPHOS_DISPLAY:=cocoa}" "${MORPHOS_RENDERER:=vulkan}" "${MORPHOS_AUDIO:=coreaudio}"
    export MVK_CONFIG_SYNCHRONOUS_QUEUE_SUBMITS=${MVK_CONFIG_SYNCHRONOUS_QUEUE_SUBMITS:-1}
fi
repo=$(cd "$(dirname "$0")/.." && pwd -P)
bundle=$(dirname "$repo")
iso=${MORPHOS_ISO:-$bundle/morphos-3.21.iso}
# The tested 2018 OpenBIOS is a user-supplied input, not part of this fork.
firmware=${MORPHOS_FIRMWARE:-$bundle/openbios-qemu.elf}
ndrv=${MORPHOS_NDRV:-$repo/firmware/radeon/qemu_vga_hwc.ndrv}
# Per-instance generated firmware, extracted kernel and logs; disks stay explicit.
vm="${MORPHOS_RUNTIME_DIR:-$repo/vm/morphos}"
serial_log="$vm/boot-serial-$$.log"
qemu=${PPCOSX_QEMU:-$repo/qemu/build/qemu-system-ppc}
vram_mb=${MORPHOS_VRAM_MB:-256}
color_compat=${MORPHOS_COLOR_COMPAT:-on}
# MORPHOS_PCI_BUS=pci.1 puts the card on the uni-north AGP bus instead of PCI.
pci_bus=${MORPHOS_PCI_BUS:+bus=$MORPHOS_PCI_BUS,}
pci_addr=${MORPHOS_PCI_ADDR:-e.0}
# The device's vblank follows the refresh MorphOS programs (pick the rate per
# mode in its Screens preferences; the EDID allows up to 160 Hz).
# MORPHOS_VBLANK_HZ=120 pins the vblank timer instead.
# MORPHOS_ROMFILE=/path/to/vbios.rom exposes a card BIOS image through the
# PCI expansion ROM (nobody executes it; a driver may read its tables).
vblank_hz=${MORPHOS_VBLANK_HZ:-0}
# MORPHOS_MODE=2560x1440 makes the EDID prefer that mode (default 1024x768; the
# running ISO picks 1920x1080 from the standard timings).
mode=${MORPHOS_MODE:-}
edid=
case "$mode" in
    "") ;;
    [0-9]*x[0-9]*) edid="edid-width=${mode%x*},edid-height=${mode#*x}," ;;
    *) echo "MORPHOS_MODE looks like 2560x1440." >&2; exit 2 ;;
esac
case "$color_compat" in
    on|off) ;;
    *) echo "MORPHOS_COLOR_COMPAT must be on or off." >&2; exit 2 ;;
esac
case "$vram_mb" in
    8|16|32|64|128|256) ;;
    *) echo "MORPHOS_VRAM_MB must be 8, 16, 32, 64, 128 or 256." >&2; exit 2 ;;
esac
# OpenBIOS copies boot-file into /chosen/bootargs; RAM debug captures Mac logs.
# Guest debug output is opt-in: MORPHOS_BOOT_ARGS='EDEBUGFLAGS=init ramdebug'
boot_args=${MORPHOS_BOOT_ARGS-}
# mac99 is the supported default; pegasos2 remains an explicit experiment.
machine=${MORPHOS_MACHINE:-mac99}
# MORPHOS_CPU overrides the CPU model (mac99 default G4; pegasos2 default 7457).
cpu=${MORPHOS_CPU:-}
case "$machine" in
    mac99|pegasos2) ;;
    *) echo "MORPHOS_MACHINE must be mac99 or pegasos2." >&2; exit 2 ;;
esac
[ -x "$qemu" ] || { echo "Build the patched QEMU fork or set PPCOSX_QEMU to a patched build." >&2; exit 1; }
[ -f "$iso" ] || { echo "MorphOS ISO not found: $iso (set MORPHOS_ISO)." >&2; exit 1; }
[ -f "$firmware" ] || { echo "OpenBIOS not found: $firmware (set MORPHOS_FIRMWARE; see docs/MORPHOS-QUICKSTART.md)." >&2; exit 1; }
[ -f "$ndrv" ] || { echo "NDRV not found: $ndrv (set MORPHOS_NDRV; see firmware/README.md)." >&2; exit 1; }
# MORPHOS_CPUS=LIST runs QEMU on those host CPUs (taskset -c); unset, on the cores
# with the largest L3 when L3 sizes differ; MORPHOS_CPUS= leaves it to the kernel.
l3=(/sys/devices/system/cpu/cpu[0-9]*/cache/index3/size)
if [ -z "${MORPHOS_CPUS+x}" ] && [ -r "${l3[0]}" ] && command -v taskset >/dev/null; then
    MORPHOS_CPUS=$(awk '{ k = $1 + 0; split(FILENAME, p, "/"); s[substr(p[6], 4)] = k; if (k > m) m = k; if (!n || k < n) n = k }
        END { if (n < m) for (c in s) if (s[c] == m) l = l (l ? "," : "") c; print l }' "${l3[@]}")
fi
pin=()
[ -n "${MORPHOS_CPUS:-}" ] && pin=(taskset -c "$MORPHOS_CPUS")

# The Ubuntu prebuilt's libraries can live here without a host installation.
if [ "$qemu" = "$repo/prebuilt/bin/qemu-system-ppc" ] &&
   [ -d "$repo/prebuilt/ubuntu-libs/usr/lib/x86_64-linux-gnu" ]; then
    export LD_LIBRARY_PATH="$repo/prebuilt/ubuntu-libs/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
# The build-tree QEMU takes shaderc/spirv-cross from .buildenv, and its RUNPATH
# is absolute to the directory it was configured in; keep it working after a move.
deps="$repo/.buildenv/morphos-deps/usr/lib/x86_64-linux-gnu"
[ -d "$deps" ] && export LD_LIBRARY_PATH="$deps${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
if ! "$qemu" -device help 2>/dev/null | grep 'ati-radeon-9700' >/dev/null; then
    echo "This QEMU cannot run the ati-radeon-9700 device (or has missing runtime libraries)." >&2
    exit 1
fi
mkdir -p "$vm"
# MorphOS boots further with this checked 2018 OpenBIOS than with UTM's.
python3 - "$firmware" "$vm/openbios-ppc" <<'PY'
import hashlib, pathlib, sys
data = bytearray(pathlib.Path(sys.argv[1]).read_bytes())
if hashlib.sha256(data).hexdigest() != 'df3d53348721b9c4f85504b44e230e8a9097f393ec2a31e418e44ccf84dc69a7':
    sys.exit('MORPHOS_FIRMWARE must be the documented 2018 OpenBIOS; refusing to patch unknown offsets.')
assert data[0x2f554:0x2f558] == bytes.fromhex('12341111')
data[0x2f554:0x2f558] = bytes.fromhex('10024e44')
# MAC99 PCI window: match QEMU's 0x80000000..0xefffffff memory hole.
# mem_len is used as an endpoint when encoding the remaining available space.
for offset, value in ((0x2d114, 0xf0000000), (0x2d13c, 0x70000000)):
    assert data[offset:offset+4] == bytes.fromhex('10000000')
    data[offset:offset+4] = value.to_bytes(4, 'big')
pathlib.Path(sys.argv[2]).write_bytes(data)
PY
export QEMU_PPC_NDRV="$ndrv"
echo "Experimental Radeon 9700 boot: Ambient works; graphics compatibility is still being tested." >&2
echo "VRAM: $vram_mb MB; MorphOS boot arguments: ${boot_args:-none}; serial log: $serial_log" >&2
echo "MorphOS color compatibility: $color_compat" >&2
# SDL with GL by default: GTK repainted the whole window background and
# cleared a full-window cairo surface for every frame while holding the BQL,
# which stalled the guest's register polling (vCPU 30% blocked); SDL just
# swaps the GL texture.  MORPHOS_DISPLAY=gtk goes back to GTK; pass -display
# to use anything else.
# MORPHOS_WINDOW_POS=x,y opens the SDL window at that desktop position (e.g.
# 3900,100 for the monitor right of a 3840-wide primary).  Wayland lets no
# program choose its screen, so this switches SDL to X11 (XWayland).
if [ -n "${MORPHOS_WINDOW_POS:-}" ]; then
    export SDL_VIDEO_WINDOW_POS="$MORPHOS_WINDOW_POS"
fi
# SDL runs as a native Wayland window under Wayland (X11 otherwise, or when
# MORPHOS_WINDOW_POS asks for a position; MORPHOS_SDL_DRIVER=x11|wayland
# overrides).  Wayland is the only way to get the guest 1:1 on a scale-1
# monitor next to a scaled one: KDE runs
# XWayland at the largest scale (1.25 here), so X11 windows on the 2560x1440
# monitor are drawn 1.25x too big and shrunk back by KWin.  Under Wayland
# only Enter used to reach the guest: SDL2 starts text input by default and
# IBus (a Wayland input method on this host) took every other key; QEMU's
# SDL window now turns text input off for graphic consoles (ui/sdl2.c).
# Wayland does not let a program place its window (MORPHOS_WINDOW_POS has
# no effect there): drag it to the monitor once.
sdl_driver=x11
[ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${MORPHOS_WINDOW_POS:-}" ] && sdl_driver=wayland
export SDL_VIDEODRIVER="${MORPHOS_SDL_DRIVER:-$sdl_driver}"
# MorphOS flips all sixteen segment registers on every kernel entry and exit
# (two address-space views) and names every page it unmaps with tlbie: the
# fork's per-segment-register TLB slots in their "partial sets ignored" mode
# and per-page tlbie turn ~22k full TLB flushes/s into none.  Set PPC_SR_SLOTS=0 / PPC_TLBIE=all to get QEMU's behaviour.
export PPC_SR_SLOTS="${PPC_SR_SLOTS:-2}"
export PPC_TLBIE="${PPC_TLBIE:-page}"
# MORPHOS_SCALE=nearest: no bilinear blur when the window is scaled to the
# monitor (sharp only at integer ratios, e.g. 1920x1080 -> 3840x2160).
# Ctrl+Alt+U in the window restores 1:1 scaling in any case.
[ -n "${MORPHOS_SCALE:-}" ] && export QEMU_GL_FILTER="$MORPHOS_SCALE"
case "${MORPHOS_DISPLAY:-sdl}" in
    sdl) display=(-display sdl,gl=on) ;;
    gtk) display=(-display gtk,gl=on) ;;
    *)   display=(-display "$MORPHOS_DISPLAY") ;;
esac
# An absolute USB tablet: GTK forwards its motion without a pointer grab,
# which the GL window did not deliver for the relative mouse.  MORPHOS_TABLET=off
# keeps only the machine's relative USB mouse.
tablet=(-device usb-tablet)
[ "${MORPHOS_TABLET:-on}" = off ] && tablet=()
# MORPHOS_USB_KBD=on adds a USB keyboard next to the machine's ADB one, for
# a guest that takes keys from USB when the ADB path misbehaves.
[ "${MORPHOS_USB_KBD:-off}" = on ] && tablet+=(-device usb-kbd)
# MORPHOS_NET=user adds user-mode networking with a Sungem NIC, as start.sh does
# (needs a QEMU built with slirp, as the local build now is); MORPHOS_NET=none disables.
net=()
# MORPHOS_EXTRA_ISO=file.iso attaches a read-only tools CD (secondary slave,
# or USB optical storage when MORPHOS_POWERFOX_DISK uses that IDE position).
extra=()
fastmem=()
export PPC_FASTMEM=${PPC_FASTMEM:-1}
[ "$PPC_FASTMEM" = 1 ] && fastmem=(-object memory-backend-shm,id=m,size=2048M -machine memory-backend=m)
# MORPHOS_HD=image attaches a hard disk (primary master, raw or qcow2 by
# extension) for an installed system; MORPHOS_BOOT=hd boots from it instead
# of the CD.  The installer's automatic layout puts an Apple partition map on
# the disk with a 120 MB HFS "Boot" partition second (boot.img and
# bootinfo.txt at its root), then SFS System and Work; OpenBIOS loads
# hd:2,\boot.img (override with MORPHOS_HD_BOOT).  A new sparse image:
# truncate -s 32G vm/morphos/morphos-hd.img
hd=()
cdrom=(-cdrom "$iso")
boot_dev='cd:,\mac_ppc32\boot.img'
boot_order=d
if [ -n "${MORPHOS_HD:-}" ]; then
    [ -f "$MORPHOS_HD" ] || { echo "MORPHOS_HD not found: $MORPHOS_HD" >&2; exit 1; }
    hd_fmt=raw; case "$MORPHOS_HD" in *.qcow2) hd_fmt=qcow2 ;; esac
    hd=(-drive "file=$MORPHOS_HD,if=ide,index=0,format=$hd_fmt,media=disk")
    if [ "${MORPHOS_BOOT:-cd}" = hd ]; then
        boot_dev="${MORPHOS_HD_BOOT:-hd:2,\\boot.img}"
        boot_order=c
        # the install CD would win MorphOS's boot-volume choice; attach it
        # only when MORPHOS_ISO names one explicitly
        [ -n "${MORPHOS_ISO:-}" ] || cdrom=()
    fi
fi
# More drives for an installed system (the IDE slots: 0 = MORPHOS_HD, 1 =
# MORPHOS_HD2, 2 = the install CD on a CD boot, else MORPHOS_XFER or
# MORPHOS_DATA_ISO, 3 = MORPHOS_POWERFOX_DISK or MORPHOS_EXTRA_ISO):
#   MORPHOS_HD2=vm/morphos/morphos-apps.img    second hard disk ("Apps")
#   MORPHOS_XFER=vm/morphos/morphos-xfer.img   ext2 exchange disk built on the
#       host from a folder (mke2fs -d; tools/morphos-apm-disk.py adds the
#       partition map MorphOS mounts)
#   MORPHOS_DATA_ISO=file.iso                  a data CD instead of MORPHOS_XFER
#   MORPHOS_POWERFOX_DISK=file.img             writable PowerFox disk on IDE3;
#       EXTRA stays attached as a read-only USB CD. Set to off to skip this disk.
drive_fmt() { case "$1" in *.qcow2) echo qcow2 ;; *) echo raw ;; esac; }
if [ -n "${MORPHOS_HD2:-}" ]; then
    [ -f "$MORPHOS_HD2" ] || { echo "MORPHOS_HD2 not found: $MORPHOS_HD2" >&2; exit 1; }
    extra+=(-drive "file=$MORPHOS_HD2,if=ide,index=1,format=$(drive_fmt "$MORPHOS_HD2"),media=disk")
fi
if [ -n "${MORPHOS_XFER:-}${MORPHOS_DATA_ISO:-}" ]; then
    [ ${#cdrom[@]} -eq 0 ] || { echo "MORPHOS_XFER/MORPHOS_DATA_ISO need MORPHOS_BOOT=hd (slot 2 holds the install CD)" >&2; exit 2; }
    [ -z "${MORPHOS_XFER:-}" ] || [ -z "${MORPHOS_DATA_ISO:-}" ] || { echo "MORPHOS_XFER and MORPHOS_DATA_ISO share a slot" >&2; exit 2; }
fi
if [ -n "${MORPHOS_XFER:-}" ]; then
    [ -f "$MORPHOS_XFER" ] || { echo "MORPHOS_XFER not found: $MORPHOS_XFER" >&2; exit 1; }
    extra+=(-drive "file=$MORPHOS_XFER,if=ide,index=2,format=$(drive_fmt "$MORPHOS_XFER"),media=disk")
fi
if [ -n "${MORPHOS_DATA_ISO:-}" ]; then
    [ -f "$MORPHOS_DATA_ISO" ] || { echo "MORPHOS_DATA_ISO not found: $MORPHOS_DATA_ISO" >&2; exit 1; }
    extra+=(-drive "file=$MORPHOS_DATA_ISO,media=cdrom,if=ide,index=2,format=raw")
fi
if [ -n "${MORPHOS_POWERFOX_DISK:-}" ] && [ "$MORPHOS_POWERFOX_DISK" != off ]; then
    [ -f "$MORPHOS_POWERFOX_DISK" ] || { echo "MORPHOS_POWERFOX_DISK not found: $MORPHOS_POWERFOX_DISK" >&2; exit 1; }
    extra+=(-drive "file=$MORPHOS_POWERFOX_DISK,if=ide,index=3,format=$(drive_fmt "$MORPHOS_POWERFOX_DISK"),media=disk")
fi
if [ -n "${MORPHOS_EXTRA_ISO:-}" ]; then
    [ -f "$MORPHOS_EXTRA_ISO" ] || { echo "MORPHOS_EXTRA_ISO not found: $MORPHOS_EXTRA_ISO" >&2; exit 1; }
    if [ -n "${MORPHOS_POWERFOX_DISK:-}" ] && [ "$MORPHOS_POWERFOX_DISK" != off ]; then
        extra+=(-drive "file=$MORPHOS_EXTRA_ISO,media=cdrom,if=none,id=powerfox-extra-cd,format=raw,readonly=on"
                -device usb-storage,drive=powerfox-extra-cd)
    else
        extra+=(-drive "file=$MORPHOS_EXTRA_ISO,media=cdrom,if=ide,index=3,format=raw,readonly=on")
    fi
fi
# AGP on by default: the UniNorth AGP bridge appears as a UniNorth 2 (device
# 0x0034) with an AGP capability, so MorphOS's Radeon driver programs the
# bridge's GART and runs its command ring through DMA instead of the PIO FIFO
# (faster drawing and video; see docs/MORPHOS.md).  MorphOS only finds the
# bridge as a board on the bus OpenBIOS scans, so mac99 also gets the
# apple-agp-bridge stub in free slot 0x10 (11 host, 12 mac-io, 13 USB,
# 14 Radeon, 15 sungem).  MORPHOS_AGP=off goes back to the PIO FIFO.
agp=()
case "${MORPHOS_AGP:-on}" in
    on|off) ;;
    *) echo "MORPHOS_AGP must be on or off." >&2; exit 2 ;;
esac
if [ "${MORPHOS_AGP:-on}" = on ]; then
    agp=(-global uni-north-agp.device-id=0x34 -global uni-north-agp.agp-capable=on)
    if [ "$machine" = mac99 ]; then
        agp+=(-device apple-agp-bridge,addr=10.0)
    fi
fi
case "${MORPHOS_NET:-user}" in
    none) ;;
    user) net=(-netdev user,id=n0 -device sungem,netdev=n0) ;;
    *) echo "MORPHOS_NET must be none or user." >&2; exit 2 ;;
esac
# Sound: the macio's Keylargo I2S cell (mac-i2s) drives the ISO's own
# applei2s.audio (AHI mode "AppleI2S", 44.1 kHz) when the kernel sees a
# Keylargo+I2S model (MORPHOS_MODEL, default PowerMac10,1; empty keeps
# OpenBIOS's PowerMac3,1, which has no I2S); MORPHOS_USB_AUDIO=on adds QEMU's
# usb-audio for Poseidon's usbaudio.class (AHI mode "QEMU USB Audio", 48 kHz
# only).  MORPHOS_AUDIO=sdl (default with a window) plays on the host,
# wav:FILE records, none keeps the devices silent, off leaves them out.
# Select the matching AHI unit in the guest.
audio=()
# PipeWire when the binary has it (built after the dev packages were installed),
# SDL otherwise: SDL's stream stops when the window loses focus on this desktop.
audio_default=sdl
"$qemu" -audiodev help 2>/dev/null | grep -qx pipewire && audio_default=pipewire
audio_mode="${MORPHOS_AUDIO:-$audio_default}"
prev=; for arg in "$@"; do [ "$prev" = -display ] && [ "$arg" = none ] && [ "$audio_mode" = sdl ] && audio_mode=none; prev=$arg; done
case "$audio_mode" in
    off) ;;
    # 48 kHz matches the USB stream (no resampling); eight 20 ms buffers ride
    # out the decode-induced jitter that made SDL's default buffers crackle
    sdl)  audio=(-audiodev "sdl,id=a0,out.frequency=48000,out.buffer-length=20000,out.buffer-count=8") ;;
    pipewire) audio=(-audiodev "pipewire,id=a0,out.frequency=48000,out.buffer-length=20000" -global mac-i2s.prime-ms=20) ;;
    pa)   audio=(-audiodev "pa,id=a0,out.frequency=48000,out.buffer-length=20000") ;;
    coreaudio) audio=(-audiodev "coreaudio,id=a0,out.frequency=48000,out.buffer-length=20000") ;;
    none) audio=(-audiodev none,id=a0) ;;
    wav:*) audio=(-audiodev "wav,id=a0,path=${audio_mode#wav:}") ;;
    *) echo "MORPHOS_AUDIO must be sdl, pipewire, pa, coreaudio, none, wav:FILE or off." >&2; exit 2 ;;
esac
if [ ${#audio[@]} -gt 0 ]; then
    # the macio screamer wants an audiodev too; give it a silent one
    audio+=(-audiodev none,id=a1 -global screamer.audiodev=a1 -global mac-i2s.audiodev=a0)
    [ "${MORPHOS_USB_AUDIO:-off}" = on ] && audio+=(-device usb-audio,audiodev=a0)
fi
echo "QEMU: $qemu (built $(date -r "$qemu" +%Y-%m-%d\ %H:%M)); machine: $machine; display: ${display[*]:-from args}; tablet: ${tablet[*]:-off}; net: ${net[*]:-none}; audio: ${audio_mode}; disk: ${MORPHOS_HD:-none} (boot ${MORPHOS_BOOT:-cd}); AGP ring: ${MORPHOS_AGP:-on}; SDL driver: $SDL_VIDEODRIVER; host CPUs: ${MORPHOS_CPUS:-any}" >&2
for arg in "$@"; do [ "$arg" = -display ] && display=(); done
radeon=(-vga none -device "ati-radeon-9700,id=radeon,${pci_bus}${edid}vblank-hz=${vblank_hz},addr=${pci_addr},vgamem_mb=$vram_mb,renderer=${MORPHOS_RENDERER:-vulkan},morphos-color-compat=$color_compat,romfile=${MORPHOS_ROMFILE:-}")
if [ "$machine" = pegasos2 ]; then
    # the kernel image comes straight out of the ISO (cached next to the logs)
    boot_img="$vm/boot-$(basename "$iso" .iso).img"
    if [ ! -s "$boot_img" ] || [ "$iso" -nt "$boot_img" ]; then
        # xorriso's exit code also reports tolerated warnings: judge by the file
        xorriso -osirrox on -indev "$iso" -extract /boot.img "$boot_img" >/dev/null 2>&1 || true
        [ -s "$boot_img" ] || { echo "Could not extract boot.img from $iso (xorriso)." >&2; exit 1; }
    fi
    exec "${pin[@]}" "$qemu" "${display[@]}" "${tablet[@]}" -L "$repo/firmware/radeon" -L "$repo/prebuilt/share/pc-bios" \
        -machine pegasos2 ${cpu:+-cpu "$cpu"} -m "${MORPHOS_RAM_MB:-1024}" "${radeon[@]}" \
        -net none -kernel "$boot_img" -append "$boot_args" -cdrom "$iso" \
        -chardev "stdio,id=morphos-serial,logfile=$serial_log" \
        -serial chardev:morphos-serial -D "$vm/gpu.log" "${net[@]}" "${extra[@]}" "${agp[@]}" "${audio[@]}" "$@"
fi
# A QMP socket per VM, /tmp/morphos-qmp-<qemu pid>.sock (exec keeps the PID):
# screendumps and input from outside without restarting the session.
prom=()
model=${MORPHOS_MODEL-PowerMac10,1}
nvramrc=
[ -n "$model" ] && nvramrc="\" /\" find-device \" $model\" encode-string \" model\" property device-end"
# MORPHOS_IDE_ATA4=on: MorphOS binds its UDMA "Keylargo ATA-4" driver to the first IDE bus (MORPHOS_HD, MORPHOS_HD2)
# instead of the PIO-only ATA-3: the node gets the name ata-4 and its DBDMA channel in "reg".
case "${MORPHOS_IDE_ATA4:-off}" in
    on) nvramrc="$nvramrc hex \" /pci@f2000000/mac-io@c/ata-3@20000\" find-device 20000 encode-int 1000 encode-int encode+ 8b00 encode-int encode+ 100 encode-int encode+ \" reg\" property \" ata-4\" encode-string \" model\" property \" ata-4\" encode-string \" name\" property device-end \" /aliases\" find-device \" /pci@f2000000/mac-io@c/ata-4@20000/disk@0\" encode-string \" hd\" property device-end" ;;
    off) ;;
    *) echo "MORPHOS_IDE_ATA4 must be on or off." >&2; exit 2 ;;
esac
[ -n "$nvramrc" ] && prom=(-prom-env 'use-nvramrc?=true' -prom-env "nvramrc=$nvramrc")
exec "${pin[@]}" "$qemu" "${display[@]}" "${tablet[@]}" -L "$repo/firmware/radeon" -L "$repo/prebuilt/share/pc-bios" \
    -bios "$vm/openbios-ppc" -machine mac99,via=pmu -cpu "${cpu:-G4}" -m 2048 "${fastmem[@]}" "${radeon[@]}" \
    -g 1024x768x32 -net none "${cdrom[@]}" -boot "$boot_order" "${hd[@]}" \
    -prom-env "boot-device=$boot_dev" "${prom[@]}" \
    -prom-env "boot-file=$boot_args" \
    -chardev "stdio,id=morphos-serial,logfile=$serial_log" \
    -serial chardev:morphos-serial -D "$vm/gpu.log" "${net[@]}" "${extra[@]}" "${agp[@]}" "${audio[@]}" \
    -qmp "unix:/tmp/morphos-qmp-$$.sock,server=on,wait=off" "$@"
