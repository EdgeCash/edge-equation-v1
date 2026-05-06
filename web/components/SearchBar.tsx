"use client";

import Link from "next/link";
import {
  ChangeEvent, KeyboardEvent, useCallback, useEffect, useRef, useState,
} from "react";


interface SearchEntry {
  id: string;
  sport: string;
  kind: "player" | "team";
  display: string;
  detail: string;
}


interface SearchResponse {
  q: string;
  n_total: number;
  n_results: number;
  results: SearchEntry[];
}


/**
 * Client-side search box. Hits /api/search whenever the user types
 * (debounced 200ms) and renders a dropdown of player + team profile
 * links. Designed so a novice typing "judge" reaches Aaron Judge's
 * profile, while a sharp typing "BOS" reaches the Red Sox team page.
 */
export function SearchBar() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Debounced fetch
  useEffect(() => {
    if (!open) return;
    const handle = window.setTimeout(async () => {
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        if (!res.ok) {
          setResults([]);
          return;
        }
        const json = (await res.json()) as SearchResponse;
        setResults(json.results || []);
        setActiveIdx(0);
      } catch {
        setResults([]);
      }
    }, 200);
    return () => window.clearTimeout(handle);
  }, [query, open]);

  // Close dropdown on outside click
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (
        wrapperRef.current
        && !wrapperRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const onChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value);
    setOpen(true);
  }, []);

  const onKey = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        setOpen(false);
        return;
      }
      if (!results.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx((i) => (i + 1) % results.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx(
          (i) => (i - 1 + results.length) % results.length,
        );
      } else if (e.key === "Enter") {
        const choice = results[activeIdx];
        if (choice) {
          window.location.href = profileHref(choice);
        }
      }
    },
    [results, activeIdx],
  );

  return (
    <div ref={wrapperRef} className="relative w-full max-w-md">
      <label className="relative block">
        <span className="sr-only">Search players or teams</span>
        <input
          ref={inputRef}
          value={query}
          onChange={onChange}
          onFocus={() => setOpen(true)}
          onKeyDown={onKey}
          placeholder="Search players or teams across all sports…"
          className="w-full rounded-md border border-chalkboard-600/70 bg-chalkboard-800/70 px-3 py-1.5 pl-9 text-sm text-chalk-100 placeholder:text-chalk-500 focus:border-elite/60 focus:outline-none focus:ring-1 focus:ring-elite/40"
          aria-label="Search players or teams"
          autoComplete="off"
        />
        <svg
          className="absolute left-2 top-1/2 -translate-y-1/2 text-chalk-500"
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round" aria-hidden
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
      </label>
      {open && (
        <div className="absolute left-0 right-0 top-full mt-2 z-40 rounded-md border border-chalkboard-600/70 bg-chalkboard-900/95 shadow-[0_8px_32px_rgba(0,0,0,0.4)] backdrop-blur-md max-h-[60vh] overflow-y-auto">
          {results.length === 0 ? (
            <p className="px-3 py-3 text-xs text-chalk-500">
              No matches yet — try a player or team name.
            </p>
          ) : (
            <ul className="py-1">
              {results.map((r, i) => (
                <li key={`${r.sport}:${r.kind}:${r.id}`}>
                  <Link
                    href={profileHref(r)}
                    onClick={() => setOpen(false)}
                    className={
                      "flex items-center justify-between gap-3 px-3 py-2 text-sm "
                      + (i === activeIdx
                        ? "bg-chalkboard-800 text-elite"
                        : "text-chalk-100 hover:bg-chalkboard-800")
                    }
                  >
                    <span className="flex items-baseline gap-2">
                      <span className="font-medium">{r.display}</span>
                      <span className="text-[10px] uppercase tracking-wider text-chalk-500">
                        {r.kind}
                      </span>
                    </span>
                    <span className="text-[10px] text-chalk-500">
                      {r.detail}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <p className="px-3 py-2 border-t border-chalkboard-700/60 text-[10px] text-chalk-500">
            Click a row to open the player or team profile. Live data
            from today&apos;s engine outputs.
          </p>
        </div>
      )}
    </div>
  );
}


function profileHref(entry: SearchEntry): string {
  const kind = entry.kind === "player" ? "player" : "team";
  return `/${kind}/${entry.sport}/${entry.id}`;
}
