/**
 * Calibration-drift alert banner.
 *
 * Renders at the top of `/`, `/daily-card`, `/reliability` whenever
 * one or more sports' Brier / ROI has drifted past the publish
 * gate. Tone: honest + factual, not alarmist. The banner is the
 * engine's voice ("we're not calibrated right now, here's the
 * number") — no dismiss button because the alert IS the signal.
 *
 * Pure server component — accepts a pre-loaded report so each page
 * renders with the same data without each component re-fetching.
 */

import Link from "next/link";

import { AlertReport, sportLabel } from "../lib/alerts";


interface AlertBannerProps {
  report: AlertReport | null;
  /** Hide the banner on pages where the alerts are already
   * surfaced inline (e.g. the reliability page renders per-sport
   * panels and doesn't need the top-of-page summary). */
  hideOnReliability?: boolean;
}


export function AlertBanner({ report }: AlertBannerProps) {
  if (!report || !report.alerts || report.alerts.length === 0) return null;

  const critical = report.alerts.filter((a) => a.level === "critical");
  const warnings = report.alerts.filter((a) => a.level === "warning");
  const tone =
    critical.length > 0
      ? "border-nosignal/60 bg-nosignal/10 text-nosignal"
      : "border-moderate/60 bg-moderate/10 text-moderate";
  const headline =
    critical.length > 0
      ? `Calibration drift — ${critical.length} sport${
          critical.length === 1 ? "" : "s"
        } past critical threshold.`
      : `Calibration drift — ${warnings.length} sport${
          warnings.length === 1 ? "" : "s"
        } above the publish gate.`;

  return (
    <section
      role="status"
      aria-live="polite"
      className={`border-y ${tone}`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span aria-hidden className="font-mono text-base mt-px">⚠</span>
          <div className="text-sm leading-snug">
            <p className="font-semibold">{headline}</p>
            <ul className="mt-1 space-y-1 text-xs">
              {report.alerts.map((a, i) => (
                <li key={`${a.sport}-${a.metric}-${i}`} className="text-chalk-100">
                  <span className="font-mono uppercase tracking-wider mr-2">
                    {sportLabel(a.sport)}
                  </span>
                  {a.message}
                </li>
              ))}
            </ul>
          </div>
        </div>
        <Link
          href="/reliability"
          className="text-xs font-mono uppercase tracking-wider text-chalk-100 hover:text-chalk-50 underline decoration-dotted underline-offset-4 mt-1 shrink-0"
        >
          See calibration →
        </Link>
      </div>
    </section>
  );
}
