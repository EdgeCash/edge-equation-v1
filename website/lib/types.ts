// Shape of API responses, mirroring FastAPI return types.
// Keep these in sync with api/routers/archive.py + data_source.pick_to_out_dict.

export type Grade = "A+" | "A" | "B" | "C" | "D" | "F";

export interface ApiLine {
  odds: number;
  number: string | null;
}

export interface ApiPick {
  sport: string;
  market_type: string;
  selection: string;
  line: ApiLine;
  fair_prob: string | null;
  expected_value: string | null;
  edge: string | null;
  kelly: string | null;
  grade: string;
  realization: number;
  game_id: string | null;
  event_time: string | null;
  decay_halflife_days: string | null;
  hfa_value: string | null;
  kelly_breakdown: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

export interface ArchivedPick extends ApiPick {
  pick_id: number;
  slate_id: string | null;
  recorded_at: string;
}

export interface SlateSummary {
  slate_id: string;
  generated_at: string;
  sport: string | null;
  card_type: "daily_edge" | "evening_edge" | string;
  n_picks: number;
  metadata: Record<string, unknown>;
}

export interface SlateDetail extends SlateSummary {
  picks: ArchivedPick[];
}

export interface GradeStats {
  n: number;
  wins: number;
  pushes: number;
  hit_rate: number;
}

export interface HitRateReport {
  sport: string | null;
  by_grade: Record<string, GradeStats>;
}
