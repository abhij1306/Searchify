'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowRight,
  ExternalLink,
  Moon,
  Plug,
  Settings2,
  Trash2,
  User,
} from 'lucide-react';
import { useState, type ComponentType } from 'react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog } from '@/components/ui/dialog';
import { ThemeToggle } from '@/components/ui/theme-toggle';
import { PageTitle } from '@/components/ui/typography';
import { projectsApi } from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import { useSessionUser } from '@/lib/auth/session-guard';
import { useProjectContext } from '@/lib/project/project-context';
import { cn, emailInitials } from '@/lib/utils';

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong. Please try again.';
}

/** Human-readable label for a timestamp (falls back to the raw value).
 * Explicit locale + UTC keep server and client output identical, so the
 * SSR markup matches during hydration. */
function formatTimestamp(timestamp: string | undefined): string | undefined {
  if (!timestamp) return undefined;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  });
}

/** One read-only account detail row: label + value. */
function DetailRow({
  label,
  children,
  mono = false,
}: Readonly<{ label: string; children: React.ReactNode; mono?: boolean }>) {
  return (
    <div className="grid grid-cols-[minmax(0,180px)_1fr] items-center gap-4 border-b border-border-subtle py-3.5 last:border-b-0 last:pb-0">
      <dt className="text-sm font-medium text-secondary">{label}</dt>
      <dd className={mono ? 'mono text-xs text-secondary' : 'text-sm text-foreground'}>{children}</dd>
    </div>
  );
}

/** In-page sub-nav anchor (scrolls to a settings section). */
function SubnavAnchor({
  href,
  icon: Icon,
  active,
  onSelect,
  children,
}: Readonly<{
  href: string;
  icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean; strokeWidth?: number }>;
  active: boolean;
  onSelect: () => void;
  children: React.ReactNode;
}>) {
  return (
    <a
      href={href}
      aria-current={active ? 'page' : undefined}
      onClick={onSelect}
      className={cn(
        'focus-ring flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors',
        active
          ? 'bg-accent-subtle text-accent-text'
          : 'text-secondary hover:bg-background-alt hover:text-foreground',
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden strokeWidth={2} />
      <span className="truncate">{children}</span>
    </a>
  );
}

/** Sub-nav link out to another route (Model providers / Project setup). */
function SubnavLink({
  href,
  icon: Icon,
  children,
}: Readonly<{
  href: string;
  icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean; strokeWidth?: number }>;
  children: React.ReactNode;
}>) {
  return (
    <Link
      href={href}
      className="focus-ring flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium text-secondary transition-colors hover:bg-background-alt hover:text-foreground"
    >
      <Icon className="size-4 shrink-0" aria-hidden strokeWidth={2} />
      <span className="truncate">{children}</span>
      <ExternalLink className="ml-auto size-3.5 shrink-0 text-subtle" aria-hidden strokeWidth={2} />
    </Link>
  );
}

/**
 * SettingsScreen — a basic, read-only account view for the authenticated user,
 * laid out as a two-column sub-navigation (Account, Appearance, Model providers)
 * beside stacked section cards. Collapses to a single column on narrow viewports.
 *
 * All account fields come from the live session (`GET /auth/me` via
 * `useSessionUser`); there are no account-mutation endpoints, so nothing here is
 * editable. `role` is the ACCOUNT-level role (free-form, defaults to `"user"`)
 * and `created_at` is when the account was created — neither is a workspace
 * membership role. Provider/project configuration is surfaced as links only (no
 * fabricated per-provider status), pointing at the existing `/providers` and
 * `/setup` screens where those are actually managed. The one mutation here is
 * the Danger-zone card, which deletes the active project (backend cascades to
 * all child data) behind a confirmation dialog.
 */
export function SettingsScreen() {
  const user = useSessionUser();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeProject, projects, setActiveProjectId } = useProjectContext();
  const createdLabel = formatTimestamp(user.created_at);
  const updatedLabel = formatTimestamp(user.updated_at);
  const [activeSection, setActiveSection] = useState<'account' | 'appearance' | 'danger'>(
    'account',
  );
  const [confirmOpen, setConfirmOpen] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: (projectId: string) => projectsApi.deleteProject(projectId),
    onSuccess: async (_data, deletedId) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      // Switch explicitly so the deleted project never flashes as "active"
      // while the context's useMemo re-resolves. Route to setup when the
      // workspace has no projects left.
      const next = projects.find((project) => project.id !== deletedId) ?? null;
      if (next) {
        setActiveProjectId(next.id);
        setConfirmOpen(false);
      } else {
        router.replace('/setup');
      }
    },
  });

  return (
    <div className="space-y-6">
      <PageTitle kicker="Account">Settings</PageTitle>
      <p className="max-w-xl text-sm text-secondary">
        Your Searchify account details and preferences. Account fields are read-only here and shown
        for reference.
      </p>

      <div className="grid items-start gap-8 lg:grid-cols-[200px_minmax(0,1fr)]">
        {/* Sub-navigation */}
        <nav aria-label="Settings" className="flex flex-col gap-4 lg:sticky lg:top-0">
          <div className="flex flex-col gap-1">
            <p className="px-2.5 text-2xs font-semibold uppercase tracking-wide text-muted">
              Account
            </p>
            <SubnavAnchor
              href="#account"
              icon={User}
              active={activeSection === 'account'}
              onSelect={() => setActiveSection('account')}
            >
              Account
            </SubnavAnchor>
            <SubnavAnchor
              href="#appearance"
              icon={Moon}
              active={activeSection === 'appearance'}
              onSelect={() => setActiveSection('appearance')}
            >
              Appearance
            </SubnavAnchor>
          </div>
          <div className="flex flex-col gap-1">
            <p className="px-2.5 text-2xs font-semibold uppercase tracking-wide text-muted">
              Configuration
            </p>
            <SubnavLink href="/providers" icon={Plug}>
              Model providers
            </SubnavLink>
            <SubnavLink href="/setup" icon={Settings2}>
              Project setup
            </SubnavLink>
          </div>
          <div className="flex flex-col gap-1">
            <p className="px-2.5 text-2xs font-semibold uppercase tracking-wide text-muted">
              Project
            </p>
            <SubnavAnchor
              href="#danger"
              icon={Trash2}
              active={activeSection === 'danger'}
              onSelect={() => setActiveSection('danger')}
            >
              Danger zone
            </SubnavAnchor>
          </div>
        </nav>

        {/* Section cards */}
        <div className="grid max-w-2xl gap-6">
          <Card id="account" className="scroll-mt-4">
            <CardHeader>
              <CardTitle>Account</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-3.5">
                <span
                  aria-hidden
                  className="flex size-11 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-bold uppercase text-accent-text"
                >
                  {emailInitials(user.email)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-foreground">{user.email}</div>
                  <div className="mt-0.5 text-sm text-muted">
                    Account role: <span className="capitalize">{user.role}</span>
                  </div>
                </div>
                <Badge variant="status" value={user.is_active ? 'success' : 'danger'}>
                  {user.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </div>

              <dl className="mt-5 border-t border-border-subtle">
                <DetailRow label="Email">{user.email}</DetailRow>
                <DetailRow label="Account role">
                  <Badge variant="neutral">{user.role}</Badge>
                </DetailRow>
                <DetailRow label="Account status">
                  <Badge variant="status" value={user.is_active ? 'success' : 'danger'}>
                    {user.is_active ? 'Active' : 'Inactive'}
                  </Badge>
                </DetailRow>
                {createdLabel ? (
                  <DetailRow label="Account created" mono>
                    {createdLabel}
                  </DetailRow>
                ) : null}
                {updatedLabel ? (
                  <DetailRow label="Last updated" mono>
                    {updatedLabel}
                  </DetailRow>
                ) : null}
                {user.id ? (
                  <DetailRow label="User ID" mono>
                    {user.id}
                  </DetailRow>
                ) : null}
              </dl>
            </CardContent>
          </Card>

          <Card id="appearance" className="scroll-mt-4">
            <CardHeader>
              <CardTitle>Appearance</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between gap-6">
                <div>
                  <div className="text-sm font-medium text-secondary">Theme</div>
                  <p className="mt-1 text-xs text-muted">
                    Applies to this browser and syncs with the top-bar toggle.
                  </p>
                </div>
                <ThemeToggle />
              </div>
            </CardContent>
          </Card>

          <Card id="providers" className="scroll-mt-4">
            <CardHeader>
              <CardTitle>Configuration</CardTitle>
              <CardDescription>
                Manage where Searchify runs from. Model provider keys are write-only — Searchify
                never displays a stored secret.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-2">
              <Button asChild variant="primary">
                <Link href="/providers">
                  Open Provider Settings
                  <ArrowRight className="size-4 shrink-0" aria-hidden strokeWidth={2} />
                </Link>
              </Button>
              <Button asChild variant="secondary">
                <Link href="/setup">
                  Open Project Setup
                  <ArrowRight className="size-4 shrink-0" aria-hidden strokeWidth={2} />
                </Link>
              </Button>
            </CardContent>
          </Card>

          <Card id="danger" className="scroll-mt-4">
            <CardHeader>
              <CardTitle>Danger zone</CardTitle>
              <CardDescription>
                Permanently delete the active project and everything inside it.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4">
              {activeProject ? (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-foreground">
                        {activeProject.name}
                      </div>
                      <p className="mt-0.5 text-xs text-muted">
                        Brand: {activeProject.brand_name}
                      </p>
                    </div>
                    <Button
                      variant="destructive"
                      onClick={() => setConfirmOpen(true)}
                      disabled={deleteMutation.isPending}
                    >
                      <Trash2 className="size-4 shrink-0" aria-hidden strokeWidth={2} />
                      Delete project
                    </Button>
                  </div>
                  <Alert tone="danger">
                    Deleting a project removes all of its prompts, topics, audits, visibility
                    history, and generated content. This cannot be undone.
                  </Alert>
                  {deleteMutation.isError ? (
                    <Alert tone="danger">{errorMessage(deleteMutation.error)}</Alert>
                  ) : null}
                </>
              ) : (
                <p className="text-sm text-muted">No project selected.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Dialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (!deleteMutation.isPending) setConfirmOpen(open);
        }}
        title="Delete project"
        description={activeProject ? `Delete "${activeProject.name}"?` : undefined}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => activeProject && deleteMutation.mutate(activeProject.id)}
              disabled={deleteMutation.isPending || !activeProject}
            >
              {deleteMutation.isPending ? 'Deleting…' : 'Delete project'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-secondary">
          This permanently deletes the project and all of its prompts, topics, audits, visibility
          history, and generated content. This cannot be undone.
        </p>
      </Dialog>
    </div>
  );
}
