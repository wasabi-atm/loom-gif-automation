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
from loomgif.overlays import facecam_mask  # noqa: E402
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
        # No ring or shadow layer: the shape sits straight on the page.
        self.assertNotIn("withring", graph)
        # The crop window must be time-varying, or the page never scrolls.
        self.assertIn("crop=%d:%d:0:'" % (cfg.width, cfg.height), graph)
        self.assertIn("t/", graph)


class TestFacecamMask(unittest.TestCase):
    def test_mask_is_greyscale_and_exact_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            with Image.open(facecam_mask(120, Path(tmp) / "m.png")) as mask:
                self.assertEqual(mask.size, (120, 120))
                self.assertEqual(mask.mode, "L")  # alphamerge needs a single channel

    def test_squircle_keeps_more_area_than_a_circle(self):
        """A squircle fills its box more fully — that is what makes it read as a
        rounded square rather than a circle."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with Image.open(facecam_mask(200, tmp / "sq.png", "squircle")) as squircle:
                sq_area = sum(squircle.point(lambda v: 1 if v > 127 else 0).getdata())
            with Image.open(facecam_mask(200, tmp / "ci.png", "circle")) as circle:
                ci_area = sum(circle.point(lambda v: 1 if v > 127 else 0).getdata())

            box = 200 * 200
            self.assertGreater(sq_area, ci_area)
            self.assertLess(sq_area, box)          # still rounded, not a plain square
            self.assertAlmostEqual(ci_area / box, 3.14159 / 4, places=2)

    def test_corners_are_transparent_and_centre_is_opaque(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            with Image.open(facecam_mask(200, Path(tmp) / "sq.png")) as mask:
                self.assertEqual(mask.getpixel((1, 1)), 0)
                self.assertEqual(mask.getpixel((198, 198)), 0)
                self.assertEqual(mask.getpixel((100, 100)), 255)
                # Flat along the edge midpoints, unlike a circle.
                self.assertEqual(mask.getpixel((100, 2)), 255)


class TestEmailEmbed(unittest.TestCase):
    def test_campaign_snippet_uses_variable_and_alt(self):
        html = email_embed.campaign_snippet()
        self.assertIn("{{Gif url}}", html)
        self.assertIn("alt=", html)
        self.assertNotIn("<a ", html)

    def test_display_width_matches_the_hosted_gif(self):
        """A mismatch means the client rescales: narrower looks soft, wider
        wastes bytes nobody sees."""
        width = RenderConfig().gif_width
        html = email_embed.campaign_snippet()
        self.assertIn(f'width="{width}"', html)
        self.assertIn(f"max-width:{width}px", html)

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
        self.assertIn(f"[3:v]scale={cfg.width}:-1", graph)
        self.assertIn("[withcam][nav]overlay=0:0", graph)
        self.assertTrue(graph.endswith("[v]"))

    def test_graph_without_navbar_uses_no_fifth_input(self):
        graph = build_filtergraph(RenderConfig(), with_navbar=False)
        self.assertNotIn("[3:v]", graph)
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


class TestExactGifTiming(unittest.TestCase):
    """GIF delays live in centiseconds, so only frame rates dividing 100 give
    an exact duration. 12fps means an 8.33cs delay that rounds to 8, running
    the GIF ~4% fast — a 5s clip lands at 4.8s."""

    def test_snap_picks_the_nearest_exact_rate_at_or_below(self):
        from loomgif.compose import snap_fps

        self.assertEqual(snap_fps(12), 10)
        self.assertEqual(snap_fps(24), 20)
        self.assertEqual(snap_fps(10), 10)
        self.assertEqual(snap_fps(1), 2)

    def test_every_ladder_rate_divides_100_exactly(self):
        from loomgif.compose import _GIF_LADDER, step_fps

        for _, steps, _ in _GIF_LADDER:
            for start in (25, 20, 10):
                fps = step_fps(start, steps)
                self.assertEqual(100 % fps, 0, f"{fps}fps cannot produce exact timing")

    def test_ladder_never_widens_or_speeds_up(self):
        from loomgif.compose import _GIF_LADDER

        widths = [rung[0] for rung in _GIF_LADDER]
        steps = [rung[1] for rung in _GIF_LADDER]
        self.assertEqual(widths, sorted(widths, reverse=True))
        self.assertEqual(steps, sorted(steps))


class TestDurationBinding(unittest.TestCase):
    def test_explicit_duration_wins(self):
        from loomgif.compose import resolve_duration

        cfg = RenderConfig()
        cfg.duration = 3.5
        self.assertEqual(resolve_duration(Path("/nonexistent.mp4"), cfg), 3.5)

    def test_falls_back_when_the_clip_cannot_be_read(self):
        from loomgif.compose import resolve_duration

        cfg = RenderConfig()
        cfg.duration = None
        self.assertEqual(resolve_duration(Path("/nonexistent.mp4"), cfg), 6.0)


class TestAutoExposure(unittest.TestCase):
    def test_gamma_brightens_dark_and_leaves_bright_alone(self):
        from loomgif import exposure

        # Solve the same relation gamma_for uses, without touching ffmpeg.
        import math

        def gamma(measured, target):
            raw = math.log(measured / 255.0) / math.log(target / 255.0)
            return max(exposure.GAMMA_MIN, min(exposure.GAMMA_MAX, raw))

        self.assertGreater(gamma(88.4, 138), 1.5)   # the dark take gets lifted
        self.assertAlmostEqual(gamma(138, 138), 1.0, places=6)  # on target = untouched
        self.assertEqual(gamma(200, 138), 1.0)      # already bright is never darkened

    def test_eq_filter_is_empty_when_nothing_to_do(self):
        from loomgif import exposure

        self.assertEqual(
            exposure.eq_filter(Path("/x.mp4"), auto=False, target_luma=138, gamma=1.0,
                               brightness=0.0, contrast=1.0, saturation=1.0),
            "",
        )

    def test_eq_filter_composes_only_active_terms(self):
        from loomgif import exposure

        built = exposure.eq_filter(Path("/x.mp4"), auto=False, target_luma=138, gamma=1.5,
                                   brightness=0.0, contrast=1.06, saturation=1.0)
        self.assertIn("gamma=1.500", built)
        self.assertIn("contrast=1.060", built)
        self.assertNotIn("brightness", built)
        self.assertNotIn("saturation", built)

    def test_correction_is_applied_to_the_facecam_stream_only(self):
        graph = build_filtergraph(RenderConfig(), facecam_eq="eq=gamma=1.500")
        self.assertIn("eq=gamma=1.500,format=rgba[fcraw]", graph)
        self.assertEqual(graph.count("eq=gamma"), 1)


class TestBubbleShadow(unittest.TestCase):
    def test_shadow_sits_behind_the_bubble(self):
        cfg = RenderConfig()
        graph = build_filtergraph(cfg, shadow_pad=46)
        # Shadow is composited onto the background first, the face cam over it.
        self.assertIn("[bg][3:v]overlay=", graph)
        self.assertIn("[withshadow][fc]overlay=", graph)
        self.assertLess(graph.index("withshadow"), graph.index("[withcam]"))

    def test_navbar_input_shifts_when_a_shadow_is_present(self):
        cfg = RenderConfig()
        with_shadow = build_filtergraph(cfg, with_navbar=True, shadow_pad=46)
        without = build_filtergraph(cfg, with_navbar=True, shadow_pad=None)
        self.assertIn("[4:v]scale=", with_shadow)   # 3 is the shadow
        self.assertIn("[3:v]scale=", without)       # no shadow, navbar moves up

    def test_shadow_can_be_switched_off(self):
        graph = build_filtergraph(RenderConfig(), shadow_pad=None)
        self.assertNotIn("withshadow", graph)

    def test_shadow_is_offset_padding_and_translucent(self):
        from PIL import Image

        from loomgif.overlays import shadow_overlay

        with tempfile.TemporaryDirectory() as tmp:
            dest, pad = shadow_overlay(160, Path(tmp) / "s.png", opacity=0.3, blur_px=14, offset_px=6)
            self.assertEqual(pad, 14 * 2 + 6)
            with Image.open(dest) as shadow:
                self.assertEqual(shadow.size, (160 + pad * 2, 160 + pad * 2))
                alpha = shadow.getchannel("A")
                self.assertEqual(alpha.getpixel((0, 0)), 0)          # clear at the corner
                centre = alpha.getpixel((shadow.width // 2, shadow.height // 2))
                self.assertGreater(centre, 0)
                self.assertLess(centre, 255)                          # never fully opaque

    def test_shadow_geometry_scales_with_the_bubble(self):
        cfg = RenderConfig()
        self.assertGreater(cfg.facecam_shadow_blur, 0)
        self.assertLess(cfg.facecam_shadow_blur, cfg.facecam_diameter // 2)
        self.assertLess(cfg.facecam_shadow_offset, cfg.facecam_shadow_blur)


class TestHostedUrls(unittest.TestCase):
    def test_version_is_appended_as_a_cache_key(self):
        """Overwriting keeps the URL, but ImageKit's CDN serves the previously
        cached bytes, so the URL handed to Instantly carries a content key."""
        from loomgif.imagekit_client import Upload

        upload = Upload(url="https://ik.imagekit.io/x/a.gif", file_id="f", name="a.gif",
                        path="/a.gif", version="abc123")
        self.assertEqual(upload.versioned_url, "https://ik.imagekit.io/x/a.gif?v=abc123")

    def test_no_version_leaves_the_url_untouched(self):
        from loomgif.imagekit_client import Upload

        upload = Upload(url="https://ik.imagekit.io/x/a.gif", file_id="f", name="a.gif", path="/a.gif")
        self.assertEqual(upload.versioned_url, upload.url)

    def test_transform_joins_onto_an_existing_query(self):
        from loomgif.config import ImageKitConfig
        from loomgif.imagekit_client import ImageKitUploader

        cfg = ImageKitConfig(url_endpoint="https://ik.imagekit.io/x", public_key="p", private_key="s")
        uploader = ImageKitUploader.__new__(ImageKitUploader)
        uploader.cfg = cfg
        self.assertEqual(
            uploader.transform("https://ik.imagekit.io/x/a.gif?v=1", "w-400"),
            "https://ik.imagekit.io/x/a.gif?v=1&tr=w-400",
        )


class TestUploadSelection(unittest.TestCase):
    def test_only_the_gif_is_hosted_by_default(self):
        cfg = RenderConfig()
        self.assertTrue(cfg.hosts("gif"))
        for asset in ("mp4", "webm", "poster"):
            self.assertFalse(cfg.hosts(asset), f"{asset} should not be hosted by default")

    def test_all_and_explicit_lists(self):
        cfg = RenderConfig()
        cfg.upload_assets = "all"
        self.assertTrue(all(cfg.hosts(a) for a in ("gif", "mp4", "webm", "poster")))
        cfg.upload_assets = "gif,poster"
        self.assertTrue(cfg.hosts("poster"))
        self.assertFalse(cfg.hosts("mp4"))


class TestCampaignPreflight(unittest.TestCase):
    """Fixtures mirror the real shape returned by GET /api/v2/campaigns/{id}."""

    def _campaign(self, **overrides):
        campaign = {
            "name": "GIF test",
            "text_only": False,
            "first_email_text_only": False,
            "custom_variables": {"jobTitle": True, "Gif url": True},
            "sequences": [{"steps": [
                {"type": "email", "variants": [{"subject": "One", "body": "<div>Hi</div>"}]},
                {"type": "email", "variants": [{"subject": "Two",
                                                "body": '<img src="{{Gif url}}" width="400" alt="x">'}]},
            ]}],
        }
        campaign.update(overrides)
        return campaign

    def _fail_labels(self, campaign):
        from loomgif.instantly import preflight

        return [c.label for c in preflight(campaign) if not c.ok]

    def test_a_healthy_campaign_passes_everything(self):
        self.assertEqual(self._fail_labels(self._campaign()), [])

    def test_text_only_is_caught(self):
        self.assertIn("'Send emails as text-only' is off", self._fail_labels(self._campaign(text_only=True)))

    def test_first_email_text_only_is_caught(self):
        self.assertIn(
            "'Send first email as text-only' is off",
            self._fail_labels(self._campaign(first_email_text_only=True)),
        )

    def test_missing_variable_reference_is_caught(self):
        campaign = self._campaign(sequences=[{"steps": [
            {"type": "email", "variants": [{"body": "<div>no image here</div>"}]}
        ]}])
        self.assertIn("A step references {{Gif url}}", self._fail_labels(campaign))

    def test_image_on_step_one_is_flagged(self):
        campaign = self._campaign(sequences=[{"steps": [
            {"type": "email", "variants": [{"body": '<img src="{{Gif url}}">'}]},
        ]}])
        self.assertIn("The image is not on step 1", self._fail_labels(campaign))

    def test_unregistered_variable_is_flagged(self):
        campaign = self._campaign(custom_variables={"jobTitle": True})
        self.assertIn("'Gif url' is registered on the campaign", self._fail_labels(campaign))

    def test_variables_with_spaces_are_valid_instantly_names(self):
        """The live workspace already runs {{NEW A 1}} and {{SUBJECT EMAIL 1}},
        so a space in 'Gif url' is not a problem."""
        from loomgif.instantly import MEDIA_COLUMNS

        self.assertTrue(any(" " in name for name in MEDIA_COLUMNS))


class TestContentCacheKey(unittest.TestCase):
    """The key must come from the file's contents.

    ImageKit's own versionInfo.id does NOT change on overwrite — the name goes
    to "Version 5" while the id stays put — so keying on it busts the CDN cache
    exactly once and is stale from then on.
    """

    def test_same_bytes_give_the_same_key(self):
        from loomgif.imagekit_client import content_key

        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.gif", Path(tmp) / "b.gif"
            a.write_bytes(b"identical"), b.write_bytes(b"identical")
            self.assertEqual(content_key(a), content_key(b))

    def test_changed_bytes_give_a_different_key(self):
        from loomgif.imagekit_client import content_key

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.gif"
            path.write_bytes(b"first render")
            before = content_key(path)
            path.write_bytes(b"second render")
            self.assertNotEqual(before, content_key(path))

    def test_key_is_short_and_url_safe(self):
        from loomgif.imagekit_client import content_key

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.gif"
            path.write_bytes(b"x")
            key = content_key(path)
            self.assertEqual(len(key), 12)
            self.assertTrue(key.isalnum())
