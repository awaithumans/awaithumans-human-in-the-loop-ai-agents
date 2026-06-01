/**
 * Form primitive types — mirrors the Python `awaithumans.forms` wire format.
 *
 * Each primitive is discriminated by `kind`. The server serializes a
 * FormDefinition as JSON; the dashboard deserializes it and dispatches
 * to the corresponding React component in components/form-renderer/.
 */

export type FormDefinition = {
	version: number;
	fields: FormField[];
};

type BaseField = {
	name: string;
	kind: string;
	label: string | null;
	hint: string | null;
	required: boolean;
};

// ─── Text ────────────────────────────────────────────────────────────

export type ShortTextSubtype =
	| "plain"
	| "email"
	| "url"
	| "phone"
	| "currency"
	| "number"
	| "password";

export type DisplayTextField = BaseField & {
	kind: "display_text";
	text: string;
	markdown: boolean;
};

export type ShortTextField = BaseField & {
	kind: "short_text";
	subtype: ShortTextSubtype;
	placeholder: string | null;
	min_length: number | null;
	max_length: number | null;
	pattern: string | null;
	currency_code: string | null;
};

export type LongTextField = BaseField & {
	kind: "long_text";
	placeholder: string | null;
	min_length: number | null;
	max_length: number | null;
	rows: number | null;
};

export type RichTextField = BaseField & {
	kind: "rich_text";
	placeholder: string | null;
	max_length: number | null;
};

// ─── Selection ───────────────────────────────────────────────────────

export type SelectOption = {
	value: string;
	label: string;
	hint: string | null;
};

export type PictureOption = {
	value: string;
	label: string;
	image_url: string;
	hint: string | null;
};

export type SwitchField = BaseField & {
	kind: "switch";
	true_label: string;
	false_label: string;
	default: boolean | null;
};

export type SingleSelectField = BaseField & {
	kind: "single_select";
	options: SelectOption[];
	default: string | null;
};

export type MultiSelectField = BaseField & {
	kind: "multi_select";
	options: SelectOption[];
	default: string[];
	min_count: number | null;
	max_count: number | null;
};

export type PictureChoiceField = BaseField & {
	kind: "picture_choice";
	options: PictureOption[];
	multiple: boolean;
	default: string[];
};

// ─── Numeric ─────────────────────────────────────────────────────────

export type SliderField = BaseField & {
	kind: "slider";
	min: number;
	max: number;
	step: number;
	default: number | null;
	prefix: string | null;
	suffix: string | null;
};

export type StarRatingField = BaseField & {
	kind: "star_rating";
	max: number;
	default: number | null;
};

export type OpinionScaleField = BaseField & {
	kind: "opinion_scale";
	min: number;
	max: number;
	min_label: string | null;
	max_label: string | null;
	default: number | null;
};

export type RankingField = BaseField & {
	kind: "ranking";
	options: SelectOption[];
};

// ─── Date / time ─────────────────────────────────────────────────────

export type DatePickerField = BaseField & {
	kind: "date";
	min_date: string | null;
	max_date: string | null;
	default: string | null;
};

export type DateTimePickerField = BaseField & {
	kind: "datetime";
	min_datetime: string | null;
	max_datetime: string | null;
	timezone: string | null;
	default: string | null;
};

export type DateRangeField = BaseField & {
	kind: "date_range";
	min_date: string | null;
	max_date: string | null;
	min_days: number | null;
	max_days: number | null;
};

export type TimePickerField = BaseField & {
	kind: "time";
	min_time: string | null;
	max_time: string | null;
	step_minutes: number;
	default: string | null;
};

// ─── Media (input) ───────────────────────────────────────────────────

export type FileUploadField = BaseField & {
	kind: "file_upload";
	accept: string[] | null;
	max_size_bytes: number | null;
	multiple: boolean;
	min_count: number | null;
	max_count: number | null;
};

export type SignatureField = BaseField & {
	kind: "signature";
	format: "png" | "svg";
};

// ─── Media (display) ─────────────────────────────────────────────────

export type ImageField = BaseField & {
	kind: "image";
	url: string;
	alt: string | null;
	width: number | null;
	height: number | null;
	// Per-page metadata (AwaitVerify M5, optional). When present the
	// carousel renders a two-level page selector + per-page nav. When
	// absent (legacy tasks created before M5) the field renders in
	// today's flat carousel — DO NOT crash on missing keys.
	page_index?: number; // 0-indexed
	fragment_in_page?: number; // 0-indexed within page
	page_count?: number; // total pages
	fragments_per_page?: number; // always 5 at v1
};

export type VideoField = BaseField & {
	kind: "video";
	url: string;
	poster_url: string | null;
	autoplay: boolean;
};

export type PdfViewerField = BaseField & {
	kind: "pdf_viewer";
	url: string;
	height: number | null;
};

export type HtmlBlockField = BaseField & {
	kind: "html";
	html: string;
};

// ─── Layout ──────────────────────────────────────────────────────────

export type SectionField = BaseField & {
	kind: "section";
	title: string;
	subtitle: string | null;
};

export type DividerField = BaseField & {
	kind: "divider";
};

export type SectionCollapseField = BaseField & {
	kind: "section_collapse";
	title: string;
	subtitle: string | null;
	fields: FormField[];
	default_open: boolean;
};

// ─── Complex ─────────────────────────────────────────────────────────

export type TableColumnKind =
	| "short_text"
	| "long_text"
	| "number"
	| "currency"
	| "switch"
	| "single_select"
	| "date"
	| "datetime";

export type TableColumn = {
	name: string;
	label: string;
	kind: TableColumnKind;
	required: boolean;
	placeholder: string | null;
	options: SelectOption[] | null;
	currency_code: string | null;
	min_value: number | null;
	max_value: number | null;
	default: string | number | boolean | null;
};

export type TableField = BaseField & {
	kind: "table";
	columns: TableColumn[];
	min_rows: number | null;
	max_rows: number | null;
	initial_rows: number;
	allow_add_row: boolean;
	allow_remove_row: boolean;
};

export type SubformField = BaseField & {
	kind: "subform";
	fields: FormField[];
	min_count: number | null;
	max_count: number | null;
	initial_count: number;
	add_label: string;
	remove_label: string;
};

// ─── Nested Pydantic groups ──────────────────────────────────────────
//
// `object_group` and `repeatable_group` are built by the managed-side
// form-definition builder when the customer's response_schema includes
// a nested Pydantic model or a `list[BaseModel]`. Pre-PR-C these
// collapsed to a `long_text` holding raw JSON, which was unusable for
// non-technical reviewers. The new kinds give the dashboard enough
// shape to render an indented section (object) or an editable table
// (list[BaseModel]) without exposing JSON anywhere.

export type ObjectGroupField = BaseField & {
	kind: "object_group";
	// Children to render inside the indented section. May contain any
	// FormField kind, including another object_group or repeatable_group.
	fields: FormField[];
};

export type RepeatableGroupField = BaseField & {
	kind: "repeatable_group";
	// Shape of a single row. Reviewer adds rows by clicking + Add row;
	// each new row is an empty {} that fills in defaults per item_field.
	item_fields: FormField[];
	min_items: number | null;
	max_items: number | null;
};

// ─── Union ───────────────────────────────────────────────────────────

export type FormField =
	| DisplayTextField
	| ShortTextField
	| LongTextField
	| RichTextField
	| SwitchField
	| SingleSelectField
	| MultiSelectField
	| PictureChoiceField
	| SliderField
	| StarRatingField
	| OpinionScaleField
	| RankingField
	| DatePickerField
	| DateTimePickerField
	| DateRangeField
	| TimePickerField
	| FileUploadField
	| SignatureField
	| ImageField
	| VideoField
	| PdfViewerField
	| HtmlBlockField
	| SectionField
	| DividerField
	| SectionCollapseField
	| TableField
	| SubformField
	| ObjectGroupField
	| RepeatableGroupField;
