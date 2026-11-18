#!/bin/bash
# Boots the installed MorphOS with everything attached: system disk (DH0-DH2),
# Apps disk (DH3), the exchange disk XFER:, PowerFox data disk
# when they exist, at 2048x1152. MORPHOS_POWERFOX_DISK=off skips the
# default PowerFox disk. Any MORPHOS_* variable still overrides (see
# tools/morphos-run.sh); extra arguments go to QEMU.
cd "$(dirname "$0")" || exit 1
d=vm/morphos
export MORPHOS_HD=${MORPHOS_HD:-$d/morphos-one.img} MORPHOS_BOOT=${MORPHOS_BOOT:-hd}
export MORPHOS_MODE=${MORPHOS_MODE:-2048x1152} MORPHOS_IDE_ATA4=${MORPHOS_IDE_ATA4:-on}
[ -f $d/morphos-apps.img ] && export MORPHOS_HD2=${MORPHOS_HD2:-$d/morphos-apps.img}
[ -f $d/morphos-xfer.img ] && export MORPHOS_XFER=${MORPHOS_XFER:-$d/morphos-xfer.img}
[ -f $d/morphos-powerfox.img ] && export MORPHOS_POWERFOX_DISK=${MORPHOS_POWERFOX_DISK:-$d/morphos-powerfox.img}
exec tools/morphos-run.sh "$@"
