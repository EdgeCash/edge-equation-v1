/**
 * /team/<sport>/<id> OG image — team tricode + sport + engine
 * record summary.
 */

import { SPORTS, SPORT_LABEL, SportKey } from "../../../../lib/feed";
import {
  OG_CONTENT_TYPE,
  OG_SIZE,
  ogResponse,
} from "../../../../lib/og-template";
import { resolveTeamProfile } from "../../../../lib/profiles";


export const runtime = "nodejs";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;
export const alt = "Edge Equation — Team profile";


interface RouteParams {
  params: Promise<{ sport: string; id: string }>;
}


export default async function OG({ params }: RouteParams) {
  const { sport: sportRaw, id } = await params;
  const sport = sportRaw.toLowerCase() as SportKey;
  if (!(SPORTS as readonly string[]).includes(sport)) {
    return ogResponse({
      eyebrow: "Team profile",
      headline: "Edge Equation",
      sub: "Click the link to open the live profile.",
    });
  }

  const profile = await resolveTeamProfile(sport, id);
  const display = profile?.display ?? id.toUpperCase();
  const summary = profile?.history_summary;

  const stats =
    summary && summary.n > 0
      ? [
          { label: "Picks logged", value: String(summary.n) },
          {
            label: "Hit rate",
            value:
              summary.graded > 0
                ? `${summary.hit_rate_pct.toFixed(1)}%`
                : "—",
            highlight: summary.hit_rate_pct >= 50,
          },
          {
            label: "Units P/L",
            value:
              `${summary.units_pl >= 0 ? "+" : ""}${summary.units_pl.toFixed(2)}u`,
            highlight: summary.units_pl > 0,
          },
        ]
      : [
          { label: "Engine record", value: "Limited data" },
          { label: "Sport", value: SPORT_LABEL[sport] },
          { label: "Today", value: "Click for live data" },
        ];

  const todayCount = profile?.todays_picks?.length ?? 0;
  const sub =
    todayCount > 0
      ? `${todayCount} pick${todayCount === 1 ? "" : "s"} on today's card · `
        + `${SPORT_LABEL[sport]}`
      : `${SPORT_LABEL[sport]} · click to open the live data view.`;

  return ogResponse({
    eyebrow: `${SPORT_LABEL[sport]} · Team profile`,
    headline: display,
    sub,
    stats,
  });
}
