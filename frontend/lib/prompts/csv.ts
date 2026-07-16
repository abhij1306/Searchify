/**
 * In-browser CSV parsing for the F7 prompt library bulk import.
 *
 * The CSV is parsed and validated entirely in the browser so the user can
 * preview + fix rows before anything is persisted; the accepted rows are then
 * posted to the B3 `/prompt-sets/{id}/import` endpoint. Mirrors the backend
 * column aliases (`app/domain/prompts/csv_import.py`) so a file that imports
 * server-side previews identically here.
 *
 * A tiny hand-rolled parser (no dependency) handles quoted fields, escaped
 * quotes (`""`), and embedded newlines — enough for prompt CSVs.
 */
import type { PromptInput } from '@/lib/api/prompts';
import { promptIntentSchema } from '@/lib/api/schemas';
import type { PromptIntent } from '@/lib/api/types';

const THEME_KEYS = new Set(['theme', 'topic', 'category']);
const TEXT_KEYS = new Set(['text', 'prompt', 'query', 'question']);
const INTENT_KEYS = new Set(['intent']);
const BRANDED_KEYS = new Set(['branded', 'is_branded']);
const ENABLED_KEYS = new Set(['enabled', 'is_enabled', 'active']);

const TRUE_VALUES = new Set(['1', 'true', 'yes', 'y', 't']);
const FALSE_VALUES = new Set(['0', 'false', 'no', 'n', 'f']);

const VALID_INTENTS = new Set<string>(promptIntentSchema.options);

export type ParsedPromptRow = {
  /** 1-based source row number (excludes the header) for user feedback. */
  line: number;
  input: PromptInput;
  /** Non-fatal issues (e.g. an unknown intent that was dropped to ''). */
  warnings: string[];
  /** Fatal issues (row is not importable, e.g. empty text). */
  errors: string[];
};

export type ParsedCsv = {
  rows: ParsedPromptRow[];
  /** True when the file had a recognizable header row. */
  hasHeader: boolean;
  /** File-level errors (empty file, no text column, etc.). */
  errors: string[];
};

/** Tokenize CSV text into a matrix of string cells (RFC-4180-ish). */
export function tokenizeCsv(raw: string): string[][] {
  const rows: string[][] = [];
  let field = '';
  let row: string[] = [];
  let inQuotes = false;
  // Strip a UTF-8 BOM if present.
  const text = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw;

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (inQuotes) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"') {
      inQuotes = true;
    } else if (char === ',') {
      row.push(field);
      field = '';
    } else if (char === '\n' || char === '\r') {
      // Handle CRLF as a single break.
      if (char === '\r' && text[i + 1] === '\n') i += 1;
      row.push(field);
      field = '';
      rows.push(row);
      row = [];
    } else {
      field += char;
    }
  }
  // Flush the trailing field/row (files without a final newline).
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  // Drop rows that are entirely empty (blank lines).
  return rows.filter((cells) => cells.some((cell) => cell.trim() !== ''));
}

function asBool(value: string | undefined, fallback: boolean): boolean {
  const normalized = (value ?? '').trim().toLowerCase();
  if (!normalized) return fallback;
  if (TRUE_VALUES.has(normalized)) return true;
  if (FALSE_VALUES.has(normalized)) return false;
  return fallback;
}

function normalizeIntent(value: string | undefined): {
  intent: PromptIntent;
  dropped: boolean;
} {
  const normalized = (value ?? '').trim().toLowerCase();
  if (!normalized) return { intent: '', dropped: false };
  if (VALID_INTENTS.has(normalized)) return { intent: normalized as PromptIntent, dropped: false };
  return { intent: '', dropped: true };
}

type ColumnMap = {
  text: number;
  theme: number;
  intent: number;
  branded: number;
  enabled: number;
};

function detectColumns(headerCells: string[]): ColumnMap | null {
  const find = (keys: Set<string>) =>
    headerCells.findIndex((cell) => keys.has(cell.trim().toLowerCase()));
  const map: ColumnMap = {
    text: find(TEXT_KEYS),
    theme: find(THEME_KEYS),
    intent: find(INTENT_KEYS),
    branded: find(BRANDED_KEYS),
    enabled: find(ENABLED_KEYS),
  };
  // A header row is recognized only if it names at least the text column.
  return map.text >= 0 ? map : null;
}

/**
 * Parse CSV text into previewable prompt rows. With a header row, columns are
 * matched by name (any order); without one, the first column is the prompt
 * text and the remaining columns follow the canonical
 * `text,theme,intent,branded,enabled` order.
 */
export function parsePromptCsv(raw: string): ParsedCsv {
  const matrix = tokenizeCsv(raw);
  if (matrix.length === 0) {
    return { rows: [], hasHeader: false, errors: ['The file is empty.'] };
  }

  const columns = detectColumns(matrix[0]);
  const hasHeader = columns !== null;
  const map: ColumnMap = columns ?? {
    text: 0,
    theme: 1,
    intent: 2,
    branded: 3,
    enabled: 4,
  };
  const dataRows = hasHeader ? matrix.slice(1) : matrix;

  const rows: ParsedPromptRow[] = dataRows.map((cells, index) => {
    const cell = (col: number) => (col >= 0 ? cells[col] : undefined);
    const text = (cell(map.text) ?? '').trim();
    const theme = (cell(map.theme) ?? '').trim();
    const { intent, dropped } = normalizeIntent(cell(map.intent));
    const warnings: string[] = [];
    const errors: string[] = [];
    if (!text) errors.push('Prompt text is required.');
    if (dropped) warnings.push(`Unknown intent "${(cell(map.intent) ?? '').trim()}" was cleared.`);

    return {
      line: index + 1,
      input: {
        text,
        theme: theme || null,
        intent,
        branded: asBool(cell(map.branded), false),
        enabled: asBool(cell(map.enabled), true),
      },
      warnings,
      errors,
    };
  });

  const errors: string[] = [];
  if (rows.length === 0) errors.push('No data rows were found.');

  return { rows, hasHeader, errors };
}

/** The importable subset (rows without fatal errors). */
export function validRows(parsed: ParsedCsv): PromptInput[] {
  return parsed.rows.filter((row) => row.errors.length === 0).map((row) => row.input);
}
