#!/usr/bin/env python3
# drainsym.py TRACE ELF: names the return addresses of a PPCGPU_DRAIN_TRACE file
# by finding their code bytes in the ELF's .text (relocated words are wildcards).
import bisect, re, struct, subprocess, sys
import numpy as np

trace, elf = sys.argv[1], sys.argv[2]
data = open(elf, 'rb').read()

shoff, = struct.unpack('>I', data[32:36])
shentsize, shnum, shstrndx = struct.unpack('>HHH', data[46:52])
secs = [struct.unpack('>IIIIIIIIII', data[shoff + i * shentsize: shoff + (i + 1) * shentsize]) for i in range(shnum)]
strtab = secs[shstrndx]
name = lambda off: data[strtab[4] + off: data.index(b'\0', strtab[4] + off)].decode()
text = next(s for s in secs if name(s[0]) == '.text')
words = np.frombuffer(data[text[4]: text[4] + text[5]], dtype='>u4').astype(np.uint32)

relocated = np.zeros(len(words), dtype=bool)
rela = next(s for s in secs if name(s[0]) == '.rela.text')
r = np.frombuffer(data[rela[4]: rela[4] + rela[5]], dtype='>u4').reshape(-1, 3)
relocated[(r[:, 0] // 4).astype(np.int64)] = True

syms = []
for line in subprocess.run(['ppc-morphos-nm', '-n', '-C', elf], capture_output=True, text=True).stdout.split('\n'):
    p = line.split(' ', 2)
    if len(p) == 3 and p[1] in 'tT':
        syms.append((int(p[0], 16), p[2]))
addrs = [a for a, _ in syms]

def where(snippet):
    w = np.frombuffer(bytes.fromhex(snippet), dtype='>u4').astype(np.uint32)
    best = None
    for k in (5, 6, 2, 1, 7, 0, 4, 3):
        cand = np.nonzero(words[k:len(words) - 8 + k] == w[k])[0]
        if 0 < len(cand) < 20000:
            break
    else:
        return None
    hits = []
    for c in cand:
        seg, rel = words[c:c + 8], relocated[c:c + 8]
        if np.all((seg == w) | rel) and (seg == w).sum() >= 6:
            hits.append(c)
    return hits[0] * 4 + 16 if len(hits) == 1 else None

lines = open(trace).read().split('\n')
cut = lines.index('code at addr-16:')
names, bases = {}, {}
for l in lines[cut + 1:]:
    if not l.strip():
        continue
    a, snip = l.split()
    off = where(snip)
    if off is None:
        continue
    i = bisect.bisect_right(addrs, off) - 1
    names[a] = f'{syms[i][1]}+0x{off - syms[i][0]:x}' if i >= 0 else f'.text+0x{off:x}'
    base = int(a, 16) - off
    bases[base] = bases.get(base, 0) + 1
print('reVC .text loaded at', ', '.join(f'{b:08x} ({n})' for b, n in sorted(bases.items(), key=lambda x: -x[1])))
print(lines[0])
for l in lines[1:cut]:
    if not l.strip():
        continue
    m = re.match(r'(.* us)\s+(.*)', l)
    print(m.group(1) + '  ' + ' <- '.join(names.get(a, a) for a in m.group(2).split()))
