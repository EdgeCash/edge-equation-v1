/**
 * /daily-card OG image — surfaces today's pick count + ROI hint.
 *
 * Reads from the unified daily feed at request time so the share
 * preview always reflects today's slate. When the feed is empty
 * or unreadable, falls back to the static brand card.
 */

import { OG_CONTENT_TYPE, OG_SIZE, ogResponse } from "../../lib/og-template";
import {
  SPORTS,
  SPORT_LABEL,
  getDailyFeed,
  picksForSport,
  gameParlaysForSport,
  propParlaysForSport,
} from "../../lib/feed";


export const runtime = "nodejs";
export const alt = "Edge Equation — Today's daily card";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;


export default async function OG() {
  const feed = await getDailyFeed();
  const date = feed?.date ?? new Date().toISOString().slice(0, 10);

  let totalPicks = 0;
  let totalParlays = 0;
  const breakdown: string[] = [];
  for (const sport of SPORTS) {
    const picks = picksForSport(feed, sport).length;
    const parlays =
      gameParlaysForSport(feed, sport).length
      + propParlaysForSport(feed, sport).length;
    totalPicks += picks;
    totalParlays += parlays;
    if (picks > 0 || parlays > 0) {
      breakdown.push(
        `${SPORT_LABEL[sport]} ${picks}P${parlays > 0 ? `/${parlays}T` : ""}`,
      );
    }
  }

  const hasAny = totalPicks > 0 || totalParlays > 0;
  const headline = hasAny
    ? `${totalPicks} pick${totalPicks === 1 ? "" : "s"}` +
      (totalParlays > 0
        ? ` · ${totalParlays} parlay${totalParlays === 1 ? "" : "s"}`
        : "")
    : "No qualifying plays today.";
  const sub = hasAny
    ? breakdown.join(" · ")
    : "When the math says pass, the engine publishes nothing.";

  return ogResponse({
    eyebrow: `Daily card · ${date}`,
    headline,
    sub,
    stats: hasAny
      ? [
          { label: "Picks", value: String(totalPicks), highlight: true },
          { label: "Parlay tickets", value: String(totalParlays) },
          { label: "Live by", value: "11 AM CDT" },
        ]
      : [
          { label: "Live by", value: "11 AM CDT" },
          { label: "Cards/year", value: "365" },
          { label: "Tone", value: "no force" },
        ],
  });
}
