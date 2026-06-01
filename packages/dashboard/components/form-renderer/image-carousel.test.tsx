/**
 * Component-level test for ImageCarousel + ImageLightbox.
 *
 * The pure-function grouping logic is covered in image-grouping.test.ts;
 * this file pins the DOM-level surface the brief calls out:
 *
 *   1. A "Full screen" button exists when the carousel mounts with 2+
 *      images, and clicking it opens a lightbox role="dialog".
 *   2. Every rendered fragment <img> has `draggable="false"` and an
 *      onContextMenu handler that calls preventDefault.
 *
 * Uses @testing-library/react + happy-dom (configured in
 * vitest.config.ts). First component test in the dashboard — future
 * component tests can follow the same pattern.
 */

import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImageField } from "@/lib/form-types";

import { ImageCarousel } from "./image-carousel";

afterEach(() => {
	cleanup();
});

function imageField(name: string, url: string): ImageField {
	return {
		kind: "image",
		name,
		label: name,
		hint: null,
		required: false,
		url,
		alt: null,
		width: null,
		height: null,
	};
}

const TWO_IMAGES: ImageField[] = [
	imageField("page_0", "https://example.com/0.png"),
	imageField("page_1", "https://example.com/1.png"),
];

describe("ImageCarousel — lightbox button", () => {
	it("renders a 'Full screen' button when the carousel mounts", () => {
		render(<ImageCarousel images={TWO_IMAGES} />);
		// Matches the brief's `getByRole("button", { name: /full[- ]?screen/i })`
		// — the actual aria-label is "Open full screen".
		const btn = screen.getByRole("button", { name: /full[- ]?screen/i });
		expect(btn).toBeTruthy();
	});

	it("opens a role='dialog' lightbox when the Full screen button is clicked", () => {
		render(<ImageCarousel images={TWO_IMAGES} />);
		const btn = screen.getByRole("button", { name: /full[- ]?screen/i });

		// Before click — no dialog.
		expect(screen.queryByRole("dialog")).toBeNull();

		fireEvent.click(btn);

		// After click — a dialog overlays the inline view.
		const dialog = screen.getByRole("dialog");
		expect(dialog).toBeTruthy();
		expect(dialog.getAttribute("aria-modal")).toBe("true");
	});

	it("closes the lightbox when Escape is pressed", () => {
		render(<ImageCarousel images={TWO_IMAGES} />);
		fireEvent.click(screen.getByRole("button", { name: /full[- ]?screen/i }));
		expect(screen.queryByRole("dialog")).not.toBeNull();

		fireEvent.keyDown(window, { key: "Escape" });

		expect(screen.queryByRole("dialog")).toBeNull();
	});

	it("closes the lightbox when the letterbox is clicked", () => {
		render(<ImageCarousel images={TWO_IMAGES} />);
		fireEvent.click(screen.getByRole("button", { name: /full[- ]?screen/i }));
		const dialog = screen.getByRole("dialog");

		// Click the dialog itself (the letterbox) — should close.
		fireEvent.click(dialog);
		expect(screen.queryByRole("dialog")).toBeNull();
	});
});

describe("ImageCarousel — image protection", () => {
	it("renders all <img> elements with draggable='false'", () => {
		const { container } = render(<ImageCarousel images={TWO_IMAGES} />);
		const imgs = container.querySelectorAll("img");
		expect(imgs.length).toBeGreaterThan(0);
		for (const img of imgs) {
			expect(img.getAttribute("draggable")).toBe("false");
		}
	});

	it("blocks the context menu on the fragment image (right-click → Save Image)", () => {
		const { container } = render(<ImageCarousel images={TWO_IMAGES} />);
		const img = container.querySelector("img");
		expect(img).not.toBeNull();
		if (!img) throw new Error("expected at least one img");

		// fireEvent.contextMenu doesn't directly observe preventDefault,
		// but we can dispatch an event and assert defaultPrevented after.
		const event = new MouseEvent("contextmenu", {
			bubbles: true,
			cancelable: true,
		});
		const dispatched = img.dispatchEvent(event);
		// dispatchEvent returns false when preventDefault was called.
		expect(dispatched).toBe(false);
		expect(event.defaultPrevented).toBe(true);
	});

	it("protects the lightbox image too", () => {
		render(<ImageCarousel images={TWO_IMAGES} />);
		fireEvent.click(screen.getByRole("button", { name: /full[- ]?screen/i }));

		const dialog = screen.getByRole("dialog");
		const lightboxImg = dialog.querySelector("img");
		expect(lightboxImg).not.toBeNull();
		if (!lightboxImg) throw new Error("expected lightbox to contain an img");

		expect(lightboxImg.getAttribute("draggable")).toBe("false");

		const event = new MouseEvent("contextmenu", {
			bubbles: true,
			cancelable: true,
		});
		lightboxImg.dispatchEvent(event);
		expect(event.defaultPrevented).toBe(true);
	});
});

describe("ImageCarousel — navigation in the lightbox", () => {
	it("arrow keys navigate between fragments while the lightbox is open", () => {
		render(<ImageCarousel images={TWO_IMAGES} />);
		fireEvent.click(screen.getByRole("button", { name: /full[- ]?screen/i }));

		// Counter starts at "1 / 2" inside the lightbox.
		const dialog = screen.getByRole("dialog");
		expect(dialog.textContent).toContain("1 / 2");

		fireEvent.keyDown(window, { key: "ArrowRight" });
		expect(dialog.textContent).toContain("2 / 2");

		fireEvent.keyDown(window, { key: "ArrowLeft" });
		expect(dialog.textContent).toContain("1 / 2");
	});

	it("does not navigate when an INPUT element has focus", () => {
		// Mirrors the carousel's policy — typing into the response
		// form must not shuffle pages, even with the lightbox open.
		render(
			<>
				<input data-testid="response-input" />
				<ImageCarousel images={TWO_IMAGES} />
			</>,
		);
		fireEvent.click(screen.getByRole("button", { name: /full[- ]?screen/i }));
		const dialog = screen.getByRole("dialog");
		const input = screen.getByTestId("response-input") as HTMLInputElement;
		input.focus();

		fireEvent.keyDown(input, { key: "ArrowRight" });
		// Counter should NOT have advanced.
		expect(dialog.textContent).toContain("1 / 2");
	});
});

describe("ImageCarousel — no lightbox button when only one image", () => {
	it("only renders the Full screen button alongside the carousel chrome (always)", () => {
		// Edge case to pin: the carousel renders for 2+ images per
		// groupImageFields, so this component is never invoked with a
		// single image. The Full screen button is part of the carousel
		// chrome, so it appears whenever the carousel itself does.
		// This test guards against an accidental "hide button when
		// images.length === 1" tweak that would make the button
		// invisible in a hypothetical single-image carousel.
		render(<ImageCarousel images={TWO_IMAGES.slice(0, 1)} />);
		expect(
			screen.queryByRole("button", { name: /full[- ]?screen/i }),
		).not.toBeNull();
	});
});

// Silence happy-dom's noisy "Not implemented" warnings for unsupported
// CSS like `user-drag` while testing — they don't represent a
// correctness issue, just happy-dom's strict-mode chatter.
vi.spyOn(console, "warn").mockImplementation(() => {});

// ── Multi-page (AwaitVerify M5) ────────────────────────────────────

function pagedImage(
	name: string,
	url: string,
	page: number,
	frag: number,
	pageCount: number,
	fragmentsPerPage = 5,
): ImageField {
	return {
		...imageField(name, url),
		page_index: page,
		fragment_in_page: frag,
		page_count: pageCount,
		fragments_per_page: fragmentsPerPage,
	};
}

function threePagesByFive(): ImageField[] {
	const out: ImageField[] = [];
	for (let p = 0; p < 3; p++) {
		for (let f = 0; f < 5; f++) {
			out.push(pagedImage(`p${p}f${f}`, `http://x/${p}-${f}.png`, p, f, 3, 5));
		}
	}
	return out;
}

describe("ImageCarousel — page selector (multi-page mode)", () => {
	it("renders a tab strip when 2 ≤ pageCount ≤ 8", () => {
		// The brief calls this the "compact tab strip" — preferred
		// over a numeric input when the page count fits on one row.
		render(<ImageCarousel images={threePagesByFive()} />);
		const tabs = screen.getByTestId("page-selector-tabs");
		expect(tabs).toBeTruthy();
		// Three tabs labeled 1, 2, 3.
		expect(tabs.textContent).toContain("1");
		expect(tabs.textContent).toContain("2");
		expect(tabs.textContent).toContain("3");
	});

	it("renders the numeric input variant when pageCount > 8", () => {
		// Threshold is 8 — past that the tab strip would wrap or
		// shrink uncomfortably on laptop widths.
		const many: ImageField[] = [];
		for (let p = 0; p < 12; p++) {
			for (let f = 0; f < 5; f++) {
				many.push(
					pagedImage(`p${p}f${f}`, `http://x/${p}-${f}.png`, p, f, 12, 5),
				);
			}
		}
		render(<ImageCarousel images={many} />);
		expect(screen.getByTestId("page-selector-input")).toBeTruthy();
		expect(screen.queryByTestId("page-selector-tabs")).toBeNull();
	});

	it("does NOT render the page selector for single-page tasks", () => {
		// Backward compat: a task with no page metadata renders
		// today's flat carousel — no page chrome.
		render(<ImageCarousel images={TWO_IMAGES} />);
		expect(screen.queryByTestId("page-selector-tabs")).toBeNull();
		expect(screen.queryByTestId("page-selector-input")).toBeNull();
	});

	it("clicking a page tab jumps to that page's first fragment", () => {
		// Test from the brief: "Clicking tab [2] shows fragment with
		// page_index === 1, fragment_in_page === 0 first."
		const { container } = render(
			<ImageCarousel images={threePagesByFive()} />,
		);

		// Tab 2 (1-indexed) maps to page_index 1 (0-indexed).
		const tabs = screen.getByTestId("page-selector-tabs");
		const tab2 = tabs.querySelector("button:nth-of-type(2)")!;
		fireEvent.click(tab2);

		const img = container.querySelector("img");
		expect(img).not.toBeNull();
		expect(img?.getAttribute("src")).toBe("http://x/1-0.png");
	});
});

describe("ImageCarousel — keyboard nav across pages", () => {
	it("→ on the last fragment of page 1 advances to page 2 fragment 1", () => {
		// "→ on the last fragment of page N advances to fragment 1
		// of the next page" — the cross-page flow that makes flat
		// scrolling still possible.
		const { container } = render(
			<ImageCarousel images={threePagesByFive()} />,
		);

		// Navigate to the last fragment of page 1: tap → four times.
		for (let i = 0; i < 4; i++) {
			fireEvent.keyDown(window, { key: "ArrowRight" });
		}
		let img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/0-4.png");

		// One more → must jump to page 2 fragment 1 (page_index=1, frag=0).
		fireEvent.keyDown(window, { key: "ArrowRight" });
		img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/1-0.png");
	});

	it("← on the first fragment of page 2 returns to last fragment of page 1", () => {
		// Symmetric: backwards traversal also crosses boundaries.
		const { container } = render(
			<ImageCarousel images={threePagesByFive()} />,
		);

		// Jump to page 2 via tab.
		const tabs = screen.getByTestId("page-selector-tabs");
		fireEvent.click(tabs.querySelector("button:nth-of-type(2)")!);
		let img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/1-0.png");

		// ← must go back to page 1's last fragment.
		fireEvent.keyDown(window, { key: "ArrowLeft" });
		img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/0-4.png");
	});

	it("PageDown jumps to fragment 1 of the next page", () => {
		// Faster than 5 × → for the reviewer who knows they want
		// the next page, not the next fragment.
		const { container } = render(
			<ImageCarousel images={threePagesByFive()} />,
		);

		fireEvent.keyDown(window, { key: "PageDown" });
		let img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/1-0.png");

		fireEvent.keyDown(window, { key: "PageDown" });
		img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/2-0.png");
	});

	it("PageUp jumps to fragment 1 of the previous page", () => {
		const { container } = render(
			<ImageCarousel images={threePagesByFive()} />,
		);

		// Start on page 3 via the tab.
		const tabs = screen.getByTestId("page-selector-tabs");
		fireEvent.click(tabs.querySelector("button:nth-of-type(3)")!);
		let img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/2-0.png");

		fireEvent.keyDown(window, { key: "PageUp" });
		img = container.querySelector("img");
		expect(img?.getAttribute("src")).toBe("http://x/1-0.png");
	});

	it("active page tab visually flips when navigation crosses pages", () => {
		// The page selector's "active" highlight must follow the
		// current fragment — without this the reviewer would see
		// "I'm on tab [1]" while looking at page 2's content.
		render(<ImageCarousel images={threePagesByFive()} />);

		const tabs = screen.getByTestId("page-selector-tabs");
		const tabButtons = tabs.querySelectorAll("button");
		// Initial: tab 1 is selected.
		expect(tabButtons[0].getAttribute("aria-selected")).toBe("true");
		expect(tabButtons[1].getAttribute("aria-selected")).toBe("false");

		// PageDown → page 2.
		fireEvent.keyDown(window, { key: "PageDown" });

		// Tab 2 should now be selected.
		const updatedTabs = screen
			.getByTestId("page-selector-tabs")
			.querySelectorAll("button");
		expect(updatedTabs[0].getAttribute("aria-selected")).toBe("false");
		expect(updatedTabs[1].getAttribute("aria-selected")).toBe("true");
	});

	it("counter shows 'view K of 5' for multi-page tasks (not the flat K/N)", () => {
		// Per the brief: the inner counter is per-page, not global.
		// Reviewer expectation: "I'm on view 3 of 5 of this page"
		// — far more useful than "I'm on 8 of 25" for grok.
		render(<ImageCarousel images={threePagesByFive()} />);

		expect(screen.getByText("view 1 of 5")).toBeTruthy();

		// One → → "view 2 of 5".
		fireEvent.keyDown(window, { key: "ArrowRight" });
		expect(screen.getByText("view 2 of 5")).toBeTruthy();
	});
});

describe("ImageLightbox — page header (multi-page mode)", () => {
	it("shows 'Page N of M, view K of 5' when the current image carries metadata", () => {
		// Brief: when active, the lightbox header should show
		// "Page N of M, view K of 5". The lightbox is mounted on
		// click via the Full screen button.
		render(<ImageCarousel images={threePagesByFive()} />);

		// Navigate to page 2 fragment 3 so the header isn't trivially
		// "Page 1 of 3, view 1 of 5".
		const tabs = screen.getByTestId("page-selector-tabs");
		fireEvent.click(tabs.querySelector("button:nth-of-type(2)")!);
		fireEvent.keyDown(window, { key: "ArrowRight" });
		fireEvent.keyDown(window, { key: "ArrowRight" });

		fireEvent.click(
			screen.getByRole("button", { name: /full[- ]?screen/i }),
		);

		const header = screen.getByTestId("lightbox-page-header");
		expect(header.textContent).toContain("Page 2 of 3");
		expect(header.textContent).toContain("view 3 of 5");
	});

	it("does NOT show the lightbox page header for legacy single-page tasks", () => {
		// Backward compat: image fields without page metadata don't
		// produce a header — the bottom "K / N" counter is sufficient.
		render(<ImageCarousel images={TWO_IMAGES} />);
		fireEvent.click(
			screen.getByRole("button", { name: /full[- ]?screen/i }),
		);
		expect(screen.queryByTestId("lightbox-page-header")).toBeNull();
	});
});
