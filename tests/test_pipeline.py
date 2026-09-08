"""Unit tests that need no network, no ffmpeg and no credentials."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loomgif import csv_merge, email_embed  # noqa: E402
from loomgif.compose import build_filtergraph  # noqa: E402
from loomgif.config import RenderConfig  # noqa: E402
from loomgif.instantly import MEDIA_COLUMNS, VAR_GIF  # noqa: E402
from loomgif.overlays import circle_mask, ring_overlay  # noqa: E402
from loomgif.screenshot import normalise_url, slug_for  # noqa: E402


class TestUrls(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(normalise_url("acme.com"), "https://acme.com")
        self.assertEqual(normalise_url("https://acme.com"), "https://acme.com")
        self.assertEqual(normalise_url("http://acme.com"), "http://acme.com")

    def test_slug_strips_www_and_scheme(self):
        self.assertEqual(slug_for("https://www.acme.co.uk/pricing"), "acme-co-uk")
        self.assertEqual(slug_for("acme.com"), "acme-com")


class TestRenderConfig(unittest.TestCase):
    def test_dimensions_snap_even(self):
        """H.264 rejects odd dimensions, so overrides must be snapped."""
        cfg = RenderConfig()
        cfg.canvas_width, cfg.canvas_height = 1201, 675
        self.assertEqual(cfg.width % 2, 0)
        self.assertEqual(cfg.height % 2, 0)
        self.assertEqual(cfg.facecam_diameter % 2, 0)

    def test_filtergraph_pins_bubble_bottom_left(self):
        cfg = RenderConfig()
        graph = build_filtergraph(cfg)
        expected_y = cfg.height - cfg.facecam_diameter - cfg.facecam_margin
        self.assertIn(f"overlay={cfg.facecam_margin}:{expected_y}", graph)
        self.assertIn("alphamerge", graph)
        # The crop window must be time-varying, or the page never scrolls.
        self.assertIn("crop=%d:%d:0:'" % (cfg.width, cfg.height), graph)
        self.assertIn("t/", graph)


class TestOverlays(unittest.TestCase):
    def test_mask_and_ring_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            with Image.open(circle_mask(120, Path(tmp) / "m.png")) as mask:
                self.assertEqual(mask.size, (120, 120))
                self.assertEqual(mask.mode, "L")

            # The ring canvas is padded by shadow_px on every side.
            with Image.open(ring_overlay(120, 4, "#FFFFFF", Path(tmp) / "r.png", shadow_px=10)) as ring:
                self.assertEqual(ring.size, (140, 140))
                self.assertEqual(ring.mode, "RGBA")


class TestEmailEmbed(unittest.TestCase):
    def test_campaign_snippet_uses_variable_and_alt(self):
        html = email_embed.campaign_snippet()
        self.assertIn("{{Gif url}}", html)
        self.assertIn('width="600"', html)
        self.assertIn("alt=", html)
        self.assertNotIn("<a ", html)

    def test_link_variant_wraps_in_anchor(self):
        html = email_embed.campaign_snippet(with_link=True)
        self.assertIn("<a href=", html)
        self.assertIn("{{Loom link}}", html)

    def test_alt_text_is_mandatory(self):
        with self.assertRaises(ValueError):
            email_embed.snippet(gif_url="https://x/y.gif", alt="  ")


class TestCsvSpec(unittest.TestCase):
    def test_media_columns_obey_instantly_rules(self):
        for column in MEDIA_COLUMNS:
            self.assertTrue(column[0].isupper(), f"{column} must start with a capital")
            self.assertLessEqual(len(column), 20, f"{column} exceeds 20 chars")

    def test_email_must_be_first_column(self):
        with self.assertRaises(csv_merge.CsvSpecError):
            csv_merge.validate_columns(["First name", "Email"])

    def test_warns_on_lowercase_and_long_names(self):
        warnings = csv_merge.validate_columns(["Email", "gif url", "A" * 21])
        self.assertTrue(any("capital" in w for w in warnings))
        self.assertTrue(any("20-char" in w for w in warnings))


class TestMerge(unittest.TestCase):
    def _write(self, path: Path, header, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def test_splits_gif_and_nogif(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            campaign = tmp / "campaign.csv"
            self._write(
                campaign,
                ["Email", "First name", "Email1Body"],
                [
                    {"Email": "a@x.com", "First name": "A", "Email1Body": "line one\n\nline two"},
                    {"Email": "b@y.com", "First name": "B", "Email1Body": "hi"},
                ],
            )
            manifest = tmp / "manifest.csv"
            self._write(
                manifest,
                ["Email", VAR_GIF],
                [
                    {"Email": "A@X.com", VAR_GIF: "https://ik/a.gif"},  # case-insensitive match
                    {"Email": "b@y.com", VAR_GIF: ""},                  # no media -> no-GIF file
                ],
            )

            report = csv_merge.merge(campaign, manifest, tmp / "out")
            self.assertEqual((report.matched, report.unmatched, report.total_rows), (1, 1, 2))

            with report.gif_path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0][VAR_GIF], "https://ik/a.gif")
            self.assertEqual(rows[0]["Email1Body"], "line one\n\nline two")  # newlines survive

            # The no-GIF file must not carry an empty Gif url — that renders a broken image.
            with report.nogif_path.open(encoding="utf-8") as handle:
                self.assertNotIn(VAR_GIF, csv.DictReader(handle).fieldnames)


if __name__ == "__main__":
    unittest.main()


class TestNavbarLayer(unittest.TestCase):
    def test_navbar_is_pinned_above_the_scrolling_page(self):
        cfg = RenderConfig()
        graph = build_filtergraph(cfg, with_navbar=True)
        # Scaled to canvas width and pinned at the very top, composited last.
        self.assertIn(f"[4:v]scale={cfg.width}:-1", graph)
        self.assertIn("[withring][nav]overlay=0:0", graph)
        self.assertTrue(graph.endswith("[v]"))

    def test_graph_without_navbar_uses_no_fifth_input(self):
        graph = build_filtergraph(RenderConfig(), with_navbar=False)
        self.assertNotIn("[4:v]", graph)
        self.assertTrue(graph.endswith("[v]"))


class TestFacecamRotation(unittest.TestCase):
    def test_pick_is_deterministic_and_spreads(self):
        from loomgif import facecam

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name in ("sam-facecam-1.mp4", "sam-facecam-2.mp4"):
                (tmp / name).write_bytes(b"x")
            (tmp / "notes.txt").write_bytes(b"ignored")

            self.assertEqual(len(facecam.takes(tmp)), 2)  # non-video ignored

            # Same prospect always draws the same take, or a re-run would swap
            # media that has already been emailed.
            first = facecam.pick(tmp, "monday-com")
            self.assertEqual(first, facecam.pick(tmp, "monday-com"))

            chosen = {facecam.pick(tmp, f"prospect-{i}-com").name for i in range(24)}
            self.assertEqual(len(chosen), 2, "both takes should get used across a list")

    def test_single_file_path_still_works(self):
        from loomgif import facecam

        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "only.mp4"
            clip.write_bytes(b"x")
            self.assertEqual(facecam.pick(clip, "anything"), clip)

    def test_missing_path_returns_none(self):
        from loomgif import facecam

        self.assertIsNone(facecam.pick(Path("/nope/missing"), "x"))
