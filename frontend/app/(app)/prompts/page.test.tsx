import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setActiveWorkspaceId } from '@/lib/api/client';
import { ProjectProvider } from '@/lib/project/project-context';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import PromptsPage from './page';

const WORKSPACE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const SET_ID = '22222222-2222-4222-8222-222222222222';
const TOPIC_ID = '55555555-5555-4555-8555-555555555555';

function makePrompt(overrides: Record<string, unknown> = {}) {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    prompt_set_id: SET_ID,
    text: 'Best running shoes?',
    theme: 'Comfort',
    intent: 'discovery',
    branded: false,
    enabled: true,
    status: 'active',
    origin: 'manual',
    ...overrides,
  };
}

function makeTopic(overrides: Record<string, unknown> = {}) {
  return {
    id: TOPIC_ID,
    project_id: PROJECT_ID,
    name: 'Footwear',
    description: '',
    origin: 'manual',
    active_count: 1,
    proposed_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeEvidenceItem(promptId: string, brandMentioned: boolean, taskId: string) {
  return {
    audit_id: '99999999-9999-4999-8999-999999999999',
    task_id: taskId,
    analysis_id: taskId,
    artifact_id: null,
    prompt_snapshot_id: taskId,
    prompt_id: promptId,
    prompt_index: 0,
    prompt_text: 'Best running shoes?',
    repetition: 1,
    completed_at: '2026-01-02T00:00:00Z',
    logical_engine: 'chatgpt',
    transport_provider: 'openai',
    transport_model: 'gpt-test',
    search_used: false,
    search_query_count: 0,
    query_text_available: false,
    state: 'count_only',
    search_events: [],
    event_source: 'none',
    mentions: brandMentioned
      ? [
          {
            kind: 'brand',
            name: 'Searchify',
            first_offset: 0,
            artifact_id: null,
            analyzer_version: 'v1',
          },
        ]
      : [],
    citations: [],
  };
}

function makeSet(prompts: unknown[]) {
  return {
    id: SET_ID,
    project_id: PROJECT_ID,
    name: 'Default prompt set',
    description: '',
    prompt_count: prompts.length,
    prompts,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function makeProject(promptSets: unknown[]) {
  return {
    id: PROJECT_ID,
    workspace_id: WORKSPACE_ID,
    name: 'Searchify',
    brand_name: 'Searchify',
    website_url: 'https://searchify.com',
    country_code: 'US',
    language_code: 'en',
    benchmark_mode: 'consumer_like',
    default_repetitions: 3,
    brand: { aliases: [] },
    owned_domains: [],
    unintended_domains: [],
    competitors: [],
    prompt_sets: promptSets,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function baseHandlers(prompts: unknown[], topics: unknown[] = [], evidenceItems: unknown[] = []) {
  const set = makeSet(prompts);
  mswServer.use(
    http.get('/api/v1/projects', () => HttpResponse.json([makeProject([set])])),
    http.get('/api/v1/prompt-sets', () => HttpResponse.json([set])),
    http.get(`/api/v1/projects/${PROJECT_ID}/topics`, () => HttpResponse.json(topics)),
    http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
      HttpResponse.json({ items: evidenceItems, truncated: false }),
    ),
  );
}

function renderPage() {
  return renderWithProviders(
    <ProjectProvider>
      <PromptsPage />
    </ProjectProvider>,
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  window.localStorage.clear();
  setActiveWorkspaceId(null);
});
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('PromptsPage (Your Prompts)', () => {
  it('groups active prompts by topic with a summary banner and Prompt Research link', async () => {
    baseHandlers(
      [
        makePrompt({ topic_id: TOPIC_ID }),
        makePrompt({
          id: '44444444-4444-4444-8444-444444444444',
          text: 'Ungrouped prompt',
        }),
        // Proposed prompts never appear on Your Prompts.
        makePrompt({
          id: '66666666-6666-4666-8666-666666666666',
          text: 'Proposed prompt',
          status: 'proposed',
        }),
      ],
      [makeTopic()],
    );
    renderPage();

    expect(
      await screen.findByText('Best running shoes?', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    // Banner counts only active prompts (2) and topics with prompts (1).
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to Prompt Research' })).toHaveAttribute(
      'href',
      '/prompt-research',
    );
    // Topic group header + ungrouped bucket.
    expect(screen.getAllByText('Footwear').length).toBeGreaterThan(0);
    expect(screen.getByText('Ungrouped')).toBeInTheDocument();
    expect(screen.queryByText('Proposed prompt')).not.toBeInTheDocument();
  });

  it('collapses a topic group when its expander is toggled', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt({ topic_id: TOPIC_ID })], [makeTopic()]);
    renderPage();

    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: 'Collapse topic Footwear' }));
    expect(screen.queryByText('Best running shoes?')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Expand topic Footwear' }));
    expect(screen.getByText('Best running shoes?')).toBeInTheDocument();
  });

  it('derives per-prompt visibility scores from persisted evidence', async () => {
    const promptId = '33333333-3333-4333-8333-333333333333';
    baseHandlers(
      [makePrompt({ topic_id: TOPIC_ID })],
      [makeTopic()],
      [
        makeEvidenceItem(promptId, true, 'bbbbbbbb-0000-4000-8000-000000000001'),
        makeEvidenceItem(promptId, true, 'bbbbbbbb-0000-4000-8000-000000000002'),
        makeEvidenceItem(promptId, false, 'bbbbbbbb-0000-4000-8000-000000000003'),
      ],
    );
    renderPage();

    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    // 2 of 3 executions mentioned the brand → 67%, on both the prompt row and
    // the single-prompt topic group row.
    expect(await screen.findAllByText('67%')).toHaveLength(2);
  });

  it('shows the empty state pointing to Prompt Research when no active prompts exist', async () => {
    baseHandlers([]);
    renderPage();

    expect(
      await screen.findByText('No active prompts yet', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Prompt Research/ }).length).toBeGreaterThan(0);
  });

  it('filters prompts by search', async () => {
    const user = userEvent.setup();
    baseHandlers([
      makePrompt(),
      makePrompt({ id: '44444444-4444-4444-8444-444444444444', text: 'Nike vs Adidas' }),
    ]);
    renderPage();

    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.type(screen.getByRole('searchbox', { name: 'Search prompts' }), 'nike');

    expect(screen.queryByText('Best running shoes?')).not.toBeInTheDocument();
    expect(screen.getByText('Nike vs Adidas')).toBeInTheDocument();
  });
});
