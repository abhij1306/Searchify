import { describe, expect, it } from 'vitest';

import {
  changeInventoryFilters,
  changeIssueFilters,
  emptyInventoryFilters,
  emptyIssueFilters,
  inventoryFiltersEqual,
  issueFiltersEqual,
  parseCursor,
  parseInventoryFilters,
  parseIssueFilters,
  serializeInventoryFilters,
  serializeIssueFilters,
  toInventoryParams,
  toIssueParams,
  type InventoryFilters,
  type IssueFilters,
} from './filters';

describe('inventory filter serialization (cursor round-trip)', () => {
  it('round-trips a full filter set through URL query state', () => {
    const filters: InventoryFilters = { query: 'blog', status: 'ok', monitored: 'monitored' };
    const params = serializeInventoryFilters(filters, 'CUR==');
    expect(params.get('query')).toBe('blog');
    expect(params.get('status')).toBe('ok');
    expect(params.get('monitored')).toBe('true');
    expect(params.get('cursor')).toBe('CUR==');
    const parsed = parseInventoryFilters(params);
    expect(parsed).toEqual(filters);
    expect(parseCursor(params)).toBe('CUR==');
  });

  it('omits empty fields and the "all" monitored default', () => {
    const params = serializeInventoryFilters(emptyInventoryFilters);
    expect(params.toString()).toBe('');
    expect(parseInventoryFilters(new URLSearchParams())).toEqual(emptyInventoryFilters);
  });

  it('serializes unmonitored as monitored=false', () => {
    const params = serializeInventoryFilters({ ...emptyInventoryFilters, monitored: 'unmonitored' });
    expect(params.get('monitored')).toBe('false');
    expect(parseInventoryFilters(params).monitored).toBe('unmonitored');
  });

  it('trims the query before serializing', () => {
    const params = serializeInventoryFilters({ ...emptyInventoryFilters, query: '  spaced  ' });
    expect(params.get('query')).toBe('spaced');
  });
});

describe('toInventoryParams', () => {
  it('maps filters + cursor to request params (monitored tri-state)', () => {
    const params = toInventoryParams(
      { query: ' foo ', status: 'ok', monitored: 'unmonitored' },
      'C1',
      200,
    );
    expect(params).toEqual({ cursor: 'C1', limit: 200, query: 'foo', status: 'ok', monitored: false });
  });

  it('drops empty query/status and "all" monitored to undefined', () => {
    const params = toInventoryParams(emptyInventoryFilters);
    expect(params.query).toBeUndefined();
    expect(params.status).toBeUndefined();
    expect(params.monitored).toBeUndefined();
    expect(params.cursor).toBeUndefined();
  });
});

describe('changeInventoryFilters drops the cursor', () => {
  it('always resets cursor to null on a filter change', () => {
    const current: InventoryFilters = { query: 'a', status: '', monitored: 'all' };
    const { filters, cursor } = changeInventoryFilters(current, { query: 'b' });
    expect(cursor).toBeNull();
    expect(filters.query).toBe('b');
  });
});

describe('inventoryFiltersEqual (cursor validity check)', () => {
  it('treats trimmed-equal queries as equal', () => {
    expect(
      inventoryFiltersEqual(
        { query: 'foo', status: '', monitored: 'all' },
        { query: '  foo  ', status: '', monitored: 'all' },
      ),
    ).toBe(true);
  });

  it('detects a differing filter', () => {
    expect(
      inventoryFiltersEqual(
        { query: 'foo', status: '', monitored: 'all' },
        { query: 'foo', status: '', monitored: 'monitored' },
      ),
    ).toBe(false);
  });
});

describe('issue filter serialization', () => {
  it('round-trips issue filters + cursor', () => {
    const filters: IssueFilters = {
      severity: 'high',
      category: 'metadata',
      dimension: 'aeo',
      rule_id: 'meta.title',
      site_url_id: 'u1',
    };
    const params = serializeIssueFilters(filters, 'IC==');
    expect(parseIssueFilters(params)).toEqual(filters);
    expect(parseCursor(params)).toBe('IC==');
  });

  it('omits empty issue fields', () => {
    expect(serializeIssueFilters(emptyIssueFilters).toString()).toBe('');
  });

  it('maps to request params, dropping empties', () => {
    expect(toIssueParams({ ...emptyIssueFilters, severity: 'low' }, 'C', 50)).toEqual({
      cursor: 'C',
      limit: 50,
      severity: 'low',
      category: undefined,
      dimension: undefined,
      rule_id: undefined,
      site_url_id: undefined,
    });
  });

  it('changeIssueFilters drops the cursor and issueFiltersEqual compares', () => {
    const { cursor } = changeIssueFilters(emptyIssueFilters, { severity: 'high' });
    expect(cursor).toBeNull();
    expect(issueFiltersEqual(emptyIssueFilters, emptyIssueFilters)).toBe(true);
    expect(issueFiltersEqual(emptyIssueFilters, { ...emptyIssueFilters, severity: 'x' })).toBe(false);
  });
});
