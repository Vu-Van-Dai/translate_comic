"""Repair Vietnamese glyph advance widths in a font.

000Augie-TB ships its precomposed Vietnamese letters with advance widths up to
25% wider than the base letter (ó 1772 vs o 1442 units), so every accented
letter is followed by a visible gap: "nhó c", "KÉ T" (Super Lovers ch.1,
2026-10-13). Each accented glyph gets its base letter's advance, widened only
if its own ink reaches further right than the base's does.

    python scripts/fix_font_advances.py assets/fonts/000Augie-TB.ttf
"""
import sys
import unicodedata

from fontTools.ttLib import TTFont

VI = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ"
VI += VI.upper()

path = sys.argv[1]
font = TTFont(path)
cmap, hmtx, glyf = font.getBestCmap(), font["hmtx"].metrics, font["glyf"]
fixed = []
for ch in VI:
    base = unicodedata.normalize("NFD", ch)[0]
    if base in "uUoO" and ("ư" in ch.lower() or "ơ" in ch.lower()):
        continue          # horned letters are legitimately wider
    if ord(ch) not in cmap or ord(base) not in cmap:
        continue
    g, b = cmap[ord(ch)], cmap[ord(base)]
    adv, lsb = hmtx[g]
    base_adv, _ = hmtx[b]
    if abs(adv - base_adv) <= 0.06 * base_adv:
        continue
    gx, bx = glyf[g], glyf[b]
    right_overhang = max(0, getattr(gx, "xMax", 0) - getattr(bx, "xMax", 0))
    hmtx[g] = (base_adv + right_overhang, lsb)
    fixed.append(ch)
font.save(path)
print(f"{path}: fixed {len(fixed)} glyphs: {''.join(fixed)}")
