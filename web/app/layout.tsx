import type { Metadata, Viewport } from "next";
import { Inter, Caveat } from "next/font/google";
import { Navigation } from "../components/Navigation";
import { Footer } from "../components/Footer";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const caveat = Caveat({
  subsets: ["latin"],
  variable: "--font-caveat",
  display: "swap",
});


// Site URL anchor — Open Graph + Twitter Card crawlers fetch
// images relative to this base. Vercel sets `NEXT_PUBLIC_SITE_URL`
// in production; preview builds fall back to the canonical
// edgeequation.com so a tweet of a preview link still resolves.
const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL
  || "https://edgeequation.com"
).replace(/\/+$/, "");


export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "Edge Equation — Facts. Not Feelings.",
    template: "%s · Edge Equation",
  },
  description:
    "Transparent, high-signal sports analytics. Honest modeling, rigorous testing, and public learning. We track Brier, ROI, and CLV — and we publish empty cards when the math says pass.",
  applicationName: "Edge Equation",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    title: "Edge Equation — Facts. Not Feelings.",
    description:
      "Transparent, high-signal sports analytics across MLB, WNBA, NFL, NCAAF. Daily card by 11 AM CDT. CLV-tracked. No hype.",
    type: "website",
    siteName: "Edge Equation",
    url: SITE_URL,
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: "Edge Equation — Facts. Not Feelings.",
    description:
      "Transparent sports analytics across MLB, WNBA, NFL, NCAAF. Daily card by 11 AM CDT. CLV-tracked.",
  },
};


export const viewport: Viewport = {
  themeColor: "#0a1421",
};


export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${caveat.variable}`}>
      <body className="font-sans">
        <div className="relative min-h-screen flex flex-col">
          <Navigation />
          <main className="flex-1 relative">{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
