'use client';

import { useMemo, useRef, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { PromptInput } from '@/lib/api/prompts';
import { parsePromptCsv, validRows, type ParsedCsv } from '@/lib/prompts/csv';
import { intentLabels } from '@/lib/prompts/forms';

/**
 * CSV import dialog (F7). The file is parsed + validated in the browser and the
 * parsed rows are previewed (with per-row warnings/errors) BEFORE anything is
 * persisted. On confirm, only the importable rows are handed to `onImport`,
 * which posts them to the B3 `/prompt-sets/{id}/import` endpoint.
 */
export function CsvImportDialog({
  open,
  onOpenChange,
  onImport,
  isImporting,
  error,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImport: (rows: PromptInput[]) => Promise<void> | void;
  isImporting?: boolean;
  error?: string;
}>) {
  const [parsed, setParsed] = useState<ParsedCsv | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const importable = useMemo(() => (parsed ? validRows(parsed) : []), [parsed]);
  const errorCount = parsed ? parsed.rows.filter((row) => row.errors.length > 0).length : 0;

  const reset = () => {
    setParsed(null);
    setFileName(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  const readFileText = (file: File) =>
    typeof file.text === 'function'
      ? file.text()
      : new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result ?? ''));
          reader.onerror = () => reject(reader.error);
          reader.readAsText(file);
        });

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setFileName(file.name);
    const text = await readFileText(file);
    setParsed(parsePromptCsv(text));
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
      title="Import prompts from CSV"
      description="Columns: text, theme, intent, branded, enabled (header row optional)."
      className="w-[820px]"
      footer={
        <>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={confirm}
            disabled={isImporting || importable.length === 0}
          >
            {isImporting
              ? 'Importing…'
              : `Import ${importable.length} prompt${importable.length === 1 ? '' : 's'}`}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <label className="grid gap-1.5">
          <span className="text-xs font-medium text-secondary">CSV file</span>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            aria-label="CSV file"
            onChange={(event) => void handleFile(event.target.files?.[0])}
            className="focus-ring block w-full rounded-md border border-border-strong bg-panel px-2.5 py-1.5 text-sm text-foreground file:mr-3 file:rounded file:border-0 file:bg-background-alt file:px-2.5 file:py-1 file:text-sm file:text-foreground"
          />
        </label>

        {parsed && parsed.errors.length > 0 ? (
          <Alert tone="danger">{parsed.errors.join(' ')}</Alert>
        ) : null}

        {parsed && parsed.rows.length > 0 ? (
          <div className="grid gap-2">
            <div className="flex items-center gap-3 text-sm text-secondary">
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

            <div className="max-h-[340px] overflow-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Row</TableHead>
                    <TableHead>Text</TableHead>
                    <TableHead>Theme</TableHead>
                    <TableHead>Intent</TableHead>
                    <TableHead>Branded</TableHead>
                    <TableHead>Enabled</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {parsed.rows.map((row) => {
                    const invalid = row.errors.length > 0;
                    return (
                      <TableRow key={row.line} className={invalid ? 'opacity-60' : undefined}>
                        <TableCell numeric className="text-muted">
                          {row.line}
                        </TableCell>
                        <TableCell className="max-w-[280px] truncate">{row.input.text || '—'}</TableCell>
                        <TableCell>{row.input.theme || '—'}</TableCell>
                        <TableCell>{intentLabels[row.input.intent]}</TableCell>
                        <TableCell>{row.input.branded ? 'Yes' : 'No'}</TableCell>
                        <TableCell>{row.input.enabled ? 'Yes' : 'No'}</TableCell>
                        <TableCell>
                          {invalid ? (
                            <span className="text-xs text-danger-text">{row.errors.join(' ')}</span>
                          ) : row.warnings.length > 0 ? (
                            <span className="text-xs text-warning-text">{row.warnings.join(' ')}</span>
                          ) : (
                            <span className="text-xs text-success-text">Ready</span>
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
