import { describe, expect, it } from 'vitest';

import {
  auditBadgeValue,
  auditStatusLabel,
  classificationBadgeValue,
  classificationLabel,
  executionBadgeValue,
  executionStatusLabel,
  formatDateTime,
  isAuditActive,
} from './status';

describe('isAuditActive', () => {
  it('is true for in-flight statuses and false for terminal ones', () => {
    expect(isAuditActive('running')).toBe(true);
    expect(isAuditActive('queued')).toBe(true);
    expect(isAuditActive('reporting')).toBe(true);
    expect(isAuditActive('completed')).toBe(false);
    expect(isAuditActive('partially_completed')).toBe(false);
    expect(isAuditActive('failed')).toBe(false);
    expect(isAuditActive('cancelled')).toBe(false);
  });
});

describe('auditBadgeValue', () => {
  it('folds the extra statuses onto the eight badge values', () => {
    expect(auditBadgeValue('validating')).toBe('queued');
    expect(auditBadgeValue('reporting')).toBe('analyzing');
    expect(auditBadgeValue('partially_completed')).toBe('partial');
    expect(auditBadgeValue('running')).toBe('running');
  });
});

describe('auditStatusLabel', () => {
  it('title-cases underscored statuses', () => {
    expect(auditStatusLabel('partially_completed')).toBe('Partially Completed');
    expect(auditStatusLabel('running')).toBe('Running');
  });
});

describe('executionBadgeValue', () => {
  it('maps execution statuses onto the status badge space', () => {
    expect(executionBadgeValue('succeeded')).toBe('success');
    expect(executionBadgeValue('failed')).toBe('danger');
    expect(executionBadgeValue('cancelled')).toBe('danger');
    expect(executionBadgeValue('retry_wait')).toBe('warning');
    expect(executionBadgeValue('running')).toBe('info');
  });

  it('labels underscored statuses', () => {
    expect(executionStatusLabel('retry_wait')).toBe('Retry Wait');
  });
});

describe('classificationBadgeValue', () => {
  it('folds unintended onto the owned visual and maps the rest', () => {
    expect(classificationBadgeValue('owned')).toBe('owned');
    expect(classificationBadgeValue('unintended')).toBe('owned');
    expect(classificationBadgeValue('competitor')).toBe('competitor');
    expect(classificationBadgeValue('third_party')).toBe('third-party');
  });

  it('labels each classification distinctly', () => {
    expect(classificationLabel('unintended')).toBe('Owned (unintended)');
    expect(classificationLabel('third_party')).toBe('Third-party');
  });
});

describe('formatDateTime', () => {
  it('renders a placeholder for null and echoes an unparseable value', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('not-a-date')).toBe('not-a-date');
  });
});
