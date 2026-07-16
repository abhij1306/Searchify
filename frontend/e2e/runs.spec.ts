import { expect, test } from '@playwright/test';

/**
 * F10 Run/Executions explorer smoke: shell → open a run → open an execution.
 *
 * The `/auth/me`, `/projects`, `/audits*`, and `/executions/*` calls are stubbed
 * at the network layer so the spec runs without a live backend. It exercises the
 * full evidence-explorer navigation the plan calls for
 * (shell → Visibility/Runs → open run → open execution).
 *
 * Note: this requires a running dev server (playwright.config.ts starts one).
 * It is skipped automatically when no browser/dev server is available.
 */
const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const WORKSPACE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const AUDIT_ID = '44444444-4444-4444-8444-444444444444';
const EXEC_ID = '77777777-7777-4777-8777-777777777777';
const ANALYSIS_ID = '88888888-8888-4888-8888-888888888888';

const user = {
  id: '22222222-2222-4222-8222-222222222222',
  email: 'runs@example.com',
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

const audit = {
  id: AUDIT_ID,
  workspace_id: WORKSPACE_ID,
  project_id: PROJECT_ID,
  status: 'completed',
  benchmark_mode: 'consumer_like',
  repetitions: 3,
  random_seed: '7',
  requested_count: 3,
  completed_count: 3,
  failed_count: 0,
  error_message: '',
  engine_snapshots: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  started_at: '2026-01-01T00:00:05Z',
  completed_at: '2026-01-01T00:10:00Z',
};

const execution = {
  id: EXEC_ID,
  audit_id: AUDIT_ID,
  prompt_index: 0,
  repetition: 1,
  randomized_position: 0,
  logical_engine: 'gemini',
  transport_provider: 'google',
  transport_model: 'gemini-flash-latest',
  status: 'succeeded',
  attempt_count: 1,
  max_attempts: 5,
  answer_text: 'Acme is a leading CRM.',
  search_used: true,
  error_code: '',
  error_detail: '',
  latency_ms: 900,
  created_at: '2026-01-01T00:00:00Z',
  completed_at: '2026-01-01T00:00:03Z',
};

const evidence = {
  id: EXEC_ID,
  analysis_id: ANALYSIS_ID,
  audit_id: AUDIT_ID,
  task_id: EXEC_ID,
  artifact_id: null,
  analyzer_version: 'v1',
  scoring_rule_version: 'v1',
  logical_engine: 'gemini',
  transport_provider: 'google',
  transport_model: 'gemini-flash-latest',
  prompt_index: 0,
  repetition: 1,
  prompt_class: 'unbranded',
  brand_mentioned: true,
  brand_first_offset: 0,
  owned_domain_cited: true,
  owned_citation_count: 1,
  unintended_domain_cited: false,
  citation_count: 1,
  search_used: true,
  search_query_count: 1,
  sentiment: null,
  avg_position: null,
  score: { visibility: 1 },
  citations: [
    {
      ordinal: 1,
      url: 'https://acme.example/a',
      title: 'Acme docs',
      domain: 'acme.example',
      classification: 'owned',
      is_owned: true,
      is_unintended: false,
      matched_competitor: null,
    },
  ],
  competitors_mentioned: [],
  created_at: '2026-01-01T00:00:00Z',
};

test('shell → open run → open execution evidence', async ({ page }) => {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route('**/api/v1/projects', (route) => route.fulfill({ json: [project] }));
  await page.route(/\/api\/v1\/audits(\?.*)?$/, (route) => route.fulfill({ json: [audit] }));
  await page.route(`**/api/v1/audits/${AUDIT_ID}`, (route) => route.fulfill({ json: audit }));
  await page.route(`**/api/v1/audits/${AUDIT_ID}/executions`, (route) =>
    route.fulfill({ json: [execution] }),
  );
  await page.route(`**/api/v1/executions/${EXEC_ID}`, (route) => route.fulfill({ json: evidence }));

  await page.goto('/runs');

  // Runs list renders and the run links to its detail page.
  await expect(page.getByRole('heading', { name: 'Audits' })).toBeVisible();
  await page.getByRole('link', { name: 'View' }).first().click();

  // Run detail: progress panel + executions table.
  await expect(page).toHaveURL(new RegExp(`/runs/${AUDIT_ID}$`));
  await expect(page.getByText('Executions')).toBeVisible();
  await page.getByRole('link', { name: 'Evidence' }).first().click();

  // Execution evidence: answer + citation.
  await expect(page).toHaveURL(new RegExp(`/executions/${EXEC_ID}$`));
  await expect(page.getByText('Acme is a leading CRM.')).toBeVisible();
  await expect(page.getByText('Acme docs')).toBeVisible();
});
