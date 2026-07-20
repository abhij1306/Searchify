import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ContentMarkdown, safeUrlTransform } from './markdown';

describe('safeUrlTransform', () => {
  it('allows http/https/mailto and relative URLs', () => {
    expect(safeUrlTransform('https://example.com/a')).toBe('https://example.com/a');
    expect(safeUrlTransform('http://example.com')).toBe('http://example.com');
    expect(safeUrlTransform('mailto:hi@example.com')).toBe('mailto:hi@example.com');
    expect(safeUrlTransform('/pricing')).toBe('/pricing');
  });

  it('neutralises javascript:, data:, and vbscript: URLs', () => {
    expect(safeUrlTransform('javascript:alert(1)')).toBe('');
    expect(safeUrlTransform(' javascript:alert(1)')).toBe('');
    expect(safeUrlTransform('data:text/html,<script>alert(1)</script>')).toBe('');
    expect(safeUrlTransform('vbscript:msgbox(1)')).toBe('');
    expect(safeUrlTransform('JAVASCRIPT:alert(1)')).toBe('');
  });
});

describe('ContentMarkdown', () => {
  it('renders headings, lists, and GFM tables', () => {
    render(
      <ContentMarkdown
        markdown={'# Title\n\n- one\n- two\n\n| A | B |\n| - | - |\n| 1 | 2 |'}
      />,
    );
    expect(screen.getByRole('heading', { level: 1, name: 'Title' })).toBeInTheDocument();
    expect(screen.getByText('one')).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
  });

  it('never parses raw HTML (script/iframe arrive as escaped text)', () => {
    const { container } = render(
      <ContentMarkdown markdown={'hello <script>window.pwned = true</script> <b>bold?</b>'} />,
    );
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect((window as { pwned?: boolean }).pwned).toBeUndefined();
  });

  it('neutralises javascript: links and hardens external ones', () => {
    render(
      <ContentMarkdown
        markdown={'[bad](javascript:alert(1)) and [good](https://example.com)'}
      />,
    );
    const good = screen.getByRole('link', { name: 'good' });
    expect(good).toHaveAttribute('href', 'https://example.com');
    expect(good).toHaveAttribute('rel', 'noopener noreferrer');
    expect(good).toHaveAttribute('target', '_blank');
    const bad = screen.getByText('bad').closest('a');
    expect(bad?.getAttribute('href') ?? '').toBe('');
  });

  it('drops images entirely', () => {
    const { container } = render(
      <ContentMarkdown markdown={'![tracker](https://evil.example/pixel.png)'} />,
    );
    expect(container.querySelector('img')).toBeNull();
  });
});
