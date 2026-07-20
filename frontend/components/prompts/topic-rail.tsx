'use client';

import { ChevronDown, Plus, Trash2 } from 'lucide-react';
import { useId, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tooltip } from '@/components/ui/tooltip';
import type { Topic } from '@/lib/api/types';
import { cn } from '@/lib/utils';

const TOPICS_LOAD_ERROR = "Couldn't load topics. Check your connection and try again.";

/** Resolve the topic error copy: load failures outrank action failures. */
function topicErrorMessage(loadError?: boolean, actionError?: string | null): string | null {
  if (loadError) return TOPICS_LOAD_ERROR;
  return actionError ?? null;
}

/**
 * Topics selection (prompt library). Two responsive variants sharing one
 * selection model:
 *  - Desktop (md+): a contained, bordered `bg-panel` rail card listing the
 *    project's topics with per-status counts, an "All topics" bucket, an inline
 *    add-topic form, and per-topic delete.
 *  - Narrow (< md): a compact full-width Topics `<select>` stacked above the
 *    status tabs — the 240px rail track would crush the table, so the rail
 *    collapses to a selector, preserving the IA with no overlap.
 * Selection filters the prompt table; deleting a topic detaches its prompts
 * (backend `SET NULL`) — it never deletes them. Presentational — mutations
 * live in the library container.
 */
export function TopicRail({
  topics,
  selectedTopicId,
  onSelect,
  onCreate,
  onDelete,
  isCreating,
  loadError,
  actionError,
}: Readonly<{
  topics: Topic[];
  /** null = "All topics". */
  selectedTopicId: string | null;
  onSelect: (topicId: string | null) => void;
  /**
   * Create a topic. Returning a promise lets the rail keep the add form open
   * (with the typed name intact) when creation fails.
   */
  onCreate: (name: string) => Promise<void> | void;
  onDelete: (topic: Topic) => void;
  isCreating?: boolean;
  /** Set when the topics list failed to load. */
  loadError?: boolean;
  /** Rendered when a create/delete mutation fails. */
  actionError?: string | null;
}>) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await onCreate(trimmed);
      // Only reset on success — a failed create keeps the form open with the
      // typed name so the user can retry without re-typing.
      setName('');
      setAdding(false);
    } catch {
      // Error surfaced via `actionError`; leave the form populated.
    }
  };

  const errorBanner = topicErrorMessage(loadError, actionError) ? (
    <Alert tone="danger" className="mx-1 mb-1">
      {topicErrorMessage(loadError, actionError)}
    </Alert>
  ) : null;

  return (
    <>
      {/* Desktop rail: contained bordered surface that clips its own content
          so nothing from the right pane can overlap it. */}
      <nav
        aria-label="Topics"
        className="border-border bg-panel hidden min-w-0 content-start gap-1 overflow-hidden rounded-lg border p-2 md:sticky md:top-4 md:grid"
      >
        <div className="flex items-center justify-between px-1">
          <h3 className="text-secondary text-xs font-semibold tracking-wide uppercase">Topics</h3>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Add topic"
            onClick={() => setAdding((v) => !v)}
          >
            <Plus className="size-4" aria-hidden />
          </Button>
        </div>

        {errorBanner}

        {adding ? (
          <form
            className="flex items-center gap-1.5 px-1 pb-1"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <Input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Topic name"
              aria-label="Topic name"
              className="h-8"
            />
            <Button
              type="submit"
              variant="secondary"
              size="sm"
              disabled={isCreating || !name.trim()}
            >
              Add
            </Button>
          </form>
        ) : null}

        <TopicItem
          label="All topics"
          selected={selectedTopicId === null}
          onSelect={() => onSelect(null)}
        />
        {topics.map((topic) => (
          <TopicItem
            key={topic.id}
            label={topic.name}
            activeCount={topic.active_count}
            proposedCount={topic.proposed_count}
            selected={selectedTopicId === topic.id}
            onSelect={() => onSelect(topic.id)}
            onDelete={() => onDelete(topic)}
          />
        ))}
      </nav>

      {/* Narrow selector: full-width Topics <select> shown below the md
          breakpoint, stacked above the status tabs. */}
      <TopicSelect
        topics={topics}
        selectedTopicId={selectedTopicId}
        onSelect={onSelect}
        loadError={loadError}
        actionError={actionError}
      />
    </>
  );
}

/** Compact full-width Topics selector for narrow viewports (< md). */
function TopicSelect({
  topics,
  selectedTopicId,
  onSelect,
  loadError,
  actionError,
}: Readonly<{
  topics: Topic[];
  selectedTopicId: string | null;
  onSelect: (topicId: string | null) => void;
  loadError?: boolean;
  actionError?: string | null;
}>) {
  const labelId = useId();
  return (
    <div className="mb-1 grid gap-1.5 md:hidden">
      <span id={labelId} className="text-secondary text-xs font-semibold tracking-wide uppercase">
        Topics
      </span>
      <div className="relative">
        <select
          aria-labelledby={labelId}
          value={selectedTopicId ?? ''}
          onChange={(event) => onSelect(event.target.value === '' ? null : event.target.value)}
          className="focus-ring border-border-strong bg-panel text-foreground block h-[var(--control-height)] w-full appearance-none rounded-md border px-2.5 pr-9 text-sm"
        >
          <option value="">All topics</option>
          {topics.map((topic) => (
            <option key={topic.id} value={topic.id}>
              {topic.name}
            </option>
          ))}
        </select>
        <ChevronDown
          className="text-muted pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2"
          aria-hidden
        />
      </div>
      {topicErrorMessage(loadError, actionError) ? (
        <Alert tone="danger">{topicErrorMessage(loadError, actionError)}</Alert>
      ) : null}
    </div>
  );
}

function TopicItem({
  label,
  activeCount,
  proposedCount,
  selected,
  onSelect,
  onDelete,
}: Readonly<{
  label: string;
  activeCount?: number;
  proposedCount?: number;
  selected: boolean;
  onSelect: () => void;
  onDelete?: () => void;
}>) {
  return (
    <div
      className={cn(
        'group flex items-center gap-1 rounded-md pr-1',
        selected ? 'bg-accent-subtle' : 'hover:bg-background-alt',
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        className={cn(
          'focus-ring flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm',
          selected ? 'text-accent-text font-medium' : 'text-foreground',
        )}
      >
        <Tooltip content={label}>
          <span className="min-w-0 flex-1 truncate">{label}</span>
        </Tooltip>
        {typeof activeCount === 'number' ? (
          <span className="text-secondary shrink-0 text-xs tabular-nums">
            {activeCount}
            {proposedCount ? (
              <span className="text-accent-text" title={`${proposedCount} proposed`}>
                {' '}
                +{proposedCount}
              </span>
            ) : null}
          </span>
        ) : null}
      </button>
      {onDelete ? (
        <button
          type="button"
          aria-label={`Delete topic ${label}`}
          onClick={onDelete}
          className="focus-ring text-muted hover:text-danger-text shrink-0 rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
        >
          <Trash2 className="size-3.5" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
