// Typed fetch helpers for the FastAPI backend.
//
// The API base URL is read from NEXT_PUBLIC_API_BASE_URL at build time.
// On Vercel this is set in Project Settings; locally it defaults to
// http://localhost:8000 so `uvicorn api.main:app` + `next dev` just works.

import type {
  HitRateReport,
  MeResponse,
  NrfiDashboard,
  SlateDetail,
  SlateSummary,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";


async function getJson<T>(path: string): Promise<T> {
  const url = `${API_BASE}${path}`;
  const resp = await fetch(url, {
    headers: { "Accept": "application/json" },
    // Always fetch fresh on the server; the daily slate changes twice a day.
    cache: "no-store",
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(
      `API ${resp.status} ${resp.statusText} for ${path}: ${body.slice(0, 200)}`,
    );
  }
  return resp.json() as Promise<T>;
}


// Public API base — exposed so pages that need server-to-server fetch with
// the caller's cookie header can build the same URL.
export const apiBase = (): string => API_BASE;


export const api = {
  async listSlates(params?: { limit?: number; card_type?: string }): Promise<SlateSummary[]> {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.card_type) qs.set("card_type", params.card_type);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return getJson<SlateSummary[]>(`/archive/slates${suffix}`);
  },

  async latestSlate(card_type: "daily_edge" | "evening_edge"): Promise<SlateDetail | null> {
    try {
      return await getJson<SlateDetail>(`/archive/slates/latest?card_type=${card_type}`);
    } catch (e: unknown) {
      // 404 => no slate yet; caller decides how to render
      if (e instanceof Error && e.message.includes("404")) return null;
      throw e;
    }
  },

  async getSlate(slate_id: string): Promise<SlateDetail | null> {
    try {
      return await getJson<SlateDetail>(`/archive/slates/${encodeURIComponent(slate_id)}`);
    } catch (e: unknown) {
      if (e instanceof Error && e.message.includes("404")) return null;
      throw e;
    }
  },

  async hitRate(sport?: string): Promise<HitRateReport> {
    const suffix = sport ? `?sport=${encodeURIComponent(sport)}` : "";
    return getJson<HitRateReport>(`/archive/hit-rate${suffix}`);
  },

  // Auth calls need the session cookie, which only exists in the browser
  // context (client-side) or must be forwarded explicitly from a server
  // request. `me(cookie)` takes an optional Cookie header string so
  // getServerSideProps can proxy the incoming cookie.
  async me(cookieHeader?: string): Promise<MeResponse | null> {
    const url = `${API_BASE}/auth/me`;
    const headers: Record<string, string> = { "Accept": "application/json" };
    if (cookieHeader) headers["Cookie"] = cookieHeader;
    const resp = await fetch(url, { headers, cache: "no-store" });
    if (resp.status === 401) return null;
    if (!resp.ok) {
      throw new Error(`API ${resp.status} on /auth/me`);
    }
    return resp.json() as Promise<MeResponse>;
  },

  // Phase 5 — single-shot dashboard payload (board + ledgers + parlays).
  async nrfiDashboard(date?: string): Promise<NrfiDashboard> {
    const suffix = date ? `?date=${encodeURIComponent(date)}` : "";
    return getJson<NrfiDashboard>(`/nrfi/dashboard${suffix}`);
  },
};


// Helpers for pretty-printing numbers from the API (they arrive as strings
// so we don't lose Decimal precision during JSON transport).
export function formatPercent(value: string | null | undefined, places = 2): string {
  if (value == null) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(places)}%`;
}

export function formatNumber(value: string | null | undefined, places?: number): string {
  if (value == null) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  return places !== undefined ? n.toFixed(places) : String(n);
}

export function formatAmericanOdds(odds: number | null | undefined): string {
  if (odds == null) return "—";
  return odds > 0 ? `+${odds}` : String(odds);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
