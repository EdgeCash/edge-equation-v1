/**
 * Lightweight fuzzy matcher.
 *
 * Scores a query against a candidate string with no external
 * dependency (Fuse.js / match-sorter would add ~5–15 kB to the
 * bundle). Targets the audit's "novice typing 'judge' reaches
 * Aaron Judge AND a typo like 'juge' still resolves" requirement.
 *
 * Scoring ladder — the highest applicable rung wins:
 *
 *   1.00   exact match (case-insensitive)
 *   0.92   case-insensitive equality after stripping non-alnum
 *   0.85   target starts with query (or any word in target does)
 *   0.70   target contains query (substring); penalised by offset
 *   0.55   Levenshtein distance ≤ 2 against any whitespace-split
 *          word in target (typo tolerance — only fires for
 *          ≥ 4-char queries to avoid noise)
 *   0.40   query characters appear in target in order
 *          (subsequence) — last-resort match for long names
 *   0.00   no overlap
 *
 * `bestFuzzyScore(query, candidates, picker)` is the bulk helper
 * the search index calls to keep loops out of the call sites.
 */


export type MatchKind =
  | "exact"
  | "alnum_eq"
  | "prefix"
  | "substring"
  | "typo"
  | "subsequence"
  | "none";


export interface FuzzyMatch {
  score: number;     // 0..1
  kind: MatchKind;
}


export const NO_MATCH: FuzzyMatch = { score: 0, kind: "none" };


// Minimum query length before Levenshtein typo tolerance kicks in.
// Lower than this is too noisy — every 2-char target would match.
const TYPO_MIN_QUERY_LEN = 4;
const TYPO_MAX_DISTANCE = 2;


/** Score a single candidate against a query. */
export function fuzzyMatch(query: string, target: string): FuzzyMatch {
  if (!query || !target) return NO_MATCH;
  const q = query.trim().toLowerCase();
  const t = target.toLowerCase();
  if (!q || !t) return NO_MATCH;

  if (q === t) return { score: 1.0, kind: "exact" };

  const qAlnum = q.replace(/[^a-z0-9]+/g, "");
  const tAlnum = t.replace(/[^a-z0-9]+/g, "");
  if (qAlnum && qAlnum === tAlnum) {
    return { score: 0.92, kind: "alnum_eq" };
  }

  // Prefix on the full target OR on any whitespace word.
  if (t.startsWith(q)) {
    return {
      score: 0.85 + 0.1 * (q.length / t.length),
      kind: "prefix",
    };
  }
  const words = t.split(/[\s\-/]+/).filter(Boolean);
  for (const w of words) {
    if (w.startsWith(q)) {
      return { score: 0.85, kind: "prefix" };
    }
  }

  // Substring anywhere — penalise by start offset / length so a
  // match at index 0 outranks one near the end.
  const idx = t.indexOf(q);
  if (idx >= 0) {
    return {
      score: 0.7 - (idx / t.length) * 0.15,
      kind: "substring",
    };
  }

  // Levenshtein typo tolerance — applied per-word so "Aaron Juge"
  // matches "Aaron Judge" via the second word.
  if (q.length >= TYPO_MIN_QUERY_LEN) {
    let bestDist = Infinity;
    for (const w of words) {
      // Skip words too far from q in length to ever land within
      // distance bound. Saves a bunch of inner loops.
      if (Math.abs(w.length - q.length) > TYPO_MAX_DISTANCE) continue;
      const dist = levenshtein(q, w, TYPO_MAX_DISTANCE);
      if (dist < bestDist) bestDist = dist;
      if (bestDist === 0) break;
    }
    if (bestDist <= TYPO_MAX_DISTANCE) {
      return {
        score: 0.55 - 0.1 * bestDist,
        kind: "typo",
      };
    }
  }

  // Last resort — subsequence (chars in order, not contiguous).
  if (isSubsequence(q, t)) {
    return { score: 0.4, kind: "subsequence" };
  }
  return NO_MATCH;
}


/** Score `query` against the best of multiple textual fields on
 * one record (display + slug + sport tag). Returns the best match. */
export function bestFuzzyScore(
  query: string, fields: Array<string | null | undefined>,
): FuzzyMatch {
  let best: FuzzyMatch = NO_MATCH;
  for (const f of fields) {
    if (!f) continue;
    const m = fuzzyMatch(query, f);
    if (m.score > best.score) best = m;
  }
  return best;
}


/** Levenshtein distance with an early-exit cutoff. */
export function levenshtein(
  a: string, b: string, cutoff: number = Infinity,
): number {
  if (a === b) return 0;
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;

  // Single-row DP table.
  let prev = new Array(n + 1);
  let curr = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;

  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    let rowMin = curr[0];
    const ai = a.charCodeAt(i - 1);
    for (let j = 1; j <= n; j++) {
      const cost = ai === b.charCodeAt(j - 1) ? 0 : 1;
      curr[j] = Math.min(
        curr[j - 1] + 1,         // insertion
        prev[j] + 1,             // deletion
        prev[j - 1] + cost,      // substitution
      );
      if (curr[j] < rowMin) rowMin = curr[j];
    }
    if (rowMin > cutoff) return cutoff + 1;
    [prev, curr] = [curr, prev];
  }
  return prev[n];
}


/** Are all chars of `q` present in `t`, in order? */
export function isSubsequence(q: string, t: string): boolean {
  if (!q) return true;
  let i = 0;
  for (let j = 0; j < t.length && i < q.length; j++) {
    if (q[i] === t[j]) i++;
  }
  return i === q.length;
}
