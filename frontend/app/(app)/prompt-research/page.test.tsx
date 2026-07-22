import { describe, expect, it, vi } from 'vitest';

// `redirect` throws NEXT_REDIRECT in the real runtime; mirror that so the
// assertion observes the call instead of silently passing through.
const redirectMock = vi.fn((url: string): never => {
  throw new Error(`NEXT_REDIRECT ${url}`);
});
vi.mock('next/navigation', () => ({
  redirect: (url: string) => redirectMock(url),
}));

import PromptResearchPage from './page';

describe('PromptResearchPage', () => {
  it('redirects to /prompts — the management UI lives there in manage mode', async () => {
    await expect(PromptResearchPage({ searchParams: Promise.resolve({}) })).rejects.toThrow(
      'NEXT_REDIRECT /prompts',
    );
    expect(redirectMock).toHaveBeenCalledWith('/prompts');
  });

  it('passes ?mode=manage through to the manage-mode deep link', async () => {
    await expect(
      PromptResearchPage({ searchParams: Promise.resolve({ mode: 'manage' }) }),
    ).rejects.toThrow('NEXT_REDIRECT /prompts?mode=manage');
    expect(redirectMock).toHaveBeenCalledWith('/prompts?mode=manage');
  });
});
