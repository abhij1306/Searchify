'use client';

import { Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { Topic } from '@/lib/api/types';
import { cn } from '@/lib/utils';

/**
 * Topics rail (prompt library). Lists the project's topics with per-status
 * prompt counts, an "All topics" bucket, an inline add-topic form, and a
 * per-topic delete. Selection filters the prompt table; deleting a topic
 * detaches its prompts (backend `SET NULL`) — it never deletes them.
 * Presentational — mutations live in the library container.
 */
export function TopicRail({
  topics,
  selectedTopicId,
  onSelect,
  onCreate,
  onDelete,
  isCreating,
}: Readonly<{
  topics: Topic[];
  /** null = "All topics". */
  selectedTopicId: string | null;
  onSelect: (topicId: string | null) => void;
  onCreate: (name: string) => void;
  onDelete: (topic: Topic) => void;
  isCreating?: boolean;
}>) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    onCreate(trimmed);
    setName('');
    setAdding(false);
  };

  return (
    <nav aria-label="Topics" className="grid content-start gap-1">
      <div className="flex items-center justify-between px-1">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-secondary">Topics</h3>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Add topic"
          onClick={() => setAdding((v) => !v)}
        >
          <Plus className="size-4" aria-hidden />
        </Button>
      </div>

      {adding ? (
        <form
          className="flex items-center gap-1.5 px-1 pb-1"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
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
          <Button type="submit" variant="secondary" size="sm" disabled={isCreating || !name.trim()}>
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
          selected ? 'font-medium text-accent-text' : 'text-foreground',
        )}
      >
        <span className="min-w-0 flex-1 truncate" title={label}>
          {label}
        </span>
        {typeof activeCount === 'number' ? (
          <span className="shrink-0 text-xs tabular-nums text-secondary">
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
          className="focus-ring shrink-0 rounded p-1 text-muted opacity-0 transition-opacity hover:text-danger-text focus-visible:opacity-100 group-hover:opacity-100"
        >
          <Trash2 className="size-3.5" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
