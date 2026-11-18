import struct, sys, zlib
src, dst, hz = sys.argv[1], sys.argv[2], float(sys.argv[3])
cap = 316e6  # the highest clock tested (2048x1152 at 120 Hz); 396 MHz is refused by the Radeon driver
d = bytearray(open(src, 'rb').read())
p = 12
while p + 8 <= len(d):
    cid = bytes(d[p:p+4]); n = struct.unpack('>I', d[p+4:p+8])[0]
    if cid[:2] == b'MD':
        body = p + 8
        for r in range(body + 4, body + n, 32):
            f = list(struct.unpack('>I12H', d[r:r+28]))
            h = f[1]
            if hz * f[6] * f[12] > cap:
                f[2:7] = [h + 48, h + 80, h, h + 144, h + 160]
            f[0] = int(round(hz * f[6] * f[12], -3))
            assert f[0] <= cap
            print(cid.decode(), f'{h}x{f[7]} htotal {f[6]} {f[0]/1e6:.1f} MHz {f[0]/f[6]/f[12]:.2f} Hz')
            d[r:r+28] = struct.pack('>I12H', *f)
        d[body:body+4] = struct.pack('>I', ~zlib.crc32(bytes(d[body+4:body+n])) & 0xffffffff)
    p += 8 + n + (n & 1)
open(dst, 'wb').write(d)
