import { describe, expect, it } from 'vitest';

import {
  buildLaunchPayload,
  canLaunch,
  clampRepetitions,
  DEFAULT_REPETITIONS,
  MAX_REPETITIONS,
  MIN_REPETITIONS,
  toggleEngine,
  type LaunchSelection,
} from './launch';

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const SET_ID = '22222222-2222-4222-8222-222222222222';

function selection(overrides: Partial<LaunchSelection> = {}): LaunchSelection {
  return {
    projectId: PROJECT_ID,
    promptSetId: SET_ID,
    engines: ['gemini'],
    repetitions: 3,
    ...overrides,
  };
}

describe('clampRepetitions', () => {
  it('clamps below/above the accepted range', () => {
    expect(clampRepetitions(0)).toBe(MIN_REPETITIONS);
    expect(clampRepetitions(99)).toBe(MAX_REPETITIONS);
    expect(clampRepetitions(4)).toBe(4);
  });

  it('falls back to the default for a non-finite value', () => {
    expect(clampRepetitions(Number.NaN)).toBe(DEFAULT_REPETITIONS);
  });
});

describe('canLaunch', () => {
  it('requires a prompt set and at least one engine', () => {
    expect(canLaunch(selection())).toBe(true);
    expect(canLaunch(selection({ promptSetId: null }))).toBe(false);
    expect(canLaunch(selection({ engines: [] }))).toBe(false);
  });
});

describe('buildLaunchPayload', () => {
  it('builds the POST /audits body from a launchable selection', () => {
    const payload = buildLaunchPayload(
      selection({ engines: ['gemini', 'claude'], repetitions: 5 }),
    );
    expect(payload).toEqual({
      project_id: PROJECT_ID,
      prompt_set_id: SET_ID,
      engines: ['gemini', 'claude'],
      repetitions: 5,
    });
  });

  it('clamps the repetition count into range', () => {
    expect(buildLaunchPayload(selection({ repetitions: 42 })).repetitions).toBe(MAX_REPETITIONS);
  });

  it('throws on an incomplete selection', () => {
    expect(() => buildLaunchPayload(selection({ engines: [] }))).toThrow();
    expect(() => buildLaunchPayload(selection({ promptSetId: null }))).toThrow();
  });
});

describe('toggleEngine', () => {
  it('adds and removes an engine immutably', () => {
    expect(toggleEngine(['gemini'], 'claude')).toEqual(['gemini', 'claude']);
    expect(toggleEngine(['gemini', 'claude'], 'gemini')).toEqual(['claude']);
  });
});
