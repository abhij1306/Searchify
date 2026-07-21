import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { CompareDetailView } from '@/components/marketing/compare-detail';
import { COMPETITORS, type Competitor } from '@/lib/marketing-content/compare';

type PageParams = { competitor: string };

/** Every comparison in the module prerenders at build time (SSG). */
export function generateStaticParams(): PageParams[] {
  return COMPETITORS.map((competitor) => ({ competitor: competitor.slug }));
}

function findCompetitor(slug: string): Competitor | undefined {
  return COMPETITORS.find((competitor) => competitor.slug === slug);
}

// NOTE: no openGraph.images / metadataBase yet — there is no canonical public
// domain for the app, and OG image URLs must be absolute. Add both once the
// production domain exists.
export async function generateMetadata({
  params,
}: Readonly<{ params: Promise<PageParams> }>): Promise<Metadata> {
  const { competitor: slug } = await params;
  const competitor = findCompetitor(slug);
  if (!competitor) {
    return { title: 'Comparison not found' };
  }
  const title = `Searchify vs ${competitor.name}`;
  const description =
    `How Searchify compares to ${competitor.name}: engines covered, scoring model, evidence ` +
    'drill-down, BYOK privacy, open source, and site-health auditing. The Searchify column is ' +
    `sourced from our docs; the ${competitor.name} column is pending first-party verification.`;
  return {
    title,
    description,
    openGraph: {
      title,
      description,
      type: 'website',
      siteName: 'Searchify',
    },
    twitter: {
      card: 'summary',
      title,
      description,
    },
  };
}

/**
 * Comparison detail (`/compare/[competitor]`) — the single allowed sync-RSC
 * exception alongside /blog/[slug]: Next 16's `params` is a Promise, so the
 * route's default export is a thin async wrapper that resolves the slug
 * (404 for unknown slugs) and hands the module entry to the sync
 * CompareDetailView, which is what the RTL tests render directly.
 */
export default async function CompareDetailPage({
  params,
}: Readonly<{ params: Promise<PageParams> }>) {
  const { competitor: slug } = await params;
  const competitor = findCompetitor(slug);
  if (!competitor) {
    notFound();
  }
  return (
    <main>
      <CompareDetailView competitor={competitor} />
    </main>
  );
}
