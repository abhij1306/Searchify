import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { COUNTRY_OPTIONS, LANGUAGE_OPTIONS } from '@/lib/setup/markets';

import { MarketSelect } from './market-select';

function renderSelect(overrides?: Partial<Parameters<typeof MarketSelect>[0]>) {
  const onChange = vi.fn();
  const utils = render(
    <MarketSelect
      ariaLabel="Country"
      value="US"
      onChange={onChange}
      options={COUNTRY_OPTIONS}
      placeholder="Search countries…"
      {...overrides}
    />,
  );
  return { onChange, ...utils };
}

describe('MarketSelect', () => {
  it('shows the committed option label and opens the full list on focus', async () => {
    const user = userEvent.setup();
    renderSelect();

    const input = screen.getByRole('combobox', { name: /^country$/i });
    expect(input).toHaveValue('United States');

    await user.click(input);
    const listbox = screen.getByRole('listbox', { name: /^country$/i });
    expect(listbox).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(COUNTRY_OPTIONS.length);
  });

  it('filters options while typing and commits the clicked option', async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect();

    const input = screen.getByRole('combobox', { name: /^country$/i });
    await user.click(input);
    await user.type(input, 'ger');

    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('Germany');

    await user.click(options[0]);
    expect(onChange).toHaveBeenCalledWith('DE');
  });

  it('commits the highlighted option with ArrowDown + Enter', async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect({ value: '' });

    const input = screen.getByRole('combobox', { name: /^country$/i });
    await user.click(input);
    await user.type(input, 'aus'); // Australia, Austria
    await user.keyboard('{ArrowDown}{Enter}');

    expect(onChange).toHaveBeenCalledWith('AT');
  });

  it('reverts the text to the committed label on Escape', async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect();

    const input = screen.getByRole('combobox', { name: /^country$/i });
    await user.click(input);
    await user.type(input, 'zzz');
    await user.keyboard('{Escape}');

    expect(onChange).not.toHaveBeenCalled();
    expect(input).toHaveValue('United States');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('discards uncommitted text on blur without firing onChange', async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect();

    const input = screen.getByRole('combobox', { name: /^country$/i });
    await user.click(input);
    await user.type(input, 'France');
    await user.tab();

    expect(onChange).not.toHaveBeenCalled();
    await screen.findByDisplayValue('United States');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('renders the raw code for an off-list stored value instead of a blank field', () => {
    renderSelect({ value: 'CZ' });
    expect(screen.getByRole('combobox', { name: /^country$/i })).toHaveValue('CZ');
  });

  it('matches on the option code as well as the label', async () => {
    const user = userEvent.setup();
    renderSelect({ value: 'en', options: LANGUAGE_OPTIONS, ariaLabel: 'Language' });

    const input = screen.getByRole('combobox', { name: /^language$/i });
    await user.click(input);
    await user.type(input, 'pt-br');

    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('Portuguese (Brazil)');
  });
});
