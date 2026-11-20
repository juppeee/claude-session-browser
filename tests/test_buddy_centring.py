"""The buddy sits in the middle of its surface. Run with the others:
python -m unittest discover -s tests
"""

import base64
import json
import unittest
import zlib

import claude_sessions as app
from clawd_sprites import BLOB

BODY = "#DE7552"
# Drawn by hand, so the body sits where the artist put it rather than where
# the grid wants it. Both are off by half a pixel in the sprite data itself
# and cannot be corrected by the surface; see issue #19.
SCHIEF = {"expression wink", "write"}


def animationen():
    roh = zlib.decompress(base64.b64decode(BLOB)).decode("utf-8")
    return json.loads(roh)


def belegt(frame):
    """Koordinaten auf der Zeichenflaeche, die nicht Hintergrund sind."""
    zellen = app.flaeche_zellen(frame, app._hintergrund_index(frame))
    return [(i % app.FLAECHE_W, i // app.FLAECHE_W, v)
            for i, v in enumerate(zellen) if v is not None]


class SurfaceTests(unittest.TestCase):
    def test_surface_is_wide_enough_for_an_odd_centre(self):
        # Eine gerade Breite hat keine mittlere Spalte - genau daran ist die
        # Zentrierung im 20er-Raster gescheitert.
        self.assertEqual(app.FLAECHE_W % 2, 1)
        self.assertGreaterEqual(app.FLAECHE_W, app.SPRITE_PX + 2)
        self.assertGreaterEqual(app.FLAECHE_H, app.SPRITE_PX + 2)

    def test_sprite_pixel_lands_at_the_offset(self):
        frame = [0] * (app.SPRITE_PX * app.SPRITE_PX)
        frame[0] = 5                      # oben links im Sprite
        zellen = app.flaeche_zellen(frame, 1)
        treffer = [i for i, v in enumerate(zellen) if v is not None]
        self.assertEqual(len(treffer), 1)
        x, y = treffer[0] % app.FLAECHE_W, treffer[0] // app.FLAECHE_W
        self.assertEqual((x, y), (app.SPRITE_OFF_X, app.SPRITE_OFF_Y))

    def test_background_of_the_sprite_counts_as_empty(self):
        frame = [3] * (app.SPRITE_PX * app.SPRITE_PX)
        self.assertTrue(all(v is None for v in app.flaeche_zellen(frame, 3)))


class EveryAnimationTests(unittest.TestCase):
    def test_body_is_centred_on_the_surface(self):
        mitte = (app.FLAECHE_W - 1) / 2
        for a in animationen():
            if a["n"] in SCHIEF:
                continue
            xs = [x for f in a["f"] for x, _y, v in belegt(f)
                  if a["p"][v] == BODY]
            if not xs:
                continue              # limit malt die Figur nicht in Koerperfarbe
            with self.subTest(anim=a["n"]):
                self.assertAlmostEqual(sum(xs) / len(xs), mitte, delta=0.1)

    def test_nothing_touches_the_edge_of_the_surface(self):
        for a in animationen():
            for nr, f in enumerate(a["f"]):
                punkte = belegt(f)
                if not punkte:
                    continue
                with self.subTest(anim=a["n"], frame=nr):
                    self.assertGreater(min(x for x, _, _ in punkte), 0)
                    self.assertLess(max(x for x, _, _ in punkte),
                                    app.FLAECHE_W - 1)
                    self.assertGreater(min(y for _, y, _ in punkte), 0)
                    self.assertLess(max(y for _, y, _ in punkte),
                                    app.FLAECHE_H - 1)


if __name__ == "__main__":
    unittest.main()
