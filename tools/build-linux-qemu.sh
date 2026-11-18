#!/bin/bash
# Linux: configure and build qemu/build the way tools/build-mac-qemu.sh does on
# the Mac: -O3, LTO, -march=native, GCC PGO.
#
#   tools/build-linux-qemu.sh              build (trains a profile first if there is none)
#   tools/build-linux-qemu.sh train        retrain the profile, then build
#   tools/build-linux-qemu.sh OUT.tar.xz   the portable prebuilt tarball (below)
#
# Training boots a clone of vm/morphos/morphos-one.img for each guest command
# in .buildenv/pgo-workloads. See docs/DEVELOPING.md for the file format.
#
# The tarball is the prebuilt QEMU that "ppcosx setup" downloads on Linux.
# Packaging needs patchelf, the build dependencies (see APT_DEPS in ppcosx) and the qemu/
# submodule checked out.  The GitHub workflow .github/workflows/
# qemu-linux.yml runs this on Ubuntu 24.04 x86_64 and
# attaches the result to the release "qemu-<first 12 of the qemu commit>".
#
# Layout: ppcosx-qemu/{bin/qemu-system-ppc, bin/qemu-img, share/pc-bios/,
# VERSION (the actual qemu commit), licenses/, SOURCE.txt, BUILD.sh, SHA256SUMS}.
# Distribution requires clean, matching bundle/QEMU commits and public HTTPS
# source repositories (PPCOSX_SOURCE_URL/PPCOSX_BUNDLE_SOURCE_URL override origin).
set -euo pipefail

case "${1:-}" in ""|train)
    repo=$(cd "$(dirname "$0")/.." && pwd)
    q=$repo/qemu
    d=$repo/.buildenv/morphos-deps/usr/lib/x86_64-linux-gnu
    ld=()
    if [ -d "$d" ]; then
        export PYTHONPATH=$repo/.buildenv/python LD_LIBRARY_PATH=$d PKG_CONFIG_PATH=$d/pkgconfig
        ld=(--extra-ldflags=-Wl,-rpath,$d)
    fi
    build() {   # DIR off|generate|use
        mkdir -p "$1"
        (cd "$1" && ../configure --target-list=ppc-softmmu --disable-docs --enable-sdl --enable-opengl --enable-gtk \
            --enable-pixman --enable-tcg --enable-fdt=internal --disable-werror --disable-curses \
            --disable-vnc --disable-tools --enable-slirp --enable-pa --enable-alsa --enable-pipewire \
            --without-default-features -Dhardening=false -Dqom_cast_debug=false "${ld[@]}" \
            -Doptimization=3 -Db_lto=true -Db_pgo="$2" -Ddtc:b_pgo="$2" -Ddtc:werror=false \
            -Dberkeley-softfloat-3:b_pgo="$2" -Dberkeley-testfloat-3:b_pgo="$2" \
            "--extra-cflags=-march=native -Wno-error=coverage-mismatch") > "$1/linux-configure.log" 2>&1 \
            || { tail -20 "$1/linux-configure.log"; exit 1; }
        ninja -C "$1" qemu-system-ppc
    }
    if [ "${1:-}" = train ] || [ -z "$(find "$q/build" -type f -name '*.gcda' -print -quit 2>/dev/null)" ]; then
        workloads=$repo/.buildenv/pgo-workloads
        [ -s "$workloads" ] || { echo "Training needs $workloads (see docs/DEVELOPING.md)" >&2; exit 1; }
        gen=$q/build-pgo-train
        build "$gen" generate
        find "$gen" -name '*.gcda' -delete
        t=$repo/vm/morphos/pgo-train-$$.img
        trap 'rm -f "$t" "$repo"/vm/morphos/guest-pgo-train-$$-* "$repo"/vm/morphos/pgo-train-$$-*.log' EXIT
        n=0
        while read -r seconds command <&3 || [ -n "$seconds$command" ]; do
            [[ $seconds =~ ^[1-9][0-9]*$ && -n $command ]] || { echo "Expected seconds and a guest command in $workloads" >&2; exit 1; }
            n=$((n + 1))
            pgrep -x qemu-system-ppc >/dev/null && { echo "a VM is running; quit it and run this again" >&2; exit 1; }
            [ -f "$t" ] || cp --reflink=always "$repo/vm/morphos/morphos-one.img" "$t"
            PPCOSX_QEMU=$gen/qemu-system-ppc MORPHOS_HD=$t MORPHOS_BOOT=hd MORPHOS_NET=none \
            MORPHOS_VBLANK_HZ=240 MORPHOS_AUDIO=none MORPHOS_POWERFOX_DISK=off \
                python3 "$repo/tools/morphos-guest-run.py" --mode fork --label "pgo-train-$$-$n" \
                --command "$command" --seconds "$seconds" --every "$seconds"
        done 3< "$workloads"
        mkdir -p "$q/build"
        find "$q/build" -name '*.gcda' -delete
        (cd "$gen" && find . -name '*.gcda' -exec cp --parents {} "$q/build/" \;)
        [ -f "$q/build/build.ninja" ] && ninja -C "$q/build" -t clean
    fi
    build "$q/build" use
    ls -l "$q/build/qemu-system-ppc"
    exit 0 ;;
esac

command -v patchelf >/dev/null || { echo "Install patchelf to build the Linux release archive." >&2; exit 1; }
out=$(realpath -m "${1:?usage: $0 OUT.tar.xz}")
repo=$(cd "$(dirname "$0")/.." && pwd)
qemu="$repo/qemu"
build="$repo/.buildenv/qemu-dist"
[ -f "$qemu/configure" ] || { echo "qemu/ is not checked out" >&2; exit 1; }
qemu_commit=$(git -C "$qemu" rev-parse HEAD)
bundle_commit=$(git -C "$repo" rev-parse HEAD)
pin=$(git -C "$repo" rev-parse HEAD:qemu)
[ "$qemu_commit" = "$pin" ] || { echo "qemu HEAD differs from the committed bundle pin; commit the selected pin before packaging." >&2; exit 1; }
for tree in "$repo" "$qemu"; do
    [ -z "$(git -C "$tree" status --porcelain --untracked-files=normal)" ] || {
        echo "Release source tree is dirty: $tree" >&2; exit 1;
    }
done
source_url=${PPCOSX_SOURCE_URL:-$(git -C "$qemu" remote get-url origin)}
bundle_source_url=${PPCOSX_BUNDLE_SOURCE_URL:-$(git -C "$repo" remote get-url origin)}
for url in "$source_url" "$bundle_source_url"; do
    case "$url" in
        https://*'@'*|https://*'?'*|https://*'#'*) echo "Source URL must be a public HTTPS repository without credentials, query or fragment." >&2; exit 1 ;;
        https://*) ;;
        *) echo "Set PPCOSX_SOURCE_URL and PPCOSX_BUNDLE_SOURCE_URL to the published HTTPS source repositories before packaging." >&2; exit 1 ;;
    esac
done
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

# QEMU clears $build/pyvenv when it creates its own environment.
venv="$build/bootstrap-venv"
rm -rf "$build"
mkdir -p "$build"
python3 -m venv "$venv"
"$venv/bin/python3" -m pip install -q distlib

(cd "$build" && env -u CFLAGS -u CXXFLAGS -u CPPFLAGS -u LDFLAGS "$qemu/configure" --python="$venv/bin/python3" \
    --target-list=ppc-softmmu --disable-docs --enable-sdl --enable-opengl \
    --enable-gtk --enable-pa --enable-slirp --disable-werror -Ddtc:werror=false -Doptimization=2 -Db_lto=false -Db_pgo=off)
ninja -C "$build" -j "$(nproc)" qemu-system-ppc qemu-img

d="$stage/ppcosx-qemu"
mkdir -p "$d/bin" "$d/share/pc-bios" "$d/licenses"
cp "$build/qemu-system-ppc" "$build/qemu-img" "$d/bin/"
for bin in "$d/bin/"*; do
    # Clear the path string before removing its ELF tag from the release copy.
    if [ -n "$(patchelf --print-rpath "$bin")" ]; then
        patchelf --set-rpath '' "$bin"
    fi
    patchelf --remove-rpath "$bin"
done
strip "$d/bin/"*
# Firmware is supplied separately; do not silently redistribute unqualified
# OpenBIOS/NDRV binaries in this artifact.
cp -r "$qemu/pc-bios/keymaps" "$d/share/pc-bios/"
cp "$qemu/COPYING" "$qemu/LICENSE" "$d/licenses/"
cp "$repo/licenses/SPIRV-Cross-copyright" "$repo/licenses/Apache-2.0.txt" "$d/licenses/"
sed '/^static const struct /,$d' "$qemu/hw/display/r300/r300_regnames.h" > "$d/licenses/Mesa-r300-notice.txt"
cp "$repo/LICENSE" "$d/licenses/bundle-LICENSE"
cp "$repo/tools/build-linux-qemu.sh" "$d/BUILD.sh"
printf '%s\n' "$qemu_commit" > "$d/VERSION"
cat > "$d/SOURCE.txt" <<EOF
QEMU source repository: $source_url
QEMU source commit: $qemu_commit
Bundle source repository: $bundle_source_url
Bundle source commit: $bundle_commit

Obtain these exact commits and initialize their submodules recursively.
Run tools/build-linux-qemu.sh OUTPUT.tar.xz from the bundle checkout.
BUILD.sh is the release's copy of that recipe; dependencies are listed in
.github/workflows/qemu-linux.yml at the bundle commit above.
Build: portable host target, optimization=2; no -march=native, LTO or PGO.
Firmware, MorphOS, guest disks and optional guest applications are not included.
The source repositories must remain publicly available for this release.
EOF
(cd "$d" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)

tar -C "$stage" -cJf "$out" ppcosx-qemu
echo "wrote $out ($(du -h "$out" | cut -f1), qemu $(cat "$d/VERSION"))"
