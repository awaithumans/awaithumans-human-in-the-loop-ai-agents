"use client";

/**
 * <img> wrapper with casual-exfiltration friction.
 *
 * Friction added (defense-in-depth, not a security guarantee — devtools
 * can always pull bytes from the network tab):
 *   - onContextMenu prevented → no right-click → Save Image
 *   - draggable={false} → no drag-to-desktop / drag-to-tab
 *   - WebkitUserDrag: none → same on older Safari (CSS extension)
 *   - select-none class → blocks ⌘+A / shift-click → copy on the
 *     rendered image
 *
 * Intentional non-goals:
 *   - We do NOT block middle-click "open in new tab" via a
 *     pointer-events overlay. The brief calls this out as a step
 *     too far — it also blocks any onClick handlers we'd want on
 *     the image (e.g. open the lightbox), and a determined user
 *     already has devtools as an out anyway.
 *   - We do NOT hide the image bytes from devtools / network tab.
 *     That's a property of any browser-rendered <img>; reviewing
 *     fragments in a browser means the fragment bytes hit the
 *     client. The point of the wrapper is to stop casual
 *     exfiltration (right-click, drag), not to deter someone with
 *     a network panel open.
 */

import type { ImgHTMLAttributes, MouseEvent } from "react";

export function ProtectedImage(props: ImgHTMLAttributes<HTMLImageElement>) {
	const { onContextMenu, style, className, ...rest } = props;

	const handleContextMenu = (e: MouseEvent<HTMLImageElement>) => {
		e.preventDefault();
		onContextMenu?.(e);
	};

	const mergedClassName = `${className ?? ""} select-none`.trim();

	// `WebkitUserDrag` is a Safari extension to CSS. React's CSSProperties
	// type doesn't list it; cast through `Record<string, string>` so the
	// rule reaches the rendered element without a `as unknown as never`
	// dance. The standard `draggable={false}` covers every Chromium and
	// Firefox build we care about; this is the older-Safari tail.
	const mergedStyle = {
		...(style ?? {}),
		...({ WebkitUserDrag: "none" } as Record<string, string>),
	};

	return (
		/* eslint-disable-next-line @next/next/no-img-element */
		<img
			{...rest}
			className={mergedClassName}
			draggable={false}
			onContextMenu={handleContextMenu}
			style={mergedStyle}
		/>
	);
}
