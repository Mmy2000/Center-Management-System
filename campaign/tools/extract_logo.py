"""Lift the codaco lockup off its mockup background into a transparent PNG.

The supplied file is a presentation render: light metallic artwork sitting on a
near-black textured card. There is no matte to use, but light-on-black is the
one case where alpha can be recovered exactly — treat the render as an additive
layer, take luminance as coverage, and unpremultiply the colour back out.

That reproduces the logo faithfully over any *dark* ground, which is what the
campaign artwork is. Over a light ground the metallic shading would wash out,
so a proper transparent master is still worth asking the designer for.
"""

import sys

from PIL import Image

SRC = sys.argv[1]
OUT_DIR = sys.argv[2]

# Sampled from the four corners of the render.
BG = (13, 16, 22)
# Measured, not guessed: the card's paper texture tops out at ~0.042 lifted
# luma, so anything below TOE is card and anything above KNEE is solid artwork.
# The ramp between them keeps the anti-aliased edges from turning into a
# staircase. The render's own drop shadow falls under TOE and is discarded,
# which is correct - it belongs to the mockup, not to the logo.
TOE = 0.055
KNEE = 0.17


def extract(img: Image.Image) -> Image.Image:
    src = img.convert("RGB")
    out = Image.new("RGBA", src.size)

    pixels = []
    for r, g, b in src.getdata():
        # Remove the card, keeping only what the artwork adds on top of it.
        r = r - BG[0] if r > BG[0] else 0
        g = g - BG[1] if g > BG[1] else 0
        b = b - BG[2] if b > BG[2] else 0

        luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        a = (luma - TOE) / (KNEE - TOE)
        if a <= 0.0:
            pixels.append((0, 0, 0, 0))
            continue
        if a > 1.0:
            a = 1.0

        # Unpremultiply so mid-tones keep their brightness once composited.
        inv = 1.0 / a
        pixels.append((
            min(255, int(r * inv)), min(255, int(g * inv)), min(255, int(b * inv)),
            int(a * 255),
        ))

    out.putdata(pixels)
    return out


def trim(img: Image.Image, pad: int = 6) -> Image.Image:
    box = img.getchannel("A").point(lambda v: 255 if v > 6 else 0).getbbox()
    left, top, right, bottom = box
    return img.crop((
        max(0, left - pad), max(0, top - pad),
        min(img.width, right + pad), min(img.height, bottom + pad),
    ))


def main():
    src = Image.open(SRC)
    full = trim(extract(src))
    full.save(f"{OUT_DIR}/codaco-logo.png")
    print(f"  codaco-logo.png  {full.size[0]}x{full.size[1]}  (mark + wordmark)")

    # The mark alone, for placements too small for the wordmark to survive.
    # The lockup is mark-over-text; the mark ends just above the "codaco" row.
    w, h = full.size
    mark = trim(full.crop((0, 0, w, int(h * 0.56))))
    mark.save(f"{OUT_DIR}/codaco-mark.png")
    print(f"  codaco-mark.png  {mark.size[0]}x{mark.size[1]}  (mark only)")

    # Contact sheets: the same asset over the campaign ground and over white,
    # so the extraction can actually be judged rather than assumed.
    for name, bg in [("dark", (10, 40, 51, 255)), ("light", (255, 255, 255, 255))]:
        sheet = Image.new("RGBA", (full.width + 120, full.height + 120), bg)
        sheet.alpha_composite(full, (60, 60))
        sheet.convert("RGB").save(f"{OUT_DIR}/check-{name}.png")
    print("  check-dark.png / check-light.png written")


if __name__ == "__main__":
    main()
