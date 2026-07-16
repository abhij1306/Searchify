'use client';

import { Check, ChevronsUpDown } from 'lucide-react';

import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownSeparator,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { useProjectContext } from '@/lib/project/project-context';
import { cn } from '@/lib/utils';

/** Two-letter avatar initials from a brand/project name. */
function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/**
 * ProjectSwitcher (F5) — brand avatar + active project name with a dropdown of
 * all projects in the workspace. Selecting one updates the project context
 * (which persists the choice and re-scopes the API client's workspace header).
 */
export function ProjectSwitcher({ className }: Readonly<{ className?: string }>) {
  const { projects, activeProject, activeProjectId, setActiveProjectId, isLoading } =
    useProjectContext();

  const label = activeProject?.brand_name ?? activeProject?.name ?? 'No project';
  const avatar = activeProject ? initials(label) : '—';

  return (
    <Dropdown>
      <DropdownTrigger
        className={cn(
          'focus-ring flex w-full items-center gap-2.5 rounded-md border border-border bg-panel px-2.5 py-1.5 text-left transition-colors hover:bg-background-alt disabled:pointer-events-none disabled:opacity-50',
          className,
        )}
        disabled={isLoading || projects.length === 0}
      >
        <span
          aria-hidden
          className="flex size-7 shrink-0 items-center justify-center rounded-md bg-accent-soft text-2xs font-bold uppercase text-accent-text"
        >
          {avatar}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold text-foreground">{label}</span>
          <span className="block truncate text-2xs text-muted">
            {isLoading ? 'Loading…' : `${projects.length} project${projects.length === 1 ? '' : 's'}`}
          </span>
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-muted" aria-hidden />
      </DropdownTrigger>
      <DropdownContent align="start" className="w-[calc(240px-2rem)]">
        <DropdownLabel>Projects</DropdownLabel>
        <DropdownSeparator className="my-1 h-px bg-border-subtle" />
        {projects.map((project) => {
          const selected = project.id === activeProjectId;
          return (
            <DropdownItem
              key={project.id}
              onSelect={() => setActiveProjectId(project.id)}
              className={selected ? 'text-accent-text' : undefined}
            >
              <span
                aria-hidden
                className="flex size-6 shrink-0 items-center justify-center rounded bg-accent-soft text-2xs font-bold uppercase text-accent-text"
              >
                {initials(project.brand_name || project.name)}
              </span>
              <span className="min-w-0 flex-1 truncate">{project.brand_name || project.name}</span>
              {selected ? <Check className="size-4 shrink-0 text-accent" aria-hidden /> : null}
            </DropdownItem>
          );
        })}
      </DropdownContent>
    </Dropdown>
  );
}
