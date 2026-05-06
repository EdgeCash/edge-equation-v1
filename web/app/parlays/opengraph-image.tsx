/**
 * /parlays OG image — counts today's qualified parlay tickets
 * across sports + universes.
 */

import {
  SPORTS,
  getDailyFeed,
  gameParlaysForSport,
  propParlaysForSport,
} from "../../lib/feed";
import {
  OG_CONTENT_TYPE,
  OG_SIZE,
  ogResponse,
} from "../../lib/og-template";


export const runtime = "nodejs";
export const alt = "Edge Equation — Strict-policy parlay viewer";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;


export default async function OG() {
  const feed = await getDailyFeed();
  let game = 0;
  let props = 0;
  for (const sport of SPORTS) {
    game += gameParlaysForSport(feed, sport).length;
    props += propParlaysForSport(feed, sport).length;
  }
  const total = game + props;

  return ogResponse({
    eyebrow: "Parlay viewer · strict policy",
    headline: total > 0
      ? `${total} qualified ticket${total === 1 ? "" : "s"} today.`
      : "No qualified parlay today.",
    sub:
      "3-6 legs · 4pp+ edge or ELITE per leg · positive EV after vig. "
      + "When the math fails, the engine publishes nothing.",
    stats: [
      {
        label: "Game-results",
        value: String(game),
        highlight: game > 0,
      },
      {
        label: "Player-props",
        value: String(props),
        highlight: props > 0,
      },
      { label: "Sports", value: "MLB · WNBA · NFL · NCAAF" },
    ],
  });
}
