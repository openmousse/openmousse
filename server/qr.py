"""小的二维码编码器（2026-09-28，给邀请码用）：字节模式、纠错 M 级、版本 1–10（最多 213 字节，邀请码约 110 字节）。
不装第三方库：算法照 ISO/IEC 18004（Reed–Solomon 用 GF(256)、生成多项式 0x11D，八种掩码按罚分挑最小的）。
matrix(text) → 行列表，每行是 "0"/"1" 串（1 = 深色），不含四格白边；svg(text) → 一段内嵌 SVG（落地页用，不用图片，CSP 不用放开）。
"""
from __future__ import annotations

import html

# 版本 1–10、纠错 M 级：每块的纠错码字数、块数（ISO/IEC 18004 表 9）
ECC_PER_BLOCK = (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
BLOCKS = (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
MAX_VERSION = 10
FORMAT_M = 0  # 纠错级别在格式信息里的两位：L=1 M=0 Q=3 H=2


def _raw_modules(ver: int) -> int:
    """这个版本里能放数据（含纠错）的格子数。"""
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        n -= (25 * align - 10) * align - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[ver] * BLOCKS[ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data: bytes) -> tuple[int, list[int]]:
    """挑最小的版本，拼好数据码字（模式、长度、数据、结束符、填充），分块加纠错、交织。"""
    for ver in range(1, MAX_VERSION + 1):
        count_bits = 8 if ver <= 9 else 16
        capacity = _data_codewords(ver) * 8
        if 4 + count_bits + 8 * len(data) <= capacity:
            break
    else:
        raise ValueError("too long for a QR code here")
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))

    put(0b0100, 4)
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, capacity - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    nblocks, ecclen = BLOCKS[ver], ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    shortlen = raw // nblocks
    divisor = _rs_divisor(ecclen)
    blocks, k = [], 0
    for i in range(nblocks):
        n = shortlen - ecclen + (0 if i < nshort else 1)
        dat = words[k:k + n]
        k += n
        ecc = _rs_remainder(dat, divisor)
        if i < nshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortlen - ecclen or j >= nshort:
                out.append(blk[i])
    return ver, out


class _Grid:
    def __init__(self, ver: int) -> None:
        self.ver = ver
        self.size = ver * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.fn[y][x] = True

    def align_positions(self) -> list[int]:
        if self.ver == 1:
            return []
        n = self.ver // 7 + 2
        step = (self.ver * 8 + n * 3 + 5) // (n * 4 - 4) * 2
        return sorted([self.size - 7 - i * step for i in range(n - 1)] + [6])

    def function_patterns(self) -> None:
        s = self.size
        for i in range(s):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (s - 4, 3), (3, s - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < s and 0 <= y < s:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = self.align_positions()
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                dark = (bits >> i) & 1 == 1
                a, b = s - 11 + i % 3, i // 3
                self.set_fn(a, b, dark)
                self.set_fn(b, a, dark)

    def format_bits(self, mask: int) -> None:
        data = FORMAT_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        s = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(s - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, s - 15 + i, bit(i))
        self.set_fn(8, s - 8, True)

    def place(self, words: list[int]) -> None:
        s, i, total = self.size, 0, len(words) * 8
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = s - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < total:
                        self.m[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        f = MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]

    def penalty(self) -> int:
        s, m, score = self.size, self.m, 0
        lines = ["".join("1" if v else "0" for v in row) for row in m]
        cols = ["".join("1" if m[y][x] else "0" for y in range(s)) for x in range(s)]
        for line in lines + cols:
            run, prev = 0, ""
            for ch in line + "x":
                if ch == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run, prev = 1, ch
            padded = "0000" + line + "0000"
            for pat in ("10111010000", "00001011101"):
                start = padded.find(pat)
                while start != -1:
                    score += 40
                    start = padded.find(pat, start + 1)
        for y in range(s - 1):
            for x in range(s - 1):
                v = m[y][x]
                if v == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(line.count("1") for line in lines)
        k = abs(dark * 20 - s * s * 10) // (s * s)
        return score + k * 10


MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def matrix(text: str) -> list[str]:
    ver, words = _codewords(text.encode("utf-8"))
    best: tuple[int, list[list[bool]]] | None = None
    for mask in range(8):
        g = _Grid(ver)
        g.function_patterns()
        g.place(words)
        g.apply_mask(mask)
        g.format_bits(mask)
        p = g.penalty()
        if best is None or p < best[0]:
            best = (p, [row[:] for row in g.m])
    assert best is not None
    return ["".join("1" if v else "0" for v in row) for row in best[1]]


def path(rows: list[str], quiet: int = 4) -> str:
    """深色格子拼成一条 SVG path（每行连着的格子合成一段），坐标按格子算、加白边。"""
    out = []
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            if row[x] == "1":
                start = x
                while x < len(row) and row[x] == "1":
                    x += 1
                out.append(f"M{start + quiet} {y + quiet}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return "".join(out)


def svg(text: str, px: int = 240, label: str = "") -> str:
    rows = matrix(text)
    n = len(rows) + 8
    aria = f' role="img" aria-label="{html.escape(label)}"' if label else ' aria-hidden="true"'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{px}" height="{px}" shape-rendering="crispEdges"{aria}>'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path(rows)}" fill="#000"/></svg>')
