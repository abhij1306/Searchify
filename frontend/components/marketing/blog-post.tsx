import { ArrowLeft, ArrowRight } from 'lucide-react';
import Link from 'next/link';

import type { BlogBlock, BlogPost } from '@/lib/marketing-content/blog';
import { GITHUB_URL } from '@/lib/marketing-content/social';

import { ByokTrust } from './byok-trust';

/**
 * BlogPostView — the SYNC view behind /blog/[slug] (the route's async wrapper
 * awaits `params`, finds the post, and renders this; keeping this component
 * sync lets the page test render it directly under Testing Library). Structure
 * and copy follow the approved mockup
 * (/code/.plans/designs/page-blog-post.html): back-link, tag row, h1 title,
 * byline, prose body from the typed BlogBlock array, CTA band.
 *
 * Scoped OUT per the plan: the mockup's prev/next post nav (needs ordering
 * metadata and ≥2 real posts to be meaningful — add when real content lands).
 * The mockup's dashed [TODO(user)] review frames are a mockup-only device and
 * are not carried over; placeholder field values render verbatim.
 */

/** First letter of the author name for the avatar tile ('[TODO(user)]' → 'T'). */
function authorInitial(author: string) {
  return author.match(/[a-z]/i)?.[0].toUpperCase() ?? '?';
}

function PostBlock({ block }: Readonly<{ block: BlogBlock }>) {
  switch (block.type) {
    case 'heading':
      return <h2>{block.text}</h2>;
    case 'list':
      return (
        <ul>
          {/* Keyed by index — items may repeat verbatim (the placeholder
              post's items are all '[TODO(user)]'). */}
          {block.items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      );
    case 'paragraph':
      return <p>{block.text}</p>;
  }
}

export function BlogPostView({ post }: Readonly<{ post: BlogPost }>) {
  return (
    <>
      <header className="article-hero">
        <div className="article-col container">
          <Link className="back-link" href="/blog">
            <ArrowLeft size={13} strokeWidth={2.2} aria-hidden />
            All notes
          </Link>
          <div className="tag-row">
            {post.tags.map((tag) => (
              <span className="post-tag" key={tag}>
                {tag}
              </span>
            ))}
          </div>
          <h1>{post.title}</h1>
          <div className="byline">
            <span className="byline-author">
              <span className="avatar" aria-hidden="true">
                {authorInitial(post.author)}
              </span>
              <span className="byline-name">{post.author}</span>
            </span>
            <span className="byline-meta">
              <span>{post.date}</span>
              <span className="sep" aria-hidden="true">
                ·
              </span>
              <span>{post.readTime}</span>
            </span>
          </div>
        </div>
      </header>
      <article className="container" aria-label="Post content">
        <div className="prose">
          <p className="lede">{post.excerpt}</p>
          {post.body.map((block, index) => (
            <PostBlock key={index} block={block} />
          ))}
        </div>
      </article>
      <section className="final-cta" aria-label="Get started">
        <div className="container">
          <h2>
            Make AI visibility
            <br />
            <span className="grad-text">measurable.</span>
          </h2>
          <p>Start your first audit in minutes. Bring your own keys, keep the evidence.</p>
          <div className="hero-ctas">
            <Link className="btn btn-primary" href="/register">
              Get started
              <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
            </Link>
            <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer">
              Star on GitHub
            </a>
          </div>
          <ByokTrust />
        </div>
      </section>
    </>
  );
}
