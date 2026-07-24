import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Project } from '@/lib/api/types';
import { assignLocation } from '@/lib/navigate';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

// Stub next/navigation (Link/useSearchParams in jsdom). `search` is mutable per
// test so the C2 callback params (?connected= / ?error=) can be exercised.
let search = '';
const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace, prefetch: vi.fn() }),
  usePathname: () => '/settings',
  useSearchParams: () => new URLSearchParams(search),
}));

// Connect/Reconnect hard-navigates to the OAuth start 302 endpoint through the
// lib/navigate seam (jsdom can't stub Location#assign).
vi.mock('@/lib/navigate', () => ({ assignLocation: vi.fn() }));
const assignMock = vi.mocked(assignLocation);

const WS = '11111111-1111-4111-8111-111111111111';
const GRANT_GOOGLE = '22222222-2222-4222-8222-222222222222';
const GRANT_MS = '55555555-5555-4555-8555-555555555555';
const CONN_GSC = '33333333-3333-4333-8333-333333333333';
const CONN_GA4 = '44444444-4444-4444-8444-444444444444';
const CONN_BING = '66666666-6666-4666-8666-666666666666';
const SYNC = '77777777-7777-4777-8777-777777777777';

const activeProject = {
  id: '88888888-8888-4888-8888-888888888888',
  workspace_id: WS,
  name: 'Example.com',
  brand_name: 'Example',
} as unknown as Project;
vi.mock('@/lib/project/project-context', () => ({
  useProjectContext: () => ({
    projects: [activeProject],
    activeProject,
    activeProjectId: activeProject.id,
    setActiveProjectId: vi.fn(),
    isLoading: false,
  }),
}));

import { IntegrationSettings } from './integration-settings';

// Fixture shapes from the F1 contract suite (lib/api/integrations.test.ts):
// a connection joined to grant status + granted scopes, never a token field.
function connection(overrides: Record<string, unknown> = {}) {
  return {
    id: CONN_GSC,
    workspace_id: WS,
    grant_id: GRANT_GOOGLE,
    provider: 'gsc',
    label: 'example.com GSC',
    account_ref: 'sc-domain:example.com',
    grant_status: 'connected',
    granted_scopes: ['https://www.googleapis.com/auth/webmasters.readonly'],
    last_synced_at: '2026-07-23T04:12:00Z',
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-23T04:12:00Z',
    ...overrides,
  };
}

const gscConnection = connection();
const ga4Connection = connection({
  id: CONN_GA4,
  provider: 'ga4',
  label: 'example.com GA4',
  account_ref: 'properties/123456789',
  granted_scopes: ['https://www.googleapis.com/auth/analytics.readonly'],
  last_synced_at: '2026-07-23T04:09:00Z',
});
const bingConnection = connection({
  id: CONN_BING,
  grant_id: GRANT_MS,
  provider: 'bing',
  label: 'example.com Bing',
  account_ref: 'https://example.com/',
  grant_status: 'needs_reauth',
  granted_scopes: ['bing.webmaster.readonly'],
  last_synced_at: '2026-07-21T22:41:00Z',
});

function syncRun(overrides: Record<string, unknown> = {}) {
  return {
    id: SYNC,
    connection_id: CONN_GSC,
    sync_kind: 'on_demand',
    status: 'queued',
    window_start: '2026-07-16',
    window_end: '2026-07-22',
    row_count: 0,
    resync_seq: 1,
    error_code: '',
    error_detail: '',
    created_at: '2026-07-23T04:31:00Z',
    updated_at: '2026-07-23T04:31:00Z',
    completed_at: null,
    ...overrides,
  };
}

function mockList(items: Record<string, unknown>[]) {
  mswServer.use(http.get('/api/v1/integrations', () => HttpResponse.json(items)));
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('IntegrationSettings — empty state + OAuth navigation', () => {
  beforeEach(() => {
    search = '';
    assignMock.mockClear();
    replace.mockClear();
  });

  it('renders the empty state and Connect CTAs hard-navigate to the OAuth start endpoints', async () => {
    const ue = userEvent.setup();
    mockList([]);
    renderWithProviders(<IntegrationSettings />);

    expect(await screen.findByText('No integrations connected')).toBeInTheDocument();

    await ue.click(screen.getByRole('button', { name: 'Connect Google' }));
    expect(assignMock).toHaveBeenCalledWith('/api/v1/integrations/oauth/gsc/start');

    await ue.click(screen.getByRole('button', { name: 'Connect Microsoft' }));
    expect(assignMock).toHaveBeenCalledWith('/api/v1/integrations/oauth/bing/start');
  });
});

describe('IntegrationSettings — grant cards', () => {
  beforeEach(() => {
    search = '';
    assignMock.mockClear();
    replace.mockClear();
  });

  it('groups connections onto one card per grant with scope chips, sub-rows, and mono last-synced', async () => {
    mockList([gscConnection, ga4Connection, bingConnection]);
    renderWithProviders(<IntegrationSettings />);

    const googleCard = await screen.findByTestId('grant-card-google');
    // Both Google connections ride one shared grant.
    expect(
      within(googleCard).getByText('One OAuth grant shared by 2 connections.'),
    ).toBeInTheDocument();
    expect(within(googleCard).getByText('Google Search Console')).toBeInTheDocument();
    expect(within(googleCard).getByText('Google Analytics 4')).toBeInTheDocument();
    expect(within(googleCard).getByText('sc-domain:example.com')).toBeInTheDocument();
    expect(within(googleCard).getByText('properties/123456789')).toBeInTheDocument();
    // Granted-scope chips (short scope names).
    expect(within(googleCard).getByText('webmasters.readonly')).toBeInTheDocument();
    expect(within(googleCard).getByText('analytics.readonly')).toBeInTheDocument();
    // Mono last-synced timestamps.
    expect(within(googleCard).getByText('Jul 23, 2026 · 04:12 UTC')).toBeInTheDocument();

    const msCard = screen.getByTestId('grant-card-microsoft');
    expect(within(msCard).getByText('Bing Webmaster Tools')).toBeInTheDocument();
    expect(within(msCard).getByText('One OAuth grant shared by 1 connection.')).toBeInTheDocument();
    // Needs reauth → warning alert + Sync now disabled (grant not connected).
    expect(within(msCard).getByText(/requires renewed consent/i)).toBeInTheDocument();
    expect(within(msCard).getByRole('button', { name: 'Sync now' })).toBeDisabled();
    expect(
      within(screen.getByTestId('connection-row-gsc')).getByRole('button', { name: 'Sync now' }),
    ).toBeEnabled();
  });

  it('maps grant statuses to badge tokens', async () => {
    const cases = [
      { status: 'connected', label: 'Connected', classes: 'bg-success-bg' },
      { status: 'needs_reauth', label: 'Needs reauth', classes: 'bg-warning-bg' },
      { status: 'pending_revocation', label: 'Pending revocation', classes: 'bg-warning-bg' },
      { status: 'error', label: 'Error', classes: 'bg-danger-bg' },
      { status: 'revoked', label: 'Revoked', classes: 'bg-neutral-bg' },
    ] as const;

    for (const { status, label, classes } of cases) {
      mockList([connection({ grant_status: status })]);
      const { unmount } = renderWithProviders(<IntegrationSettings />);
      const badge = await screen.findByTestId('grant-status-google');
      expect(badge).toHaveTextContent(label);
      expect(badge.className).toContain(classes);
      unmount();
    }
  });

  it('renders a not-connected card for a grant family with no grant', async () => {
    assignMock.mockClear();
    const ue = userEvent.setup();
    mockList([gscConnection, ga4Connection]);
    renderWithProviders(<IntegrationSettings />);

    const msCard = await screen.findByTestId('grant-card-microsoft');
    expect(within(msCard).getByText('Not connected')).toBeInTheDocument();
    await ue.click(within(msCard).getByRole('button', { name: 'Connect Microsoft' }));
    expect(assignMock).toHaveBeenCalledWith('/api/v1/integrations/oauth/bing/start');
  });

  it('Reconnect hard-navigates to the family OAuth start endpoint', async () => {
    const ue = userEvent.setup();
    mockList([gscConnection, ga4Connection, bingConnection]);
    renderWithProviders(<IntegrationSettings />);

    const googleCard = await screen.findByTestId('grant-card-google');
    await ue.click(within(googleCard).getByRole('button', { name: 'Reconnect' }));
    expect(assignMock).toHaveBeenCalledWith('/api/v1/integrations/oauth/gsc/start');

    const msCard = screen.getByTestId('grant-card-microsoft');
    await ue.click(within(msCard).getByRole('button', { name: 'Reconnect' }));
    expect(assignMock).toHaveBeenCalledWith('/api/v1/integrations/oauth/bing/start');
  });

  it('Test runs the probe and shows the inline result', async () => {
    const ue = userEvent.setup();
    mockList([gscConnection, ga4Connection]);
    mswServer.use(
      http.post(`/api/v1/integrations/${CONN_GSC}/test`, () =>
        HttpResponse.json({
          connection_id: CONN_GSC,
          status: 'ok',
          error_code: '',
          detail: '',
          tested_at: '2026-07-23T05:00:00Z',
        }),
      ),
    );
    renderWithProviders(<IntegrationSettings />);

    const row = await screen.findByTestId('connection-row-gsc');
    await ue.click(within(row).getByRole('button', { name: 'Test' }));
    expect(await within(row).findByText('Connection succeeded.')).toBeInTheDocument();
  });
});

describe('IntegrationSettings — disconnect dialog (shared-grant semantics)', () => {
  beforeEach(() => {
    search = '';
    assignMock.mockClear();
    replace.mockClear();
  });

  it('disconnecting one of two Google connections keeps the shared grant alive', async () => {
    const ue = userEvent.setup();
    let deleted = '';
    mockList([gscConnection, ga4Connection]);
    mswServer.use(
      http.delete(`/api/v1/integrations/${CONN_GSC}`, () => {
        deleted = CONN_GSC;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<IntegrationSettings />);

    const row = await screen.findByTestId('connection-row-gsc');
    await ue.click(within(row).getByRole('button', { name: 'Disconnect' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/stays connected/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/grant remains active/i)).toBeInTheDocument();

    await ue.click(within(dialog).getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(deleted).toBe(CONN_GSC));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('disconnecting the last connection on a grant warns that the whole grant is revoked', async () => {
    const ue = userEvent.setup();
    let deleted = '';
    mockList([
      connection({
        grant_status: 'connected',
        id: CONN_BING,
        grant_id: GRANT_MS,
        provider: 'bing',
        account_ref: 'https://example.com/',
      }),
    ]);
    mswServer.use(
      http.delete(`/api/v1/integrations/${CONN_BING}`, () => {
        deleted = CONN_BING;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<IntegrationSettings />);

    const row = await screen.findByTestId('connection-row-bing');
    await ue.click(within(row).getByRole('button', { name: 'Disconnect' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/last connection/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/revokes the grant/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/pending revocation/i)).toBeInTheDocument();

    await ue.click(within(dialog).getByRole('button', { name: 'Disconnect & revoke' }));
    await waitFor(() => expect(deleted).toBe(CONN_BING));
  });

  it('cancelling the dialog does not delete the connection', async () => {
    const ue = userEvent.setup();
    let deleteCalled = false;
    mockList([gscConnection, ga4Connection]);
    mswServer.use(
      http.delete('/api/v1/integrations/:id', () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<IntegrationSettings />);

    const row = await screen.findByTestId('connection-row-gsc');
    await ue.click(within(row).getByRole('button', { name: 'Disconnect' }));
    const dialog = await screen.findByRole('dialog');
    await ue.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(deleteCalled).toBe(false);
  });
});

describe('IntegrationSettings — sync polling', () => {
  beforeEach(() => {
    search = '';
    assignMock.mockClear();
    replace.mockClear();
  });

  it('Sync now enqueues, polls the run until terminal, then refreshes the connections list', async () => {
    const ue = userEvent.setup();
    let runStatus = 'running';
    let listCalls = 0;
    mswServer.use(
      http.get('/api/v1/integrations', () => {
        listCalls += 1;
        return HttpResponse.json([gscConnection, ga4Connection]);
      }),
      http.post(`/api/v1/integrations/${CONN_GSC}/sync`, () =>
        HttpResponse.json(
          { sync_run_id: SYNC, connection_id: CONN_GSC, status: 'queued' },
          { status: 202 },
        ),
      ),
      http.get(`/api/v1/integrations/${CONN_GSC}/syncs/${SYNC}`, () =>
        HttpResponse.json(
          syncRun({
            status: runStatus,
            row_count: 2148,
            completed_at: runStatus === 'succeeded' ? '2026-07-23T04:33:00Z' : null,
          }),
        ),
      ),
    );
    const { queryClient } = renderWithProviders(<IntegrationSettings />);

    const row = await screen.findByTestId('connection-row-gsc');
    const syncButton = within(row).getByRole('button', { name: 'Sync now' });
    await waitFor(() => expect(listCalls).toBe(1));
    await ue.click(syncButton);

    // The enqueued run is polled and surfaced as a run-status badge; Sync now
    // stays disabled while the run is non-terminal.
    expect(await within(row).findByText('running')).toBeInTheDocument();
    expect(within(row).getByText(/2,148 rows · window Jul 16–Jul 22/)).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: 'Sync now' })).toBeDisabled();

    // The run finishes server-side; the next poll lands the terminal status,
    // hides the badge, and refreshes the connections list (last_synced_at).
    runStatus = 'succeeded';
    await queryClient.invalidateQueries();
    await waitFor(() => expect(within(row).queryByText('running')).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThan(1));
    expect(within(row).getByRole('button', { name: 'Sync now' })).toBeEnabled();
  });
});

describe('IntegrationSettings — OAuth callback notice (C2)', () => {
  beforeEach(() => {
    assignMock.mockClear();
    replace.mockClear();
  });

  it('shows the success notice for ?connected= and strips the params from the URL', async () => {
    search = 'tab=integrations&connected=gsc';
    mockList([gscConnection, ga4Connection]);
    renderWithProviders(<IntegrationSettings />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Google connected.');
    expect(alert).toHaveTextContent(/shared OAuth grant/i);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/settings?tab=integrations'));
  });

  it('shows the failure notice for ?error= with the provider code in mono', async () => {
    search = 'tab=integrations&error=oauth_exchange_failed';
    mockList([]);
    renderWithProviders(<IntegrationSettings />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Connection failed.');
    expect(alert).toHaveTextContent('oauth_exchange_failed');
    // The empty state still renders beneath the notice.
    expect(await screen.findByText('No integrations connected')).toBeInTheDocument();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/settings?tab=integrations'));
  });

  it('renders no notice and does not touch the URL without callback params', async () => {
    search = 'tab=integrations';
    mockList([gscConnection, ga4Connection]);
    renderWithProviders(<IntegrationSettings />);

    await screen.findByTestId('grant-card-google');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
