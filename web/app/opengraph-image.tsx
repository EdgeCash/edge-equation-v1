/**
 * Default site Open Graph image — used by `/` and any page that
 * doesn't define its own.
 *
 * Generated dynamically at request time via Next's `ImageResponse`
 * so a tone tweak or brand-color change reflects on the next
 * request without a redeploy.
 */

import { OG_CONTENT_TYPE, OG_SIZE, ogResponse } from "../lib/og-template";


export const runtime = "nodejs";
export const alt = "Edge Equation — Facts. Not Feelings.";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;


export default async function OG() {
  return ogResponse({
    eyebrow: "MLB · WNBA · NFL · NCAAF",
    headline: "Facts. Not Feelings.",
    sub:
      "Transparent sports analytics. Honest modeling, rigorous testing, "
      + "every pick tracked against the closing line.",
    stats: [
      { label: "Daily card", value: "by 11 AM CDT" },
      { label: "Markets shipped", value: "after Brier <0.246" },
      { label: "Tracking", value: "100% CLV" },
    ],
  });
}
