#!/usr/bin/env python3
"""extract_metrics.py REGULAR.ttf BOLD.ttf ITALIC.ttf BOLDITALIC.ttf OUT.json

Reads the metrics Fauxdana reproduces from a reference family: per code
point the advance width and ink box (xMin, xMax) of each weight, and the
hhea / OS/2 vertical metrics, so the face can be rebuilt from the JSON
without the reference font at hand. Written for Verdana; any TrueType family
works."""
import json
import sys
from fontTools.ttLib import TTFont


def metrics(path):
    font = TTFont(path)
    glyf, hmtx, cmap = font["glyf"], font["hmtx"], font.getBestCmap()
    glyphs = {}
    for code, name in sorted(cmap.items()):
        g = glyf[name]
        advance, _ = hmtx[name]
        if g.numberOfContours == 0 and not g.isComposite():
            glyphs[f"{code:04X}"] = [advance]
        else:
            g.recalcBounds(glyf)
            glyphs[f"{code:04X}"] = [advance, g.xMin, g.xMax]
    hhea, os2 = font["hhea"], font["OS/2"]
    return {
        "unitsPerEm": font["head"].unitsPerEm,
        "italicAngle": font["post"].italicAngle,
        "hhea": {"ascent": hhea.ascent, "descent": hhea.descent, "lineGap": hhea.lineGap},
        "OS/2": {f: getattr(os2, f) for f in ("sTypoAscender", "sTypoDescender", "sTypoLineGap", "usWinAscent", "usWinDescent")},
        "glyphs": glyphs,
    }


def main():
    regular, bold, italic, bold_italic, out = sys.argv[1:6]
    data = {"source": "advance widths, ink extents and vertical metrics of the reference family",
            "regular": metrics(regular), "bold": metrics(bold),
            "italic": metrics(italic), "boldItalic": metrics(bold_italic)}
    with open(out, "w") as f:
        json.dump(data, f, indent=0, sort_keys=True)
        f.write("\n")
    print(f"{out}: " + ", ".join(f"{len(data[k]['glyphs'])} {k}" for k in ("regular", "bold", "italic", "boldItalic")) + " code points")


if __name__ == "__main__":
    main()
