/**
 * In-browser CSV parsing for the product catalog bulk import.
 *
 * The file is parsed + validated in the browser so the user can preview rows
 * before anything is persisted; the accepted rows are then posted to the
 * `/projects/{id}/products/import` endpoint. Column aliases mirror the backend
 * (`app/domain/products/csv_import.py`) so a file that imports server-side
 * previews identically here — and like the backend, a recognizable header row
 * is REQUIRED (headerless files are rejected).
 *
 * Columns: name, sku, variant, category, price, currency, url, gtin (+ the
 * remaining attribute columns brand/mpn/availability/condition/description).
 */
import type { ProductInput } from '@/lib/api/products';
import { tokenizeCsv } from '@/lib/prompts/csv';

const SKU_KEYS = new Set(['sku', 'sku_id', 'product_sku', 'product_id']);
const NAME_KEYS = new Set(['name', 'product', 'product_name', 'product_title', 'title']);
const PRICE_KEYS = new Set(['price', 'price_amount', 'amount']);
const CURRENCY_KEYS = new Set(['currency', 'currency_code', 'price_currency']);
const URL_KEYS = new Set(['url', 'link', 'product_url', 'owned_url']);
const ALIASES_KEYS = new Set(['aliases', 'alias']);
const VARIANT_KEYS = new Set(['variant', 'variants']);

// Attribute columns folded into `attributes` (backend _ATTRIBUTE_KEYS).
const ATTRIBUTE_KEYS: Record<string, Set<string>> = {
  brand: new Set(['brand']),
  category: new Set(['category', 'collection', 'product_type']),
  gtin: new Set(['gtin', 'barcode', 'upc', 'ean', 'gtin13']),
  mpn: new Set(['mpn']),
  availability: new Set(['availability', 'stock_status']),
  condition: new Set(['condition']),
  description: new Set(['description', 'desc']),
};

const ALIAS_SEPARATORS = ['|', ';'];

export type ParsedProductRow = {
  /** 1-based source row number (excludes the header) for user feedback. */
  line: number;
  input: ProductInput;
  /** Non-fatal issues (e.g. an unparsable price that was dropped). */
  warnings: string[];
  /** Fatal issues (row is not importable, e.g. empty sku). */
  errors: string[];
};

export type ParsedProductCsv = {
  rows: ParsedProductRow[];
  /** File-level errors (empty file, missing header, etc.). */
  errors: string[];
};

function splitAliases(value: string): string[] {
  let parts = [value];
  for (const separator of ALIAS_SEPARATORS) {
    parts = parts.flatMap((part) => part.split(separator));
  }
  return parts.map((part) => part.trim()).filter(Boolean);
}

// Currency markers the price column may carry (longest-first so `US$`/`AU$`/
// `CA$` strip before `$`/`A$`/`C$`) — mirrors the backend `csv_import._parse_price`.
const PRICE_CURRENCY_SYMBOLS = ['US$', 'AU$', 'CA$', 'A$', 'C$', '$', '€', '£'];

/** Parse `$2,499.00` / `US$49.99` / `2499` into a number; null when unparseable. */
function parsePrice(value: string): number | null {
  let cleaned = value.replace(/,/g, '');
  for (const symbol of PRICE_CURRENCY_SYMBOLS) {
    cleaned = cleaned.split(symbol).join('');
  }
  cleaned = cleaned.replace(/\s/g, '');
  if (!cleaned) return null;
  const parsed = Number.parseFloat(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

type ColumnMap = Record<string, number>;

function detectColumns(headerCells: string[]): ColumnMap | null {
  // Mirrors the backend header fold (`csv_import`: lower + spaces to
  // underscores) so a CSV the server would accept never fails browser preview.
  const normalized = headerCells.map((cell) => cell.trim().toLowerCase().replace(/\s+/g, '_'));
  const find = (keys: Set<string>) => normalized.findIndex((cell) => keys.has(cell));
  const map: ColumnMap = {
    sku: find(SKU_KEYS),
    name: find(NAME_KEYS),
    price: find(PRICE_KEYS),
    currency: find(CURRENCY_KEYS),
    url: find(URL_KEYS),
    aliases: find(ALIASES_KEYS),
    variant: find(VARIANT_KEYS),
  };
  for (const [key, aliases] of Object.entries(ATTRIBUTE_KEYS)) {
    map[`attr_${key}`] = find(aliases);
  }
  // A header row is recognized only if it names the sku or name column.
  return map.sku >= 0 || map.name >= 0 ? map : null;
}

/**
 * Parse CSV text into previewable product rows. A header row is REQUIRED
 * (matching the backend): headerless content returns a file-level error.
 */
export function parseProductCsv(raw: string): ParsedProductCsv {
  const matrix = tokenizeCsv(raw);
  if (matrix.length === 0) {
    return { rows: [], errors: ['The file is empty.'] };
  }

  const columns = detectColumns(matrix[0]);
  if (columns === null) {
    return {
      rows: [],
      errors: [
        'A header row is required. Expected columns like: name, sku, variant, category, price, currency, url, gtin.',
      ],
    };
  }

  const rows: ParsedProductRow[] = matrix.slice(1).map((cells, index) => {
    const cell = (col: number) => (col >= 0 ? (cells[col] ?? '').trim() : '');
    const sku = cell(columns.sku);
    const name = cell(columns.name) || sku;
    const rawPrice = cell(columns.price);
    const price = parsePrice(rawPrice);
    const warnings: string[] = [];
    const errors: string[] = [];
    if (!sku) errors.push('SKU is required.');
    if (rawPrice && price === null) {
      warnings.push(`Unparseable price "${rawPrice}" was cleared.`);
    }

    const attributes: Record<string, unknown> = {};
    for (const key of Object.keys(ATTRIBUTE_KEYS)) {
      const value = cell(columns[`attr_${key}`]);
      if (value) attributes[key] = value;
    }

    const variantName = cell(columns.variant);
    const aliasesCell = cell(columns.aliases);
    return {
      line: index + 1,
      input: {
        sku,
        name,
        aliases: aliasesCell ? splitAliases(aliasesCell) : [],
        variants: variantName ? [{ name: variantName }] : [],
        price,
        currency: cell(columns.currency).toUpperCase(),
        url: cell(columns.url),
        attributes,
      },
      warnings,
      errors,
    };
  });

  const errors: string[] = [];
  if (rows.length === 0) errors.push('No data rows were found.');

  return { rows, errors };
}

/** The importable subset (rows without fatal errors). */
export function validProductRows(parsed: ParsedProductCsv): ProductInput[] {
  return parsed.rows.filter((row) => row.errors.length === 0).map((row) => row.input);
}
