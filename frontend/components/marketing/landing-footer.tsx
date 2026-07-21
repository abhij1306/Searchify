import Link from 'next/link';

import { LogoCube } from './logo-cube';

/** LandingFooter — minimal footer: wordmark, product line, anchor/auth links. */
export function LandingFooter() {
  return (
    <footer className="footer">
      <div className="footer-inner container">
        <Link className="wordmark" href="/" aria-label="Searchify home">
          <LogoCube size={22} />
          <span>Searchify</span>
        </Link>
        <span className="footer-copy">© 2026 Searchify · A CUBE27 product</span>
        <nav className="footer-links" aria-label="Footer">
          <a href="#product">Product</a>
          <a href="#how-it-works">How it works</a>
          <a href="#evidence">Evidence</a>
          <Link href="/login">Sign in</Link>
          <Link href="/register">Get started</Link>
        </nav>
      </div>
    </footer>
  );
}
