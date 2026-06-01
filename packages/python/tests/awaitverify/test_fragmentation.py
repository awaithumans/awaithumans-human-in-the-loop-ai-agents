"""Tests for client-side document fragmentation.

These tests cover the load-bearing security claim: the SDK generates
exactly five masks per page in the customer's environment, and the
mask geometry covers every pixel without any single fragment showing
more than ~half the page.

Pillow is required to run these tests. Install with:
    pip install 'awaithumans[awaitverify]'
"""

from __future__ import annotations

import io

import pytest

PIL = pytest.importorskip("PIL", reason="Pillow not installed; skip fragmentation tests")
from PIL import Image  # noqa: E402

from awaithumans.awaitverify.errors import (  # noqa: E402
    VerifyDocumentLoadError,
    VerifyDocumentTooLargeError,
)
from awaithumans.awaitverify.fragmentation import (  # noqa: E402
    _mask_regions,
    fragment_document,
    load_pages,
)
from awaithumans.utils.constants import (  # noqa: E402
    AWAITVERIFY_FRAGMENT_COUNT,
    AWAITVERIFY_MAX_PAGES,
)


def _make_png(width: int = 200, height: int = 100, color: str = "white") -> bytes:
    """Generate a small in-memory PNG for tests."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestMaskRegions:
    def test_returns_exactly_five_regions(self) -> None:
        regions = _mask_regions(width=200, height=100)
        assert len(regions) == AWAITVERIFY_FRAGMENT_COUNT

    def test_region_zero_covers_right_half(self) -> None:
        regions = _mask_regions(width=200, height=100)
        # Region 0: right half
        left, top, right, bottom = regions[0]
        assert left == 100  # width // 2
        assert top == 0
        assert right == 200
        assert bottom == 100

    def test_region_two_covers_center_strip(self) -> None:
        regions = _mask_regions(width=200, height=100)
        # Region 2: center 50% (quarter to three-quarter)
        left, _, right, _ = regions[2]
        assert left == 50  # width // 4
        assert right == 150  # 3 * width // 4

    def test_top_and_bottom_regions_partition_height(self) -> None:
        regions = _mask_regions(width=200, height=100)
        # Region 3: top half. Region 4: bottom half. Together they cover the page.
        _, top3, _, bottom3 = regions[3]
        _, top4, _, bottom4 = regions[4]
        assert top3 == 0 and bottom3 == 50
        assert top4 == 50 and bottom4 == 100
        # Their union is the whole height
        assert bottom3 == top4

    def test_no_single_region_covers_more_than_half_the_page(self) -> None:
        """Symmetry invariant: every mask blacks out AT MOST 50% of the
        page area. A reviewer mentally re-assembles the document by
        scanning across fragments, so each fragment must leak roughly
        the same amount — pre-fix, mask 1 blacked out 75% (only the
        right quarter visible) which was both inconsistent with the
        other four and visually dominated the carousel.

        Tightened from the prior ``<= 75%`` tolerance specifically so
        an asymmetric mask can't sneak back in. If a future change
        introduces a wider mask, this test fails loudly.
        """
        regions = _mask_regions(width=200, height=100)
        whole_area = 200 * 100
        for i, (left, top, right, bottom) in enumerate(regions):
            area = (right - left) * (bottom - top)
            assert area <= whole_area // 2, (
                f"mask {i} blacks out {area} px² of a {whole_area} px² "
                f"page ({100 * area // whole_area}%) — design contract "
                "is ≤ 50% per mask"
            )

    def test_left_and_right_half_masks_are_mirror_images(self) -> None:
        """Mask 0 (right half hidden) and mask 1 (left half hidden) must
        produce mirror-image fragments — same area, complementary
        coverage. Pre-fix, mask 1 used ``three_quarter_w`` which made
        the right-half fragment leak only 25% instead of 50%.
        """
        regions = _mask_regions(width=200, height=100)
        left_half_mask = regions[0]
        right_half_mask = regions[1]

        left_area = (left_half_mask[2] - left_half_mask[0]) * (
            left_half_mask[3] - left_half_mask[1]
        )
        right_area = (right_half_mask[2] - right_half_mask[0]) * (
            right_half_mask[3] - right_half_mask[1]
        )
        assert left_area == right_area, (
            f"left-half mask ({left_area}) and right-half mask ({right_area}) must have equal area"
        )

        # Together they should partition the page horizontally with
        # no overlap and no gap.
        assert left_half_mask == (100, 0, 200, 100)  # right half blacked
        assert right_half_mask == (0, 0, 100, 100)  # left half blacked

    def test_top_and_bottom_half_masks_are_mirror_images(self) -> None:
        """Sibling assertion to the horizontal-half test: vertical
        siblings (top half hidden vs bottom half hidden) are also
        mirror-image. Catches a regression where someone "fixes" the
        horizontal pair but breaks the vertical one.
        """
        regions = _mask_regions(width=200, height=100)
        top_blacked = regions[3]
        bottom_blacked = regions[4]

        top_area = (top_blacked[2] - top_blacked[0]) * (top_blacked[3] - top_blacked[1])
        bottom_area = (bottom_blacked[2] - bottom_blacked[0]) * (
            bottom_blacked[3] - bottom_blacked[1]
        )
        assert top_area == bottom_area


class TestFragmentDocument:
    def test_single_image_produces_five_fragments(self) -> None:
        png = _make_png()
        result = fragment_document(png)
        assert len(result) == 1  # one page
        assert len(result[0]) == AWAITVERIFY_FRAGMENT_COUNT

    def test_fragments_are_png_bytes(self) -> None:
        png = _make_png()
        result = fragment_document(png)
        for fragment_bytes in result[0]:
            # PNG magic bytes are 89 50 4E 47
            assert fragment_bytes[:4] == b"\x89PNG"

    def test_fragments_differ_from_original(self) -> None:
        png = _make_png(color="red")
        result = fragment_document(png)
        # Each fragment should NOT equal the source — at minimum a
        # black rectangle has been pasted over part of the image.
        for fragment_bytes in result[0]:
            assert fragment_bytes != png

    def test_each_fragment_contains_black_region(self) -> None:
        """The five masks each paint a different black rectangle."""
        png = _make_png(color="white")
        result = fragment_document(png)
        for fragment_bytes in result[0]:
            img = Image.open(io.BytesIO(fragment_bytes))
            # An originally-white page has black pixels after masking
            colors = img.getcolors(maxcolors=10_000) or []
            has_black = any(count > 0 and rgb == (0, 0, 0) for count, rgb in colors)
            assert has_black, "fragment did not contain a masked (black) region"


class TestLoadPages:
    def test_rejects_unsupported_format(self) -> None:
        with pytest.raises(VerifyDocumentLoadError):
            load_pages(b"not an image and not a pdf")

    def test_accepts_png_bytes(self) -> None:
        png = _make_png()
        pages = load_pages(png)
        assert len(pages) == 1

    def test_too_many_pages_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Confirm the page cap is enforced.

        We monkeypatch the loader to return a fake oversized PDF
        result rather than actually constructing a 20-page document —
        the cap logic is the only thing under test.
        """
        png = _make_png()
        too_many = [Image.open(io.BytesIO(png)) for _ in range(AWAITVERIFY_MAX_PAGES + 1)]

        def _fake_convert_from_bytes(_raw: bytes, **_kwargs: object) -> list[Image.Image]:
            return too_many

        def _fake_looks_like_pdf(_raw: bytes) -> bool:
            return True

        def _fake_require_pdf2image() -> object:
            return _fake_convert_from_bytes

        from awaithumans.awaitverify import fragmentation as frag_mod

        monkeypatch.setattr(frag_mod, "_looks_like_pdf", _fake_looks_like_pdf)
        monkeypatch.setattr(frag_mod, "_require_pdf2image", _fake_require_pdf2image)

        with pytest.raises(VerifyDocumentTooLargeError):
            load_pages(b"%PDF-1.4 fake")
