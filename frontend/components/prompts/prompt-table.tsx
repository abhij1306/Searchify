'use client';

import { Archive, Check, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownSeparator,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TablePagination, useTablePage } from '@/components/ui/table-pagination';
import { Tooltip } from '@/components/ui/tooltip';
import type { Prompt, PromptStatus } from '@/lib/api/types';
import { intentLabels } from '@/lib/prompts/forms';

/** Rows per page on the prompt table (client-side; the list arrives whole). */
const PAGE_SIZE = 10;

/**
 * Prompt table (F7). Dense analytics table with columns text / theme / intent /
 * branded / enabled and per-row actions (edit, delete, enable/disable toggle,
 * and — when `onSetStatus` is wired — review transitions: accept a proposed
 * prompt, archive, or restore an archived one). Client-side pagination footer
 * (mono indicator + ghost buttons) per the midnight prompts frame. Purely
 * presentational — CRUD is delegated to callbacks owned by the page.
 */
export function PromptTable({
  prompts,
  onEdit,
  onDelete,
  onToggleEnabled,
  onSetStatus,
  busyId,
}: Readonly<{
  prompts: Prompt[];
  onEdit: (prompt: Prompt) => void;
  onDelete: (prompt: Prompt) => void;
  onToggleEnabled: (prompt: Prompt) => void;
  onSetStatus?: (prompt: Prompt, status: PromptStatus) => void;
  busyId?: string | null;
}>) {
  const { page, setPage, pageCount, from, to } = useTablePage(prompts.length, PAGE_SIZE);
  const pagedPrompts = prompts.slice(from - 1, to);

  return (
    <div className="border-border bg-panel shadow-card overflow-hidden rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Prompt</TableHead>
            <TableHead>Theme</TableHead>
            <TableHead>Intent</TableHead>
            <TableHead>Branded</TableHead>
            <TableHead>Enabled</TableHead>
            <TableHead className="w-16 text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {pagedPrompts.map((prompt) => (
            <TableRow key={prompt.id}>
              <TableCell className="max-w-[520px] min-w-[240px]">
                <Tooltip content={prompt.text}>
                  <span className="text-foreground line-clamp-2 block">{prompt.text}</span>
                </Tooltip>
              </TableCell>
              <TableCell className="max-w-[180px]">
                {prompt.theme ? (
                  <Tooltip content={prompt.theme}>
                    <Badge variant="neutral" className="max-w-full">
                      <span className="min-w-0 truncate">{prompt.theme}</span>
                    </Badge>
                  </Tooltip>
                ) : (
                  <span className="text-subtle">—</span>
                )}
              </TableCell>
              <TableCell className="text-secondary">{intentLabels[prompt.intent]}</TableCell>
              <TableCell>
                {prompt.branded ? (
                  <Badge variant="status" value="info">
                    Branded
                  </Badge>
                ) : (
                  <span className="text-subtle">—</span>
                )}
              </TableCell>
              <TableCell>
                <button
                  type="button"
                  role="switch"
                  aria-checked={prompt.enabled}
                  aria-label={`${prompt.enabled ? 'Disable' : 'Enable'} prompt`}
                  disabled={busyId === prompt.id}
                  onClick={() => onToggleEnabled(prompt)}
                  className="focus-ring border-border-strong bg-background-alt aria-checked:border-accent aria-checked:bg-accent group inline-flex h-5 w-9 items-center rounded-full border px-0.5 transition-colors disabled:opacity-50 aria-checked:justify-end"
                >
                  <span
                    className="bg-muted group-aria-checked:bg-accent-fg size-4 rounded-full shadow-sm"
                    aria-hidden
                  />
                </button>
              </TableCell>
              <TableCell className="text-right">
                <Dropdown>
                  <DropdownTrigger asChild>
                    <Button variant="ghost" size="icon" aria-label="Prompt actions">
                      <MoreHorizontal className="size-4" aria-hidden />
                    </Button>
                  </DropdownTrigger>
                  <DropdownContent align="end">
                    {onSetStatus && prompt.status === 'proposed' ? (
                      <DropdownItem onSelect={() => onSetStatus(prompt, 'active')}>
                        <Check className="size-4" aria-hidden />
                        Accept
                      </DropdownItem>
                    ) : null}
                    {onSetStatus && prompt.status === 'archived' ? (
                      <DropdownItem onSelect={() => onSetStatus(prompt, 'active')}>
                        <Check className="size-4" aria-hidden />
                        Restore
                      </DropdownItem>
                    ) : null}
                    <DropdownItem onSelect={() => onEdit(prompt)}>
                      <Pencil className="size-4" aria-hidden />
                      Edit
                    </DropdownItem>
                    {onSetStatus && prompt.status !== 'archived' ? (
                      <DropdownItem onSelect={() => onSetStatus(prompt, 'archived')}>
                        <Archive className="size-4" aria-hidden />
                        Archive
                      </DropdownItem>
                    ) : null}
                    <DropdownSeparator className="bg-border-subtle my-1 h-px" />
                    <DropdownItem
                      onSelect={() => onDelete(prompt)}
                      className="text-danger-text data-[highlighted]:bg-danger-bg"
                    >
                      <Trash2 className="size-4" aria-hidden />
                      Delete
                    </DropdownItem>
                  </DropdownContent>
                </Dropdown>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <TablePagination
        page={page}
        pageCount={pageCount}
        from={from}
        to={to}
        total={prompts.length}
        noun="prompts"
        onPageChange={setPage}
      />
    </div>
  );
}
