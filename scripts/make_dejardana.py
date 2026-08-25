#!/usr/bin/env python3
"""make_dejardana.py DEJAVUSANS.ttf METRICS.json OUT.ttf [--bold] [--italic] [--accents ACCENTS.json]

Derives a Verdana-metric-compatible face from DejaVu Sans (the built TTF of
this repository's DejaVuSans.sfd / DejaVuSans-Bold.sfd) and the reference
metrics extracted by extract_metrics.py: every glyph both
fonts have takes Verdana's advance width and sits where Verdana's ink sits
(centred within Verdana's ink box; scaled horizontally only when the ink
widths differ by more than 3%). Capital I gets Verdana's top and bottom bars,
capital J Verdana's long tail and right-hand stem with a top bar and the
hook lifted onto the baseline (stroke weights untouched: the part below a
cut through the straight stem moves up, only the flat stretch of the hook
scales horizontally), lowercase j the same tail plus Verdana's top-left
serif; accents over I and J are placed at the offsets from the stem that
the reference family uses (scripts/measure_accents.py → verdana-accents.json). For the
oblique sources the letters are un-slanted by the face's italic angle before
they are reshaped and slanted back afterwards, so the lifted hook stays on
its stem and the bars come out as parallelograms. Vertical metrics (hhea, OS/2) are Verdana's so line boxes match.
Kerning and layout tables are dropped: the face exists to reproduce Word's
advance-based layout. Renamed as the Bitstream Vera / DejaVu licence
requires for modified fonts; the original copyright and licence travel in
the name table.
"""
import json
import math
import sys
import unicodedata
from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.basePen import BasePen
from fontTools.pens.recordingPen import DecomposingRecordingPen

FAMILY = "Dejardana"

class MapPen(BasePen):
    """Forwards an outline to another pen with every point run through fn."""
    def __init__(self, out, fn):
        super().__init__(None)
        self.out, self.fn = out, fn
    def _moveTo(self, p): self.out.moveTo(self.fn(*p))
    def _lineTo(self, p): self.out.lineTo(self.fn(*p))
    def _qCurveToOne(self, p1, p2): self.out.qCurveTo(self.fn(*p1), self.fn(*p2))
    def _curveToOne(self, p1, p2, p3): self.out.curveTo(self.fn(*p1), self.fn(*p2), self.fn(*p3))
    def _closePath(self): self.out.closePath()
    def _endPath(self): self.out.endPath()
    def addComponent(self, name, transform): pass  # sources are decomposed before reaching here

def ink(glyf, hmtx, name):
    g = glyf[name]
    if g.numberOfContours == 0 and not hasattr(g, "components"):
        return None
    g.recalcBounds(glyf)
    return g.xMin, g.xMax, g.yMin, g.yMax

SLANT = 0.0   # x shift per unit of y for the face being built (0 for upright)

def rect(pen, x0, y0, x1, y1):
    """An upright-space rectangle, drawn slanted by SLANT."""
    pen.moveTo((x0 + y0 * SLANT, y0)); pen.lineTo((x0 + y1 * SLANT, y1))
    pen.lineTo((x1 + y1 * SLANT, y1)); pen.lineTo((x1 + y0 * SLANT, y0)); pen.closePath()

def unslant(x, y):
    return (x - y * SLANT, y)

def reslant(x, y):
    return (x + y * SLANT, y)

def upright_ink(glyf, name):
    """Ink box of a glyph in upright (un-slanted) coordinates."""
    g = glyf[name]
    coords, _, _ = g.getCoordinates(glyf)
    xs = [x - y * SLANT for x, y in coords]
    ys = [y for _, y in coords]
    return min(xs), max(xs), min(ys), max(ys)

def main():
    global SLANT
    argv = sys.argv[1:]
    bold = "--bold" in argv
    italic = "--italic" in argv
    style_key = {(False, False): "regular", (True, False): "bold", (False, True): "italic", (True, True): "boldItalic"}[(bold, italic)]
    accents_path = argv[argv.index("--accents") + 1] if "--accents" in argv else None
    positional = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--accents")]
    src, metrics_path, out = positional
    font = TTFont(src)
    with open(metrics_path) as f:
        ref = json.load(f)[style_key]
    glyf, hmtx, cmap = font["glyf"], font["hmtx"], font.getBestCmap()
    glyph_set = font.getGlyphSet()
    upem = font["head"].unitsPerEm
    assert upem == ref["unitsPerEm"] == 2048
    SLANT = -math.tan(math.radians(font["post"].italicAngle)) if italic else 0.0
    # The reference's own slant (Verdana Italic leans 13°, DejaVu Oblique 11°):
    # its slanted ink widths are reduced to upright widths by it.
    ref_slant = -math.tan(math.radians(ref.get("italicAngle", 0.0))) if italic else 0.0
    def ref_upright_width(code, height):
        box = ref_ink(code)
        return None if box is None else (box[1] - box[0]) - height * ref_slant
    # Reference metrics per code point: [advance] or [advance, xMin, xMax].
    rmetrics = {int(code, 16): values for code, values in ref["glyphs"].items()}
    def ref_ink(code):
        values = rmetrics.get(code)
        return (values[1], values[2]) if values and len(values) == 3 else None

    def rebuild(name, fn=None, extra=None, transform=None):
        """Re-draws `name` decomposed, optionally mapping points / adding contours."""
        pen = TTGlyphPen(None)
        target = pen
        if transform is not None:
            target = TransformPen(target, transform)
        if fn is not None:
            slanted_fn = fn if SLANT == 0 else (lambda x, y, fn=fn: reslant(*fn(*unslant(x, y))))
            target = MapPen(target, slanted_fn)
        recording = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(recording)
        recording.replay(target)
        if extra:
            extra(pen)
        glyf[name] = pen.glyph()
        glyf[name].recalcBounds(glyf)
        # The glyph set draws outlines shifted by lsb - xMin, so the side
        # bearing must follow the new outline or the next replay moves it.
        advance, _ = hmtx[name]
        hmtx[name] = (advance, glyf[name].xMin if glyf[name].numberOfContours else 0)

    # --- Capital I: Verdana's serifs. Stem thickness from the plain stem.
    i_name, j_name = cmap[ord("I")], cmap[ord("J")]
    ix0, ix1, _, cap = upright_ink(glyf, i_name)
    stem = round(ix1 - ix0)
    bar_t = round(stem * 0.74)           # horizontal strokes are lighter than stems
    bar_ext = (ref_upright_width(ord("I"), cap) - stem) // 2    # bars reach Verdana's I ink width
    def i_bars(pen):
        rect(pen, ix0 - bar_ext, 0, ix1 + bar_ext, bar_t)
        rect(pen, ix0 - bar_ext, cap - bar_t, ix1 + bar_ext, cap)
    # DejaVu draws the accented capitals as their own outlines, so every
    # glyph whose canonical decomposition starts with I (or J, below) gets the
    # same treatment; their stems sit where the plain letter's does.
    def derived_from(base, base_name):
        # Accented forms drawn as their own outlines need the treatment;
        # composites that reference the (already treated) base letter only
        # need decomposing, which the shared-glyph pass does.
        out = []
        for code, gname in cmap.items():
            if gname in (i_name, j_name, base_name):
                continue
            decomposed = unicodedata.normalize("NFD", chr(code))
            if decomposed[0] != base or len(decomposed) == 1:
                continue
            g = glyf[gname]
            if g.isComposite() and any(c.glyphName == base_name for c in g.components):
                continue
            out.append(gname)
        return sorted(set(out))
    rebuild(i_name, extra=i_bars)
    for gname in derived_from("I", i_name):
        rebuild(gname, extra=i_bars)

    # --- Capital J. Verdana's J stands on the baseline with a long tail: its
    # stem sits at the right of the ink box (0.72 of the width in regular,
    # 0.58 in bold), the top bar reaches 1.6 stems to the left and ends flush
    # on the right, and the hook sweeps under the whole width. DejaVu's J is
    # reshaped without touching stroke weights: the hook is lifted (the part
    # below a cut through the straight stem moves up), the stem and everything
    # above the hook shift right so the ink box comes out exactly Verdana's,
    # the terminal keeps its shape and only the flat stretch of the hook
    # between terminal and stem is scaled horizontally.
    jx0, jx1, jy0, jy1 = upright_ink(glyf, j_name)
    lift = -jy0 - 21                     # Verdana's J overshoots the baseline by 21 units
    y_cut = cap * 0.55                   # inside the straight stem
    stem_x0 = jx1 - stem                 # the J's stem is its right edge
    terminal_end = jx0 + stem * 0.38     # the hook's end cap; DejaVu's J ends at -29
    bar_left_ext = round(upem * 0.16)
    shift_J = round(ref_upright_width(ord("J"), cap + 21) - (jx1 - jx0))   # flush right bar: ink width = Verdana's
    stretch_J = (stem_x0 + shift_J - terminal_end) / (stem_x0 - terminal_end)
    def reshape_J(x, y):
        if y < y_cut:
            y += lift
            if x >= stem_x0 - 1:
                x += shift_J
            elif x > terminal_end:
                x = terminal_end + (x - terminal_end) * stretch_J
        else:
            x += shift_J                 # stem, and accents above it
        return (x, y)
    def j_bar(pen):
        rect(pen, stem_x0 + shift_J - bar_left_ext, cap - bar_t, jx1 + shift_J, cap)
    rebuild(j_name, fn=reshape_J, extra=j_bar)
    for gname in derived_from("J", j_name):
        rebuild(gname, fn=reshape_J, extra=j_bar)

    # --- Lowercase j: the same long tail and right-hand stem as Verdana's,
    # plus its top-left serif at the x-height. The dot and the stem shift
    # together; the hook stretches between terminal and stem.
    lj_name, l_name, x_name = cmap[ord("j")], cmap[ord("l")], cmap[ord("x")]
    lx0, lx1, _, _ = upright_ink(glyf, l_name)
    lc_stem = lx1 - lx0
    _, _, _, x_height = upright_ink(glyf, x_name)
    ljx0, ljx1, ljy0, _ = upright_ink(glyf, lj_name)
    _, _, ljy0_, ljy1_ = upright_ink(glyf, lj_name)
    shift_j = round(ref_upright_width(ord("j"), ljy1_ - ljy0_) - (ljx1 - ljx0))
    lj_terminal_end = ljx0 + lc_stem * 0.38
    stretch_j = (lx0 + shift_j - lj_terminal_end) / (lx0 - lj_terminal_end)
    lj_cut = x_height * 0.5
    def reshape_j(x, y):
        if y < lj_cut and x < lx0 - 1:
            if x > lj_terminal_end:
                x = lj_terminal_end + (x - lj_terminal_end) * stretch_j
        else:
            x += shift_j                 # stem, dot, accents
        return (x, y)
    lc_bar_t = round(lc_stem * 0.7)
    lc_serif_ext = round(upem * 0.11)
    def lj_serif(pen):
        rect(pen, lx0 + shift_j - lc_serif_ext, x_height - lc_bar_t, lx1 + shift_j, x_height)
    rebuild(lj_name, fn=reshape_j, extra=lj_serif)
    for gname in derived_from("j", lj_name):
        rebuild(gname, fn=reshape_j, extra=lj_serif)

    # Composites of J and j (Ĵ, ĵ, ǰ): the base moved, so their accents move
    # with it before the shared pass decomposes them.
    for name in font.getGlyphOrder():
        g = glyf[name]
        if not g.isComposite():
            continue
        for base, shift in ((j_name, shift_J), (lj_name, shift_j)):
            if any(c.glyphName == base for c in g.components):
                for c in g.components:
                    if c.glyphName != base and hasattr(c, "x"):
                        c.x += shift

    # --- Accents over I and J sit where the reference family puts them: at
    # measured offsets of the accent's centre from the stem's centre (Verdana
    # sets the acute 82 units right of the I's stem, the grave 74 left, and
    # centres Ĵ's circumflex over the J's ink, 143 left of the stem).
    accents = {}
    if accents_path:
        with open(accents_path) as f:
            accents = {int(code, 16): offset for code, offset in json.load(f)["bold" if bold else "regular"].items()}  # upright offsets, applied in un-slanted space
    stem_centres = {"I": (ix0 + ix1) / 2, "J": stem_x0 + shift_J + stem / 2}
    placed = 0
    for code, target in accents.items():
        gname = cmap.get(code)
        base = unicodedata.normalize("NFD", chr(code))[0]
        if gname is None or base not in stem_centres or gname in (i_name, j_name):
            continue
        recording = DecomposingRecordingPen(glyph_set)
        glyph_set[gname].draw(recording)
        xs = [pt[0] - pt[1] * SLANT for op, args in recording.value if op in ("moveTo", "lineTo", "qCurveTo", "curveTo")
              for pt in args if pt is not None and pt[1] > cap * 1.03]
        if not xs:
            continue
        delta = stem_centres[base] + target - (min(xs) + max(xs)) / 2
        def move_accent(x, y, delta=delta):
            return (x + delta, y) if y > cap * 1.03 else (x, y)
        rebuild(gname, fn=move_accent)
        placed += 1

    # --- Every shared glyph: Verdana's advance, centred in Verdana's ink box,
    # horizontally scaled when the ink widths differ by more than 3%.
    shared, scaled = 0, 0
    for code, name in sorted(cmap.items()):
        values = rmetrics.get(code)
        if values is None or name not in glyf:
            continue
        radv = values[0]
        rbox = ref_ink(code)
        box = ink(glyf, hmtx, name)
        if box is None or rbox is None:   # space-like glyphs: advance only
            adv, lsb = hmtx[name]
            hmtx[name] = (radv, lsb)
            shared += 1
            continue
        x0, x1, _, _ = box
        rx0, rx1 = rbox
        width, rwidth = x1 - x0, rx1 - rx0
        # Scale the ink to Verdana's only when the proportions really differ
        # and the glyph is more than a stem (stems, dots and bars would just
        # change weight); clamp so shapes stay recognisably DejaVu.
        sx = 1.0
        if width > upem * 0.22 and abs(rwidth / width - 1) > 0.05:
            sx = max(0.85, min(1.18, rwidth / width))
            scaled += 1
        new_width = width * sx
        new_x0 = rx0 + (rwidth - new_width) / 2
        dx = new_x0 - x0 * sx
        rebuild(name, transform=(sx, 0, 0, 1, dx, 0))
        hmtx[name] = (radv, round(glyf[name].xMin))
        shared += 1

    # Glyphs not shared with Verdana keep DejaVu's outlines; decompose them
    # too so nothing references a moved base glyph with stale offsets.
    for name in font.getGlyphOrder():
        g = glyf[name]
        if getattr(g, "isComposite", lambda: False)():
            rebuild(name)

    # Side bearings follow the outlines everywhere (TrueType convention).
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours:
            g.recalcBounds(glyf)
            hmtx[name] = (hmtx[name][0], g.xMin)

    # --- Vertical metrics from Verdana.
    for table in ("hhea", "OS/2"):
        for field, value in ref[table].items():
            setattr(font[table], field, value)
    font["OS/2"].recalcAvgCharWidth(font)
    for tag in ("GPOS", "GSUB", "kern", "GDEF", "morx", "feat"):
        if tag in font:
            del font[tag]

    # --- Names.
    style = {"regular": "Regular", "bold": "Bold", "italic": "Italic", "boldItalic": "Bold Italic"}[style_key]
    name_table = font["name"]
    copyright_ = name_table.getDebugName(0) or ""
    records = {
        0: copyright_ + " Dejardana: a metric-compatible derivative (advance widths and vertical metrics of Verdana) for Slack's document previews; renamed as the Bitstream Vera and DejaVu licences require.",
        1: FAMILY, 2: style, 3: f"{FAMILY} {style}; 1.000", 4: f"{FAMILY} {style}".replace(" Regular", ""),
        5: "Version 1.000", 6: f"{FAMILY}-{style.replace(' ', '')}", 16: FAMILY, 17: style,
    }
    for rec in list(name_table.names):
        if rec.nameID in records:
            rec.string = records[rec.nameID]
    for nid, value in records.items():
        if not name_table.getName(nid, 3, 1, 0x409):
            name_table.setName(value, nid, 3, 1, 0x409)
    font["head"].fontRevision = 1.0
    font["OS/2"].achVendID = "SLAK"
    # Deterministic output: keep the source's timestamps instead of stamping
    # the build time (fontTools rewrites head.modified on save unless told
    # not to), so identical inputs rebuild byte-identical fonts.
    font.recalcTimestamp = False
    font.save(out)
    print(f"{out}: {shared} glyphs took Verdana's metrics ({scaled} scaled); stem {stem}, I bars {bar_t}x{bar_ext}; J lift {lift}, shift {shift_J}, hook stretch {stretch_J:.2f}; j shift {shift_j}, stretch {stretch_j:.2f}, serif {lc_serif_ext}x{lc_bar_t}; accents placed {placed}")

if __name__ == "__main__":
    main()
