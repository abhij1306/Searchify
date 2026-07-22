import { ArrowRight, KeyRound, PenLine, Sigma } from 'lucide-react';
import Link from 'next/link';

import { BLOG_EMPTY_STATE, POSTS, type BlogPost } from '@/lib/marketing-content/blog';

/**
 * BlogIndex — /blog below the shared chrome. Copy and structure are verbatim
 * from the approved mockup (/code/.plans/designs/page-blog.html): centered
 * page hero, featured-post slot (first post in the array, larger card), the
 * post-card grid for the remaining posts, and the empty state when the typed
 * posts array is empty. Data comes from lib/marketing-content/blog — the
 * single import site.
 *
 * Heading-rule note: post titles are rendered as styled paragraphs
 * (.featured-title / .post-card-title), NOT h2/h3 like the mockup — the
 * placeholder title contains "Searchify" and no h2–h6 may contain the
 * product name (keeps heading queries unambiguous in tests).
 */

function TagRow({ tags }: Readonly<{ tags: readonly string[] }>) {
  return (
    <div className="tag-row">
      {tags.map((tag) => (
        <span className="post-tag" key={tag}>
          {tag}
        </span>
      ))}
    </div>
  );
}

function PostMeta({ post, withSep }: Readonly<{ post: BlogPost; withSep?: boolean }>) {
  return (
    <div className="post-meta">
      <span>{post.date}</span>
      {withSep ? (
        <span className="sep" aria-hidden="true">
          ·
        </span>
      ) : null}
      <span>{post.readTime}</span>
    </div>
  );
}

/** Featured-post slot: posts[0] as the large split card (filtered from the grid). */
function FeaturedPost({ post }: Readonly<{ post: BlogPost }>) {
  const href = `/blog/${post.slug}`;
  return (
    <section className="featured-wrap" aria-label="Featured post">
      <div className="container">
        <div className="card rim featured">
          <div className="featured-copy">
            <TagRow tags={post.tags} />
            <p className="featured-title">
              <Link href={href}>{post.title}</Link>
            </p>
            <p className="excerpt">{post.excerpt}</p>
            <Link className="read-link" href={href}>
              Read the post
              <ArrowRight className="arr" size={14} strokeWidth={2.2} aria-hidden />
            </Link>
            <PostMeta post={post} withSep />
          </div>
          {/* Cover art is user-supplied per post; this slot shows the
              empty-image treatment from the mockup until then. */}
          <div className="featured-visual">
            <span className="todo-tag">[post cover image — user supplied]</span>
          </div>
        </div>
      </div>
    </section>
  );
}

/** Post-card grid: every post after the featured one. */
function PostGrid({ posts }: Readonly<{ posts: readonly BlogPost[] }>) {
  return (
    <section className="post-list" aria-label="All posts">
      <div className="container">
        <div className="list-head">
          <span className="panel-label">All notes</span>
          <span className="post-count">
            {posts.length} {posts.length === 1 ? 'post' : 'posts'}
          </span>
        </div>
        <div className="post-grid">
          {posts.map((post) => (
            <article className="card post-card" key={post.slug}>
              <TagRow tags={post.tags} />
              <p className="post-card-title">
                <Link href={`/blog/${post.slug}`}>{post.title}</Link>
              </p>
              <p className="excerpt">{post.excerpt}</p>
              <PostMeta post={post} />
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

/** Rendered INSTEAD of the featured slot + grid when the posts array is empty. */
function EmptyState() {
  return (
    <div className="container">
      <section className="empty-wrap" aria-label="No posts yet">
        <div className="empty-state">
          <span className="empty-icon">
            <PenLine size={20} strokeWidth={1.8} aria-hidden />
          </span>
          <h2>{BLOG_EMPTY_STATE.heading}</h2>
          <p>{BLOG_EMPTY_STATE.body}</p>
          <div className="hero-ctas">
            <Link className="btn btn-primary" href="/register">
              Get started
              <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
            </Link>
            <Link className="btn btn-ghost" href="/faq">
              Read the FAQ
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}

export function BlogIndex() {
  const [featured, ...rest] = POSTS;
  return (
    <>
      <header className="page-hero">
        <div className="hero-inner container">
          <span className="eyebrow">The Searchify blog</span>
          <h1>
            Notes on <span className="grad-text">AI{'\u00a0'}visibility</span>.
          </h1>
          <p className="hero-sub">
            Essays, release notes, and field reports on answer-engine optimization — evidence-first,
            and straight from the team building Searchify.
          </p>
        </div>
      </header>
      {featured ? (
        <>
          <FeaturedPost post={featured} />
          {/* The grid renders only when posts exist beyond the featured one —
              an "All notes" header over an empty grid would be dead chrome. */}
          {rest.length > 0 ? <PostGrid posts={rest} /> : null}
        </>
      ) : (
        <EmptyState />
      )}
      <section className="final-cta" aria-label="Get started">
        <div className="container">
          <h2>
            See the product
            <br />
            <span className="grad-text">the notes are about.</span>
          </h2>
          <p>Start your first audit in minutes. Bring your own keys, keep the evidence.</p>
          <div className="hero-ctas">
            <Link className="btn btn-primary" href="/register">
              Get started
              <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
            </Link>
            <Link className="btn btn-ghost" href="/faq">
              Read the FAQ
            </Link>
          </div>
          <div className="trust">
            <span>
              <KeyRound strokeWidth={1.8} aria-hidden />
              Bring your own API keys
            </span>
            <span className="sep" aria-hidden="true">
              ·
            </span>
            <span>
              <Sigma strokeWidth={1.8} aria-hidden />
              Deterministic scoring
            </span>
          </div>
        </div>
      </section>
    </>
  );
}
