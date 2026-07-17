import { describe, expect, it } from 'vitest';

import type { ProviderCatalog, ProviderConnection } from '@/lib/api/types';
import {
  buildEngineCards,
  connectionForTransport,
  discoveryModelOptions,
  isConfigured,
  mergeRoutePayload,
} from './catalog';

// v2 direct-provider retirement: the catalog lists exactly one direct
// transport per logical engine (ChatGPT/OpenAI, Gemini/Google, Claude/Anthropic).
const catalog: ProviderCatalog = {
  transports: ['openai', 'anthropic', 'google'],
  engines: [
    { logical_engine: 'chatgpt', routes: [{ transport_provider: 'openai', default_model: 'gpt-5.4' }] },
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

describe('buildEngineCards', () => {
  it('renders all three engines in order', () => {
    const cards = buildEngineCards(catalog);
    expect(cards.map((c) => c.logical_engine)).toEqual(['chatgpt', 'gemini', 'claude']);
  });

  it('gives each engine exactly one direct route with the direct label', () => {
    const cards = buildEngineCards(catalog);
    const matrix: Record<string, { transport: string; model: string }> = {
      chatgpt: { transport: 'openai', model: 'gpt-5.4' },
      gemini: { transport: 'google', model: 'gemini-flash-latest' },
      claude: { transport: 'anthropic', model: 'claude-sonnet-4-6' },
    };
    for (const [engine, expected] of Object.entries(matrix)) {
      const card = cards.find((c) => c.logical_engine === engine)!;
      expect(card.route).not.toBeNull();
      expect(card.route!.transport_provider).toBe(expected.transport);
      expect(card.route!.default_model).toBe(expected.model);
    }
  });

  it('labels the ChatGPT route as Direct (OpenAI) — no OpenRouter, no "coming soon"', () => {
    const chatgpt = buildEngineCards(catalog).find((c) => c.logical_engine === 'chatgpt')!;
    expect(chatgpt.route!.label).toBe('Direct (OpenAI)');
    const serialized = JSON.stringify(chatgpt);
    expect(serialized).not.toContain('openrouter');
    expect(serialized).not.toContain('coming soon');
  });

  it('is resilient to an undefined catalog (three engines, null routes)', () => {
    const cards = buildEngineCards(undefined);
    expect(cards).toHaveLength(3);
    expect(cards.every((c) => c.route === null)).toBe(true);
  });
});

describe('connection helpers', () => {
  const conn: ProviderConnection = {
    id: '11111111-1111-4111-8111-111111111111',
    workspace_id: '22222222-2222-4222-8222-222222222222',
    transport_provider: 'openai',
    base_url: null,
    active: true,
    api_key_set: true,
    routes: [
      {
        id: '33333333-3333-4333-8333-333333333333',
        logical_engine: 'chatgpt',
        transport_provider: 'openai',
        transport_model: 'gpt-5.4',
        is_default: true,
      },
    ],
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
  };

  it('finds a connection by transport and reports configured', () => {
    expect(connectionForTransport([conn], 'openai')).toBe(conn);
    expect(connectionForTransport([conn], 'google')).toBeUndefined();
    expect(isConfigured(conn)).toBe(true);
    expect(isConfigured(undefined)).toBe(false);
    expect(isConfigured({ ...conn, api_key_set: false })).toBe(false);
  });

  it('merges a new engine route while preserving existing ones', () => {
    const merged = mergeRoutePayload(conn, 'gemini', 'gemini-flash-latest');
    expect(merged.map((r) => r.logical_engine).sort()).toEqual(['chatgpt', 'gemini']);
    // Idempotent: re-adding an existing engine does not duplicate it.
    const again = mergeRoutePayload(conn, 'chatgpt', 'gpt-5.4');
    expect(again).toHaveLength(1);
  });
});

describe('discoveryModelOptions', () => {
  it('flattens every approved route into a labelled option', () => {
    const options = discoveryModelOptions(catalog);
    expect(options).toHaveLength(3);
    expect(options[0].label).toContain('ChatGPT');
    expect(options[0].label).toContain('OpenAI');
    expect(discoveryModelOptions(undefined)).toEqual([]);
  });
});
