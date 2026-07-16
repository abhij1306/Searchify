import { expect, test, type Request } from '@playwright/test';

/**
 * F8 direct-provider Provider Settings e2e (Task 4).
 *
 * All backend calls are stubbed at the network layer so the spec runs without a
 * live backend (mirrors `runs.spec.ts`). It asserts the v2 direct-provider
 * retirement UI — exactly three direct engine cards (ChatGPT/OpenAI,
 * Gemini/Google, Claude/Anthropic), NO OpenRouter and NO "coming soon" options —
 * and exercises saving + testing an OpenAI key.
 *
 * The app calls only relative `/api/v1` paths (Next rewrites proxy them), so the
 * spec also asserts that every `/api/` request the browser issues is
 * same-origin with the page's baseURL — no cross-origin backend URL.
 */
const CONNECTION_ID = '11111111-1111-4111-8111-111111111111';
const WORKSPACE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT_ID = '22222222-2222-4222-8222-222222222222';

const user = {
  id: '33333333-3333-4333-8333-333333333333',
  email: 'providers@example.com',
  role: 'owner',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const project = {
  id: PROJECT_ID,
  workspace_id: WORKSPACE_ID,
  name: 'Acme',
  brand_name: 'Acme',
  website_url: 'https://acme.example',
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

// v2 direct-provider retirement: exactly one direct transport per engine.
const catalog = {
  transports: ['openai', 'anthropic', 'google'],
  engines: [
    {
      logical_engine: 'chatgpt',
      routes: [{ transport_provider: 'openai', default_model: 'gpt-5.4' }],
    },
    {
      logical_engine: 'gemini',
      routes: [{ transport_provider: 'google', default_model: 'gemini-flash-latest' }],
    },
    {
      logical_engine: 'claude',
      routes: [{ transport_provider: 'anthropic', default_model: 'claude-sonnet-4-6' }],
    },
  ],
};

function connection() {
  return {
    id: CONNECTION_ID,
    workspace_id: WORKSPACE_ID,
    label: 'chatgpt',
    transport_provider: 'openai',
    base_url: null,
    active: true,
    api_key_set: true,
    last_tested_at: null,
    last_test_status: '',
    routes: [
      {
        id: '44444444-4444-4444-8444-444444444444',
        logical_engine: 'chatgpt',
        transport_provider: 'openai',
        transport_model: 'gpt-5.4',
        is_default: false,
        active: true,
      },
    ],
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
  };
}

/** Assert every observed `/api/` request is same-origin with the baseURL. */
function assertSameOriginApi(requests: Request[], baseURL: string) {
  const origin = new URL(baseURL).origin;
  const apiRequests = requests.filter((r) => new URL(r.url()).pathname.includes('/api/'));
  // The app must actually have talked to the API (sanity check).
  expect(apiRequests.length).toBeGreaterThan(0);
  for (const request of apiRequests) {
    expect(new URL(request.url()).origin).toBe(origin);
  }
}

test('direct-only provider settings: three engines, no OpenRouter, save + test an OpenAI key', async ({
  page,
  baseURL,
}) => {
  const requests: Request[] = [];
  page.on('request', (request) => requests.push(request));

  // Connection list flips to "configured" once the OpenAI key is saved.
  let created = false;

  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route('**/api/v1/projects', (route) => route.fulfill({ json: [project] }));
  await page.route('**/api/v1/provider-catalog', (route) => route.fulfill({ json: catalog }));
  await page.route('**/api/v1/provider-connections', (route) => {
    if (route.request().method() === 'POST') {
      created = true;
      return route.fulfill({ status: 201, json: connection() });
    }
    return route.fulfill({ json: created ? [connection()] : [] });
  });
  await page.route(`**/api/v1/provider-connections/${CONNECTION_ID}/test`, (route) =>
    route.fulfill({
      json: {
        connection_id: CONNECTION_ID,
        status: 'ok',
        error_code: '',
        detail: 'Connection succeeded',
        latency_ms: 42,
        logical_engine: 'chatgpt',
        transport_provider: 'openai',
        transport_model: 'gpt-5.4',
        tested_at: '2026-07-15T00:00:00Z',
      },
    }),
  );

  await page.goto('/providers');

  // Exactly the three direct engine cards render, in order.
  await expect(page.getByRole('heading', { name: 'ChatGPT' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Gemini' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Claude' })).toBeVisible();

  // Transport labels: openai (OpenAI) / google (Google) / anthropic (Anthropic).
  await expect(page.getByText('via OpenAI')).toBeVisible();
  await expect(page.getByText('via Google')).toBeVisible();
  await expect(page.getByText('via Anthropic')).toBeVisible();
  await expect(page.getByText('Direct (OpenAI)')).toBeVisible();
  await expect(page.getByText('Direct (Google)')).toBeVisible();
  await expect(page.getByText('Direct (Anthropic)')).toBeVisible();

  // No OpenRouter option, no route toggle, no "coming soon" disabled option.
  await expect(page.getByText(/openrouter/i)).toHaveCount(0);
  await expect(page.getByRole('radio')).toHaveCount(0);
  await expect(page.getByText(/coming soon/i)).toHaveCount(0);
  await expect(page.getByText(/direct openai — coming soon/i)).toHaveCount(0);

  // Exercise the ChatGPT card: fill the key, save, then test the connection.
  const chatgptCard = page.locator('section', { has: page.getByRole('heading', { name: 'ChatGPT' }) });
  await expect(chatgptCard.getByText('Not configured')).toBeVisible();

  await chatgptCard.getByPlaceholder(/paste your api key/i).fill('sk-test-key');
  await chatgptCard.getByRole('button', { name: /save key/i }).click();

  // After save the connection list refetches → the card becomes configured.
  await expect(chatgptCard.getByText('Configured')).toBeVisible();

  await chatgptCard.getByRole('button', { name: /test connection/i }).click();
  await expect(chatgptCard.getByText(/connection succeeded/i)).toBeVisible();

  // Same-origin: every /api/ request went to the page origin (no cross-origin backend).
  assertSameOriginApi(requests, baseURL!);
});
