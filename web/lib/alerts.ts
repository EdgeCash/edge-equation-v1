/**
 * Calibration-drift alert loader.
 *
 * Reads `public/data/calibration_alerts.json` (written by the
 * Python checker `exporters.calibration_alerts`) and exposes a
 * tolerant loader so the website can render the banner inline on
 * `/`, `/daily-card`, and `/reliability`.
 *
 * Best-effort — when the file isn't on disk yet, the loader returns
 * a clean empty report so the page renders the no-alerts state
 * (silent + no banner).
 */

import fs from "node:fs/promises";
import path from "node:path";

import { SportKey, SPORT_LABEL } from "./feed";


export type AlertLevel = "warning" | "critical";
export type SportStatus = "ok" | "warning" | "critical" | "no_data";


export interface SportSummary {
  brier: number | null;
  roi_pct: number | null;
  n: number;
  status: SportStatus;
}


export interface CalibrationAlert {
  sport: SportKey;
  level: AlertLevel;
  metric: "brier" | "roi";
  value: number;
  threshold: number;
  n_picks: number;
  message: string;
}


export interface AlertReport {
  version: number;
  generated_at: string;
  publish_gate: number;
  summary: Partial<Record<SportKey, SportSummary>>;
  alerts: CalibrationAlert[];
}


export async function loadAlertReport(): Promise<AlertReport | null> {
  const file = path.join(
    process.cwd(), "public", "data", "calibration_alerts.json",
  );
  try {
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw) as AlertReport;
    if (!parsed || !Array.isArray(parsed.alerts)) return null;
    return parsed;
  } catch {
    return null;
  }
}


/** Map for the per-sport reliability / track-record badges. */
export function summaryFor(
  report: AlertReport | null, sport: SportKey,
): SportSummary | null {
  if (!report) return null;
  return report.summary?.[sport] ?? null;
}


/** True iff there's at least one warning or critical alert. Used by
 * the layout-level banner. */
export function hasActiveAlerts(report: AlertReport | null): boolean {
  return !!report && (report.alerts ?? []).length > 0;
}


/** Pretty sport label used in the banner copy. */
export function sportLabel(key: SportKey | string): string {
  const k = key as SportKey;
  return SPORT_LABEL[k] ?? String(key).toUpperCase();
}
