'use client';

import { useMemo, useRef, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { ProductInput } from '@/lib/api/products';
import { parseProductCsv, validProductRows, type ParsedProductCsv } from '@/lib/products/csv';

/** Read a File as text, falling back to FileReader where `File.text` is absent (jsdom). */
const readFileText = (file: File) =>
  typeof file.text === 'function'
    ? file.text()
    : new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ''));
        reader.onerror = () => reject(reader.error);
        reader.readAsText(file);
      });

/**
 * Product CSV import dialog (mirrors the prompts CSV import dialog). The file
 * is parsed + validated in the browser and previewed (with per-row
 * warnings/errors) BEFORE anything is persisted. On confirm, only the
 * importable rows are handed to `onImport`, which posts them to the
 * `/projects/{id}/products/import` endpoint. A header row is required —
 * matching the backend.
 */
export function ProductImportDialog({
  open,
  onOpenChange,
  onImport,
  isImporting,
  error,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImport: (rows: ProductInput[]) => Promise<void> | void;
  isImporting?: boolean;
  error?: string;
}>) {
  const [parsed, setParsed] = useState<ParsedProductCsv | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const importable = useMemo(() => (parsed ? validProductRows(parsed) : []), [parsed]);
  const errorCount = parsed ? parsed.rows.filter((row) => row.errors.length > 0).length : 0;

  const reset = () => {
    setParsed(null);
    setFileName(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setFileName(file.name);
    const text = await readFileText(file);
    setParsed(parseProductCsv(text));
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const confirm = async () => {
    if (importable.length === 0) return;
    await onImport(importable);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Import products from CSV"
      description="Columns: name, sku, variant, category, price, currency, url, gtin (header row required)."
      className="w-[860px]"
      footer={
        <>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => void confirm()}
            disabled={isImporting || importable.length === 0}
          >
            {isImporting
              ? 'Importing…'
              : `Import ${importable.length} product${importable.length === 1 ? '' : 's'}`}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <label className="grid gap-1.5">
          <span className="text-secondary text-xs font-medium">CSV file</span>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            aria-label="CSV file"
            onChange={(event) => void handleFile(event.target.files?.[0])}
            className="focus-ring border-border bg-well text-foreground file:bg-background-alt file:text-foreground block w-full rounded-md border px-2.5 py-1.5 text-sm file:mr-3 file:rounded file:border-0 file:px-2.5 file:py-1 file:text-sm"
          />
        </label>

        {parsed && parsed.errors.length > 0 ? (
          <Alert tone="danger">{parsed.errors.join(' ')}</Alert>
        ) : null}

        {parsed && parsed.rows.length > 0 ? (
          <div className="grid gap-2">
            <div className="text-secondary flex items-center gap-3 text-sm">
              <span>
                Parsed <strong className="text-foreground">{parsed.rows.length}</strong> row
                {parsed.rows.length === 1 ? '' : 's'}
                {fileName ? ` from ${fileName}` : ''}.
              </span>
              {errorCount > 0 ? (
                <Badge variant="status" value="danger">
                  {errorCount} skipped
                </Badge>
              ) : null}
            </div>

            <div className="border-border max-h-[340px] overflow-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Row</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>SKU</TableHead>
                    <TableHead>Variant</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Price</TableHead>
                    <TableHead>Currency</TableHead>
                    <TableHead>URL</TableHead>
                    <TableHead>GTIN</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {parsed.rows.map((row) => {
                    const invalid = row.errors.length > 0;
                    const attributes = row.input.attributes ?? {};
                    return (
                      <TableRow key={row.line} className={invalid ? 'opacity-60' : undefined}>
                        <TableCell numeric className="text-muted">
                          {row.line}
                        </TableCell>
                        <TableCell className="max-w-[180px] truncate">
                          {row.input.name || '—'}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{row.input.sku || '—'}</TableCell>
                        <TableCell className="max-w-[140px] truncate">
                          {row.input.variants?.[0]?.name || '—'}
                        </TableCell>
                        <TableCell>{String(attributes.category ?? '') || '—'}</TableCell>
                        <TableCell numeric>
                          {row.input.price !== null && row.input.price !== undefined
                            ? row.input.price
                            : '—'}
                        </TableCell>
                        <TableCell>{row.input.currency || '—'}</TableCell>
                        <TableCell className="max-w-[160px] truncate">
                          {row.input.url || '—'}
                        </TableCell>
                        <TableCell>{String(attributes.gtin ?? '') || '—'}</TableCell>
                        <TableCell>
                          {invalid ? (
                            <span className="text-danger-text text-xs">{row.errors.join(' ')}</span>
                          ) : row.warnings.length > 0 ? (
                            <span className="text-warning-text text-xs">
                              {row.warnings.join(' ')}
                            </span>
                          ) : (
                            <span className="text-success-text text-xs">Ready</span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
