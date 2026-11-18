#!/bin/bash
# macOS (Apple silicon): configure and build qemu/build the way the Mac
# benchmarks validated it: -O3, LTO, -mcpu=native, clang PGO.
#
#   tools/build-mac-qemu.sh          build (trains a profile first if there is none)
#   tools/build-mac-qemu.sh train    retrain the profile, then build
#
# Training boots a clone of vm/morphos/morphos-one.img for each guest command
# in .buildenv/pgo-workloads. See docs/DEVELOPING.md for the file format.
set -euo pipefail
[ "$(uname)" = Darwin ] || { echo "macOS only; Linux: tools/build-linux-qemu.sh" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/.." && pwd)
q=$repo/qemu
export PATH=/opt/homebrew/bin:$PATH
py=$repo/.pyvenv/bin/python3
"$py" -c 'import distlib' 2>/dev/null || { rm -rf "$repo/.pyvenv"; python3 -m venv "$repo/.pyvenv"; "$py" -m pip install -q distlib; }

build() {   # DIR off|generate|use
    mkdir -p "$1"
    (cd "$1" && ../configure --python="$py" --target-list=ppc-softmmu --disable-docs \
        --disable-sdl --disable-werror --disable-tools --disable-fuse --enable-slirp \
        -Dhardening=false -Dqom_cast_debug=false \
        -Doptimization=3 -Db_lto=true -Db_pgo="$2" -Ddtc:b_pgo="$2" \
        -Dberkeley-softfloat-3:b_pgo="$2" -Dberkeley-testfloat-3:b_pgo="$2" \
        --extra-cflags=-mcpu=native --extra-objcflags=-mcpu=native) > "$1/mac-configure.log" 2>&1 \
        || { tail -20 "$1/mac-configure.log"; exit 1; }
    ninja -C "$1" qemu-system-ppc
}

if [ "${1:-}" = train ] || [ ! -f "$q/build/default.profdata" ]; then
    workloads=$repo/.buildenv/pgo-workloads
    [ -s "$workloads" ] || { echo "Training needs $workloads (see docs/DEVELOPING.md)" >&2; exit 1; }
    gen=$q/build-pgo-train
    build "$gen" generate
    rm -f "$gen"/*.profraw
    t=$repo/vm/morphos/pgo-train-$$.img
    trap 'rm -f "$t"' EXIT
    n=0
    while read -r seconds command <&3 || [ -n "$seconds$command" ]; do
        [[ $seconds =~ ^[1-9][0-9]*$ && -n $command ]] || { echo "Expected seconds and a guest command in $workloads" >&2; exit 1; }
        n=$((n + 1))
        pgrep -q qemu-system-ppc && { echo "a VM is running; quit it and run this again" >&2; exit 1; }
        [ -f "$t" ] || cp -c "$repo/vm/morphos/morphos-one.img" "$t"
        LLVM_PROFILE_FILE="$gen/%p.profraw" PPCOSX_QEMU=$gen/qemu-system-ppc MORPHOS_HD=$t \
        MORPHOS_BOOT=hd MORPHOS_NET=none MORPHOS_VBLANK_HZ=240 \
            python3 "$repo/tools/morphos-guest-run.py" --mode fork --label "pgo-train-$$-$n" \
            --command "$command" --display cocoa --seconds "$seconds" --every "$seconds"
    done 3< "$workloads"
    mkdir -p "$q/build"
    xcrun llvm-profdata merge -o "$q/build/default.profdata" "$gen"/*.profraw
    [ -f "$q/build/build.ninja" ] && ninja -C "$q/build" -t clean
fi
build "$q/build" use
ls -l "$q/build/qemu-system-ppc"
