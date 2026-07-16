import { describe, expect, it } from 'vitest';

import { parsePromptCsv, tokenizeCsv, validRows } from './csv';

describe('tokenizeCsv', () => {
  it('handles quoted fields, escaped quotes, and embedded newlines', () => {
    const raw = 'text,theme\n"Hello, ""world""","multi\nline"\n';
    expect(tokenizeCsv(raw)).toEqual([
      ['text', 'theme'],
      ['Hello, "world"', 'multi\nline'],
    ]);
  });

  it('strips a BOM and drops blank lines', () => {
    const raw = '\ufefftext\n\nfoo\n';
    expect(tokenizeCsv(raw)).toEqual([['text'], ['foo']]);
  });
});

describe('parsePromptCsv', () => {
  it('parses a header row with columns in any order', () => {
    const parsed = parsePromptCsv('intent,text,branded\ndiscovery,Best shoes?,yes');
    expect(parsed.hasHeader).toBe(true);
    expect(parsed.rows).toHaveLength(1);
    expect(parsed.rows[0].input).toMatchObject({
      text: 'Best shoes?',
      intent: 'discovery',
      branded: true,
      enabled: true,
    });
    expect(parsed.rows[0].errors).toEqual([]);
  });

  it('treats a file without a recognized header as positional', () => {
    const parsed = parsePromptCsv('Best shoes?,Comfort,purchase,true,false');
    expect(parsed.hasHeader).toBe(false);
    expect(parsed.rows[0].input).toMatchObject({
      text: 'Best shoes?',
      theme: 'Comfort',
      intent: 'purchase',
      branded: true,
      enabled: false,
    });
  });

  it('flags empty-text rows as errors and drops them from validRows', () => {
    const parsed = parsePromptCsv('text\nGood prompt\n');
    const withEmpty = parsePromptCsv('text,theme\n,Comfort\nGood,Fit');
    expect(parsed.rows[0].errors).toEqual([]);
    expect(withEmpty.rows[0].errors.length).toBeGreaterThan(0);
    expect(validRows(withEmpty)).toHaveLength(1);
    expect(validRows(withEmpty)[0].text).toBe('Good');
  });

  it('warns and clears an unknown intent', () => {
    const parsed = parsePromptCsv('text,intent\nHi,frobnicate');
    expect(parsed.rows[0].input.intent).toBe('');
    expect(parsed.rows[0].warnings.length).toBeGreaterThan(0);
  });

  it('reports a file-level error for an empty file', () => {
    expect(parsePromptCsv('').errors.length).toBeGreaterThan(0);
  });
});
