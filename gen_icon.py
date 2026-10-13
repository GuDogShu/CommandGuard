# gen_icon.py -- generate icon.ico (16/32/48/256) + preview png for CommandGuard.
# Pure stdlib. ASCII only.
#
# Copyright (C) 2026 GuDogShu
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
import os
import math
import struct
import zlib


def seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    l2 = vx * vx + vy * vy
    if l2 <= 0:
        return math.hypot(px - ax, py - ay)
    t = (wx * vx + wy * vy) / l2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def render(S):
    cx = (S - 1) / 2.0
    cy = (S - 1) / 2.0
    r = S * 0.47
    p1 = (0.30 * S, 0.53 * S)
    p2 = (0.44 * S, 0.66 * S)
    p3 = (0.73 * S, 0.35 * S)
    th = max(1.5, S * 0.09)
    ss = 4
    rows = []  # bottom-up, each item: list of (r, g, b, a)
    for y in range(S - 1, -1, -1):
        row = []
        for x in range(S):
            acc_a = 0
            acc_r = 0
            acc_g = 0
            acc_b = 0
            for sy in range(ss):
                for sx in range(ss):
                    px = x + (sx + 0.5) / ss
                    py = y + (sy + 0.5) / ss
                    if math.hypot(px - cx, py - cy) <= r:
                        if (seg_dist(px, py, p1[0], p1[1], p2[0], p2[1]) <= th / 2.0 or
                                seg_dist(px, py, p2[0], p2[1], p3[0], p3[1]) <= th / 2.0):
                            acc_r += 255
                            acc_g += 255
                            acc_b += 255
                        else:
                            acc_r += 37
                            acc_g += 99
                            acc_b += 235
                        acc_a += 255
            n = ss * ss
            row.append((acc_r // n, acc_g // n, acc_b // n, acc_a // n))
        rows.append(row)
    return rows


def bmp_data(S, rows):
    hdr = struct.pack('<IiiHHIIiiII', 40, S, 2 * S, 1, 32, 0, S * S * 4, 0, 0, 0, 0)
    data = bytearray()
    for row in rows:
        for (r, g, b, a) in row:
            data += bytes((b, g, r, a))
    mask_row = ((S + 31) // 32) * 4
    data += b'\x00' * (mask_row * S)
    return hdr + bytes(data)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sizes = [16, 32, 48, 256]
    blobs = [bmp_data(S, render(S)) for S in sizes]

    out = struct.pack('<HHH', 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b''
    for S, blob in zip(sizes, blobs):
        w = 0 if S >= 256 else S
        entries += struct.pack('<BBBBHHII', w, w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    ico_path = os.path.join(here, 'icon.ico')
    with open(ico_path, 'wb') as f:
        f.write(out + entries + b''.join(blobs))
    print('icon.ico =', os.path.getsize(ico_path), 'bytes')

    # preview png (256, composited over light gray) for visual check
    S = 256
    rows = render(S)

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        crc = struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
        return c + crc

    raw = bytearray()
    for row in reversed(rows):  # top-down for PNG
        raw.append(0)
        for (r, g, b, a) in row:
            rr = (r * a + 240 * (255 - a)) // 255
            gg = (g * a + 240 * (255 - a)) // 255
            bb = (b * a + 240 * (255 - a)) // 255
            raw += bytes((rr, gg, bb, 255))
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', S, S, 8, 6, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(bytes(raw), 9))
    png += chunk(b'IEND', b'')
    png_path = os.path.join(here, 'icon_preview.png')
    with open(png_path, 'wb') as f:
        f.write(png)
    print('icon_preview.png =', os.path.getsize(png_path), 'bytes')


if __name__ == '__main__':
    main()
