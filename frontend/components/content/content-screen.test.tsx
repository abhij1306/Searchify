import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import { ProjectProvider } from '@/lib/project/project-context';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { ContentScreen } from './content-screen';

const WORKSPACE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT = '11111111-1111-4111-8111-111111111111';
const GEN = '33333333-3333-4333-8333-333333333333';

const project = {
  id: PROJECT,
  workspace_id: WORKSPACE,
  name: 'Acme',
  brand_name: 'Acme',
  website_url: 'https://acme.com',
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

function generation(overrides: Record<string, unknown> = {}) {
  return {
    id: GEN,
    project_id: PROJECT,
    status: 'queued',
    output_type: 'website_page',
    website_context_status: 'included',
    requested_model: 'mistral-small-latest',
    returned_model: null,
    provider: 'mistral',
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
    completed_at: null,
    error_code: '',
    prompt_preview: 'Write a landing page',
    prompt: 'Write a landing page for Acme.',
    website_context_enabled: true,
    website_context_summary: {
      crawl_id: '44444444-4444-4444-8444-444444444444',
      crawl_completed_at: '2026-07-14T00:00:00Z',
      extractor_version: 'ex-v1',
      analyzer_version: 'an-v1',
      page_count: 3,
      char_count: 1200,
      site_url_ids: [],
      artifact_ids: [],
      content_hashes: [],
    },
    finish_reason: null,
    output_truncated: false,
    output_text: null,
    usage: null,
    latency_ms: null,
    error_detail: '',
    generator_version: 'content-v1',
    ...overrides,
  };
}

const succeededGen = generation({
  status: 'succeeded',
  returned_model: 'mistral-small-2506',
  finish_reason: 'stop',
  output_text: '# About Acme\n\nWe make things.',
  usage: { total_tokens: 30 },
  latency_ms: 420,
  completed_at: '2026-07-15T00:01:00Z',
});

function mockBase(listItems: Record<string, unknown>[] = []) {
  mswServer.use(
    http.get('/api/v1/projects', () => HttpResponse.json([project])),
    http.get('/api/v1/content/generations', () => HttpResponse.json(listItems)),
  );
}

function renderScreen() {
  return renderWithProviders(
    <ProjectProvider>
      <ContentScreen />
    </ProjectProvider>,
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('ContentScreen — ready state', () => {
  it('disables Generate until a prompt is typed, and the context toggle defaults on', async () => {
    mockBase();
    renderScreen();
    const generate = await screen.findByRole('button', { name: 'Generate' });
    expect(generate).toBeDisabled();

    const toggle = screen.getByRole('switch', { name: /website context/i });
    expect(toggle).toHaveAttribute('aria-checked', 'true');

    await userEvent.type(
      screen.getByRole('textbox', { name: /describe the website content/i }),
      'Write a landing page',
    );
    expect(generate).toBeEnabled();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('shows the no-project state with a /setup link when there is no project', async () => {
    mswServer.use(http.get('/api/v1/projects', () => HttpResponse.json([])));
    renderScreen();
    const link = await screen.findByRole('link', { name: /go to setup/i });
    expect(link).toHaveAttribute('href', '/setup');
    expect(screen.queryByRole('button', { name: 'Generate' })).not.toBeInTheDocument();
  });
});

describe('ContentScreen — generate flow', () => {
  it('enqueues, shows the generating panel with Cancel, then renders the result with provenance', async () => {
    let detailCalls = 0;
    mockBase();
    mswServer.use(
      http.post('/api/v1/content/generations', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        expect(body.project_id).toBe(PROJECT);
        expect(body.website_context_enabled).toBe(true);
        return HttpResponse.json(generation(), { status: 201 });
      }),
      http.get(`/api/v1/content/generations/${GEN}`, () => {
        detailCalls += 1;
        return HttpResponse.json(detailCalls < 2 ? generation() : succeededGen);
      }),
    );
    renderScreen();

    await userEvent.type(
      await screen.findByRole('textbox', { name: /describe the website content/i }),
      'Write a landing page',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));

    // Generating: status region + Cancel, composer locked.
    expect(await screen.findByRole('status', { name: /generating content/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /describe the website content/i })).toBeDisabled();

    // Result (poll flips to succeeded): markdown + provenance + actions.
    expect(
      await screen.findByRole('heading', { level: 1, name: 'About Acme' }, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByText(/requested model: mistral-small-latest/i)).toBeInTheDocument();
    expect(screen.getByText(/returned model: mistral-small-2506/i)).toBeInTheDocument();
    expect(screen.getByText(/website context: 3 pages/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeInTheDocument();
    expect(screen.queryByText(/hit the length limit/i)).not.toBeInTheDocument();
  });

  it('renders the truncation warning when output_truncated is true', async () => {
    mockBase();
    mswServer.use(
      http.post('/api/v1/content/generations', () =>
        HttpResponse.json(generation(), { status: 201 }),
      ),
      http.get(`/api/v1/content/generations/${GEN}`, () =>
        HttpResponse.json({ ...succeededGen, output_truncated: true, finish_reason: 'length' }),
      ),
    );
    renderScreen();
    await userEvent.type(
      await screen.findByRole('textbox', { name: /describe the website content/i }),
      'Long page',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));
    expect(await screen.findByText(/hit the length limit/i)).toBeInTheDocument();
  });

  it('copies the raw markdown to the clipboard', async () => {
    mockBase();
    mswServer.use(
      http.post('/api/v1/content/generations', () =>
        HttpResponse.json(generation(), { status: 201 }),
      ),
      http.get(`/api/v1/content/generations/${GEN}`, () => HttpResponse.json(succeededGen)),
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderScreen();
    await userEvent.type(
      await screen.findByRole('textbox', { name: /describe the website content/i }),
      'Page',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));
    await userEvent.click(await screen.findByRole('button', { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith('# About Acme\n\nWe make things.');
  });

  it('cancel calls the cancel endpoint and leaves the generating state', async () => {
    let cancelled = false;
    mockBase();
    mswServer.use(
      http.post('/api/v1/content/generations', () =>
        HttpResponse.json(generation(), { status: 201 }),
      ),
      http.get(`/api/v1/content/generations/${GEN}`, () =>
        HttpResponse.json(
          cancelled ? generation({ status: 'cancelled', error_code: 'cancelled' }) : generation(),
        ),
      ),
      http.post(`/api/v1/content/generations/${GEN}/cancel`, () => {
        cancelled = true;
        return HttpResponse.json(generation({ status: 'cancelled', error_code: 'cancelled' }));
      }),
    );
    renderScreen();
    await userEvent.type(
      await screen.findByRole('textbox', { name: /describe the website content/i }),
      'Page',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(cancelled).toBe(true);
    await waitFor(() =>
      expect(screen.queryByRole('status', { name: /generating/i })).not.toBeInTheDocument(),
    );
  });
});

describe('ContentScreen — error state', () => {
  it('shows the provider-not-configured 409 message and Dismiss restores the composer preserving prompt + toggle', async () => {
    mockBase();
    mswServer.use(
      http.post('/api/v1/content/generations', () =>
        HttpResponse.json({ detail: 'provider_not_configured' }, { status: 409 }),
      ),
    );
    renderScreen();
    const textarea = await screen.findByRole('textbox', {
      name: /describe the website content/i,
    });
    await userEvent.type(textarea, 'My prompt text');
    const toggle = screen.getByRole('switch', { name: /website context/i });
    await userEvent.click(toggle); // off

    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/not configured/i);

    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    // Prompt + toggle preserved, composer editable again.
    expect(textarea).toHaveValue('My prompt text');
    expect(textarea).toBeEnabled();
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('a failed generation offers Try again, which enqueues a new record', async () => {
    let retried = false;
    mockBase();
    mswServer.use(
      http.post('/api/v1/content/generations', () =>
        HttpResponse.json(generation(), { status: 201 }),
      ),
      http.get(`/api/v1/content/generations/${GEN}`, () =>
        HttpResponse.json(generation({ status: 'failed', error_code: 'auth_failure' })),
      ),
      http.post(`/api/v1/content/generations/${GEN}/try-again`, () => {
        retried = true;
        return HttpResponse.json(generation({ id: '55555555-5555-4555-8555-555555555555' }), {
          status: 201,
        });
      }),
      http.get('/api/v1/content/generations/55555555-5555-4555-8555-555555555555', () =>
        HttpResponse.json(generation({ id: '55555555-5555-4555-8555-555555555555' })),
      ),
    );
    renderScreen();
    await userEvent.type(
      await screen.findByRole('textbox', { name: /describe the website content/i }),
      'Page',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/generation failed/i);
    await userEvent.click(screen.getByRole('button', { name: /try again/i }));
    await waitFor(() => expect(retried).toBe(true));
  });
});
