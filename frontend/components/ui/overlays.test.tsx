import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Dialog } from './dialog';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownTrigger,
} from './dropdown';
import { HistoryDrawer } from './history-drawer';
import { Tooltip, TooltipProvider } from './tooltip';

describe('Dialog', () => {
  it('renders title/description/children/footer when open', () => {
    render(
      <Dialog
        open
        onOpenChange={() => {}}
        title="Launch audit"
        description="Pick engines"
        footer={<button type="button">Confirm</button>}
      >
        <p>Body content</p>
      </Dialog>,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Launch audit')).toBeInTheDocument();
    expect(screen.getByText('Pick engines')).toBeInTheDocument();
    expect(screen.getByText('Body content')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close dialog' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
  });

  it('renders nothing when closed', () => {
    render(
      <Dialog open={false} onOpenChange={() => {}} title="Hidden">
        <p>Nope</p>
      </Dialog>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('Dropdown', () => {
  it('exposes a trigger with menu semantics (closed by default)', () => {
    render(
      <Dropdown>
        <DropdownTrigger>Menu</DropdownTrigger>
        <DropdownContent>
          <DropdownItem>Edit</DropdownItem>
        </DropdownContent>
      </Dropdown>,
    );
    const trigger = screen.getByRole('button', { name: 'Menu' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    // Items are not mounted until opened.
    expect(screen.queryByText('Edit')).not.toBeInTheDocument();
  });
});

describe('Tooltip', () => {
  it('renders its trigger child', () => {
    render(
      <TooltipProvider>
        <Tooltip content="Coming soon">
          <button type="button">Generate</button>
        </Tooltip>
      </TooltipProvider>,
    );
    expect(screen.getByRole('button', { name: 'Generate' })).toBeInTheDocument();
  });
});

describe('HistoryDrawer', () => {
  it('renders items with run-status badges when open', () => {
    render(
      <HistoryDrawer
        open
        onOpenChange={() => {}}
        onSelect={() => {}}
        items={[
          {
            id: 'abcdef12-3456-7890-abcd-ef1234567890',
            status: 'completed',
            createdAt: '2026-07-10T00:00:00Z',
            label: 'Audit #1',
          },
        ]}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Run history')).toBeInTheDocument();
    expect(screen.getByText('Audit #1')).toBeInTheDocument();
    // Short id + run-status badge label.
    expect(screen.getByText('#abcdef12')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
  });

  it('shows an empty state when there is no history', () => {
    render(
      <HistoryDrawer open onOpenChange={() => {}} onSelect={() => {}} items={[]} />,
    );
    expect(screen.getByText('No history found.')).toBeInTheDocument();
  });
});
