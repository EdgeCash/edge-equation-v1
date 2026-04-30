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

export interface AuthUser {
  user_id: number;
  email: string;
  email_verified_at: string | null;
  stripe_customer_id: string | null;
  created_at: string;
}

export interface SubscriptionRecord {
  subscription_id: number;
  user_id: number;
  stripe_subscription_id: string;
  status: string;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  created_at: string;
  updated_at: string;
}

export interface MeResponse {
  user: AuthUser;
  subscription: SubscriptionRecord | null;
  has_active_subscription: boolean;
}


// ---------------------------------------------------------------------------
// Phase 5 — NRFI dashboard payload (mirrors api.routers.nrfi.get_nrfi_dashboard)
// ---------------------------------------------------------------------------

export type NrfiTier = "LOCK" | "STRONG" | "MODERATE" | "LEAN" | "NO_PLAY";

export interface NrfiBoardRow {
  game_pk: number;
  home_team?: string;
  away_team?: string;
  first_pitch_ts?: string;
  nrfi_pct?: number;
  lambda_total?: number;
  color_band?: string;
  signal?: string;
  mc_low?: number;
  mc_high?: number;
  edge?: number;
  kelly_units?: number;
  shap_drivers?: string;
  nrfi_tier?: NrfiTier;
  yrfi_tier?: NrfiTier;
}

export interface NrfiTierLedgerRow {
  season: number;
  market_type: "NRFI" | "YRFI" | "ALL";
  tier: NrfiTier | "ALL";
  n_settled: number;
  wins: number;
  losses: number;
  units_won: number;
  last_updated?: string;
}

export interface ParlayCandidateLeg {
  market_type: string;
  side: string;
  side_probability: number;
  american_odds: number;
  tier: NrfiTier;
  label: string;
}

export interface ParlayCandidate {
  n_legs: number;
  joint_prob_independent: number;
  joint_prob_corr: number;
  combined_decimal_odds: number;
  combined_american_odds: number;
  implied_prob: number;
  edge_pp: number;
  ev_units: number;
  stake_units: number;
  legs: ParlayCandidateLeg[];
}

export interface ParlayLedgerSummary {
  recorded: number;
  settled: number;
  pending: number;
  units_returned: number;
  total_stake: number;
  roi_pct: number;
}

export interface NrfiDashboard {
  date: string;
  board: NrfiBoardRow[];
  ytd_ledger: NrfiTierLedgerRow[];
  parlay_candidates: ParlayCandidate[];
  parlay_ledger: ParlayLedgerSummary;
}
