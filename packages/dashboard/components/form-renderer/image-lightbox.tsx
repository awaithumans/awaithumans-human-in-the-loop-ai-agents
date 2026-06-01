"use client";

/**
 * Full-screen lightbox for the document-fragment carousel.
 *
 * Opens via the carousel's "Full screen" button. Renders the active
 * image at object-contain with a black letterbox so the reviewer can
 * read fine print (e.g. a handwritten T12C3 vs 712C3) without the
 * carousel's max-height squeezing it.
 *
 * Interactions:
 *   ← / →   navigate between fragments. Suppressed when an INPUT /
 *           TEXTAREA / contentEditable has focus, mirroring the
 *           carousel's policy so a stray keystroke can't shuffle
 *           pages while the reviewer is typing a response.
 *   Esc     closes back to inline view.
 *   Click letterbox (not the image) → also closes.
 *
 * Rendered into a top-level fixed-position div (no React portal —
 * z-50 + fixed inset-0 is sufficient and avoids an extra import).
 * The image inside is a `ProtectedImage` so right-click and drag
 * are blocked even in full-screen.
 */

import { useCallback, useEffect } from "react";
import type { ImageField } from "@/lib/form-types";
import { ProtectedImage } from "./protected-image";

export function ImageLightbox({
	images,
	index,
	onClose,
	onIndexChange,
}: {
	images: ImageField[];
	index: number;
	onClose: () => void;
	onIndexChange: (next: number) => void;
}) {
	const count = images.length;
	const current = images[index];

	const goPrev = useCallback(
		() => onIndexChange(Math.max(0, index - 1)),
		[index, onIndexChange],
	);
	const goNext = useCallback(
		() => onIndexChange(Math.min(count - 1, index + 1)),
		[count, index, onIndexChange],
	);

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
			if (e.key === "Escape") {
				e.preventDefault();
				onClose();
			} else if (e.key === "ArrowLeft") {
				e.preventDefault();
				goPrev();
			} else if (e.key === "ArrowRight") {
				e.preventDefault();
				goNext();
			}
		}
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [onClose, goPrev, goNext]);

	return (
		<div
			role="dialog"
			aria-modal="true"
			aria-label="Image full-screen view"
			onClick={onClose}
			onKeyDown={(e) => {
				// Letterbox is a div, not a button — keep clicks on it
				// closing the dialog, but ignore key events here (the
				// window listener owns keyboard navigation).
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					onClose();
				}
			}}
			className="fixed inset-0 z-50 bg-black/95 flex items-center justify-center"
			data-testid="image-lightbox"
		>
			<ProtectedImage
				key={current.url}
				src={current.url}
				alt={current.alt ?? current.label ?? `Image ${index + 1} of ${count}`}
				onClick={(e) => {
					// Clicks on the image itself don't close — only
					// clicks on the letterbox around it.
					e.stopPropagation();
				}}
				className="max-w-[95vw] max-h-[95vh] object-contain"
			/>

			<button
				type="button"
				onClick={(e) => {
					e.stopPropagation();
					onClose();
				}}
				aria-label="Close full-screen view"
				className="absolute top-4 right-4 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white text-lg flex items-center justify-center"
			>
				✕
			</button>

			{count > 1 && (
				<div
					className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-3 bg-white/10 backdrop-blur-sm rounded-full px-4 py-2"
					onClick={(e) => e.stopPropagation()}
					onKeyDown={(e) => e.stopPropagation()}
				>
					<button
						type="button"
						onClick={goPrev}
						disabled={index === 0}
						aria-label="Previous image"
						className="text-white hover:text-white/80 disabled:opacity-30 disabled:cursor-not-allowed px-2"
					>
						◀
					</button>
					<span
						className="text-white/80 text-sm tabular-nums"
						aria-live="polite"
					>
						{index + 1} / {count}
					</span>
					<button
						type="button"
						onClick={goNext}
						disabled={index === count - 1}
						aria-label="Next image"
						className="text-white hover:text-white/80 disabled:opacity-30 disabled:cursor-not-allowed px-2"
					>
						▶
					</button>
				</div>
			)}
		</div>
	);
}
