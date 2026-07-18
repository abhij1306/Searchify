import { describe, expect, it } from 'vitest';

import {
  SUMMARY_SEVERITIES,
  dimensionLabel,
  issueTitle,
  severityBadgeValue,
  severityCount,
  severityLabel,
} from './issues';

describe('severityBadgeValue', () => {
  it('maps critical and high to danger', () => {
    expect(severityBadgeValue('critical')).toBe('danger');
    expect(severityBadgeValue('high')).toBe('danger');
  });

  it('maps medium to warning and low/info to info', () => {
    expect(severityBadgeValue('medium')).toBe('warning');
    expect(severityBadgeValue('low')).toBe('info');
    expect(severityBadgeValue('info')).toBe('info');
  });
});

describe('labels', () => {
  it('uppercases severity labels', () => {
    expect(severityLabel('high')).toBe('HIGH');
    expect(severityLabel('medium')).toBe('MEDIUM');
  });

  it('folds critical into HIGH (three-tier catalog vocabulary)', () => {
    expect(severityLabel('critical')).toBe('HIGH');
  });

  it('maps dimensions to their catalog labels', () => {
    expect(dimensionLabel('aeo')).toBe('AEO');
    expect(dimensionLabel('technical')).toBe('TECHNICAL');
  });
});

describe('issueTitle fallback', () => {
  it('uses the title when present', () => {
    expect(issueTitle({ title: 'WebSite schema is missing', rule_id: 'aeo.website' })).toBe(
      'WebSite schema is missing',
    );
  });

  it('falls back to rule_id when the title is blank', () => {
    expect(issueTitle({ title: '   ', rule_id: 'aeo.website' })).toBe('aeo.website');
    expect(issueTitle({ title: '', rule_id: 'technical.canonical' })).toBe('technical.canonical');
  });
});

describe('severityCount', () => {
  it('folds critical into high', () => {
    expect(severityCount({ high: 10, critical: 2 }, 'high')).toBe(12);
  });

  it('reads medium/low directly and defaults missing keys to 0', () => {
    expect(severityCount({ medium: 23 }, 'medium')).toBe(23);
    expect(severityCount({}, 'low')).toBe(0);
    expect(severityCount({ high: 5 }, 'low')).toBe(0);
  });

  it('exposes the three-tier summary order', () => {
    expect(SUMMARY_SEVERITIES).toEqual(['high', 'medium', 'low']);
  });
});
