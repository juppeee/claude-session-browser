"""Packt einen Ordner mit editierten 20x20-PNGs zurueck zu clawd_sprites.py.

Struktur erwartet:
  <root>/<anim_name_underscored>/frame_XX.png

Regeln:
- Alle PNGs muessen 20x20 sein
- Alpha < 128 -> Transparent-Slot (Palette-Index 0)
- Alpha >= 128 -> in Palette einsortieren (Index 1-9)
- Max 9 opake Farben pro Anim (wenn mehr: automatische Quantisierung)
- Anim-Namen: Underscore -> Space (`work_coding` -> `work coding`)
- Reihenfolge im BLOB wird aus dem alten clawd_sprites.py uebernommen, damit
  neue Anim-Positionen die gleichen bleiben.
"""
import os
import sys
import json
import zlib
import base64
import glob
from PIL import Image

ROOT = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path.home() / "Downloads" / "clawd_sprites")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clawd_sprites.py")

# Aktuelle Reihenfolge aus altem BLOB extrahieren, damit die Sortierung stabil bleibt
def _current_order():
    try:
        from clawd_sprites import BLOB
        data = json.loads(zlib.decompress(base64.b64decode(BLOB)).decode("utf-8"))
        return [a["n"] for a in data]
    except Exception:
        return []

def _folder_to_anim(folder):
    return folder.replace("_", " ")

def _load_frames(anim_dir):
    files = sorted(f for f in os.listdir(anim_dir) if f.lower().endswith(".png"))
    frames = []
    for f in files:
        img = Image.open(os.path.join(anim_dir, f)).convert("RGBA")
        if img.size != (20, 20):
            raise ValueError(f"{f} in {anim_dir} ist nicht 20x20 sondern {img.size}")
        frames.append(list(img.getdata()))  # 400 (r,g,b,a) Tupel
    return frames

def _hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)

def _build_anim(name, frames):
    """Erzeuge (palette-hex-list, frames-as-index-lists)."""
    # Opake Farben sammeln
    opaque = {}  # (r,g,b) -> count
    for f in frames:
        for r, g, b, a in f:
            if a >= 128:
                key = (r, g, b)
                opaque[key] = opaque.get(key, 0) + 1
    # Wenn > 9 Farben: quantisieren via PIL (median cut)
    if len(opaque) > 9:
        # Baue ein "Superframe" aus allen Pixeln fuer Palette-Berechnung
        big = Image.new("RGB", (20, 20 * len(frames)))
        for i, f in enumerate(frames):
            row = Image.new("RGB", (20, 20))
            row.putdata([(r, g, b) if a >= 128 else (0, 0, 0)
                         for r, g, b, a in f])
            big.paste(row, (0, i * 20))
        # Quantisiere auf 9 Farben
        q = big.quantize(colors=9, method=Image.MEDIANCUT, dither=Image.NONE)
        pal = q.getpalette()[:9 * 3]
        # Ersetze in "opaque" durch die 9 quantisierten Farben
        opaque = {}
        for i in range(9):
            opaque[(pal[i*3], pal[i*3+1], pal[i*3+2])] = 1
        # Map jedes Original-Pixel auf das nearest neighbor in der neuen Palette
        def nearest(rgb):
            best_d, best = 1e12, next(iter(opaque))
            for c in opaque:
                d = (rgb[0]-c[0])**2 + (rgb[1]-c[1])**2 + (rgb[2]-c[2])**2
                if d < best_d:
                    best_d, best = d, c
            return best
        # Frames neu bauen mit den neuen Farben
        new_frames = []
        for f in frames:
            new_frames.append([
                (0, 0, 0, 0) if a < 128 else (*nearest((r, g, b)), 255)
                for r, g, b, a in f
            ])
        frames = new_frames

    # Palette: Index 0 = transparent placeholder, Index 1-9 = opake Farben
    opaque_list = list(opaque.keys())
    # Wenn weniger als 9 opake, mit schwarz auffuellen (fuer 10-Slot Layout)
    palette_rgb = [(0, 0, 0)] + opaque_list  # Index 0 ist transparent (unused hex)
    while len(palette_rgb) < 10:
        palette_rgb.append((0, 0, 0))
    palette_hex = [_hex(rgb) for rgb in palette_rgb]

    # Frames zu Index-Listen (400 ints je Frame)
    color_to_idx = {rgb: i + 1 for i, rgb in enumerate(opaque_list)}
    idx_frames = []
    for f in frames:
        idx = []
        for r, g, b, a in f:
            if a < 128:
                idx.append(0)
            else:
                # Suche exakte Farbe. Wenn nicht drin (nach quantisation kann
                # es passieren dass ein Pixel geringfuegig anders ist), nearest.
                c = color_to_idx.get((r, g, b))
                if c is None:
                    # Nearest neighbor
                    best_d, best_i = 1e12, 1
                    for k, i in color_to_idx.items():
                        d = (k[0]-r)**2 + (k[1]-g)**2 + (k[2]-b)**2
                        if d < best_d:
                            best_d, best_i = d, i
                    c = best_i
                idx.append(c)
        idx_frames.append(idx)
    return palette_hex, idx_frames


def main():
    if not os.path.isdir(ROOT):
        print(f"Fehler: {ROOT} nicht gefunden")
        return 1
    folders = sorted(f for f in os.listdir(ROOT)
                     if os.path.isdir(os.path.join(ROOT, f)))
    print(f"Gefundene Anim-Ordner: {len(folders)}")

    # Reihenfolge aus altem BLOB uebernehmen
    order = _current_order()
    anim_names = [_folder_to_anim(f) for f in folders]
    if order:
        ordered = [n for n in order if n in anim_names]
        # Neue die noch nicht in order sind, hinten anhaengen
        for n in anim_names:
            if n not in ordered:
                ordered.append(n)
        anim_names = ordered
    print(f"Anim-Reihenfolge: {anim_names}")

    result = []
    for anim in anim_names:
        folder = anim.replace(" ", "_")
        d = os.path.join(ROOT, folder)
        if not os.path.isdir(d):
            print(f"  SKIP {anim}: Ordner {folder} fehlt")
            continue
        frames = _load_frames(d)
        if not frames:
            print(f"  SKIP {anim}: keine Frames")
            continue
        palette, idx_frames = _build_anim(anim, frames)
        result.append({"n": anim, "p": palette, "f": idx_frames})
        opaque_count = sum(1 for h in palette[1:] if h != "#000000")
        print(f"  OK {anim}: {len(frames)} Frames, "
              f"{opaque_count} opake Farben")

    # Zu BLOB packen
    raw = json.dumps(result, separators=(",", ":")).encode("utf-8")
    blob = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    print(f"\nBLOB-Groesse: {len(blob)} Zeichen")

    # clawd_sprites.py schreiben
    content = (
        "# Automatisch generiert aus clawdmeter clawd-animations-viewer.html\n"
        f"# {len(result)} x 20x20 Sprites, zlib+base64. Nicht editieren.\n"
        f'BLOB = "{blob}"\n'
    )
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Geschrieben: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
