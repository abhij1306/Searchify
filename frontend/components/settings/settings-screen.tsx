'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter, useSearchParams } from 'next/navigation';
import { Trash2 } from 'lucide-react';
import { useRef, useState, type KeyboardEvent } from 'react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog } from '@/components/ui/dialog';
import { ThemeToggle } from '@/components/ui/theme-toggle';
import { ProviderSettings } from '@/components/settings/provider-settings';
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
    <div className="border-border-subtle grid grid-cols-[minmax(0,180px)_1fr] items-center gap-4 border-b py-3.5 last:border-b-0 last:pb-0">
      <dt className="text-secondary text-sm font-medium">{label}</dt>
      <dd className={mono ? 'mono text-secondary text-xs' : 'text-foreground text-sm'}>
        {children}
      </dd>
    </div>
  );
}

const SETTINGS_TABS = [
  { id: 'account', label: 'Account' },
  { id: 'providers', label: 'Provider Settings' },
  { id: 'danger', label: 'Danger Zone' },
] as const;

type SettingsTab = (typeof SETTINGS_TABS)[number]['id'];

const TAB_ID = (tab: SettingsTab) => `settings-tab-${tab}`;
const PANEL_ID = (tab: SettingsTab) => `settings-panel-${tab}`;

/**
 * SettingsScreen — tabbed settings (Account / Provider Settings / Danger Zone),
 * following the WAI-ARIA tabs idiom used by the Visibility workspace (roving
 * tabindex, Arrow/Home/End navigation, `aria-selected`, labelled panels).
 *
 * - **Account**: read-only session details from `GET /auth/me` via
 *   `useSessionUser` (no account-mutation endpoints exist) plus the
 *   appearance/theme control. `role` is the ACCOUNT-level role (free-form,
 *   defaults to `"user"`) and `created_at` is when the account was created —
 *   neither is a workspace membership role.
 * - **Provider Settings**: the BYOK provider configuration (formerly the
 *   standalone `/providers` page), rendered by `ProviderSettings`.
 * - **Danger Zone**: deletes the active project (backend cascades to all child
 *   data) behind a confirmation dialog — the one mutation on this screen.
 */
export function SettingsScreen() {
  const user = useSessionUser();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeProject, projects, setActiveProjectId } = useProjectContext();
  const createdLabel = formatTimestamp(user.created_at);
  const updatedLabel = formatTimestamp(user.updated_at);
  // Deep-linkable initial tab (`/settings?tab=providers` from the onboarding
  // card); invalid/absent values fall back to Account.
  const searchParams = useSearchParams();
  const requestedTab = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState<SettingsTab>(() =>
    SETTINGS_TABS.some((tab) => tab.id === requestedTab)
      ? (requestedTab as SettingsTab)
      : 'account',
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

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

  const activeIndex = SETTINGS_TABS.findIndex((tab) => tab.id === activeTab);

  function focusTab(index: number) {
    const tab = SETTINGS_TABS[index];
    if (!tab) return;
    setActiveTab(tab.id);
    // Move DOM focus to the newly selected tab (roving tabindex + focus xfer).
    tabRefs.current[tab.id]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const last = SETTINGS_TABS.length - 1;
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        event.preventDefault();
        focusTab(activeIndex >= last ? 0 : activeIndex + 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        event.preventDefault();
        focusTab(activeIndex <= 0 ? last : activeIndex - 1);
        break;
      case 'Home':
        event.preventDefault();
        focusTab(0);
        break;
      case 'End':
        event.preventDefault();
        focusTab(last);
        break;
      default:
        break;
    }
  }

  return (
    <div className="grid gap-5">
      <div
        role="tablist"
        aria-label="Settings sections"
        aria-orientation="horizontal"
        className="border-border flex [scrollbar-width:none] flex-nowrap gap-0 overflow-x-auto border-b-2 [&::-webkit-scrollbar]:hidden"
      >
        {SETTINGS_TABS.map((tab) => {
          const selected = tab.id === activeTab;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                tabRefs.current[tab.id] = node;
              }}
              type="button"
              role="tab"
              id={TAB_ID(tab.id)}
              aria-selected={selected}
              aria-controls={PANEL_ID(tab.id)}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActiveTab(tab.id)}
              onKeyDown={onKeyDown}
              className={cn(
                'focus-ring -mb-0.5 shrink-0 border-b-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors',
                selected
                  ? 'border-accent text-foreground font-semibold'
                  : 'text-secondary hover:text-foreground border-transparent',
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* All panels stay mounted (hidden when inactive) so every tab's
          aria-controls resolves and panel state survives tab switches. */}
      <div
        role="tabpanel"
        id={PANEL_ID('account')}
        aria-labelledby={TAB_ID('account')}
        hidden={activeTab !== 'account'}
        tabIndex={0}
        className="focus-ring outline-none"
      >
        <div className="grid max-w-2xl gap-6">
          <Card>
            <CardHeader>
              <CardTitle>Account</CardTitle>
              <CardDescription>
                Your Searchify account details. Account fields are read-only here and shown for
                reference.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-3.5">
                <span
                  aria-hidden
                  className="bg-accent-soft text-accent-text flex size-11 shrink-0 items-center justify-center rounded-full text-sm font-bold uppercase"
                >
                  {emailInitials(user.email)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-foreground truncate text-sm font-semibold">{user.email}</div>
                  <div className="text-muted mt-0.5 text-sm">
                    Account role: <span className="capitalize">{user.role}</span>
                  </div>
                </div>
                <Badge variant="status" value={user.is_active ? 'success' : 'danger'}>
                  {user.is_active ? 'Active' : 'Inactive'}
                </Badge>
              </div>

              <dl className="border-border-subtle mt-5 border-t">
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

          <Card>
            <CardHeader>
              <CardTitle>Appearance</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between gap-6">
                <div>
                  <div className="text-secondary text-sm font-medium">Theme</div>
                  <p className="text-muted mt-1 text-xs">
                    Applies to this browser and syncs with the top-bar toggle.
                  </p>
                </div>
                <ThemeToggle />
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <div
        role="tabpanel"
        id={PANEL_ID('providers')}
        aria-labelledby={TAB_ID('providers')}
        hidden={activeTab !== 'providers'}
        tabIndex={0}
        className="focus-ring outline-none"
      >
        <ProviderSettings />
      </div>

      <div
        role="tabpanel"
        id={PANEL_ID('danger')}
        aria-labelledby={TAB_ID('danger')}
        hidden={activeTab !== 'danger'}
        tabIndex={0}
        className="focus-ring outline-none"
      >
        <div className="grid max-w-2xl gap-6">
          <Card>
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
                      <div className="text-foreground truncate text-sm font-semibold">
                        {activeProject.name}
                      </div>
                      <p className="text-muted mt-0.5 text-xs">Brand: {activeProject.brand_name}</p>
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
                <p className="text-muted text-sm">No project selected.</p>
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
        <p className="text-secondary text-sm">
          This permanently deletes the project and all of its prompts, topics, audits, visibility
          history, and generated content. This cannot be undone.
        </p>
      </Dialog>
    </div>
  );
}
