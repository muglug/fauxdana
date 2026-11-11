#!/usr/bin/env python3
"""make_fauxdana.py DEJAVUSANS.ttf METRICS.json OUT.ttf [--bold] [--italic] [--accents ACCENTS.json] [--upright DEJAVUSANS-UPRIGHT.ttf]

Derives a Verdana-metric-compatible face from DejaVu Sans (the built TTF of
this repository's DejaVuSans.sfd / DejaVuSans-Bold.sfd) and the reference
metrics extracted by extract_metrics.py: every glyph both
fonts have takes Verdana's advance width and sits where Verdana's ink sits
(centred within Verdana's ink box; scaled horizontally only when the ink
widths differ by more than 5%). Capital I gets Verdana's top and bottom bars,
capital J Verdana's long tail and right-hand stem with a top bar and the
hook lifted onto the baseline (stroke weights untouched: the part below a
cut through the straight stem moves up, only the flat stretch of the hook
scales horizontally), lowercase j the same tail plus Verdana's top-left
serif; accents over I and J are placed at the offsets from the stem that
the reference family uses (scripts/measure_accents.py → verdana-accents.json). The
oblique sources are first re-slanted from DejaVu's 11° to the reference's
italic angle (Verdana Italic leans 13°), so slanted ink boxes compare like
with like and the caret slope and italic angle are the reference's; the
letters are then un-slanted by that angle before they are reshaped and
slanted back afterwards, so the lifted hook stays on its stem and the bars
come out as parallelograms. Vertical metrics (hhea, OS/2) are Verdana's so
line boxes match. Accented letters move exactly as their plain letter does;
circles, squares, bullets and a few other symbols are scaled proportionally
rather than stretched; Ĳ and ĳ are rebuilt from the finished letters; and
characters the reference lacks keep DejaVu's drawing and advance, including
those DejaVu builds from I, J or j. The underline, strikeout, subscript and
superscript metrics and the average character width are Verdana's too. DejaVu's own kerning
and layout tables are dropped; the reference's kerning pairs take their
place, written to the same table(s) the reference carries them in (Verdana:
a legacy 'kern' table in the regular face only), so every layout engine
that kerns Verdana kerns Fauxdana identically and every one that does not
kern Verdana leaves Fauxdana unkerned too. Renamed as the Bitstream Vera /
DejaVu licence requires for modified fonts; the original copyright and
licence travel in the name table.
"""
import json
import math
import sys
import unicodedata
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._k_e_r_n import KernTable_format_0
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.basePen import BasePen
from fontTools.pens.recordingPen import DecomposingRecordingPen

FAMILY = "Fauxdana"
VERSION = "1.102"

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

def shear_all(font, glyph_set, shear, keep=frozenset()):
    """Slants every outline further by `shear` (x += shear * y, about the
    baseline). Composites keep their structure where they can: a plain
    offset component moves with the shear of its offset; components that are
    scaled, flipped or attached by anchor points are decomposed first.
    Glyphs named in `keep` (those the oblique leaves upright) are untouched."""
    glyf, hmtx = font["glyf"], font["hmtx"]
    order = [name for name in font.getGlyphOrder() if name not in keep]
    for name in order:
        g = glyf[name]
        if g.isComposite() and any(hasattr(c, "transform") or not hasattr(c, "x") for c in g.components):
            pen = TTGlyphPen(None)
            recording = DecomposingRecordingPen(glyph_set)
            glyph_set[name].draw(recording)
            recording.replay(pen)
            glyf[name] = pen.glyph()
    for name in order:
        g = glyf[name]
        if g.isComposite():
            for c in g.components:
                c.x = round(c.x + shear * c.y)
        elif g.numberOfContours > 0:
            g.coordinates.transform(((1, 0), (shear, 1)))
            g.coordinates.toInt()
    for name in order:                   # composites' bounds follow their (sheared) parts
        g = glyf[name]
        if g.numberOfContours:
            g.recalcBounds(glyf)
            hmtx[name] = (hmtx[name][0], g.xMin)

def kerning(cmap, pairs_by_code, tag):
    """The reference's kerning pairs ('LLLL RRRR' code points → value) as
    Fauxdana glyph pairs; pairs with a code point Fauxdana lacks are skipped."""
    pairs = {}
    for code_pair, value in pairs_by_code.items():
        left, right = (cmap.get(int(code, 16)) for code in code_pair.split())
        if left is None or right is None:
            continue
        if pairs.get((left, right), value) != value:
            raise SystemExit(f"{tag}: conflicting kerning for {left} {right}")
        pairs[(left, right)] = value
    return pairs

def main():
    global SLANT
    argv = sys.argv[1:]
    bold = "--bold" in argv
    italic = "--italic" in argv
    style_key = {(False, False): "regular", (True, False): "bold", (False, True): "italic", (True, True): "boldItalic"}[(bold, italic)]
    accents_path = argv[argv.index("--accents") + 1] if "--accents" in argv else None
    upright_path = argv[argv.index("--upright") + 1] if "--upright" in argv else None
    positional = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] not in ("--accents", "--upright"))]
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
    # Lean the oblique as far as the reference does before anything is
    # measured: compared at different slants, every tall glyph's ink looks
    # too narrow and would be stretched to make up the difference.
    # DejaVu's oblique leaves some 1,500 glyphs upright (+ < = > | ° • ™,
    # arrows, mathematical operators, box drawing and blocks; Verdana Italic
    # keeps most of the ones it has upright too). Given the upright face
    # (--upright), those whose outline is the same in both stay upright;
    # without it, everything leans.
    source_angle = font["post"].italicAngle
    if italic and ref_slant and abs(ref_slant - SLANT) > 1e-6:
        keep = set()
        if upright_path:
            upright = TTFont(upright_path)
            upright_set = upright.getGlyphSet()
            for name in font.getGlyphOrder():
                if name not in upright_set:
                    continue
                a, b = DecomposingRecordingPen(glyph_set), DecomposingRecordingPen(upright_set)
                glyph_set[name].draw(a)
                upright_set[name].draw(b)
                if a.value and a.value == b.value:
                    keep.add(name)
        shear_all(font, glyph_set, ref_slant - SLANT, keep)
        SLANT = ref_slant
        font["post"].italicAngle = ref["italicAngle"]
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

    # --- Before I, J and j are reshaped, decide which composites built from
    # them should follow. Verdana builds Ι Ί Ϊ І Ї Ј ј and the accented I/J
    # forms from its own I, J and j, so composites whose only letter is I, J
    # or j inherit the reshaped letter. Everything else that uses them is
    # flattened now, from DejaVu's plain letters: characters the reference
    # lacks keep DejaVu's own drawing and advance (Roman numerals, the Ǉ/ǌ
    # digraphs, polytonic Ἰ…: a Verdana-width I or J would overrun their
    # DejaVu advance and collide), as does a letter that merely uses I as a
    # stroke (Ы), and the letter pairs Ĳ and ĳ, which are rebuilt from the
    # finished letters after the shared pass below.
    first_code = {}
    for code, gname in sorted(cmap.items()):
        first_code.setdefault(gname, code)
    ij_names = {cmap[ord("I")], cmap[ord("J")], cmap[ord("j")]}
    def uses_ij(name):
        g = glyf[name]
        return g.isComposite() and any(c.glyphName in ij_names or uses_ij(c.glyphName) for c in g.components)
    def letters(name):
        g = glyf[name]
        if g.isComposite():
            return set().union(*(letters(c.glyphName) for c in g.components))
        code = first_code.get(name)
        is_letter = code is not None and unicodedata.category(chr(code)) in ("Lu", "Ll", "Lt", "Lo")
        return {name} if name in ij_names or is_letter else set()
    pairs = {}     # letter pairs (Ĳ, ĳ) → their letters, rebuilt after the shared pass
    for name in font.getGlyphOrder():
        if not uses_ij(name):
            continue
        code = first_code.get(name)
        if code in rmetrics and len(letters(name)) == 1:
            continue                       # inherits the reshaped I, J or j
        parts = unicodedata.normalize("NFKD", chr(code)) if code is not None else ""
        if code in rmetrics and len(parts) > 1 and all(ord(p) in rmetrics and ord(p) in cmap for p in parts):
            pairs[code] = [ord(p) for p in parts]
        rebuild(name)

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
    # glyph the reference has whose canonical decomposition starts with I (or
    # J, below) gets the same treatment; their stems sit where the plain
    # letter's does. Accented forms the reference lacks (Ǐ, Ȉ, ǰ…) keep their
    # DejaVu drawing, which fits their DejaVu advance.
    def derived_from(base, base_name):
        # Accented forms drawn as their own outlines need the treatment;
        # composites that reference the (already treated) base letter only
        # need decomposing, which happens just before the shared-glyph pass.
        out = []
        for code, gname in cmap.items():
            if gname in (i_name, j_name, base_name) or code not in rmetrics:
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

    # Other two-part compatibility characters DejaVu composes side by side
    # from two characters the reference also has (Ŀ ŀ ‼ ″) are rebuilt from
    # the finished parts the same way as Ĳ and ĳ, keeping DejaVu's vertical
    # offsets (the raised dot of ŀ), rather than fitted as one shape, which
    # would stretch or squeeze the letter inside them. (ŉ, whose apostrophe
    # the reference lacks, follows its n like an accented letter, below.)
    pairs = {code: [(part, 0) for part in parts] for code, parts in pairs.items()}
    for code, gname in sorted(cmap.items()):
        g = glyf[gname]
        if code in pairs or code not in rmetrics or not ref_ink(code) or not g.isComposite() \
                or len(g.components) != 2 or any(not hasattr(c, "x") or hasattr(c, "transform") for c in g.components):
            continue
        if len(unicodedata.normalize("NFD", chr(code))) != 1 or len(unicodedata.normalize("NFKD", chr(code))) < 2:
            continue
        parts = [(first_code.get(c.glyphName), c.y) for c in sorted(g.components, key=lambda c: c.x)]
        if all(p is not None and ref_ink(p) for p, _ in parts) and \
                "".join(chr(p) for p, _ in parts) == unicodedata.normalize("NFKD", chr(code)):
            pairs[code] = parts

    # Decompose every composite before any shared glyph moves: DejaVu builds
    # accented letters from the spacing accents and base letters (ï is
    # dotlessi + dieresis, É is E + Acute), and the shared pass below moves
    # those parts into Verdana's ink boxes one code point at a time. A
    # composite decomposed after its parts moved would inherit their new
    # positions (ï's diaeresis would land where Verdana puts a lone ¨, far
    # right of the stem), so each accented letter is flattened from DejaVu's
    # own geometry first and then fitted to Verdana as a whole.
    for name in font.getGlyphOrder():
        g = glyf[name]
        if getattr(g, "isComposite", lambda: False)():
            rebuild(name)

    # --- Every shared glyph: Verdana's advance, centred in Verdana's ink box,
    # horizontally scaled when the ink widths differ by more than 5%. The
    # fits are all measured first, on the outlines as they stand, and applied
    # afterwards, so accented letters can borrow their base letter's.
    # Circles, squares and bullets (and ©, ®, °, ™, ∞, whose shapes a one-way
    # stretch would also distort) are scaled the same in both directions, about
    # their vertical centre, so a round bullet stays round; scaled this way
    # they land within a few units of Verdana's own heights.
    def proportional(code):
        return code in (0x00A9, 0x00AE, 0x00B0, 0x2022, 0x2122, 0x2219, 0x221E) or 0x25A0 <= code <= 0x25FF
    def fit(code, name):
        """(sx, dx, sy, dy) placing the glyph in the reference's ink box."""
        rbox, box = ref_ink(code), ink(glyf, hmtx, name)
        if box is None or rbox is None:   # space-like glyphs: advance only
            return None
        x0, x1, y0, y1 = box
        rx0, rx1 = rbox
        width, rwidth = x1 - x0, rx1 - rx0
        if width <= 0:
            return 1.0, rx0 + rwidth / 2 - x0, 1.0, 0.0
        need = rwidth / width
        # Scale the ink to Verdana's only when the proportions really differ
        # and the glyph is more than a stem (stems, dots and bars would just
        # change weight); clamp so shapes stay recognisably DejaVu.
        sx = 1.0
        if width > upem * 0.22 and abs(need - 1) > 0.05:
            sx = max(0.85, min(1.18, need))
        new_x0 = rx0 + (rwidth - width * sx) / 2
        # Symbols whose shape is proportion, and anything the clamped stretch
        # would leave spilling over its advance into the neighbouring
        # characters (DejaVu's □ ● ₠ are far wider than Verdana's), take the
        # reference's ink width exactly by scaling both ways; letter-like
        # glyphs standing on the baseline stay on it.
        radv = rmetrics[code][0]
        spills = radv > 0 and unicodedata.category(chr(code)).startswith("S") and \
            (new_x0 < min(0, rx0) - 40 or new_x0 + width * sx > max(radv, rx1) + 40)
        if (proportional(code) and abs(need - 1) > 0.05) or spills:
            anchor = 0.0 if spills and not proportional(code) and abs(y0) <= upem * 0.02 else (y0 + y1) / 2
            return need, rx0 - x0 * need, need, anchor * (1 - need)
        return sx, new_x0 - x0 * sx, 1.0, 0.0
    fits = {code: fit(code, name) for code, name in cmap.items()
            if code in rmetrics and name in glyf}

    # Accented letters move exactly as their base letter does, as in Verdana,
    # which keeps the letter's body where the plain letter has it (í's stem
    # where ı's is, é's bowl where e's is) instead of centring body and accent
    # together: centred as a whole, an accent that sits off to one side would
    # drag the body the other way and could rescale it. The body stays put
    # even when the accent widens the glyph on the right (ď, ľ, ť), and moves
    # right by the extra advance when the accent stands to its left (Greek
    # tonos capitals: Έ, Ό), which shows in the reference's ink boxes as a
    # left edge that moved while the right edge moved by exactly the extra
    # advance. The body is found as the base letter's outline intact in the
    # accented one (ignoring the dot of i, j, Cyrillic і and ј); failing that (a ring or horn
    # fused to the stroke), as the part of the outline at the base letter's
    # mid-height when it is exactly as wide as the base letter there; failing
    # that too (a horn widening the body), the glyph keeps its own fit.
    def contours(name, below=None):
        g = glyf[name]
        if g.isComposite() or g.numberOfContours <= 0:
            return []
        out, start = [], 0
        for end in g.endPtsOfContours:
            # One- and two-point contours draw nothing (DejaVu leaves stray
            # points in u and dotlessi, among others) and do not survive
            # decomposition, so they cannot be part of the match.
            pts = g.coordinates[start:end + 1]
            if end - start >= 2 and (below is None or min(y for _, y in pts) < below):
                out.append(frozenset((round(x), round(y)) for x, y in pts))
            start = end + 1
        return out
    x_height = ink(glyf, hmtx, cmap[ord("x")])[3]
    def body_offset(name, base_name, below=None):
        """dx at which every contour of base_name (those starting below
        `below`) occurs in name, else None."""
        acc, base = contours(name), contours(base_name, below)
        if not base:
            return None
        acc_set = set(acc)
        first_min = min(x for x, _ in base[0])
        for c in acc:
            if len(c) != len(base[0]):
                continue
            dx = min(x for x, _ in c) - first_min
            if all(frozenset((x + dx, y) for x, y in b) in acc_set for b in base):
                return dx
        return None
    def zone_offset(name, base_name):
        """dx between the outlines' extents across the middle half of the
        base letter's height (clear of any join at its top or foot), when
        they are equally wide there, else None."""
        _, _, by0, by1 = ink(glyf, hmtx, base_name)
        lo, hi = by0 + (by1 - by0) * 0.25, by0 + (by1 - by0) * 0.75
        def span(n):
            coords, _, _ = glyf[n].getCoordinates(glyf)
            xs = [x for x, y in coords if lo <= y <= hi]
            return (min(xs), max(xs)) if xs else None
        a, b = span(name), span(base_name)
        if a is None or b is None or abs((a[1] - a[0]) - (b[1] - b[0])) > 4:
            return None
        return a[0] - b[0]
    follows = 0
    letter_categories = ("Lu", "Ll", "Lt", "Lo")
    for code in sorted(fits):
        decomposed = unicodedata.normalize("NFD", chr(code))
        if len(decomposed) == 1 and code not in pairs:
            # A compatibility character that is one letter plus marks or
            # punctuation (ŉ is ʼ + n) follows that letter the same way.
            compat = unicodedata.normalize("NFKD", chr(code))
            found_letters = [p for p in compat if unicodedata.category(p) in letter_categories]
            if len(compat) > 1 and len(found_letters) == 1:
                decomposed = found_letters[0] + compat.replace(found_letters[0], "", 1)
        elif not all(unicodedata.category(m).startswith("M") for m in decomposed[1:]):
            continue
        if fits[code] is None or len(decomposed) < 2 or unicodedata.category(decomposed[0]) not in letter_categories:
            continue
        letter = decomposed[0]
        tries = [(letter, None), ({"i": "ı", "j": "ȷ"}.get(letter), None)]
        if letter.islower():
            tries.append((letter, x_height * 0.95))     # without a dot over the x-height (ї is і + ¨)
        found = None
        for base, below in tries:
            if base is not None and fits.get(ord(base)) is not None:
                dx_src = body_offset(cmap[code], cmap[ord(base)], below)
                if dx_src is not None:
                    found = base, dx_src
                    break
        if found is None and fits.get(ord(letter)) is not None:
            dx_src = zone_offset(cmap[code], cmap[ord(letter)])
            if dx_src is not None:
                found = letter, dx_src
        if found is not None:
            base, dx_src = found
            sx, dx, sy, dy = fits[ord(base)]
            extra = rmetrics[code][0] - rmetrics[ord(base)][0]
            (ax0, ax1), (bx0, bx1) = ref_ink(code), ref_ink(ord(base))
            accent_on_left = extra and abs(ax0 - bx0) > 2 and abs((ax1 - bx1) - extra) <= 2
            body_shift = extra if accent_on_left else 0
            fits[code] = (sx, dx + body_shift - sx * dx_src, sy, dy)
            follows += 1

    shared, scaled = 0, 0
    for code, name in sorted(cmap.items()):
        if code not in fits:
            continue
        radv = rmetrics[code][0]
        if fits[code] is None:
            hmtx[name] = (radv, hmtx[name][1])
        else:
            sx, dx, sy, dy = fits[code]
            scaled += sx != 1.0
            rebuild(name, transform=(sx, 0, 0, sy, dx, dy))
            hmtx[name] = (radv, round(glyf[name].xMin))
        shared += 1

    # --- Letter pairs (Ĳ ĳ Ŀ ŀ ‼ ″) as the reference draws them: its
    # finished parts side by side, the first at the left of the pair's ink box
    # and the last at its right (Verdana: I + J 10 units tighter than I's advance).
    for code, parts in pairs.items():
        name, (rx0, rx1) = cmap[code], ref_ink(code)
        pen = TTGlyphPen(None)
        for k, (part, raise_by) in enumerate(parts):
            px0, px1 = ref_ink(part)
            shift = rx0 - px0 if k == 0 else rx1 - px1
            recording = DecomposingRecordingPen(glyph_set)
            glyph_set[cmap[part]].draw(recording)
            recording.replay(TransformPen(pen, (1, 0, 0, 1, shift, raise_by)))
        glyf[name] = pen.glyph()
        glyf[name].recalcBounds(glyf)
        hmtx[name] = (rmetrics[code][0], glyf[name].xMin)

    # Side bearings follow the outlines everywhere (TrueType convention).
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours:
            g.recalcBounds(glyf)
            hmtx[name] = (hmtx[name][0], g.xMin)

    # --- Vertical metrics, caret slope, underline / strikeout / subscript /
    # superscript metrics and average width from Verdana (the measured
    # average after recalculation is overridden by the reference's, which
    # some layouts use as the width of a "character" unit).
    font["OS/2"].recalcAvgCharWidth(font)
    for table in ("hhea", "OS/2", "post"):
        for field, value in ref.get(table, {}).items():
            setattr(font[table], field, value)
    for tag in ("GPOS", "GSUB", "kern", "GDEF", "morx", "feat"):
        if tag in font:
            del font[tag]

    # --- Kerning: the reference's pairs, in the same table(s) it uses.
    kern_pairs = kerning(cmap, ref.get("kern", {}), "kern")
    if kern_pairs:
        assert len(kern_pairs) <= (0xFFFF - 14) // 6, "too many pairs for one format 0 subtable"
        sub = KernTable_format_0()
        sub.coverage, sub.kernTable = 1, kern_pairs   # horizontal kerning values
        font["kern"] = newTable("kern")
        font["kern"].version, font["kern"].kernTables = 0, [sub]
    gpos_pairs = kerning(cmap, ref.get("gposKern", {}), "GPOS")
    if gpos_pairs:
        rules = "\n".join(f"  pos \\{l} \\{r} {v};" for (l, r), v in sorted(gpos_pairs.items()))
        addOpenTypeFeaturesFromString(font, "languagesystem DFLT dflt;\nlanguagesystem latn dflt;\n"
                                            f"feature kern {{\n{rules}\n}} kern;\n")

    # --- Code points the reference maps to the same glyph as another one and
    # has no measurements of their own (Verdana 2.35 shows its fi and fl at
    # the private-use U+F001 and U+F002 too) map to Fauxdana's glyph for that
    # other code point, replacing whatever DejaVu put there.
    aliased = 0
    for alias, target in ref.get("aliases", {}).items():
        a, t = int(alias, 16), int(target, 16)
        if a in rmetrics or t not in cmap:
            continue
        for table in font["cmap"].tables:
            if table.isUnicode() and (a <= 0xFFFF or table.format in (12, 13)):
                table.cmap[a] = cmap[t]
        aliased += 1

    # --- Names.
    style = {"regular": "Regular", "bold": "Bold", "italic": "Italic", "boldItalic": "Bold Italic"}[style_key]
    name_table = font["name"]
    copyright_ = name_table.getDebugName(0) or ""
    records = {
        0: copyright_ + " Fauxdana: a metric-compatible derivative (advance widths, kerning and vertical metrics of Verdana) for Slack's document previews; renamed as the Bitstream Vera and DejaVu licences require.",
        1: FAMILY, 2: style, 3: f"{FAMILY} {style}; {VERSION}", 4: f"{FAMILY} {style}".replace(" Regular", ""),
        5: f"Version {VERSION}", 6: f"{FAMILY}-{style.replace(' ', '')}", 16: FAMILY, 17: style,
    }
    for rec in list(name_table.names):
        if rec.nameID in records:
            rec.string = records[rec.nameID]
    for nid, value in records.items():
        if not name_table.getName(nid, 3, 1, 0x409):
            name_table.setName(value, nid, 3, 1, 0x409)
    font["head"].fontRevision = float(VERSION)
    font["OS/2"].achVendID = "SLAK"
    # Deterministic output: keep the source's timestamps instead of stamping
    # the build time (fontTools rewrites head.modified on save unless told
    # not to), so identical inputs rebuild byte-identical fonts.
    font.recalcTimestamp = False
    font.save(out)
    slant = f"; re-slanted {source_angle:g}° → {font['post'].italicAngle:g}°" if italic else ""
    print(f"{out}: {shared} glyphs took Verdana's metrics ({scaled} scaled, {follows} placed as their base letter, {len(pairs)} pairs rebuilt, {aliased} aliases){slant}; stem {stem}, I bars {bar_t}x{bar_ext}; J lift {lift}, shift {shift_J}, hook stretch {stretch_J:.2f}; j shift {shift_j}, stretch {stretch_j:.2f}, serif {lc_serif_ext}x{lc_bar_t}; accents placed {placed}; kerning pairs {len(kern_pairs)} kern, {len(gpos_pairs)} GPOS")

if __name__ == "__main__":
    main()
