#!/usr/bin/env python3
"""extract_metrics.py REGULAR.ttf BOLD.ttf ITALIC.ttf BOLDITALIC.ttf OUT.json [--glyphs-from EXISTING.json]

Reads the metrics Fauxdana reproduces from a reference family: per code
point the advance width and ink box (xMin, xMax) of each weight; the hhea /
OS/2 vertical metrics and caret slope; the underline, strikeout, subscript
and superscript metrics and the average character width; the kerning pairs;
and the code points that share a glyph with another ("aliases"), so the
face can be rebuilt from the JSON without the reference font at hand. Kerning is recorded per code point pair, separately for the legacy
'kern' table ("kern") and the GPOS 'kern' feature ("gposKern"), because
layout engines differ in which of the two they read and the build reproduces
exactly the tables the reference carries.

--glyphs-from keeps the per-glyph measurements of an earlier extraction and
takes everything else from the fonts given: the committed measurements were
read from one release of the reference and the kerning and line metrics from
another whose advance widths agree with it. Written for Verdana; any TrueType
family works."""
import json
import sys
from collections import defaultdict
from fontTools.ttLib import TTFont

HHEA = ("ascent", "descent", "lineGap", "caretSlopeRise", "caretSlopeRun", "caretOffset")
OS2 = ("sTypoAscender", "sTypoDescender", "sTypoLineGap", "usWinAscent", "usWinDescent",
       "xAvgCharWidth", "yStrikeoutSize", "yStrikeoutPosition",
       "ySubscriptXSize", "ySubscriptYSize", "ySubscriptXOffset", "ySubscriptYOffset",
       "ySuperscriptXSize", "ySuperscriptYSize", "ySuperscriptXOffset", "ySuperscriptYOffset")
POST = ("underlinePosition", "underlineThickness")


def legacy_kern(font):
    """Horizontal kerning pairs of a 'kern' table, by glyph name."""
    pairs = defaultdict(int)
    if "kern" not in font:
        return None
    for sub in font["kern"].kernTables:
        # Format 0 only; coverage bit 0 = horizontal, bit 1 = minimum values,
        # bit 2 = cross-stream (the last two are not kerning in this sense).
        if sub.format != 0 or (sub.coverage & 0x07) != 0x01:
            continue
        for pair, value in sub.kernTable.items():
            pairs[pair] += value
    return dict(pairs)


def gpos_kern(font):
    """Advance adjustments of the GPOS 'kern' feature's pair lookups, by
    glyph name. Within a lookup the first subtable covering a pair wins (as
    in OpenType); separate lookups add up."""
    if "GPOS" not in font or not font["GPOS"].table.FeatureList:
        return None
    gpos = font["GPOS"].table
    indices = sorted({i for rec in gpos.FeatureList.FeatureRecord if rec.FeatureTag == "kern"
                      for i in rec.Feature.LookupListIndex})
    if not indices:
        return None
    total = defaultdict(int)
    for index in indices:
        lookup = gpos.LookupList.Lookup[index]
        subtables = [s.ExtSubTable if lookup.LookupType == 9 else s for s in lookup.SubTable]
        claimed_pairs, claimed_firsts = set(), set()
        for sub in subtables:
            if getattr(sub, "LookupType", 2) != 2:
                continue
            firsts = sub.Coverage.glyphs
            if sub.Format == 1:
                for first, pairset in zip(firsts, sub.PairSet):
                    if first in claimed_firsts:
                        continue
                    for rec in pairset.PairValueRecord:
                        pair = (first, rec.SecondGlyph)
                        if pair in claimed_pairs:
                            continue
                        claimed_pairs.add(pair)
                        value = getattr(rec.Value1, "XAdvance", 0) if rec.Value1 else 0
                        if value:
                            total[pair] += value
            elif sub.Format == 2:
                classes1, classes2 = sub.ClassDef1.classDefs, sub.ClassDef2.classDefs
                seconds = defaultdict(list)
                for glyph in font.getGlyphOrder():
                    seconds[classes2.get(glyph, 0)].append(glyph)
                for first in firsts:
                    if first in claimed_firsts:
                        continue
                    row = sub.Class1Record[classes1.get(first, 0)].Class2Record
                    for c2, rec in enumerate(row):
                        value = getattr(rec.Value1, "XAdvance", 0) if rec.Value1 else 0
                        for second in seconds[c2]:
                            pair = (first, second)
                            if pair in claimed_pairs:
                                continue
                            if value:
                                total[pair] += value
                    claimed_firsts.add(first)   # format 2 applies to every pair it covers
    return {pair: value for pair, value in total.items() if value}


def by_code_point(pairs, cmap):
    """Glyph-name pairs → 'LLLL RRRR' code point pairs (every code point a
    glyph is mapped from, so aliases such as hyphen / soft hyphen kern alike)."""
    codes = defaultdict(list)
    for code, name in cmap.items():
        codes[name].append(code)
    out = {}
    for (left, right), value in pairs.items():
        for lc in codes.get(left, ()):
            for rc in codes.get(right, ()):
                out[f"{lc:04X} {rc:04X}"] = value
    return out


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
    hhea, os2, post = font["hhea"], font["OS/2"], font["post"]
    data = {
        "unitsPerEm": font["head"].unitsPerEm,
        "italicAngle": post.italicAngle,
        "hhea": {f: getattr(hhea, f) for f in HHEA},
        "OS/2": {f: getattr(os2, f) for f in OS2},
        "post": {f: getattr(post, f) for f in POST},
        "glyphs": glyphs,
    }
    for key, pairs in (("kern", legacy_kern(font)), ("gposKern", gpos_kern(font))):
        if pairs:
            data[key] = by_code_point(pairs, cmap)
    # Code points sharing one glyph: each extra one is recorded as an alias
    # of the lowest code point outside the private use area mapped to it.
    by_glyph = defaultdict(list)
    for code, name in cmap.items():
        by_glyph[name].append(code)
    aliases = {}
    for codes in by_glyph.values():
        public = sorted(c for c in codes if not 0xE000 <= c <= 0xF8FF)
        if len(codes) > 1 and public:
            aliases.update({f"{c:04X}": f"{public[0]:04X}" for c in codes if c != public[0]})
    if aliases:
        data["aliases"] = aliases
    return data


def main():
    argv = sys.argv[1:]
    glyphs_from = argv[argv.index("--glyphs-from") + 1] if "--glyphs-from" in argv else None
    positional = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--glyphs-from")]
    regular, bold, italic, bold_italic, out = positional
    data = {"source": "advance widths, ink extents, vertical, line-decoration and caret metrics, and kerning of the reference family",
            "regular": metrics(regular), "bold": metrics(bold),
            "italic": metrics(italic), "boldItalic": metrics(bold_italic)}
    if glyphs_from:
        with open(glyphs_from) as f:
            kept = json.load(f)
        for style in ("regular", "bold", "italic", "boldItalic"):
            assert kept[style]["unitsPerEm"] == data[style]["unitsPerEm"]
            fresh, old = data[style]["glyphs"], kept[style]["glyphs"]
            disagree = sorted(c for c in fresh.keys() & old.keys() if fresh[c][0] != old[c][0])
            if disagree:
                print(f"warning: {style}: {len(disagree)} advance widths differ from {glyphs_from}: {disagree[:10]}")
            data[style]["glyphs"] = old
    with open(out, "w") as f:
        json.dump(data, f, indent=0, sort_keys=True)
        f.write("\n")
    print(f"{out}: " + ", ".join(f"{len(data[k]['glyphs'])} {k}" for k in ("regular", "bold", "italic", "boldItalic")) + " code points; kerning pairs "
          + ", ".join(f"{k} {len(data[k].get('kern', {}))} kern / {len(data[k].get('gposKern', {}))} GPOS" for k in ("regular", "bold", "italic", "boldItalic")))


if __name__ == "__main__":
    main()
