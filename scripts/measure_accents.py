#!/usr/bin/env python3
"""measure_accents.py REGULAR.ttf BOLD.ttf OUT.json

Measures where a reference family places the accents over its capital I
(and J): the horizontal offset, in font units, of the accent's centre from
the stem's centre, read off rasterised glyphs (ImageMagick + Pillow).
Dejardana moves DejaVu's accents to the same offsets so the accented capitals
match too."""
import json
import subprocess
import sys
import tempfile
from PIL import Image

LETTERS = "ÌÍÎÏĨĪĬİǏȈȊÍĴ"   # accented I, plus Ĵ; duplicates are harmless
SIZE = 1000                 # pt; ImageMagick renders 1 em = SIZE px


def measure(font, ch, cap_fraction=0.22):
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        subprocess.run(["magick", "-background", "white", "-fill", "black", "-kerning", "0",
                        "-font", font, "-pointsize", str(SIZE), f"label:{ch}", "-trim", "+repage", tmp.name], check=True)
        im = Image.open(tmp.name).convert("L")
    w, h = im.size
    px = im.load()
    accent = [x for x in range(w) if any(px[x, y] < 128 for y in range(int(cap_fraction * h)))]
    stem = [x for x in range(w) if sum(1 for y in range(h) if px[x, y] < 128) > 0.5 * h]
    if not accent or not stem:
        return None
    accent_centre = (min(accent) + max(accent)) / 2
    stem_centre = (min(stem) + max(stem)) / 2
    return round((accent_centre - stem_centre) * 2048 / SIZE)


def main():
    regular, bold, out = sys.argv[1:4]
    data = {"note": "accent centre minus stem centre, font units (2048/em), measured from rasterised glyphs"}
    for weight, path in (("regular", regular), ("bold", bold)):
        offsets = {}
        for ch in dict.fromkeys(LETTERS):
            value = measure(path, ch)
            if value is not None:
                offsets[f"{ord(ch):04X}"] = value
        data[weight] = offsets
    with open(out, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    for weight in ("regular", "bold"):
        print(weight, {chr(int(k, 16)): v for k, v in data[weight].items()})


if __name__ == "__main__":
    main()
