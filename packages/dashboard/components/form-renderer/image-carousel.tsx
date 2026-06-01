"use client";

/**
 * ImageCarousel — single-image-at-a-time viewer with prev/next, a
 * "k / N" counter, keyboard arrow navigation, a fit/actual zoom
 * toggle, and a full-screen lightbox. Rendered by the FormRenderer
 * when a form has 2+ top-level image fields (typical AwaitVerify
 * review surface: N document fragments).
 *
 * Page-grouped mode (AwaitVerify M5)
 * ----------------------------------
 * When the image fields carry per-page metadata (``page_index``,
 * ``fragment_in_page``, ``page_count``, ``fragments_per_page``), the
 * carousel renders a two-level navigation:
 *
 *   - Top: a page selector — tab strip for ≤ 8 pages, numeric input
 *     + arrows otherwise.
 *   - Inner: today's prev/next carousel scoped to one page's
 *     fragments. Counter changes to "view K of 5" (this page)
 *     rather than "K / 25" (all fragments).
 *
 * Internally we still track a single flat index across all fragments
 * — that lets ← / → flow across page boundaries naturally, and lets
 * us swap the entire flat list to the lightbox without translating
 * coordinates. The active page is derived from the current
 * fragment's ``page_index``.
 *
 * Reviewers work on laptops — we don't optimize for mobile, but the
 * ``<img>`` container is overflow-auto in actual-size mode so native
 * touchpad / pinch-zoom gestures work on the natural-size view
 * without us reimplementing them.
 *
 * Keyboard
 * --------
 * ← / →           navigate fragments. Wraps across page boundaries:
 *                 → on the last fragment of page N advances to
 *                 fragment 1 of page N+1. Suppressed when an INPUT /
 *                 TEXTAREA / contentEditable has focus.
 * PageDown / PageUp  jump to fragment 1 of the next/previous page.
 *                    Faster than tapping → repeatedly for
 *                    multi-page documents.
 * Esc             reset zoom to fit (when lightbox is closed).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Eyebrow } from "@/components/eyebrow";
import type { ImageField } from "@/lib/form-types";
import {
	derivePageLayout,
	flattenLayout,
	pageStartIndices,
	type PagedLayout,
} from "./image-grouping";
import { ImageLightbox } from "./image-lightbox";
import { ProtectedImage } from "./protected-image";

type Zoom = "fit" | "actual";

// Threshold from the brief: tab strip up to 8 pages, numeric
// input + arrows beyond that. 9+ tabs gets too tight visually on
// laptop-width screens.
const PAGE_TAB_THRESHOLD = 8;

export function ImageCarousel({ images }: { images: ImageField[] }) {
	const layout = useMemo(() => derivePageLayout(images), [images]);
	const flatImages = useMemo(() => flattenLayout(layout), [layout]);
	const pageStarts = useMemo(() => pageStartIndices(layout), [layout]);

	const [index, setIndex] = useState(0);
	const [zoom, setZoom] = useState<Zoom>("fit");
	const [lightboxOpen, setLightboxOpen] = useState(false);
	const containerRef = useRef<HTMLDivElement>(null);

	const count = flatImages.length;
	const current = flatImages[index];

	const goPrev = useCallback(
		() => setIndex((i) => (i > 0 ? i - 1 : i)),
		[],
	);
	const goNext = useCallback(
		() => setIndex((i) => (i < count - 1 ? i + 1 : i)),
		[count],
	);

	// Page-level jumps. No-op when the layout is flat (pageStarts ==
	// [0] and the bounds check below evaluates to "stay at 0").
	const goPrevPage = useCallback(() => {
		setIndex((cur) => {
			// Find the page index containing the current flat index.
			// pageStarts is monotonically increasing, so this is a
			// straight linear scan.
			let p = 0;
			for (let i = 0; i < pageStarts.length; i++) {
				if (pageStarts[i] <= cur) p = i;
			}
			return pageStarts[Math.max(0, p - 1)] ?? cur;
		});
	}, [pageStarts]);
	const goNextPage = useCallback(() => {
		setIndex((cur) => {
			let p = 0;
			for (let i = 0; i < pageStarts.length; i++) {
				if (pageStarts[i] <= cur) p = i;
			}
			return pageStarts[Math.min(pageStarts.length - 1, p + 1)] ?? cur;
		});
	}, [pageStarts]);
	const setActivePage = useCallback(
		(p: number) => {
			const clamped = Math.max(0, Math.min(pageStarts.length - 1, p));
			setIndex(pageStarts[clamped] ?? 0);
		},
		[pageStarts],
	);

	// Keyboard navigation. Global listener, but skip when a text input
	// has focus — the reviewer typing into the form's response fields
	// should not shuffle pages. Reset zoom on Esc. While the lightbox
	// is open, the lightbox owns keyboard handling — bail out here so
	// we don't dispatch twice.
	useEffect(() => {
		if (lightboxOpen) return;
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
			} else if (e.key === "PageDown") {
				e.preventDefault();
				goNextPage();
			} else if (e.key === "PageUp") {
				e.preventDefault();
				goPrevPage();
			} else if (e.key === "Escape") {
				setZoom("fit");
			}
		}
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [goPrev, goNext, goPrevPage, goNextPage, lightboxOpen]);

	// Reset zoom when the slide changes — operators expect a fresh
	// view per page; carrying "actual" across slides leaves the next
	// image scrolled to a meaningless position.
	useEffect(() => {
		setZoom("fit");
	}, []);

	// Page-derived values for the counter / page selector / etc.
	const activePage = current?.page_index ?? 0;
	const fragmentInPage = current?.fragment_in_page ?? index;
	const isPaged = layout.kind === "paged";

	return (
		<div
			ref={containerRef}
			className="space-y-2"
			data-testid="image-carousel"
		>
			{/* Page selector — only when multi-page. */}
			{isPaged && (
				<PageSelector
					layout={layout}
					activePage={activePage}
					onChange={setActivePage}
				/>
			)}

			{current?.label && (
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
				{current && (
					<ProtectedImage
						key={current.url}
						src={current.url}
						alt={
							current.alt ??
							current.label ??
							`Image ${index + 1} of ${count}`
						}
						className={
							zoom === "fit"
								? "max-w-full max-h-[640px] object-contain"
								: "block"
						}
					/>
				)}
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
						{isPaged
							? `view ${fragmentInPage + 1} of ${
									layout.pages[activePage]?.length ??
									layout.fragmentsPerPage
								}`
							: `${index + 1} / ${count}`}
					</span>
				</div>

				<div className="flex items-center gap-1">
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
					<button
						type="button"
						onClick={() => setLightboxOpen(true)}
						className="px-3 py-1 rounded-md border border-white/10 text-white/70 hover:text-white hover:border-white/30"
						aria-label="Open full screen"
					>
						⛶ Full screen
					</button>
				</div>
			</div>

			{lightboxOpen && (
				<ImageLightbox
					images={flatImages}
					index={index}
					onClose={() => setLightboxOpen(false)}
					onIndexChange={setIndex}
				/>
			)}
		</div>
	);
}

// ── Page selector ──────────────────────────────────────────────────
//
// Two flavors, switched on ``pageCount``:
//   ≤ 8 pages: tab strip `[1] [2] [3] ...`
//   > 8 pages: `< Page N of M >` with a numeric input + arrows
//
// Threshold is set so the tab strip stays single-line on a typical
// laptop width without horizontal scrolling. Numeric input wins
// when there are too many tabs to fit comfortably.

function PageSelector({
	layout,
	activePage,
	onChange,
}: {
	layout: PagedLayout;
	activePage: number;
	onChange: (next: number) => void;
}) {
	if (layout.pageCount <= PAGE_TAB_THRESHOLD) {
		return (
			<div
				className="flex items-center gap-1 flex-wrap"
				role="tablist"
				aria-label="Document pages"
				data-testid="page-selector-tabs"
			>
				<span className="text-xs text-white/40 mr-1">Pages:</span>
				{Array.from({ length: layout.pageCount }).map((_, i) => {
					const isActive = i === activePage;
					return (
						<button
							key={i}
							type="button"
							onClick={() => onChange(i)}
							role="tab"
							aria-selected={isActive}
							className={
								isActive
									? "px-3 py-1 text-sm rounded-md bg-brand/20 border border-brand text-brand"
									: "px-3 py-1 text-sm rounded-md border border-white/10 text-white/60 hover:text-white hover:border-white/30"
							}
						>
							{i + 1}
						</button>
					);
				})}
			</div>
		);
	}
	return (
		<NumericPageInput
			pageCount={layout.pageCount}
			activePage={activePage}
			onChange={onChange}
		/>
	);
}

function NumericPageInput({
	pageCount,
	activePage,
	onChange,
}: {
	pageCount: number;
	activePage: number;
	onChange: (next: number) => void;
}) {
	// Local input value so the reviewer can type while the formatted
	// "Page N of M" caption stays accurate. Commit on blur or Enter
	// — typing in real-time would re-render and reset focus on every
	// keystroke.
	const [draft, setDraft] = useState<string>(String(activePage + 1));

	// Keep the draft in sync when the active page changes from
	// elsewhere (← / → / PageUp / PageDown crossed a boundary).
	useEffect(() => {
		setDraft(String(activePage + 1));
	}, [activePage]);

	const commit = () => {
		const n = Number.parseInt(draft, 10);
		if (Number.isFinite(n) && n >= 1 && n <= pageCount) {
			onChange(n - 1);
		} else {
			// Reset to current — operator typed something invalid.
			setDraft(String(activePage + 1));
		}
	};

	return (
		<div
			className="flex items-center gap-2 text-sm"
			data-testid="page-selector-input"
		>
			<button
				type="button"
				onClick={() => onChange(Math.max(0, activePage - 1))}
				disabled={activePage === 0}
				className="px-2 py-1 rounded-md border border-white/10 text-white/70 hover:text-white hover:border-white/30 disabled:opacity-30 disabled:cursor-not-allowed"
				aria-label="Previous page"
			>
				◀
			</button>
			<span className="text-white/60">Page</span>
			<input
				type="text"
				inputMode="numeric"
				value={draft}
				onChange={(e) => setDraft(e.target.value)}
				onBlur={commit}
				onKeyDown={(e) => {
					if (e.key === "Enter") {
						e.preventDefault();
						commit();
						(e.target as HTMLInputElement).blur();
					}
				}}
				aria-label="Page number"
				className="w-12 px-2 py-1 text-center rounded-md bg-white/5 border border-white/10 text-white tabular-nums focus:outline-none focus:border-brand/40"
			/>
			<span className="text-white/60">of {pageCount}</span>
			<button
				type="button"
				onClick={() =>
					onChange(Math.min(pageCount - 1, activePage + 1))
				}
				disabled={activePage === pageCount - 1}
				className="px-2 py-1 rounded-md border border-white/10 text-white/70 hover:text-white hover:border-white/30 disabled:opacity-30 disabled:cursor-not-allowed"
				aria-label="Next page"
			>
				▶
			</button>
		</div>
	);
}
