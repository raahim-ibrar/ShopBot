import "@fontsource-variable/manrope";
import "./globals.css";
import Link from "next/link";
import Icon from "./icons";

export const metadata = {
  title: "PortPair",
  description: "Shop chargers, cables, and tech accessories that fit your devices.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">Skip to content</a>
        <header className="site-header">
          <div className="site-width header-inner">
            <Link className="wordmark" href="/"><span className="brand-icon"><Icon name="plug" size={20} /></span>PortPair</Link>
            <nav aria-label="Main navigation">
              <Link href="/#catalog">Shop</Link>
              <Link href="/seller">Seller dashboard</Link>
            </nav>
          </div>
        </header>
        {children}
        <footer className="site-width footer"><span>PortPair</span><span>Demo store · Prices in USD</span></footer>
      </body>
    </html>
  );
}
