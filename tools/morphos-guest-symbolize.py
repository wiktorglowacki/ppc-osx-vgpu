"""Attribute a perf profile of QEMU (run with -perfmap) to guest functions of a
relocatable MorphOS ELF: guest-symbolize.py PERF_DATA RAM_DUMP ELF FUNCS_TXT
FUNCS_TXT: lines "hexoff size name" (readelf -s, FUNC entries, .text-relative)."""
import re, subprocess, sys, bisect
perf, ram, elf, funcs = sys.argv[1:5]
# 1. where .text sits in guest RAM: search the first 256 bytes of .text (after the ELF header's section offset)
import struct
e = open(elf, 'rb').read()
shoff = struct.unpack('>I', e[0x20:0x24])[0]; shentsize, shnum, shstrndx = struct.unpack('>HHH', e[0x2e:0x34])
secs = []
for i in range(shnum):
    s = e[shoff + i * shentsize: shoff + (i + 1) * shentsize]
    name, typ, flags, addr, off, size = struct.unpack('>IIIIII', s[:24]); secs.append((name, off, size, flags))
strtab = secs[shstrndx]
def secname(n): o = strtab[1] + n; return e[o:e.index(b'\0', o)].decode()
text = [s for s in secs if secname(s[0]) == '.text'][0]
# the loader relocates .text in place (a relocatable ELF): probe a window free of .rela.text entries
rela = [s for s in secs if secname(s[0]) == '.rela.text'][0]
import array
relocs = set()
for i in range(rela[2] // 12):
    r_off = struct.unpack_from('>I', e, rela[1] + 12 * i)[0]; relocs.add(r_off & ~3)
win = 512; probe_off = None
for cand in range(0x10000, text[2] - win, 256):
    if not any((cand + k) in relocs for k in range(0, win, 4)):
        probe_off = cand; break
probe = e[text[1] + probe_off: text[1] + probe_off + win]
print('reloc-free probe at .text+%#x' % probe_off)
d = open(ram, 'rb')
bases = []; off = 0; chunk = 64 << 20
while True:
    d.seek(off); b = d.read(chunk)
    if not b: break
    k = 0
    while True:
        k = b.find(probe, k)
        if k < 0: break
        bases.append(off + k - probe_off); k += 1
    off += chunk - 4096
bases = sorted(set(bases)); print('probe matches (file copies and the loaded text):', [hex(x) for x in bases])
base = None
# 2. perf symbols of the form "ADDR SIZE guest-..." come from the map; perf report gives names per sample
out = subprocess.run(['perf', 'report', '-i', perf, '--no-children', '-g', 'none', '--sort', 'symbol', '--stdio'],
                     capture_output=True, text=True).stdout
pcs = []
for line in out.splitlines():
    m = re.match(r'\s+([\d.]+)%\s+\[\.\]\s+(\S+)', line)
    if not m: continue
    pct, sym = float(m.group(1)), m.group(2)
    mm = re.search(r'0x([0-9a-f]+)', sym)
    if 'subject' in sym or 'guest' in sym or sym.startswith('0x'):
        if mm: pcs.append((pct, int(mm.group(1), 16), sym))
    else:
        pcs.append((pct, None, sym))
# choose the base under which the most sample weight lands inside .text
best = None
for cand in bases:
    w = sum(pct for pct, pc, sym in pcs if pc is not None and 0 <= pc - cand < text[2])
    print('  base %#x: %.1f%% of samples inside .text' % (cand, w))
    if best is None or w > best[0]: best = (w, cand)
base = best[1] if best else None
# 3. map guest pcs to functions
fl = []
for line in open(funcs):
    o, sz, name = line.split(None, 2); fl.append((int(o, 16), int(sz), name.strip()))
fl.sort(); starts = [f[0] for f in fl]
agg = {}
for pct, pc, sym in pcs:
    if pc is None or base is None:
        key = sym if pc is None else 'guest-unknown'
    else:
        rel = pc - base
        i = bisect.bisect_right(starts, rel) - 1
        key = fl[i][2] if 0 <= i < len(fl) and rel < fl[i][0] + fl[i][1] + 64 else f'guest-other({rel:#x})'
    agg[key] = agg.get(key, 0) + pct
for k, v in sorted(agg.items(), key=lambda x: -x[1])[:40]:
    print(f'{v:6.2f}%  {k}')
