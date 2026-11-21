"""Minimal dependency-free QR Code generator (byte mode, ECC level M, versions 1-10).

Used to put a scannable code on the vehicle report so a buyer can open the verified
history from the windscreen sticker. Algorithm follows the ISO/IEC 18004 structure.
"""
from __future__ import annotations

ECC_PER_BLOCK_M = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
NUM_BLOCKS_M = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(n: int) -> list[int]:
    res, root = [0] * (n - 1) + [1], 1
    for _ in range(n):
        for j in range(n):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < n:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 2)
    return res


def _rs_remainder(data: list[int], div: list[int]) -> list[int]:
    res = [0] * len(div)
    for b in data:
        factor = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(div):
            res[i] ^= _gf_mul(c, factor)
    return res


def _raw_modules(ver: int) -> int:
    r = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        r -= (25 * n - 10) * n - 55
        if ver >= 7:
            r -= 36
    return r


def _data_capacity(ver: int) -> int:
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK_M[ver] * NUM_BLOCKS_M[ver]


def encode(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    for ver in range(1, 11):
        cc_bits = 8 if ver < 10 else 16
        if 4 + cc_bits + 8 * len(data) <= _data_capacity(ver) * 8:
            break
    else:
        raise ValueError("Text too long for this QR generator")
    bits: list[int] = []
    def put(val, n): bits.extend((val >> i) & 1 for i in reversed(range(n)))
    put(0b0100, 4); put(len(data), cc_bits)
    for b in data: put(b, 8)
    cap = _data_capacity(ver) * 8
    put(0, min(4, cap - len(bits)))
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8); pad ^= 0xEC ^ 0x11
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    # error correction + interleave
    nblocks, eccl = NUM_BLOCKS_M[ver], ECC_PER_BLOCK_M[ver]
    raw = _raw_modules(ver) // 8
    nshort, shortlen = nblocks - raw % nblocks, raw // nblocks
    div, blocks, k = _rs_divisor(eccl), [], 0
    for i in range(nblocks):
        dat = codewords[k:k + shortlen - eccl + (0 if i < nshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, div)
        if i < nshort: dat = dat + [0]
        blocks.append(dat + ecc)
    final = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortlen - eccl or j >= nshort:
                final.append(blk[i])
    return _build(ver, final)


def _build(ver: int, data: list[int]) -> list[list[bool]]:
    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]
    def setf(x, y, dark):
        mod[y][x] = dark; fn[y][x] = True
    for i in range(size):
        setf(6, i, i % 2 == 0); setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    if ver > 1:
        n = ver // 7 + 2
        step = (ver * 4 + n * 2 + 1) // (n * 2 - 2) * 2
        pos = list(reversed([size - 7 - i * step for i in range(n - 1)] + [6]))
        for i, ay in enumerate(pos):
            for j, ax in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    def format_bits(mask):
        d = (0 << 3) | mask            # ECC level M = 0
        rem = d
        for _ in range(10): rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((d << 10) | rem) ^ 0x5412
        g = lambda i: (bits >> i) & 1 == 1
        for i in range(6): setf(8, i, g(i))
        setf(8, 7, g(6)); setf(8, 8, g(7)); setf(7, 8, g(8))
        for i in range(9, 15): setf(14 - i, 8, g(i))
        for i in range(8): setf(size - 1 - i, 8, g(i))
        for i in range(8, 15): setf(8, size - 15 + i, g(i))
        setf(8, size - 8, True)
    format_bits(0)                      # reserve area
    if ver >= 7:
        rem = ver
        for _ in range(12): rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (ver << 12) | rem
        for i in range(18):
            b = (bits >> i) & 1 == 1
            a, c = size - 11 + i % 3, i // 3
            setf(a, c, b); setf(c, a, b)
    i = 0
    for right in range(size - 1, 0, -2):
        if right == 6: right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < len(data) * 8:
                    mod[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
    masks = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]
    def apply(m):
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and masks[m](x, y):
                    mod[y][x] = not mod[y][x]
    def penalty():
        p = 0
        for grid in (mod, list(zip(*mod))):
            for row in grid:
                run = 1
                for a, b in zip(row, row[1:]):
                    run = run + 1 if a == b else 1
                    if run == 5: p += 3
                    elif run > 5: p += 1
        for y in range(size - 1):
            for x in range(size - 1):
                if mod[y][x] == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]: p += 3
        dark = sum(map(sum, mod)); p += abs(dark * 20 - size * size * 10) // (size * size) * 10
        return p
    best, best_p = 0, None
    for m in range(8):
        apply(m); format_bits(m); pen = penalty(); apply(m)
        if best_p is None or pen < best_p: best, best_p = m, pen
    apply(best); format_bits(best)
    return mod


def svg(text: str, border: int = 3, cls: str = "qr") -> str:
    m = encode(text); n = len(m) + border * 2
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, d in enumerate(row) if d)
    return (f'<svg class="{cls}" viewBox="0 0 {n} {n}" shape-rendering="crispEdges" role="img" '
            f'aria-label="QR code linking to this vehicle report"><rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path d="{path}" fill="#18222d"/></svg>')
