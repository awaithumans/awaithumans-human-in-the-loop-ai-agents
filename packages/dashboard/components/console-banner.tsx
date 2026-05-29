"use client";

import { useEffect } from "react";

/**
 * Dev-console easter egg — a tiny hello for anyone opening the inspector.
 *
 * Classic Stripe/GitHub move. Costs ~200 bytes gzipped, runs once per
 * page load (module-scoped guard so SPA nav doesn't repeat it).
 *
 * Lives in its own file so the root layout stays declarative.
 */

let printed = false;

export function ConsoleBanner() {
	useEffect(() => {
		if (printed || typeof window === "undefined") return;
		printed = true;

		const brand = "color:#00E676;font-weight:600;";
		const body = "color:#9CA3AF;";
		const mono =
			"color:#F5F5F5;font-family:'JetBrains Mono',monospace;";

		// Two-line greeting: the primitive the product ships, then an
		// invitation. Short, specific, not generic AI filler.
		// eslint-disable-next-line no-console
		console.log(
			"%c$ await_human()%c\n%c# the human layer for AI agents\n# source: github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents",
			mono,
			"",
			body,
		);
		// Separate call so the labels render on their own line in Safari.
		// eslint-disable-next-line no-console
		console.log("%cawait humans%c like you await promises.", brand, body);
	}, []);

	return null;
}
