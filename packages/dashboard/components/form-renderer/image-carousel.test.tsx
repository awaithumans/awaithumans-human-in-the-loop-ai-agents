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
