import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Alert } from './alert';
import { Badge } from './badge';
import { Button } from './button';
import { buttonVariants } from './button-variants';
import { Card, CardContent, CardEyebrow, CardHeader, CardTitle } from './card';
import { Donut } from './donut';
import { Field } from './field';
import { Input } from './input';
import { ScoreRing } from './score-ring';
import { Skeleton } from './skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './table';
import { TrendChart } from './trend-chart';
import { scoreBand } from './score-band';

describe('Button', () => {
  it('renders default variant/size classes', () => {
    render(<Button>Save</Button>);
    const btn = screen.getByRole('button', { name: 'Save' });
    // primary variant → midnight monochrome pill (text-primary bg, bg-base
    // text, pill radius); md size → control-height
    expect(btn.className).toContain('bg-foreground');
    expect(btn.className).toContain('text-background');
    expect(btn.className).toContain('rounded-full');
    expect(btn.className).toContain('h-[var(--control-height)]');
    // real <button> defaults to type=button (no accidental submit)
    expect(btn).toHaveAttribute('type', 'button');
  });

  it('applies the requested variant and size', () => {
    render(
      <Button variant="destructive" size="lg">
        Delete
      </Button>,
    );
    const btn = screen.getByRole('button', { name: 'Delete' });
    expect(btn.className).toContain('bg-danger');
    expect(btn.className).toContain('h-[var(--control-height-lg)]');
  });

  it('renders as the child element when asChild is set (Radix Slot)', () => {
    render(
      <Button asChild variant="secondary">
        <a href="/next">Go</a>
      </Button>,
    );
    const link = screen.getByRole('link', { name: 'Go' });
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/next');
    // The button surface classes are forwarded onto the anchor.
    expect(link.className).toContain('bg-elevated');
    // asChild must NOT inject a type attribute onto the anchor.
    expect(link).not.toHaveAttribute('type');
  });

  it('buttonVariants is a pure class generator', () => {
    expect(buttonVariants({ variant: 'ghost', size: 'sm' })).toContain(
      'h-[var(--control-height-sm)]',
    );
  });
});

describe('Badge', () => {
  it('maps status variant to the success token classes', () => {
    render(
      <Badge variant="status" value="success">
        Configured
      </Badge>,
    );
    const badge = screen.getByText('Configured');
    expect(badge.className).toContain('bg-success-bg');
    expect(badge.className).toContain('text-success-text');
    expect(badge.className).toContain('border-success-border');
    // Pill radius + mono chip type.
    expect(badge.className).toContain('rounded-full');
    expect(badge.className).toContain('font-mono');
  });

  it('maps sentiment variant to sentiment tokens', () => {
    render(
      <Badge variant="sentiment" value="negative">
        Negative
      </Badge>,
    );
    expect(screen.getByText('Negative').className).toContain('bg-sentiment-negative-bg');
  });

  it('maps classification variant to citation tokens', () => {
    render(
      <Badge variant="classification" value="owned">
        Owned
      </Badge>,
    );
    expect(screen.getByText('Owned').className).toContain('bg-citation-owned-bg');
  });

  it('maps run-status variant to run-status tokens', () => {
    render(
      <Badge variant="run-status" value="completed">
        Completed
      </Badge>,
    );
    const badge = screen.getByText('Completed');
    expect(badge.className).toContain('bg-run-completed-bg');
    expect(badge.className).toContain('text-run-completed');
  });

  it('falls back to the neutral token when no variant is given', () => {
    render(<Badge>Draft</Badge>);
    expect(screen.getByText('Draft').className).toContain('bg-neutral-bg');
  });
});

describe('Card', () => {
  it('renders panel surface with header/title/content slots', () => {
    render(
      <Card data-testid="card">
        <CardHeader>
          <CardTitle>Visibility</CardTitle>
        </CardHeader>
        <CardContent>Body</CardContent>
      </Card>,
    );
    expect(screen.getByTestId('card').className).toContain('bg-panel');
    expect(screen.getByText('Visibility').tagName).toBe('H3');
    expect(screen.getByText('Body')).toBeInTheDocument();
  });

  it('CardEyebrow renders the mono panel-label pattern (never a heading)', () => {
    render(<CardEyebrow>Visibility score</CardEyebrow>);
    const eyebrow = screen.getByText('Visibility score');
    expect(eyebrow.tagName).toBe('SPAN');
    expect(eyebrow.className).toContain('font-mono');
    expect(eyebrow.className).toContain('text-2xs');
    expect(eyebrow.className).toContain('uppercase');
    expect(eyebrow.className).toContain('tracking-[0.08em]');
  });
});

describe('Table (dense)', () => {
  it('renders header and rows with dense heights', () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Prompt</TableHead>
            <TableHead numeric>Score</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>How good is X?</TableCell>
            <TableCell numeric>82</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    const headers = screen.getAllByRole('columnheader');
    expect(headers).toHaveLength(2);
    // Sticky 32px header height + uppercase mono eyebrow.
    expect(headers[0].className).toContain('h-[var(--table-header-height)]');
    expect(headers[0].className).toContain('uppercase');
    expect(headers[0].className).toContain('font-mono');
    expect(headers[0].className).toContain('sticky');
    // Numeric header/cell align right + tabular nums.
    expect(headers[1].className).toContain('tabular-nums');

    const rows = screen.getAllByRole('row');
    // 1 header row + 1 body row.
    expect(rows).toHaveLength(2);
    const bodyRow = rows[1];
    expect(bodyRow.className).toContain('h-[var(--table-row-height)]');
    expect(screen.getByText('82').className).toContain('tabular-nums');
  });
});

describe('Input + Field', () => {
  it('wires label to input via generated id, and surfaces errors', () => {
    render(
      <Field label="Email" error="Required">
        {(fieldProps) => <Input placeholder="you@co" {...fieldProps} />}
      </Field>,
    );
    const input = screen.getByPlaceholderText('you@co');
    // Field associates the label htmlFor with the input id.
    const label = screen.getByText('Email');
    expect(label).toHaveAttribute('for', input.id);
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('Required');
    expect(input.className).toContain('h-[var(--control-height)]');
  });
});

describe('Alert', () => {
  it('renders a role=alert region with tone token classes', () => {
    render(<Alert tone="danger">Something failed</Alert>);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Something failed');
    expect(alert.className).toContain('bg-danger-bg');
  });
});

describe('Skeleton', () => {
  it('renders the shimmer class and is aria-hidden', () => {
    render(<Skeleton className="h-4 w-20" />);
    const el = document.querySelector('.skeleton');
    expect(el).not.toBeNull();
    expect(el).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('scoreBand mapping', () => {
  it('maps values to the four bands', () => {
    expect(scoreBand(10)).toBe('low');
    expect(scoreBand(30)).toBe('mid');
    expect(scoreBand(60)).toBe('good');
    expect(scoreBand(90)).toBe('high');
  });
});

describe('ScoreRing', () => {
  it('renders an ARIA-labelled ring with the score-band stroke and center value', () => {
    render(<ScoreRing value={82} />);
    const ring = screen.getByRole('img', { name: 'Visibility score: 82%' });
    expect(ring).toBeInTheDocument();
    // High band (>=75) → score-high stroke on the progress arc.
    expect(ring.querySelector('.stroke-score-high')).not.toBeNull();
    // Center mono value.
    expect(screen.getByText('82')).toBeInTheDocument();
  });

  it('clamps out-of-range values', () => {
    render(<ScoreRing value={140} label="Overflow" />);
    expect(screen.getByRole('img', { name: 'Overflow' })).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
  });

  it('renders the display-size numeral with numeralSize="lg"', () => {
    render(<ScoreRing value={82} size={128} numeralSize="lg" />);
    const numeral = screen.getByText('82');
    expect(numeral.className).toContain('text-2xl');
    expect(numeral).toHaveAttribute('aria-hidden', 'true');
    // The accessible label still lives on the ring, not the numeral.
    expect(screen.getByRole('img', { name: 'Visibility score: 82%' })).toBeInTheDocument();
  });

  it('defaults to the text-lg numeral', () => {
    render(<ScoreRing value={82} />);
    expect(screen.getByText('82').className).toContain('text-lg');
  });
});

describe('Donut', () => {
  it('renders an ARIA-labelled donut summarising segment shares + a legend', () => {
    render(
      <Donut
        label="Share of voice"
        segments={[
          { label: 'You', value: 60, colorClass: 'stroke-accent' },
          { label: 'Competitor', value: 40, colorClass: 'stroke-citation-competitor' },
        ]}
      />,
    );
    const donut = screen.getByRole('img', {
      name: 'Share of voice: You 60%, Competitor 40%',
    });
    expect(donut).toBeInTheDocument();
    // Legend entries.
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.getByText('Competitor')).toBeInTheDocument();
  });
});

describe('TrendChart (cross-run Visibility trend)', () => {
  it('renders with an ARIA label describing the trend', () => {
    render(
      <TrendChart
        label="Visibility trend"
        data={[
          { label: 'Jun', value: 40 },
          { label: 'Jul', value: 70 },
        ]}
      />,
    );
    const chart = screen.getByRole('img', {
      name: 'Visibility trend: Trend from Jun (40) to Jul (70)',
    });
    expect(chart).toBeInTheDocument();
    // Line stroke uses the accent token.
    expect(chart.querySelector('.stroke-accent')).not.toBeNull();
  });

  it('renders a single point without a misleading slope or area', () => {
    render(<TrendChart label="Visibility trend" data={[{ label: 'Jul', value: 55 }]} />);
    const chart = screen.getByRole('img', {
      name: 'Visibility trend: Single point Jul (55)',
    });
    expect(chart).toBeInTheDocument();
    // No connecting line and no area fill for a single point — just a dot.
    expect(chart.querySelector('.stroke-accent')).toBeNull();
    expect(chart.querySelector('.fill-accent-soft')).toBeNull();
    expect(chart.querySelectorAll('circle.fill-accent')).toHaveLength(1);
  });

  it('renders an empty state with no data points', () => {
    render(<TrendChart label="Visibility trend" data={[]} />);
    expect(
      screen.getByRole('img', { name: 'Visibility trend: No trend data' }),
    ).toBeInTheDocument();
  });

  it('marks a version boundary with an accessible warning marker', () => {
    render(
      <TrendChart
        label="Visibility trend"
        data={[
          { label: 'Jun', value: 40 },
          { label: 'Jul', value: 70, versionChange: { note: 'Scoring rule scoring-v2 applied' } },
        ]}
      />,
    );
    const chart = screen.getByRole('img');
    const marker = chart.querySelector('[data-version-marker]');
    expect(marker).not.toBeNull();
    // The change is announced via a <title>, not conveyed by color alone.
    expect(
      within(chart as unknown as HTMLElement).getByText(/Scoring rule scoring-v2 applied/),
    ).toBeInTheDocument();
    // The dashed marker line uses the warning token (bridged), not raw hex.
    expect(chart.querySelector('.stroke-warning')).not.toBeNull();
  });

  it('renders null values as gaps, announces them, and never draws a zero dot', () => {
    render(
      <TrendChart
        label="Visibility trend"
        data={[
          { label: 'Jun', value: 40 },
          { label: 'Jul', value: 50 },
          { label: 'Aug', value: null },
          { label: 'Sep', value: 60 },
          { label: 'Oct', value: 70 },
        ]}
      />,
    );
    const chart = screen.getByRole('img');
    // Endpoints announce the numeric value; the gap is announced explicitly.
    expect(chart).toHaveAttribute(
      'aria-label',
      'Visibility trend: Trend from Jun (40) to Oct (70) Some points are unavailable and shown as gaps.',
    );
    // The null point produces NO dot: only the four available points have dots.
    expect(chart.querySelectorAll('circle.fill-accent')).toHaveLength(4);
    // The line splits across the gap into two separate multi-point sub-paths.
    expect(chart.querySelectorAll('path.stroke-accent')).toHaveLength(2);
  });

  it('announces an unavailable endpoint value as "unavailable"', () => {
    render(
      <TrendChart
        label="Visibility trend"
        data={[
          { label: 'Jun', value: null },
          { label: 'Jul', value: 55 },
        ]}
      />,
    );
    expect(
      screen.getByRole('img', {
        name: 'Visibility trend: Trend from Jun (unavailable) to Jul (55) Some points are unavailable and shown as gaps.',
      }),
    ).toBeInTheDocument();
  });

  it('renders a single available point among nulls as a lone dot (no slope)', () => {
    render(
      <TrendChart
        label="Visibility trend"
        data={[
          { label: 'Jun', value: null },
          { label: 'Jul', value: 55 },
          { label: 'Aug', value: null },
        ]}
      />,
    );
    const chart = screen.getByRole('img');
    // One dot for the lone available point; no line/area (segment length 1).
    expect(chart.querySelectorAll('circle.fill-accent')).toHaveLength(1);
    expect(chart.querySelector('path.stroke-accent')).toBeNull();
    expect(chart.querySelector('.fill-accent-soft')).toBeNull();
  });

  it('scales count metrics against a custom domainMax instead of clamping to 100', () => {
    const data = [
      { label: 'Jun', value: 250 },
      { label: 'Jul', value: 500 },
    ];
    const { unmount } = render(<TrendChart label="Clicks trend" data={data} domainMax={500} />);
    let chart = screen.getByRole('img', {
      name: 'Clicks trend: Trend from Jun (250) to Jul (500)',
    });
    let dots = chart.querySelectorAll('circle.fill-accent');
    // height 120, padding 8 → innerHeight 104: 250/500 → y=60, 500/500 → y=8.
    expect(dots[0]).toHaveAttribute('cy', '60');
    expect(dots[1]).toHaveAttribute('cy', '8');
    unmount();

    // Default domain (100) is unchanged: the same counts clamp to the top.
    render(<TrendChart label="Clicks trend" data={data} />);
    chart = screen.getByRole('img', {
      name: 'Clicks trend: Trend from Jun (250) to Jul (500)',
    });
    dots = chart.querySelectorAll('circle.fill-accent');
    expect(dots[0]).toHaveAttribute('cy', '8');
    expect(dots[1]).toHaveAttribute('cy', '8');
  });
});
