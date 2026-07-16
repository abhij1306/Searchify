import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { getActiveWorkspaceId, setActiveWorkspaceId } from '@/lib/api/client';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { ProjectProvider, useProjectContext } from './project-context';

const WORKSPACE_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT_1 = '11111111-1111-4111-8111-111111111111';
const PROJECT_2 = '22222222-2222-4222-8222-222222222222';

function project(id: string, name: string, workspaceId = WORKSPACE_A) {
  return {
    id,
    workspace_id: workspaceId,
    name,
    brand_name: name,
    website_url: 'https://example.com',
    country_code: 'US',
    language_code: 'en',
    benchmark_mode: 'consumer_like',
    default_repetitions: 3,
    brand: { aliases: [] },
    owned_domains: [],
    unintended_domains: [],
    competitors: [],
    prompt_sets: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function Harness() {
  const { activeProject, activeProjectId, projects, setActiveProjectId } = useProjectContext();
  return (
    <div>
      <div data-testid="active">{activeProject?.name ?? 'none'}</div>
      <div data-testid="active-id">{activeProjectId ?? 'none'}</div>
      <div data-testid="count">{projects.length}</div>
      {projects.map((p) => (
        <button key={p.id} type="button" onClick={() => setActiveProjectId(p.id)}>
          select {p.name}
        </button>
      ))}
    </div>
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  window.localStorage.clear();
  setActiveWorkspaceId(null);
});
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('ProjectProvider', () => {
  it('auto-selects the first project and stamps the workspace header', async () => {
    mswServer.use(
      http.get('/api/v1/projects', () =>
        HttpResponse.json([project(PROJECT_1, 'Acme'), project(PROJECT_2, 'Globex')]),
      ),
    );

    renderWithProviders(
      <ProjectProvider>
        <Harness />
      </ProjectProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('active')).toHaveTextContent('Acme'));
    expect(screen.getByTestId('active-id')).toHaveTextContent(PROJECT_1);
    expect(getActiveWorkspaceId()).toBe(WORKSPACE_A);
  });

  it('changes the active project on selection and persists it', async () => {
    mswServer.use(
      http.get('/api/v1/projects', () =>
        HttpResponse.json([project(PROJECT_1, 'Acme'), project(PROJECT_2, 'Globex')]),
      ),
    );

    renderWithProviders(
      <ProjectProvider>
        <Harness />
      </ProjectProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('active')).toHaveTextContent('Acme'));

    await userEvent.click(screen.getByRole('button', { name: 'select Globex' }));

    await waitFor(() => expect(screen.getByTestId('active')).toHaveTextContent('Globex'));
    expect(screen.getByTestId('active-id')).toHaveTextContent(PROJECT_2);
    expect(window.localStorage.getItem('searchify.active-project-id')).toBe(PROJECT_2);
  });

  it('restores a persisted selection when it still exists', async () => {
    window.localStorage.setItem('searchify.active-project-id', PROJECT_2);
    mswServer.use(
      http.get('/api/v1/projects', () =>
        HttpResponse.json([project(PROJECT_1, 'Acme'), project(PROJECT_2, 'Globex')]),
      ),
    );

    renderWithProviders(
      <ProjectProvider>
        <Harness />
      </ProjectProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('active')).toHaveTextContent('Globex'));
  });

  it('is empty (no active project) when the workspace has none', async () => {
    mswServer.use(http.get('/api/v1/projects', () => HttpResponse.json([])));

    renderWithProviders(
      <ProjectProvider>
        <Harness />
      </ProjectProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('0'));
    expect(screen.getByTestId('active')).toHaveTextContent('none');
    expect(getActiveWorkspaceId()).toBeNull();
  });
});
