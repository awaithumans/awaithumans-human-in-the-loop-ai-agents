/**
 * FormRenderer — walks a FormDefinition and renders it with the dashboard's
 * per-primitive components. Layout primitives (section, divider,
 * section_collapse) have no value. Input primitives read and write into
 * `value` keyed by `field.name`.
 *
 * Recursive primitives (subform, section_collapse) recurse by re-entering
 * the dispatcher with their own child fields.
 */

"use client";

import type { FormDefinition, FormField } from "@/lib/form-types";
import {
	DisplayTextRenderer,
	LongTextRenderer,
	RichTextRenderer,
	ShortTextRenderer,
} from "./text";
import {
	MultiSelectRenderer,
	PictureChoiceRenderer,
	SingleSelectRenderer,
	SwitchRenderer,
} from "./selection";
import {
	OpinionScaleRenderer,
	RankingRenderer,
	SliderRenderer,
	StarRatingRenderer,
} from "./numeric";
import {
	DatePickerRenderer,
	DateRangeRenderer,
	DateTimePickerRenderer,
	TimePickerRenderer,
} from "./date-time";
import {
	FileUploadRenderer,
	HtmlBlockRenderer,
	ImageDisplayRenderer,
	PdfViewerRenderer,
	SignatureRenderer,
	VideoDisplayRenderer,
} from "./media";
import {
	DividerRenderer,
	SectionCollapseRenderer,
	SectionRenderer,
} from "./layout";
import {
	ObjectGroupRenderer,
	RepeatableGroupRenderer,
	SubformRenderer,
	TableRenderer,
} from "./complex";
import { ImageCarousel } from "./image-carousel";
import { groupImageFields } from "./image-grouping";
import type { FormValue } from "./types";

export { buildResponseValue } from "./build-response-value";
export { initialValueFor } from "./initial-value";
export type { FormValue } from "./types";

type Props = {
	form: FormDefinition;
	value: FormValue;
	onChange: (next: FormValue) => void;
	disabled?: boolean;
};

export function FormRenderer({ form, value, onChange, disabled }: Props) {
	// Group top-level image fields into a single carousel when there
	// are 2+ — reviewers don't have to scroll a long vertical stack
	// of document fragments. Single-image forms keep their inline
	// renderer (no carousel chrome).
	const slots = groupImageFields(form.fields);
	return (
		<div className="space-y-4">
			{slots.map((slot, i) => {
				if (slot.kind === "carousel") {
					return (
						<ImageCarousel
							key={`carousel-${i}`}
							images={slot.images}
						/>
					);
				}
				const field = slot.field;
				return (
					<FieldDispatch
						key={`${field.name || field.kind}-${i}`}
						field={field}
						value={value}
						onChange={onChange}
						disabled={disabled}
					/>
				);
			})}
		</div>
	);
}

function FieldDispatch({
	field,
	value,
	onChange,
	disabled,
}: {
	field: FormField;
	value: FormValue;
	onChange: (next: FormValue) => void;
	disabled?: boolean;
}) {
	const setField = (next: unknown) =>
		onChange({ ...value, [field.name]: next });

	switch (field.kind) {
		// ── Display (no value) ──────────────────────────────
		case "display_text":
			return <DisplayTextRenderer field={field} />;
		case "image":
			return <ImageDisplayRenderer field={field} />;
		case "video":
			return <VideoDisplayRenderer field={field} />;
		case "pdf_viewer":
			return <PdfViewerRenderer field={field} />;
		case "html":
			return <HtmlBlockRenderer field={field} />;

		// ── Layout ──────────────────────────────────────────
		case "section":
			return <SectionRenderer field={field} />;
		case "divider":
			return <DividerRenderer field={field} />;
		case "section_collapse":
			return (
				<SectionCollapseRenderer field={field}>
					{field.fields.map((child, j) => (
						<FieldDispatch
							key={`${child.name || child.kind}-${j}`}
							field={child}
							value={value}
							onChange={onChange}
							disabled={disabled}
						/>
					))}
				</SectionCollapseRenderer>
			);

		// ── Text input ──────────────────────────────────────
		case "short_text":
			return (
				<ShortTextRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "long_text":
			return (
				<LongTextRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "rich_text":
			return (
				<RichTextRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);

		// ── Selection ───────────────────────────────────────
		case "switch":
			return (
				<SwitchRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "single_select":
			return (
				<SingleSelectRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "multi_select":
			return (
				<MultiSelectRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "picture_choice":
			return (
				<PictureChoiceRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);

		// ── Numeric ─────────────────────────────────────────
		case "slider":
			return (
				<SliderRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "star_rating":
			return (
				<StarRatingRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "opinion_scale":
			return (
				<OpinionScaleRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "ranking":
			return (
				<RankingRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);

		// ── Date / time ─────────────────────────────────────
		case "date":
			return (
				<DatePickerRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "datetime":
			return (
				<DateTimePickerRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "date_range":
			return (
				<DateRangeRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "time":
			return (
				<TimePickerRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);

		// ── Media input ─────────────────────────────────────
		case "file_upload":
			return (
				<FileUploadRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "signature":
			return (
				<SignatureRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);

		// ── Complex ─────────────────────────────────────────
		case "table":
			return (
				<TableRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
				/>
			);
		case "subform":
			return (
				<SubformRenderer
					field={field}
					value={value[field.name]}
					onChange={setField}
					disabled={disabled}
					renderChildren={(entryValue, setEntry) => (
						<div className="space-y-3">
							{field.fields.map((child, j) => (
								<FieldDispatch
									key={`${child.name || child.kind}-${j}`}
									field={child}
									value={entryValue}
									onChange={setEntry}
									disabled={disabled}
								/>
							))}
						</div>
					)}
				/>
			);

		// ── Nested Pydantic groups ──────────────────────────
		// object_group: read the nested sub-FormValue at value[field.name]
		// and dispatch children with that as their scope. Children's
		// `setField` wraps back to set value[field.name][child.name],
		// preserving the rest of the sub-scope.
		case "object_group": {
			const groupValue = isPlainObject(value[field.name])
				? (value[field.name] as FormValue)
				: {};
			const setGroup = (next: FormValue) =>
				onChange({ ...value, [field.name]: next });
			return (
				<ObjectGroupRenderer field={field}>
					{field.fields.map((child, j) => (
						<FieldDispatch
							key={`${child.name || child.kind}-${j}`}
							field={child}
							value={groupValue}
							onChange={setGroup}
							disabled={disabled}
						/>
					))}
				</ObjectGroupRenderer>
			);
		}

		// repeatable_group: read the array at value[field.name]. Each
		// row is itself a FormValue (dict). Cells dispatch the actual
		// item_field renderer with the row as the value scope.
		case "repeatable_group": {
			const rows: FormValue[] = Array.isArray(value[field.name])
				? (value[field.name] as FormValue[])
				: [];
			return (
				<RepeatableGroupRenderer
					field={field}
					rows={rows}
					onRowsChange={(next) =>
						onChange({ ...value, [field.name]: next })
					}
					disabled={disabled}
					renderCell={(row, setRow, itemField) => (
						<FieldDispatch
							field={itemField}
							value={row}
							onChange={setRow}
							disabled={disabled}
						/>
					)}
				/>
			);
		}
	}
	// Unknown field kind — version skew between this dashboard and a
	// newer managed-side schema walker, or a malformed form_definition
	// payload. Show a friendly message instead of dumping JSON the
	// reviewer can't action. Never reached in normal operation.
	return (
		<div className="border border-yellow-500/30 bg-yellow-500/5 rounded-md px-3 py-2 text-sm text-yellow-200/80">
			This field isn&apos;t supported by the review form yet — please
			contact support.
		</div>
	);
}

function isPlainObject(v: unknown): v is FormValue {
	// "Plain" enough for our needs: non-null object that isn't an array.
	// Doesn't try to defend against exotic prototypes — the form value
	// is always either a literal {} from us or whatever JSON the server
	// round-tripped, both of which qualify.
	return typeof v === "object" && v !== null && !Array.isArray(v);
}

// initialValueFor / walk live in ./initial-value.ts so the test file
// can import them without going through this .tsx (which the vitest
// JSX-preserve loader can't parse). Re-exported above for callers.

