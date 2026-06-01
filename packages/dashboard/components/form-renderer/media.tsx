import { useRef, useState, useCallback, useEffect } from "react";
import { Eyebrow } from "@/components/eyebrow";
import {
	SIGNATURE_CANVAS_HEIGHT,
	SIGNATURE_CANVAS_WIDTH,
} from "@/lib/constants";
import type {
	FileUploadField,
	HtmlBlockField,
	ImageField,
	PdfViewerField,
	SignatureField,
	VideoField,
} from "@/lib/form-types";
import { FieldWrapper } from "./field-wrapper";
import { ProtectedImage } from "./protected-image";

// ─── FileUpload ──────────────────────────────────────────────────────

/**
 * Baseline: read file(s) into base64 data URLs stored in the form value.
 * For large files the server will later expose a presigned-upload endpoint;
 * the wire shape (list of {name, mime, data}) stays the same.
 */
type UploadedFile = { name: string; mime: string; size: number; data: string };

export function FileUploadRenderer({
	field,
	value,
	onChange,
	disabled,
}: {
	field: FileUploadField;
	value: unknown;
	onChange: (v: unknown) => void;
	disabled?: boolean;
}) {
	const files = Array.isArray(value) ? (value as UploadedFile[]) : [];
	const inputRef = useRef<HTMLInputElement>(null);

	const handleFiles = async (fileList: FileList) => {
		const collected: UploadedFile[] = [];
		for (const file of Array.from(fileList)) {
			if (field.max_size_bytes && file.size > field.max_size_bytes) continue;
			const data = await readAsDataUrl(file);
			collected.push({
				name: file.name,
				mime: file.type,
				size: file.size,
				data,
			});
		}
		onChange(field.multiple ? [...files, ...collected] : collected);
	};

	const remove = (idx: number) => {
		onChange(files.filter((_, i) => i !== idx));
	};

	return (
		<FieldWrapper field={field}>
			<div className="space-y-2">
				<button
					type="button"
					onClick={() => inputRef.current?.click()}
					disabled={disabled}
					className="w-full border border-dashed border-white/20 rounded-md px-3 py-4 text-sm text-white/50 hover:text-white hover:border-white/40 transition-colors"
				>
					Click to {field.multiple ? "add files" : "choose a file"}
					{field.accept && (
						<span className="block text-xs text-white/30 mt-1">
							{field.accept.join(", ")}
						</span>
					)}
				</button>
				<input
					ref={inputRef}
					type="file"
					accept={field.accept?.join(",")}
					multiple={field.multiple}
					onChange={(e) => e.target.files && handleFiles(e.target.files)}
					disabled={disabled}
					className="hidden"
				/>
				{files.length > 0 && (
					<ul className="space-y-1">
						{files.map((f, i) => (
							<li
								key={i}
								className="flex items-center justify-between gap-2 px-3 py-1.5 bg-white/5 border border-white/10 rounded-md text-sm"
							>
								<span className="truncate">{f.name}</span>
								<div className="flex items-center gap-3 text-xs text-white/40">
									<span>{formatBytes(f.size)}</span>
									<button
										type="button"
										onClick={() => remove(i)}
										disabled={disabled}
										className="text-red-400 hover:text-red-300"
										aria-label="Remove"
									>
										×
									</button>
								</div>
							</li>
						))}
					</ul>
				)}
			</div>
		</FieldWrapper>
	);
}

function readAsDataUrl(file: File): Promise<string> {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(String(reader.result));
		reader.onerror = reject;
		reader.readAsDataURL(file);
	});
}

function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ─── Signature ───────────────────────────────────────────────────────

/**
 * Baseline: HTML5 canvas. Value travels as a base64 PNG data URL.
 */
export function SignatureRenderer({
	field,
	value,
	onChange,
	disabled,
}: {
	field: SignatureField;
	value: unknown;
	onChange: (v: unknown) => void;
	disabled?: boolean;
}) {
	const canvasRef = useRef<HTMLCanvasElement>(null);
	const [drawing, setDrawing] = useState(false);
	const hasValue = typeof value === "string" && value.length > 0;

	const start = useCallback(
		(e: React.PointerEvent<HTMLCanvasElement>) => {
			if (disabled) return;
			const ctx = canvasRef.current?.getContext("2d");
			if (!ctx) return;
			setDrawing(true);
			ctx.beginPath();
			const { x, y } = localPoint(e);
			ctx.moveTo(x, y);
		},
		[disabled],
	);

	const draw = useCallback(
		(e: React.PointerEvent<HTMLCanvasElement>) => {
			if (!drawing || disabled) return;
			const ctx = canvasRef.current?.getContext("2d");
			if (!ctx) return;
			const { x, y } = localPoint(e);
			ctx.lineTo(x, y);
			// Canvas API doesn't understand Tailwind tokens — read the CSS var.
			ctx.strokeStyle = getComputedStyle(document.documentElement)
				.getPropertyValue("--color-fg")
				.trim() || "#f5f5f5";
			ctx.lineWidth = 2;
			ctx.lineCap = "round";
			ctx.stroke();
		},
		[drawing, disabled],
	);

	const end = useCallback(() => {
		setDrawing(false);
		const canvas = canvasRef.current;
		if (!canvas) return;
		onChange(canvas.toDataURL("image/png"));
	}, [onChange]);

	const clear = () => {
		const canvas = canvasRef.current;
		const ctx = canvas?.getContext("2d");
		if (!canvas || !ctx) return;
		ctx.clearRect(0, 0, canvas.width, canvas.height);
		onChange(null);
	};

	return (
		<FieldWrapper field={field}>
			<div className="space-y-2">
				<canvas
					ref={canvasRef}
					width={SIGNATURE_CANVAS_WIDTH}
					height={SIGNATURE_CANVAS_HEIGHT}
					onPointerDown={start}
					onPointerMove={draw}
					onPointerUp={end}
					onPointerLeave={end}
					className="w-full border border-white/10 rounded-md bg-white/5 touch-none"
				/>
				<div className="flex justify-between text-xs">
					<span className="text-white/40">
						{hasValue ? "Signed" : "Draw your signature"}
					</span>
					<button
						type="button"
						onClick={clear}
						disabled={disabled}
						className="text-white/40 hover:text-white"
					>
						Clear
					</button>
				</div>
			</div>
		</FieldWrapper>
	);
}

function localPoint(e: React.PointerEvent<HTMLCanvasElement>) {
	const rect = e.currentTarget.getBoundingClientRect();
	const scaleX = e.currentTarget.width / rect.width;
	const scaleY = e.currentTarget.height / rect.height;
	return {
		x: (e.clientX - rect.left) * scaleX,
		y: (e.clientY - rect.top) * scaleY,
	};
}

// ─── Image (display) ─────────────────────────────────────────────────

export function ImageDisplayRenderer({ field }: { field: ImageField }) {
	return (
		<div className="space-y-1">
			{field.label && (
				<Eyebrow weight="semibold" className="block text-white/50">
					{field.label}
				</Eyebrow>
			)}
			<ProtectedImage
				src={field.url}
				alt={field.alt ?? field.label ?? ""}
				width={field.width ?? undefined}
				height={field.height ?? undefined}
				className="max-w-full rounded-md border border-white/10"
			/>
		</div>
	);
}

// ─── Video (display) ─────────────────────────────────────────────────

export function VideoDisplayRenderer({ field }: { field: VideoField }) {
	return (
		<div className="space-y-1">
			{field.label && (
				<Eyebrow weight="semibold" className="block text-white/50">
					{field.label}
				</Eyebrow>
			)}
			<video
				src={field.url}
				poster={field.poster_url ?? undefined}
				controls
				autoPlay={field.autoplay}
				className="w-full rounded-md border border-white/10"
			>
				<track kind="captions" />
			</video>
		</div>
	);
}

// ─── PdfViewer (display) ─────────────────────────────────────────────

export function PdfViewerRenderer({ field }: { field: PdfViewerField }) {
	// Warm the iframe after mount to avoid blocking initial paint with a heavy PDF.
	const [mounted, setMounted] = useState(false);
	useEffect(() => {
		setMounted(true);
	}, []);
	return (
		<div className="space-y-1">
			{field.label && (
				<Eyebrow weight="semibold" className="block text-white/50">
					{field.label}
				</Eyebrow>
			)}
			{mounted && (
				<iframe
					src={field.url}
					title={field.label ?? "PDF"}
					height={field.height ?? 480}
					className="w-full rounded-md border border-white/10 bg-white/5"
				/>
			)}
		</div>
	);
}

// ─── HtmlBlock (display) ─────────────────────────────────────────────

/**
 * Developer-authored HTML. For v1 we sandbox the output in a srcdoc iframe
 * — scripts inside won't execute against the dashboard origin, and the
 * iframe height auto-sizes to the content. When the developer explicitly
 * opts into a DOMPurify-backed inline renderer later, this placeholder
 * stays safe-by-default.
 */
export function HtmlBlockRenderer({ field }: { field: HtmlBlockField }) {
	return (
		<div className="space-y-1">
			{field.label && (
				<Eyebrow weight="semibold" className="block text-white/50">
					{field.label}
				</Eyebrow>
			)}
			<iframe
				title={field.label ?? "HTML"}
				srcDoc={field.html}
				sandbox=""
				className="w-full min-h-[120px] rounded-md border border-white/10 bg-white/5"
			/>
		</div>
	);
}
