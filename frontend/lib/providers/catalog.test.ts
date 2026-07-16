import { describe, expect, it } from 'vitest';

import type { ProviderCatalog, ProviderConnection } from '@/lib/api/types';
import {
  buildEngineCards,
  connectionForTransport,
  discoveryModelOptions,
  isConfigured,
  mergeRoutePayload,
} from './catalog';

const catalog: ProviderCatalog = {
  transports: ['anthropic', 'google', 'openrouter'],
  engines: [
    { logical_engine: 'chatgpt', routes: [{ transport_provider: 'openrouter', default_model: 'openai/gpt-5.4' }] },
    {
      logical_engine: 'gemini',
      routes: [
        { transport_provider: 'google', default_model: 'gemini-flash-latest' },
        { transport_provider: 'openrouter', default_model: 'google/gemini-2.5-flash' },
      ],
    },
    {
      logical_engine: 'claude',
      routes: [
        { transport_provider: 'anthropic', default_model: 'claude-sonnet-4-6' },
        { transport_provider: 'openrouter', default_model: 'anthropic/claude-sonnet-4.6' },
      ],
    },
  ],
};

describe('buildEngineCards', () => {
  it('renders all three engines in order', () => {
    const cards = buildEngineCards(catalog);
    expect(cards.map((c) => c.logical_engine)).toEqual(['chatgpt', 'gemini', 'claude']);
  });

  it('gives ChatGPT an OpenRouter-only route plus a disabled direct-OpenAI option', () => {
    const chatgpt = buildEngineCards(catalog).find((c) => c.logical_engine === 'chatgpt')!;
    expect(chatgpt.singleRoute).toBe(true);
    const enabled = chatgpt.options.filter((o) => !o.disabled);
    expect(enabled).toHaveLength(1);
    expect(enabled[0].transport_provider).toBe('openrouter');
    const disabled = chatgpt.options.filter((o) => o.disabled);
    expect(disabled).toHaveLength(1);
    expect(disabled[0].transport_provider).toBe('openai');
    expect(disabled[0].disabledReason).toBe('coming soon');
  });

  it('gives Gemini and Claude a real direct/OpenRouter toggle (no reserved option)', () => {
    const cards = buildEngineCards(catalog);
    for (const engine of ['gemini', 'claude'] as const) {
      const card = cards.find((c) => c.logical_engine === engine)!;
      expect(card.singleRoute).toBe(false);
      expect(card.options.every((o) => !o.disabled)).toBe(true);
      expect(card.options).toHaveLength(2);
    }
  });

  it('is resilient to an undefined catalog', () => {
    const cards = buildEngineCards(undefined);
    expect(cards).toHaveLength(3);
    // ChatGPT still surfaces its reserved disabled option.
    const chatgpt = cards.find((c) => c.logical_engine === 'chatgpt')!;
    expect(chatgpt.options.some((o) => o.disabled)).toBe(true);
  });
});

describe('connection helpers', () => {
  const conn: ProviderConnection = {
    id: '11111111-1111-4111-8111-111111111111',
    workspace_id: '22222222-2222-4222-8222-222222222222',
    transport_provider: 'openrouter',
    base_url: null,
    active: true,
    api_key_set: true,
    routes: [
      {
        id: '33333333-3333-4333-8333-333333333333',
        logical_engine: 'chatgpt',
        transport_provider: 'openrouter',
        transport_model: 'openai/gpt-5.4',
        is_default: true,
      },
    ],
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
  };

  it('finds a connection by transport and reports configured', () => {
    expect(connectionForTransport([conn], 'openrouter')).toBe(conn);
    expect(connectionForTransport([conn], 'google')).toBeUndefined();
    expect(isConfigured(conn)).toBe(true);
    expect(isConfigured(undefined)).toBe(false);
    expect(isConfigured({ ...conn, api_key_set: false })).toBe(false);
  });

  it('merges a new engine route while preserving existing ones', () => {
    const merged = mergeRoutePayload(conn, 'gemini', 'google/gemini-2.5-flash');
    expect(merged.map((r) => r.logical_engine).sort()).toEqual(['chatgpt', 'gemini']);
    // Idempotent: re-adding an existing engine does not duplicate it.
    const again = mergeRoutePayload(conn, 'chatgpt', 'openai/gpt-5.4');
    expect(again).toHaveLength(1);
  });
});

describe('discoveryModelOptions', () => {
  it('flattens every approved route into a labelled option', () => {
    const options = discoveryModelOptions(catalog);
    expect(options).toHaveLength(5);
    expect(options[0].label).toContain('ChatGPT');
    expect(discoveryModelOptions(undefined)).toEqual([]);
  });
});
