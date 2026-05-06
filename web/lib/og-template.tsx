/**
 * Shared Open Graph image template.
 *
 * Every page's `opengraph-image.tsx` returns an `ImageResponse`
 * built from `OGCard` + `ogResponse`. The template is intentionally
 * lean — Next.js's Satori renderer doesn't support every CSS
 * property, so we stick to flexbox + inline backgrounds + system
 * fonts for maximum reliability across Twitter / Slack / Discord
 * / iMessage / LinkedIn previewers.
 *
 * Dimensions: 1200×630 — the OG / Twitter Card standard.
 *
 * Brand frame (matches the site):
 *   - Background:    chalkboard-950 #070d16 with subtle grid lines
 *   - Accent:        electric blue #38bdf8
 *   - Body text:     #e2e8f0 / #94a3b8
 *   - Stat numbers:  monospace
 *   - Footer:        Σ EdgeEquation · Facts. Not Feelings. · edgeequation.com
 */

import { ImageResponse } from "next/og";


export const OG_SIZE = { width: 1200, height: 630 } as const;
export const OG_CONTENT_TYPE = "image/png" as const;


export interface OGStat {
  label: string;
  value: string;
  highlight?: boolean;
}


export interface OGCardProps {
  /** Top-line caption, monospace + uppercase. e.g. "MLB · Daily card" */
  eyebrow: string;
  /** Big white headline (one or two lines). Truncated by ellipsis CSS. */
  headline: string;
  /** Optional sub-line under the headline. */
  sub?: string;
  /** 0–4 stat tiles rendered along the bottom-left strip. */
  stats?: OGStat[];
  /** Optional accent label rendered in the top-right (e.g. tier). */
  accent?: string;
  /** Override the bottom-right tagline. Defaults to the brand line. */
  tagline?: string;
}


/**
 * Returns the JSX tree for an OG card. Caller wraps it with
 * `ogResponse(...)` to produce the actual `ImageResponse`.
 *
 * Kept as a pure function so tests can render-trees inspect it
 * without booting the full Next runtime.
 */
export function OGCard({
  eyebrow,
  headline,
  sub,
  stats = [],
  accent,
  tagline,
}: OGCardProps) {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "#070d16",
        backgroundImage: GRID_BG,
        backgroundSize: "60px 60px",
        color: "#e2e8f0",
        padding: "72px 80px",
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      }}
    >
      {/* Top row: eyebrow + accent */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          width: "100%",
        }}
      >
        <span
          style={{
            fontSize: 22,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "#94a3b8",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
          }}
        >
          {eyebrow}
        </span>
        {accent ? (
          <span
            style={{
              fontSize: 18,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              color: "#38bdf8",
              border: "2px solid #38bdf8",
              borderRadius: 999,
              padding: "8px 18px",
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              backgroundColor: "rgba(56, 189, 248, 0.08)",
            }}
          >
            {accent}
          </span>
        ) : null}
      </div>

      {/* Headline block */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          marginTop: 56,
          flex: 1,
          minHeight: 0,
        }}
      >
        <span
          style={{
            fontSize: headline.length > 36 ? 64 : 80,
            lineHeight: 1.05,
            fontWeight: 700,
            color: "#f8fafc",
            letterSpacing: "-0.015em",
            maxWidth: "100%",
            wordBreak: "break-word",
          }}
        >
          {headline}
        </span>
        {sub ? (
          <span
            style={{
              marginTop: 28,
              fontSize: 30,
              color: "#cbd5e1",
              lineHeight: 1.3,
              maxWidth: "92%",
            }}
          >
            {sub}
          </span>
        ) : null}
      </div>

      {/* Stat strip */}
      {stats.length > 0 ? (
        <div
          style={{
            display: "flex",
            gap: 28,
            marginTop: 32,
          }}
        >
          {stats.map((s, i) => (
            <StatTile key={`${s.label}-${i}`} stat={s} />
          ))}
        </div>
      ) : null}

      {/* Footer brand line */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: 40,
          width: "100%",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <Sigma />
          <span
            style={{
              fontSize: 22,
              fontWeight: 600,
              color: "#f8fafc",
              letterSpacing: "0.01em",
            }}
          >
            Edge<span style={{ color: "#38bdf8" }}>Equation</span>
          </span>
          <span
            style={{
              fontSize: 18,
              color: "#64748b",
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
            }}
          >
            ·
          </span>
          <span
            style={{
              fontSize: 18,
              color: "#94a3b8",
              fontStyle: "italic",
            }}
          >
            {tagline ?? "Facts. Not Feelings."}
          </span>
        </div>
        <span
          style={{
            fontSize: 18,
            color: "#64748b",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
          }}
        >
          edgeequation.com
        </span>
      </div>
    </div>
  );
}


function StatTile({ stat }: { stat: OGStat }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        padding: "18px 24px",
        borderRadius: 12,
        border: stat.highlight
          ? "2px solid rgba(56, 189, 248, 0.6)"
          : "2px solid rgba(56, 189, 248, 0.18)",
        backgroundColor: stat.highlight
          ? "rgba(56, 189, 248, 0.10)"
          : "rgba(255, 255, 255, 0.03)",
        minWidth: 200,
      }}
    >
      <span
        style={{
          fontSize: 14,
          letterSpacing: "0.14em",
          textTransform: "uppercase",
          color: "#94a3b8",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
        }}
      >
        {stat.label}
      </span>
      <span
        style={{
          marginTop: 6,
          fontSize: 38,
          fontWeight: 600,
          color: stat.highlight ? "#38bdf8" : "#e2e8f0",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
        }}
      >
        {stat.value}
      </span>
    </div>
  );
}


function Sigma() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 44,
        height: 44,
        borderRadius: 8,
        backgroundColor: "#0f1623",
        fontSize: 30,
        color: "#38bdf8",
        fontFamily: "ui-monospace, SFMono-Regular, monospace",
        fontWeight: 700,
      }}
    >
      Σ
    </div>
  );
}


// Subtle grid background — matches the site's chalkboard texture.
// Encoded inline so the OG renderer doesn't need a network fetch.
const GRID_BG =
  `linear-gradient(rgba(56, 189, 248, 0.04) 1px, transparent 1px),`
  + ` linear-gradient(90deg, rgba(56, 189, 248, 0.04) 1px, transparent 1px)`;


/**
 * Convenience wrapper: returns the configured `ImageResponse` for
 * a given OG card spec. Caller exports the result from each
 * route's `opengraph-image.tsx`.
 */
export function ogResponse(props: OGCardProps): ImageResponse {
  return new ImageResponse(<OGCard {...props} />, {
    ...OG_SIZE,
  });
}
