"use client";

/**
 * Daily-digest email signup form.
 *
 * Posts to `/api/subscribe`. Three render states:
 *
 *   - idle    — input + submit
 *   - sending — submit disabled, "Adding…" label
 *   - done    — success or error message; allow another attempt
 *
 * Tone: factual, non-hyped. Copy mirrors the audit's
 * "Facts. Not Feelings." voice — no marketing puffery, no
 * urgency cues, no "100% accuracy" claims.
 */

import { FormEvent, useState } from "react";


interface EmailSignupProps {
  /** Optional headline override — defaults to a short factual line. */
  headline?: string;
  /** Optional subline override. */
  subline?: string;
}


type SubmitState =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; ok: boolean; message: string };


export function EmailSignup({
  headline = "Get the daily card by email.",
  subline =
    "One message per day, before 11 AM CDT, with every sport's picks "
    + "and parlay tickets. No hype, no upsell. Unsubscribe any time.",
}: EmailSignupProps) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<SubmitState>({ kind: "idle" });

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (state.kind === "sending") return;
    setState({ kind: "sending" });
    try {
      const resp = await fetch("/api/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      const body = (await resp.json().catch(() => null)) as
        | { ok?: boolean; message?: string; error?: string }
        | null;
      if (resp.ok && body?.ok) {
        setState({
          kind: "done",
          ok: true,
          message:
            body.message
            ?? "You're in. The next daily card lands in your inbox.",
        });
        setEmail("");
      } else {
        setState({
          kind: "done",
          ok: false,
          message:
            body?.error
            ?? `Subscription failed (HTTP ${resp.status}). Try again.`,
        });
      }
    } catch {
      setState({
        kind: "done",
        ok: false,
        message:
          "Couldn't reach the subscribe endpoint. Try again in a minute.",
      });
    }
  }

  return (
    <section className="chalk-card p-5">
      <p className="font-mono text-[11px] uppercase tracking-wider text-chalk-500">
        Daily digest
      </p>
      <h3 className="mt-1 text-xl font-semibold text-chalk-50">
        {headline}
      </h3>
      <p className="mt-2 text-sm text-chalk-300 leading-relaxed">
        {subline}
      </p>
      <form
        onSubmit={onSubmit}
        className="mt-4 flex flex-col sm:flex-row gap-2"
      >
        <label className="sr-only" htmlFor="email-signup-input">
          Email address
        </label>
        <input
          id="email-signup-input"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (state.kind === "done") setState({ kind: "idle" });
          }}
          placeholder="you@example.com"
          className="flex-1 rounded-md border border-chalkboard-600/70 bg-chalkboard-900/60 px-3 py-2 text-sm text-chalk-100 placeholder:text-chalk-500 focus:border-elite/60 focus:outline-none focus:ring-1 focus:ring-elite/40"
          disabled={state.kind === "sending"}
        />
        <button
          type="submit"
          disabled={state.kind === "sending" || !email.trim()}
          className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {state.kind === "sending" ? "Adding…" : "Subscribe"}
        </button>
      </form>
      {state.kind === "done" && (
        <p
          role="status"
          aria-live="polite"
          className={
            "mt-3 text-xs " +
            (state.ok ? "text-strong" : "text-nosignal")
          }
        >
          {state.message}
        </p>
      )}
      <p className="mt-3 text-[10px] text-chalk-500 leading-snug">
        Email is stored only for sending the daily digest. We never
        share or sell it. The unsubscribe link in every email is the
        single source of truth.
      </p>
    </section>
  );
}
