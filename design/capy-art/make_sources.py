"""Draw the grayscale source sprites for the Capy dither art.

Every sprite is built from our own cmmn mark (the polygon in
commonplace/web/static/icon.svg) with plain gradients, so the art owns no
third-party pixels. Output: 8-bit grayscale PNG files next to this script;
scripts/capy_art.py turns them into the inline SVG the Deck ships.

Usage: python3 design/capy-art/make_sources.py
"""

import math
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The icon.svg path, one polygon on a 16 x 16 grid.
MARK = [
    (1, 12),
    (8, 16),
    (15, 12),
    (15, 9),
    (11, 9),
    (11, 10),
    (8, 12),
    (5, 10),
    (5, 6),
    (8, 4),
    (11, 6),
    (11, 7),
    (15, 7),
    (15, 4),
    (8, 0),
    (1, 4),
]
SS = 3  # samples per axis for anti-aliased coverage


def inside(poly, x, y):
    hit = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            hit = not hit
    return hit


def place(scale, ox, oy):
    return [(ox + x * scale, oy + y * scale) for x, y in MARK]


def canvas(w, h):
    return [[0.0] * w for _ in range(h)]


def paint(img, shape, shade):
    """Add coverage * shade(x, y) for a point-test shape; shade returns 0..1."""
    h, w = len(img), len(img[0])
    for py in range(h):
        for px in range(w):
            hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    if shape((px + (sx + 0.5) / SS), (py + (sy + 0.5) / SS)):
                        hits += 1
            if hits:
                img[py][px] = max(img[py][px], hits / SS**2 * shade(px, py))


def mark(scale, ox, oy):
    poly = place(scale, ox, oy)
    return lambda x, y: inside(poly, x, y)


def ring(cx, cy, r0, r1):
    return lambda x, y: r0 <= math.hypot(x - cx, y - cy) <= r1


def stroke_only(shape, scale):
    """The outline of a shape: inside it, but not inside its own shrunken self."""
    return lambda x, y: (
        shape(x, y)
        and not all(
            shape(x + dx, y + dy) for dx, dy in ((scale, 0), (-scale, 0), (0, scale), (0, -scale))
        )
    )


def rect(x0, y0, x1, y1):
    return lambda x, y: x0 <= x <= x1 and y0 <= y <= y1


def segment(ax, ay, bx, by, width):
    """Points within width / 2 of the segment a-b."""
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy

    def hit(x, y):
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / span))
        return math.hypot(x - ax - t * dx, y - ay - t * dy) <= width / 2

    return hit


def union(*shapes):
    return lambda x, y: any(shape(x, y) for shape in shapes)


def vgrad(y0, y1, hi=1.0, lo=0.35):
    return lambda x, y: hi + (lo - hi) * min(1, max(0, (y - y0) / (y1 - y0)))


def hgrad(x0, x1, hi=1.0, lo=0.1):
    return lambda x, y: hi + (lo - hi) * min(1, max(0, (x - x0) / (x1 - x0)))


def write_png(path, img):
    h, w = len(img), len(img[0])
    raw = b"".join(b"\0" + bytes(round(max(0, min(1, v)) * 255) for v in row) for row in img)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    blob = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    blob += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(blob)


def main():
    # clear: one large mark, lit from above, with a few stars.
    img = canvas(40, 30)
    paint(img, mark(1.6, 7.2, 2.4), vgrad(2, 28, 1.0, 0.3))
    for x, y in ((3, 5), (35, 9), (33, 24), (5, 22)):
        img[y][x] = 0.7
    write_png(HERE / "clear.png", img)

    # bots: three marks of growing size standing on a ground line.
    img = canvas(44, 30)
    left = 1
    for scale in (0.6, 0.85, 1.15):
        oy = 26 - 16 * scale
        paint(img, mark(scale, left - scale, oy), vgrad(oy, 26, 1.0, 0.35))
        left += 14 * scale + 3
    for x in range(1, 43):
        img[28][x] = 0.55
    write_png(HERE / "bots.png", img)

    # column: one small outlined mark.
    img = canvas(28, 20)
    outline = stroke_only(mark(1.0, 6, 2), 0.9)
    paint(img, outline, lambda x, y: 0.9)
    for x in (2, 24):
        img[16][x] = 0.6
    write_png(HERE / "column.png", img)

    # search: a mark seen through a lens ring.
    img = canvas(40, 30)
    paint(img, mark(1.1, 6, 6), vgrad(6, 24, 0.9, 0.35))
    paint(img, ring(16, 14, 11, 12.6), lambda x, y: 1.0)
    paint(
        img,
        stroke_only(lambda x, y: abs((x - 28.2) - (y - 22.2)) < 1.1 and 24 <= x <= 33, 1),
        lambda x, y: 1.0,
    )
    write_png(HERE / "search.png", img)

    # loading: the mark fading left to right, like a scan line passing over it.
    img = canvas(40, 20)
    paint(img, mark(1.0, 12, 2), hgrad(10, 30, 1.0, 0.08))
    for y in (10,):
        for x in range(2, 38):
            img[y][x] = max(img[y][x], 0.25 if (x // 2) % 2 else 0)
    write_png(HERE / "loading.png", img)

    # header: a small mark with a scan fading away to the right, for the Memory page title row.
    img = canvas(48, 10)
    paint(img, mark(0.5, 1, 1), lambda x, y: 1.0)
    paint(img, rect(12, 4, 47, 6), hgrad(12, 47, 0.9, 0.05))
    write_png(HERE / "header.png", img)

    # rule: a thin fading line that opens a section.
    img = canvas(48, 3)
    paint(img, rect(0, 0, 47, 2), hgrad(0, 47, 1.0, 0.0))
    write_png(HERE / "rule.png", img)

    # rail: one small mark for the narrow rail.
    img = canvas(20, 12)
    paint(img, mark(0.65, 4, 1), vgrad(1, 11, 1.0, 0.4))
    write_png(HERE / "rail.png", img)

    # Import steps, 24 x 12 each: the mark plus one cue.
    img = canvas(24, 12)
    paint(img, mark(0.65, 1, 1), vgrad(1, 11, 1.0, 0.4))
    paint(
        img,
        union(segment(14, 6, 22, 6, 1.4), segment(19, 3, 22, 6, 1.4), segment(19, 9, 22, 6, 1.4)),
        lambda x, y: 0.9,
    )
    write_png(HERE / "step-choose.png", img)

    img = canvas(24, 12)
    paint(img, stroke_only(mark(0.65, 1, 1), 0.8), lambda x, y: 0.9)
    paint(img, union(*[rect(14, y0, 22, y0 + 0.8) for y0 in (2, 5, 8)]), lambda x, y: 0.85)
    write_png(HERE / "step-preview.png", img)

    img = canvas(24, 12)
    paint(img, mark(0.65, 1, 1), hgrad(1, 12, 1.0, 0.15))
    paint(img, rect(14, 5, 22, 6.5), hgrad(14, 22, 0.7, 0.1))
    write_png(HERE / "step-progress.png", img)

    img = canvas(24, 12)
    paint(img, mark(0.65, 1, 1), vgrad(1, 11, 1.0, 0.4))
    paint(img, union(segment(14, 6.5, 17, 9, 1.5), segment(17, 9, 22, 3, 1.5)), lambda x, y: 0.95)
    write_png(HERE / "step-report.png", img)


if __name__ == "__main__":
    main()
