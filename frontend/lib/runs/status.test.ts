import { describe, expect, it } from 'vitest';

import {
  auditBadgeValue,
  auditStatusLabel,
  classificationBadgeValue,
  classificationLabel,
  executionBadgeValue,
  executionStatusLabel,
  formatDateTime,
  isAuditCancelable,
  shouldPollAudit,
} from './status';

describe('shouldPollAudit', () => {
  it('polls while non-terminal (including reporting) and stops when terminal', () => {
    expect(shouldPollAudit('running')).toBe(true);
    expect(shouldPollAudit('queued')).toBe(true);
    // `reporting` is still non-terminal: keep polling until it terminalizes.
    expect(shouldPollAudit('reporting')).toBe(true);
    expect(shouldPollAudit('analyzing')).toBe(true);
    expect(shouldPollAudit('completed')).toBe(false);
    expect(shouldPollAudit('partially_completed')).toBe(false);
    expect(shouldPollAudit('failed')).toBe(false);
    expect(shouldPollAudit('cancelled')).toBe(false);
  });
});

describe('isAuditCancelable', () => {
  it('mirrors the backend AUDIT_ACTIVE_STATUSES (reporting is NOT cancelable)', () => {
    expect(isAuditCancelable('draft')).toBe(true);
    expect(isAuditCancelable('validating')).toBe(true);
    expect(isAuditCancelable('queued')).toBe(true);
    expect(isAuditCancelable('running')).toBe(true);
    expect(isAuditCancelable('analyzing')).toBe(true);
    // The backend rejects REPORTING -> CANCELLED; the button must be disabled.
    expect(isAuditCancelable('reporting')).toBe(false);
    expect(isAuditCancelable('completed')).toBe(false);
    expect(isAuditCancelable('partially_completed')).toBe(false);
    expect(isAuditCancelable('failed')).toBe(false);
    expect(isAuditCancelable('cancelled')).toBe(false);
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
