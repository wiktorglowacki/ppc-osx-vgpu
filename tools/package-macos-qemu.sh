#!/bin/bash
# Package the native binaries produced by ./ppcosx setup --build.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $(uname -s) == Darwin && $(uname -m) == arm64 && $(brew --prefix) == /opt/homebrew ]]
pin=$(git rev-parse HEAD:qemu)
[[ $(git -C qemu rev-parse HEAD) == "$pin" ]]
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
d=$stage/ppcosx-qemu
mkdir -p "$d"/{bin,tools,licenses,share/pc-bios}
cp qemu/build/{qemu-system-ppc,qemu-img} "$d/bin/"
cp tools/morphos-run.sh "$d/tools/"
cp -R qemu/pc-bios/keymaps "$d/share/pc-bios/"
cp qemu/COPYING qemu/LICENSE licenses/SPIRV-Cross-copyright licenses/Apache-2.0.txt "$d/licenses/"
cp LICENSE "$d/licenses/bundle-LICENSE"
sed '/^static const struct /,$d' qemu/hw/display/r300/r300_regnames.h > "$d/licenses/Mesa-r300-notice.txt"
cp docs/MACOS-ARCHIVE.md "$d/README.md"
printf '%s\n' "$pin" > "$d/VERSION"
printf 'Bundle source commit: %s\nQEMU source commit: %s\n' "$(git rev-parse HEAD)" "$pin" > "$d/SOURCE.txt"

# MoltenVK is discovered at runtime. Python is used to prepare OpenBIOS.
printf '%s\n' molten-vk python > "$stage/formulae"
for bin in "$d"/bin/*; do
    lipo -verify_arch arm64 "$bin"
    strip -S "$bin"
    codesign --force --sign - "$bin"
    codesign --verify "$bin"
    otool -L "$bin" | awk '
        NR == 1 || $1 ~ /^\/(usr\/lib|System\/Library)\// { next }
        $1 ~ /^\/opt\/homebrew\/opt\// { split($1, path, "/"); print path[5]; next }
        { print "Unexpected library path: " $1 > "/dev/stderr"; exit 1 }
    ' >> "$stage/formulae"
done
LC_ALL=C sort -u "$stage/formulae" > "$d/runtime-formulae.txt"
(cd "$d" && find . -type f ! -name SHA256SUMS -exec shasum -a 256 {} + > SHA256SUMS)
COPYFILE_DISABLE=1 tar -C "$stage" -czf ppcosx-qemu-macos-arm64.tar.gz ppcosx-qemu
