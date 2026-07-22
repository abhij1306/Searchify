import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { TooltipProvider } from '@/components/ui/tooltip';
import type { Prompt } from '@/lib/api/types';

import { PromptTable } from './prompt-table';

function makePrompt(n: number, overrides: Partial<Prompt> = {}): Prompt {
  return {
    id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
    prompt_set_id: '11111111-1111-4111-8111-111111111111',
    text: `Prompt number ${n}`,
    theme: 'Comfort',
    intent: 'discovery',
    branded: false,
    enabled: true,
    status: 'active',
    origin: 'manual',
    ...overrides,
  } as Prompt;
}

function renderTable(prompts: Prompt[]) {
  return render(
    <TooltipProvider>
      <PromptTable
        prompts={prompts}
        onEdit={() => {}}
        onDelete={() => {}}
        onToggleEnabled={() => {}}
      />
    </TooltipProvider>,
  );
}

describe('PromptTable pagination', () => {
  it('pages through rows with the mono indicator and ghost buttons', async () => {
    const user = userEvent.setup();
    const prompts = Array.from({ length: 12 }, (_, i) => makePrompt(i + 1));
    renderTable(prompts);

    const table = screen.getByRole('table');
    // Page 1 shows the first 10 rows with the mono page indicator.
    expect(within(table).getByText('Prompt number 1')).toBeInTheDocument();
    expect(within(table).queryByText('Prompt number 11')).not.toBeInTheDocument();
    expect(screen.getByText('1–10 of 12 prompts')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next page' }));
    expect(within(table).queryByText('Prompt number 1')).not.toBeInTheDocument();
    expect(within(table).getByText('Prompt number 11')).toBeInTheDocument();
    expect(screen.getByText('11–12 of 12 prompts')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(within(table).getByText('Prompt number 1')).toBeInTheDocument();
    expect(screen.getByText('1–10 of 12 prompts')).toBeInTheDocument();
  });

  it('clamps the current page when the list shrinks instead of resetting', async () => {
    const user = userEvent.setup();
    const prompts = Array.from({ length: 12 }, (_, i) => makePrompt(i + 1));
    const { rerender } = render(
      <TooltipProvider>
        <PromptTable
          prompts={prompts}
          onEdit={() => {}}
          onDelete={() => {}}
          onToggleEnabled={() => {}}
        />
      </TooltipProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'Next page' }));
    expect(screen.getByText('11–12 of 12 prompts')).toBeInTheDocument();

    // A refetch/filter shrinking the list clamps the page into range.
    rerender(
      <TooltipProvider>
        <PromptTable
          prompts={prompts.slice(0, 5)}
          onEdit={() => {}}
          onDelete={() => {}}
          onToggleEnabled={() => {}}
        />
      </TooltipProvider>,
    );
    expect(screen.getByText('1–5 of 5 prompts')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
  });

  it('keeps row actions wired on paginated rows', async () => {
    const user = userEvent.setup();
    const onToggleEnabled = vi.fn();
    render(
      <TooltipProvider>
        <PromptTable
          prompts={[makePrompt(1)]}
          onEdit={() => {}}
          onDelete={() => {}}
          onToggleEnabled={onToggleEnabled}
        />
      </TooltipProvider>,
    );

    await user.click(screen.getByRole('switch', { name: 'Disable prompt' }));
    expect(onToggleEnabled).toHaveBeenCalledTimes(1);
  });
});
