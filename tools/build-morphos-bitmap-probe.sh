#!/bin/bash
# Cross-build with host clang and PowerPC GNU binutils; installs nothing.
set -euo pipefail
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "Usage: $0 /path/to/Development/gg /path/to/probe [source.c]" >&2
    exit 2
fi
sdk=$1
output=$2
repo=$(cd "$(dirname "$0")/.." && pwd)
clang=${CLANG:-clang}
prefix=${PPC_BINUTILS_PREFIX:-powerpc64-linux-gnu-}
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
"$clang" --target=powerpc-unknown-linux-gnu -m32 -mbig-endian \
    -fno-pic -ffreestanding -funsigned-char -fno-addrsig \
    -D__MORPHOS__ -D__PPC__ -D_CALL_SYSV -D__libnix__ -Dlibnix \
    -nostdinc -isystem "$sdk/os-include" -isystem "$sdk/includestd" \
    -isystem "$sdk/include" -isystem "$("$clang" -print-resource-dir)/include" \
    -O2 -Wall -Wextra -c "${3:-$repo/tools/morphos-bitmap-probe.c}" \
    -o "$temporary/probe.o"
"${prefix}ld" -m elf32ppc -r -d --strip-debug --no-warn-execstack \
    "$sdk/ppc-morphos/lib/libnix/crt0.o" "$temporary/probe.o" \
    --start-group "$sdk/ppc-morphos/lib/libnix/libGL.a" \
    "$sdk/ppc-morphos/lib/libnix/libc.a" "$sdk/ppc-morphos/lib/libabox.a" \
    "$sdk/ppc-morphos/lib/libaboxstubs.a" "$sdk/ppc-morphos/lib/libnix/libauto.a" \
    --end-group -o "$output"
"${prefix}objcopy" --remove-section=.llvm_addrsig --remove-section=.comment \
    --remove-section=.eh_frame --remove-section=.note.GNU-stack "$output"
undefined=$("${prefix}nm" -u "$output")
if [ -n "$undefined" ]; then
    printf 'Unresolved symbols in probe:\n%s\n' "$undefined" >&2
    exit 1
fi
chmod +x "$output"
echo "Built $output"
