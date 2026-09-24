"""Draw static/favicon.ico: a sieve seen from above with a play triangle.

Every size is drawn on its own rather than shrunk from the big one. The mesh is
the whole idea of the icon, and a 7x7 mesh scaled down to 16px is a grey
smudge — so the thread count drops with the size (7, 5, 3, then a 2x2 "#").
Drawn 8x oversize and downsampled, which is what gives the edges their
anti-aliasing.

    python scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "favicon.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SUPERSAMPLE = 8

MESH_BG = "#FBF6EC"   # Sepia panel parchment
THREAD = "#8C6B4A"
RIM = "#9A6636"
PLAY = "#2F6F66"      # Sepia's second colour, the one the "go" buttons use


def _detail(size: int) -> dict:
    """(thread positions, thread width, rim width, outline around the play)
    in the 160-unit design space, per size band."""
    if size >= 128:
        return dict(threads=range(35, 126, 15), thread_w=3, rim_w=13, outline=5,
                    play=((66, 56), (66, 104), (108, 80)))
    if size >= 48:
        return dict(threads=range(40, 121, 20), thread_w=6, rim_w=15, outline=7,
                    play=((64, 54), (64, 106), (110, 80)))
    if size >= 24:
        return dict(threads=(48, 80, 112), thread_w=10, rim_w=18, outline=9,
                    play=((62, 52), (62, 108), (114, 80)))
    return dict(threads=(52, 108), thread_w=16, rim_w=22, outline=0,
                play=((64, 56), (64, 104), (106, 80)))


def _grow(points, by: float):
    """The triangle pushed out from its centre by `by` units: a stand-in for a
    stroke, which ImageDraw can only draw inside a polygon, not around it."""
    cx = sum(x for x, _ in points) / 3
    cy = sum(y for _, y in points) / 3
    grown = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        length = (dx * dx + dy * dy) ** 0.5
        grown.append((x + dx / length * by * 1.6, y + dy / length * by * 1.6))
    return grown


def draw(size: int) -> Image.Image:
    d = _detail(size)
    canvas = size * SUPERSAMPLE
    k = canvas / 160
    at = lambda v: v * k
    pts = lambda ps: [(at(x), at(y)) for x, y in ps]

    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([at(20), at(20), at(140), at(140)], fill=MESH_BG)

    # threads on their own layer, cut to the disc
    mesh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    md = ImageDraw.Draw(mesh)
    for t in d["threads"]:
        md.line([at(t), at(15), at(t), at(145)], fill=THREAD, width=round(at(d["thread_w"])))
        md.line([at(15), at(t), at(145), at(t)], fill=THREAD, width=round(at(d["thread_w"])))
    disc = Image.new("L", img.size, 0)
    ImageDraw.Draw(disc).ellipse([at(20), at(20), at(140), at(140)], fill=255)
    img.paste(mesh, (0, 0), Image.composite(mesh, Image.new("RGBA", img.size), disc))

    # the rim, centred on r=66 (ImageDraw strokes inwards from the box)
    outer = 66 + d["rim_w"] / 2
    draw.ellipse([at(80 - outer), at(80 - outer), at(80 + outer), at(80 + outer)],
                 outline=RIM, width=round(at(d["rim_w"])))

    if d["outline"]:
        draw.polygon(pts(_grow(d["play"], d["outline"] / 2)), fill=MESH_BG)
    draw.polygon(pts(d["play"]), fill=PLAY)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    frames = [draw(s) for s in SIZES]
    frames[-1].save(OUT, sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    print(f"wrote {OUT} ({', '.join(map(str, SIZES))} px)")


if __name__ == "__main__":
    main()
