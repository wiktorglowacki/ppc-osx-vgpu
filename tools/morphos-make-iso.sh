#!/bin/bash
# Build an ISO 9660 image (Joliet + Rock Ridge) from a directory, for
# MORPHOS_EXTRA_ISO:  tools/morphos-make-iso.sh DIR OUT.iso
set -euo pipefail
[ $# = 2 ] || { echo "usage: $0 DIR OUT.iso" >&2; exit 2; }
xorriso -as mkisofs -quiet -o "$2" -J -R -V EXTRA "$1"
echo "wrote $2 ($(du -h "$2" | cut -f1))"
