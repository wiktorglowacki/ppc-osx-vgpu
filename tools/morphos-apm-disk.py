#!/usr/bin/env python3
"""Write an Apple partition map with one MorphOS partition onto a disk image,
laid out like MorphOS's installer does (512-byte blocks, map in blocks 1-63,
partition from block 64; entry type "MorphOS_0x<dostype>").

    tools/morphos-apm-disk.py IMAGE NAME DOSTYPE   e.g. apps.img DH3 0x53465300 (SFS)
                                                       xfer.img DH4 0x45585402 (ext2)
The image must already have its final size (truncate -s 64G IMAGE).  The
partition is left unformatted (format it in MorphOS) unless you write a
file system image at byte offset 64*512 yourself."""
import os, struct, sys

def entry(mapcnt, start, count, name, ptype, status, dcnt=0):
    e = struct.pack('>2sHIII32s32sIII', b'PM', 0, mapcnt, start, count,
                    name.encode(), ptype.encode(), 0, dcnt, status)
    return e.ljust(512, b'\0')

img, name, dostype = sys.argv[1], sys.argv[2], int(sys.argv[3], 16)
blocks = os.path.getsize(img) // 512
with open(img, 'r+b') as f:
    f.write(struct.pack('>2sHI', b'ER', 512, blocks).ljust(512, b'\0'))
    f.write(entry(2, 1, 63, 'Apple', 'Apple_partition_map', 0x3, dcnt=63))
    f.write(entry(2, 64, blocks - 64, name, f'MorphOS_0x{dostype:08X}', 0x40000037))
print(f'{img}: {blocks} blocks, {name} {dostype:#010x} at block 64, {blocks - 64} blocks')
