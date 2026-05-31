import type { ReactNode } from "react";
import { Eyebrow } from "@/components/eyebrow";
import type {
	FormField,
	ObjectGroupField,
	RepeatableGroupField,
	SubformField,
	TableColumn,
	TableField,
} from "@/lib/form-types";
import { CompactFieldContext } from "./compact-context";
import { FieldWrapper } from "./field-wrapper";

const cellClass =
	"bg-white/5 border border-white/10 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-brand/40 w-full";

// ─── Table ───────────────────────────────────────────────────────────

type TableRow = Record<string, unknown>;

export function TableRenderer({
	field,
	value,
	onChange,
	disabled,
}: {
	field: TableField;
	value: unknown;
	onChange: (v: unknown) => void;
	disabled?: boolean;
}) {
	const rows: TableRow[] = Array.isArray(value)
		? (value as TableRow[])
		: Array.from({ length: field.initial_rows }).map(() =>
				defaultRow(field.columns),
			);

	const setCell = (rowIdx: number, col: TableColumn, raw: unknown) => {
		const next = rows.map((r, i) =>
			i === rowIdx ? { ...r, [col.name]: coerceCell(col, raw) } : r,
		);
		onChange(next);
	};

	const addRow = () => onChange([...rows, defaultRow(field.columns)]);
	const removeRow = (idx: number) =>
		onChange(rows.filter((_, i) => i !== idx));

	const canAdd =
		field.allow_add_row &&
		!disabled &&
		(field.max_rows === null || rows.length < field.max_rows);
	const canRemove =
		field.allow_remove_row &&
		!disabled &&
		(field.min_rows === null || rows.length > (field.min_rows ?? 0));

	return (
		<FieldWrapper field={field}>
			<div className="border border-white/10 rounded-md overflow-hidden">
				<table className="w-full">
					<thead className="bg-white/5 text-xs text-white/60">
						<tr>
							{field.columns.map((col) => (
								<th key={col.name} className="text-left px-2 py-1.5">
									{col.label}
									{col.required && (
										<span className="text-red-400 ml-0.5">*</span>
									)}
								</th>
							))}
							{canRemove && <th className="w-8" />}
						</tr>
					</thead>
					<tbody>
						{rows.map((row, i) => (
							<tr key={i} className="border-t border-white/5">
								{field.columns.map((col) => (
									<td key={col.name} className="p-1">
										<TableCell
											col={col}
											value={row[col.name]}
											onChange={(v) => setCell(i, col, v)}
											disabled={disabled}
										/>
									</td>
								))}
								{canRemove && (
									<td className="p-1 text-center">
										<button
											type="button"
											onClick={() => removeRow(i)}
											disabled={disabled}
											aria-label="Remove row"
											className="text-red-400/70 hover:text-red-400 w-6 h-6"
										>
											×
										</button>
									</td>
								)}
							</tr>
						))}
					</tbody>
				</table>
				{canAdd && (
					<button
						type="button"
						onClick={addRow}
						className="w-full px-3 py-1.5 text-xs text-brand hover:bg-brand/10 transition-colors border-t border-white/5"
					>
						+ Add row
					</button>
				)}
			</div>
		</FieldWrapper>
	);
}

function defaultRow(columns: TableColumn[]): TableRow {
	const row: TableRow = {};
	for (const col of columns) row[col.name] = col.default ?? null;
	return row;
}

function coerceCell(col: TableColumn, raw: unknown): unknown {
	if (col.kind === "switch") return Boolean(raw);
	if (col.kind === "number" || col.kind === "currency") {
		if (raw === "" || raw === null || raw === undefined) return null;
		const n = Number(raw);
		return Number.isFinite(n) ? n : null;
	}
	return raw ?? null;
}

function TableCell({
	col,
	value,
	onChange,
	disabled,
}: {
	col: TableColumn;
	value: unknown;
	onChange: (v: unknown) => void;
	disabled?: boolean;
}) {
	if (col.kind === "switch") {
		return (
			<input
				type="checkbox"
				checked={Boolean(value)}
				onChange={(e) => onChange(e.target.checked)}
				disabled={disabled}
				className="accent-brand w-4 h-4"
			/>
		);
	}
	if (col.kind === "single_select" && col.options) {
		return (
			<select
				value={typeof value === "string" ? value : ""}
				onChange={(e) => onChange(e.target.value || null)}
				disabled={disabled}
				className={cellClass}
			>
				<option value="">—</option>
				{col.options.map((o) => (
					<option key={o.value} value={o.value}>
						{o.label}
					</option>
				))}
			</select>
		);
	}
	if (col.kind === "date") {
		return (
			<input
				type="date"
				value={typeof value === "string" ? value : ""}
				onChange={(e) => onChange(e.target.value || null)}
				disabled={disabled}
				className={cellClass}
			/>
		);
	}
	if (col.kind === "datetime") {
		return (
			<input
				type="datetime-local"
				value={typeof value === "string" ? value.slice(0, 16) : ""}
				onChange={(e) => onChange(e.target.value || null)}
				disabled={disabled}
				className={cellClass}
			/>
		);
	}
	if (col.kind === "long_text") {
		return (
			<textarea
				rows={2}
				value={typeof value === "string" ? value : ""}
				onChange={(e) => onChange(e.target.value || null)}
				disabled={disabled}
				placeholder={col.placeholder ?? undefined}
				className={cellClass}
			/>
		);
	}
	const isNumber = col.kind === "number" || col.kind === "currency";
	return (
		<input
			type={isNumber ? "number" : "text"}
			value={
				value === null || value === undefined ? "" : String(value as string)
			}
			onChange={(e) => onChange(e.target.value)}
			min={col.min_value ?? undefined}
			max={col.max_value ?? undefined}
			disabled={disabled}
			placeholder={col.placeholder ?? undefined}
			className={cellClass}
		/>
	);
}

// ─── Subform ─────────────────────────────────────────────────────────

type SubformEntry = Record<string, unknown>;

export function SubformRenderer({
	field,
	value,
	onChange,
	disabled,
	renderChildren,
}: {
	field: SubformField;
	value: unknown;
	onChange: (v: unknown) => void;
	disabled?: boolean;
	renderChildren: (
		entryValue: SubformEntry,
		setEntry: (v: SubformEntry) => void,
	) => ReactNode;
}) {
	const entries: SubformEntry[] = Array.isArray(value)
		? (value as SubformEntry[])
		: Array.from({ length: field.initial_count }).map(() => ({}));

	const setEntry = (idx: number, next: SubformEntry) => {
		onChange(entries.map((e, i) => (i === idx ? next : e)));
	};
	const addEntry = () => onChange([...entries, {}]);
	const removeEntry = (idx: number) =>
		onChange(entries.filter((_, i) => i !== idx));

	const canAdd =
		!disabled && (field.max_count === null || entries.length < field.max_count);
	const canRemove =
		!disabled &&
		(field.min_count === null || entries.length > (field.min_count ?? 0));

	return (
		<FieldWrapper field={field}>
			<div className="space-y-3">
				{entries.map((entry, i) => (
					<div
						key={i}
						className="border border-white/10 rounded-md p-3 space-y-3 bg-white/5"
					>
						<div className="flex items-center justify-between">
							<Eyebrow className="text-white/40">#{i + 1}</Eyebrow>
							{canRemove && (
								<button
									type="button"
									onClick={() => removeEntry(i)}
									className="text-red-400/70 hover:text-red-400 text-xs"
								>
									{field.remove_label}
								</button>
							)}
						</div>
						{renderChildren(entry, (v) => setEntry(i, v))}
					</div>
				))}
				{canAdd && (
					<button
						type="button"
						onClick={addEntry}
						className="text-sm text-brand hover:underline"
					>
						+ {field.add_label}
					</button>
				)}
			</div>
		</FieldWrapper>
	);
}

// ─── ObjectGroup (nested Pydantic object) ───────────────────────────
//
// Renders as an indented section with the group's label as a small
// uppercase header. Children dispatch normally at this level — they
// read/write a nested sub-FormValue keyed by their own `name`. The
// FieldDispatch case in index.tsx owns the value/onChange scoping.

export function ObjectGroupRenderer({
	field,
	children,
}: {
	field: ObjectGroupField;
	children: ReactNode;
}) {
	const hasLabel = field.label && field.label.length > 0;
	return (
		<div className="space-y-2">
			{hasLabel && (
				<div className="pt-2 border-t border-white/10">
					<Eyebrow weight="semibold" className="block text-white/50">
						{field.label}
						{field.required && (
							<span className="text-red-400 ml-0.5" aria-hidden>
								*
							</span>
						)}
					</Eyebrow>
					{field.hint && (
						<p className="text-white/40 text-xs mt-0.5">{field.hint}</p>
					)}
				</div>
			)}
			<div className="pl-3 border-l-2 border-white/10 space-y-3">
				{children}
			</div>
		</div>
	);
}

// ─── RepeatableGroup (list[BaseModel] → spreadsheet) ────────────────
//
// Renders as an editable table. Columns come from item_fields[].label;
// each row is a dict matching the item_fields shape. Cells dispatch
// the actual item_field renderer (so a date column is a date picker,
// a switch column is a switch, etc.), wrapped in CompactFieldContext
// so the per-field label doesn't duplicate the column header.
//
// Reviewer can edit any cell, add new rows via "+ Add row", or remove
// rows. Min/max constraints from the schema gate the buttons.

type RepeatableRow = Record<string, unknown>;

export function RepeatableGroupRenderer({
	field,
	rows,
	onRowsChange,
	disabled,
	renderCell,
}: {
	field: RepeatableGroupField;
	rows: RepeatableRow[];
	onRowsChange: (next: RepeatableRow[]) => void;
	disabled?: boolean;
	renderCell: (
		row: RepeatableRow,
		setRow: (next: RepeatableRow) => void,
		itemField: FormField,
	) => ReactNode;
}) {
	const setRow = (idx: number) => (next: RepeatableRow) => {
		onRowsChange(rows.map((r, i) => (i === idx ? next : r)));
	};
	const addRow = () => onRowsChange([...rows, {}]);
	const removeRow = (idx: number) =>
		onRowsChange(rows.filter((_, i) => i !== idx));

	// Column-bearing item fields (the ones the reviewer actually fills).
	// Layout-only fields (section, divider, image, etc.) don't have a
	// row-cell shape and would render as a confusing empty cell — filter
	// them out of the column header AND the row cell list.
	const columns = field.item_fields.filter((f) => !LAYOUT_AND_DISPLAY_KINDS.has(f.kind));

	const canAdd =
		!disabled && (field.max_items === null || rows.length < (field.max_items ?? Infinity));
	const canRemove =
		!disabled && (field.min_items === null || rows.length > (field.min_items ?? 0));

	return (
		<FieldWrapper field={field}>
			<CompactFieldContext.Provider value={true}>
				{rows.length === 0 ? (
					<div className="border border-dashed border-white/15 rounded-md px-4 py-6 text-sm text-white/40 text-center">
						No rows yet — click + Add row to begin.
					</div>
				) : (
					<div className="border border-white/10 rounded-md overflow-x-auto">
						<table className="w-full">
							<thead className="bg-white/5 text-xs text-white/60">
								<tr>
									{columns.map((col) => (
										<th
											key={col.name}
											className="text-left px-2 py-1.5 whitespace-nowrap"
										>
											{col.label || humanizeName(col.name)}
											{col.required && (
												<span className="text-red-400 ml-0.5">*</span>
											)}
										</th>
									))}
									{canRemove && <th className="w-8" aria-label="Remove" />}
								</tr>
							</thead>
							<tbody>
								{rows.map((row, i) => (
									<tr key={i} className="border-t border-white/5 align-top">
										{columns.map((col) => (
											<td key={col.name} className="p-1 min-w-[8rem]">
												{renderCell(row, setRow(i), col)}
											</td>
										))}
										{canRemove && (
											<td className="p-1 text-center align-middle">
												<button
													type="button"
													onClick={() => removeRow(i)}
													disabled={disabled}
													aria-label="Remove row"
													className="text-red-400/70 hover:text-red-400 w-6 h-6"
												>
													×
												</button>
											</td>
										)}
									</tr>
								))}
							</tbody>
						</table>
					</div>
				)}
				{canAdd && (
					<button
						type="button"
						onClick={addRow}
						className="mt-2 text-sm text-brand hover:underline"
					>
						+ Add row
					</button>
				)}
			</CompactFieldContext.Provider>
		</FieldWrapper>
	);
}

// Layout/display kinds that would render as an empty cell inside a
// repeatable_group row. Mirrors the same set in `index.tsx`'s walk()
// — duplicated rather than imported because each module owns the
// concept of "which kinds carry a value."
const LAYOUT_AND_DISPLAY_KINDS = new Set([
	"display_text",
	"image",
	"video",
	"pdf_viewer",
	"html",
	"section",
	"divider",
]);

function humanizeName(name: string): string {
	// Fallback when a column has no label — turn snake_case_field
	// into "Snake Case Field" so the reviewer doesn't see a raw
	// identifier. Managed should always supply labels but defending
	// against drift here costs nothing.
	if (!name) return "";
	return name
		.replace(/_/g, " ")
		.replace(/\b\w/g, (c) => c.toUpperCase());
}
