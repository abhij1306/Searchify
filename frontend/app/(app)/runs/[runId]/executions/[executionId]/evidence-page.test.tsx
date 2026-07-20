import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import ExecutionEvidencePage from './page';

const AUDIT_ID = '44444444-4444-4444-8444-444444444444';
const EXEC_ID = '77777777-7777-4777-8777-777777777777';
const ANALYSIS_ID = '88888888-8888-4888-8888-888888888888';

vi.mock('next/navigation', () => ({
  useParams: () => ({ runId: AUDIT_ID, executionId: EXEC_ID }),
}));

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
  brand_first_offset: 5,
  owned_domain_cited: true,
  owned_citation_count: 1,
  unintended_domain_cited: false,
  citation_count: 2,
  search_used: true,
  search_query_count: 1,
  sentiment: null,
  avg_position: null,
  score: { visibility: 1, brand_mentioned: true },
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
    {
      ordinal: 2,
      url: 'https://beta.example/b',
      title: 'Beta blog',
      domain: 'beta.example',
      classification: 'competitor',
      is_owned: false,
      is_unintended: false,
      matched_competitor: 'Beta',
    },
  ],
  competitors_mentioned: ['Beta'],
  created_at: '2026-07-15T00:00:00Z',
};

const executionRow = {
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
  answer_text: 'Acme is the leading CRM.',
  search_used: true,
  error_code: '',
  error_detail: '',
  latency_ms: 900,
  created_at: '2026-07-15T00:00:00Z',
  completed_at: '2026-07-15T00:00:03Z',
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('ExecutionEvidencePage', () => {
  it('renders answer, grounding, classified citations, mentions, and score', async () => {
    mswServer.use(
      http.get(`/api/v1/executions/${EXEC_ID}`, () => HttpResponse.json(evidence)),
      http.get(`/api/v1/audits/${AUDIT_ID}/executions`, () => HttpResponse.json([executionRow])),
    );

    renderWithProviders(<ExecutionEvidencePage />);

    // Answer text (from the execution row).
    expect(await screen.findByText('Acme is the leading CRM.')).toBeInTheDocument();
    // Grounding badge.
    expect(screen.getByText('Search used')).toBeInTheDocument();
    // Classified citations.
    expect(screen.getByText('Acme docs')).toBeInTheDocument();
    expect(screen.getByText('Owned')).toBeInTheDocument();
    expect(screen.getByText('Beta blog')).toBeInTheDocument();
    // Competitor mention chip (appears in citations + mentions).
    expect(screen.getAllByText('Beta').length).toBeGreaterThan(0);
    // Score dict (keys are humanized for display; "Brand mentioned" also
    // appears as the header badge, hence getAllByText).
    expect(screen.getByText('Visibility')).toBeInTheDocument();
    expect(screen.getAllByText('Brand mentioned').length).toBeGreaterThan(1);
    // Sentiment placeholder.
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('surfaces an error when evidence cannot be loaded', async () => {
    mswServer.use(
      http.get(`/api/v1/executions/${EXEC_ID}`, () =>
        HttpResponse.json({ detail: 'Execution not found' }, { status: 404 }),
      ),
      http.get(`/api/v1/audits/${AUDIT_ID}/executions`, () => HttpResponse.json([])),
    );

    renderWithProviders(<ExecutionEvidencePage />);

    expect(await screen.findByText(/could not load this execution/i)).toBeInTheDocument();
  });
});
