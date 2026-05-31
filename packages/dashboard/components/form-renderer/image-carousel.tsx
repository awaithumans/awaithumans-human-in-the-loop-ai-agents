"use client";

/**
 * ImageCarousel — single-image-at-a-time viewer with prev/next, a
 * "k / N" counter, keyboard arrow navigation, and a fit/actual zoom
 * toggle. Rendered by the FormRenderer when a form has 2+ top-level
 * image fields (typical AwaitVerify review surface: N document
 * fragments).
 *
 * Reviewers work on laptops — we don't optimize for mobile, but the
 * `<img>` container is overflow-auto in actual-size mode so native
 * touchpad / pinch-zoom gestures work on the natural-size view
 * without us reimplementing them.
 *
 * Keyboard:
 *   ← / →   navigate. Suppressed when an INPUT / TEXTAREA /
 *           contentEditable element is focused, so typing into the
 *           reviewer's response form doesn't shuffle pages.
 *   Esc     reset zoom to fit.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Eyebrow } from "@/components/eyebrow";
import type { ImageField } from "@/lib/form-types";

type Zoom = "fit" | "actual";

export function ImageCarousel({ images }: { images: ImageField[] }) {
	const [index, setIndex] = useState(0);
	const [zoom, setZoom] = useState<Zoom>("fit");
	const containerRef = useRef<HTMLDivElement>(null);

	const count = images.length;
	const current = images[index];

	const goPrev = useCallback(
		() => setIndex((i) => (i > 0 ? i - 1 : i)),
		[],
	);
	const goNext = useCallback(
		() => setIndex((i) => (i < count - 1 ? i + 1 : i)),
		[count],
	);

	// Keyboard navigation. Global listener, but skip when a text input
	// has focus — the reviewer typing into the form's response fields
	// should not shuffle pages. Reset zoom on Esc.
	useEffect(() => {
		function onKey(e: KeyboardEvent) {
			const target = e.target as HTMLElement | null;
			if (
				target &&
				(target.tagName === "INPUT" ||
					target.tagName === "TEXTAREA" ||
					target.isContentEditable)
			) {
				return;
			}
			if (e.key === "ArrowLeft") {
				e.preventDefault();
				goPrev();
			} else if (e.key === "ArrowRight") {
				e.preventDefault();
				goNext();
			} else if (e.key === "Escape") {
				setZoom("fit");
			}
		}
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [goPrev, goNext]);

	// Reset zoom when the slide changes — operators expect a fresh
	// view per page; carrying "actual" across slides leaves the next
	// image scrolled to a meaningless position.
	useEffect(() => {
		setZoom("fit");
	}, []);

	return (
		<div
			ref={containerRef}
			className="space-y-2"
			data-testid="image-carousel"
		>
			{current.label && (
				<Eyebrow weight="semibold" className="block text-white/50">
					{current.label}
				</Eyebrow>
			)}

			<div
				className={
					zoom === "fit"
						? "rounded-md border border-white/10 bg-black/20 flex items-center justify-center min-h-[320px] max-h-[640px] overflow-hidden"
						: "rounded-md border border-white/10 bg-black/20 max-h-[640px] overflow-auto"
				}
			>
				{/* eslint-disable-next-line @next/next/no-img-element */}
				<img
					key={current.url}
					src={current.url}
					alt={current.alt ?? current.label ?? `Image ${index + 1} of ${count}`}
					className={
						zoom === "fit"
							? "max-w-full max-h-[640px] object-contain"
							: "block"
					}
				/>
			</div>

			<div className="flex items-center justify-between gap-2 text-sm">
				<div className="flex items-center gap-1">
					<button
						type="button"
						onClick={goPrev}
						disabled={index === 0}
						className="px-3 py-1 rounded-md border border-white/10 text-white/70 hover:text-white hover:border-white/30 disabled:opacity-30 disabled:cursor-not-allowed"
						aria-label="Previous image"
					>
						◀
					</button>
					<button
						type="button"
						onClick={goNext}
						disabled={index === count - 1}
						className="px-3 py-1 rounded-md border border-white/10 text-white/70 hover:text-white hover:border-white/30 disabled:opacity-30 disabled:cursor-not-allowed"
						aria-label="Next image"
					>
						▶
					</button>
					<span
						className="ml-2 tabular-nums text-white/50"
						aria-live="polite"
					>
						{index + 1} / {count}
					</span>
				</div>

				<button
					type="button"
					onClick={() =>
						setZoom((z) => (z === "fit" ? "actual" : "fit"))
					}
					className="px-3 py-1 rounded-md border border-white/10 text-white/70 hover:text-white hover:border-white/30"
					aria-label={
						zoom === "fit"
							? "Switch to actual size"
							: "Switch to fit"
					}
				>
					{zoom === "fit" ? "Actual size" : "Fit"}
				</button>
			</div>
		</div>
	);
}
