/**
 * Markdown — renders a Markdown string with the dashboard's dark brand
 * styling. Used for the task description/brief so reviewers can read
 * long prose (paragraphs, lists, headings, links, code) instead of a
 * single collapsed run of text.
 *
 * Safe by default: react-markdown v9 does not render raw HTML (no
 * rehype-raw is wired in) and sanitizes URL schemes, so a malicious
 * task description cannot inject markup or javascript: links.
 */

"use client";

import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
	h1: (props: ComponentPropsWithoutRef<"h1">) => (
		<h1 className="mt-4 mb-2 font-bold text-xl first:mt-0" {...props} />
	),
	h2: (props: ComponentPropsWithoutRef<"h2">) => (
		<h2 className="mt-4 mb-2 font-semibold text-lg first:mt-0" {...props} />
	),
	h3: (props: ComponentPropsWithoutRef<"h3">) => (
		<h3 className="mt-3 mb-1.5 font-semibold text-base first:mt-0" {...props} />
	),
	p: (props: ComponentPropsWithoutRef<"p">) => (
		<p className="mb-3 text-sm text-white/80 leading-relaxed last:mb-0" {...props} />
	),
	ul: (props: ComponentPropsWithoutRef<"ul">) => (
		<ul className="mb-3 list-disc space-y-1 pl-5 text-sm text-white/80" {...props} />
	),
	ol: (props: ComponentPropsWithoutRef<"ol">) => (
		<ol className="mb-3 list-decimal space-y-1 pl-5 text-sm text-white/80" {...props} />
	),
	li: (props: ComponentPropsWithoutRef<"li">) => <li className="leading-relaxed" {...props} />,
	a: (props: ComponentPropsWithoutRef<"a">) => (
		<a
			className="text-brand hover:underline"
			target="_blank"
			rel="noopener noreferrer"
			{...props}
		/>
	),
	strong: (props: ComponentPropsWithoutRef<"strong">) => (
		<strong className="font-semibold text-white" {...props} />
	),
	em: (props: ComponentPropsWithoutRef<"em">) => <em className="italic" {...props} />,
	code: (props: ComponentPropsWithoutRef<"code">) => (
		<code className="rounded bg-white/10 px-1 py-0.5 font-mono text-xs" {...props} />
	),
	pre: (props: ComponentPropsWithoutRef<"pre">) => (
		<pre
			className="mb-3 overflow-x-auto rounded-md bg-white/5 p-3 text-xs [&_code]:bg-transparent [&_code]:p-0"
			{...props}
		/>
	),
	blockquote: (props: ComponentPropsWithoutRef<"blockquote">) => (
		<blockquote className="mb-3 border-brand/40 border-l-2 pl-3 text-white/70 italic" {...props} />
	),
	hr: () => <hr className="my-4 border-white/10" />,
	table: (props: ComponentPropsWithoutRef<"table">) => (
		<table className="mb-3 w-full border-collapse text-sm" {...props} />
	),
	th: (props: ComponentPropsWithoutRef<"th">) => (
		<th className="border border-white/15 px-2 py-1 text-left font-semibold" {...props} />
	),
	td: (props: ComponentPropsWithoutRef<"td">) => (
		<td className="border border-white/10 px-2 py-1" {...props} />
	),
};

export function Markdown({
	children,
	className,
}: {
	children: string;
	className?: string;
}) {
	return (
		<div className={className}>
			<ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
				{children}
			</ReactMarkdown>
		</div>
	);
}

// A single-line title past this length is really a brief, not a
// heading. Rendering it as a giant <h1> wraps into a wall of bold text,
// so we route it through prose rendering instead. Tuned so a normal
// one-line task title (a dozen-ish words) stays a heading.
const TITLE_PROSE_LENGTH_THRESHOLD = 120;

/**
 * Heuristic: does this string benefit from Markdown/prose rendering?
 * True when it spans multiple lines, contains common Markdown syntax,
 * or is long enough that a heading would look messy. Short single-line
 * titles stay as a plain heading so existing document tasks look
 * unchanged.
 */
export function looksRich(text: string): boolean {
	if (text.length > TITLE_PROSE_LENGTH_THRESHOLD) return true;
	if (text.includes("\n")) return true;
	return /(\*\*|__|^#{1,6}\s|^[-*+]\s|\[[^\]]+\]\([^)]+\)|`)/m.test(text);
}
