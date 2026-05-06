/**
 * /sport/<sport> OG image — per-sport hub preview with today's
 * pick count + parlay count.
 */

import {
  SPORTS,
  SPORT_LABEL,
  SportKey,
  gameParlaysForSport,
  getDailyFeed,
  picksForSport,
  propParlaysForSport,
} from "../../../lib/feed";
import {
  OG_CONTENT_TYPE,
  OG_SIZE,
  ogResponse,
} from "../../../lib/og-template";


export const runtime = "nodejs";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;
export const alt = "Edge Equation — Sport hub";


interface RouteParams {
  params: Promise<{ sport: string }>;
}


export default async function OG({ params }: RouteParams) {
  const { sport: sportRaw } = await params;
  const sport = sportRaw.toLowerCase() as SportKey;
  if (!(SPORTS as readonly string[]).includes(sport)) {
    return ogResponse({
      eyebrow: "Sport hub",
      headline: "Edge Equation",
      sub: "Click the link to open the live hub.",
    });
  }

  const feed = await getDailyFeed();
  const picks = picksForSport(feed, sport).length;
  const game = gameParlaysForSport(feed, sport).length;
  const props = propParlaysForSport(feed, sport).length;
  const total = picks + game + props;

  const headline =
    total > 0
      ? `${picks} pick${picks === 1 ? "" : "s"}` +
        (game + props > 0
          ? ` · ${game + props} parlay${game + props === 1 ? "" : "s"}`
          : "")
      : "No qualifying plays today.";

  return ogResponse({
    eyebrow: `${SPORT_LABEL[sport]} · daily hub`,
    headline,
    sub:
      total > 0
        ? `Click to open today's full ${SPORT_LABEL[sport]} card.`
        : "When the math says pass, the engine publishes nothing.",
    stats: [
      {
        label: "Picks",
        value: String(picks),
        highlight: picks > 0,
      },
      {
        label: "Game-results parlays",
        value: String(game),
        highlight: game > 0,
      },
      {
        label: "Player-props parlays",
        value: String(props),
        highlight: props > 0,
      },
    ],
  });
}
