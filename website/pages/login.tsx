import { FormEvent, useState } from "react";

import CardShell from "@/components/CardShell";
import Layout from "@/components/Layout";
import { apiBase } from "@/lib/api";


type State = "idle" | "sending" | "sent" | "error";


export default function Login() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setState("sending");
    setError(null);
    try {
      const resp = await fetch(`${apiBase()}/auth/request-link`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (resp.status === 202) {
        setState("sent");
      } else if (resp.status === 422) {
        setState("error");
        setError("That doesn't look like a valid email.");
      } else {
        setState("error");
        setError(`Server returned ${resp.status}.`);
      }
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Network error");
    }
  }

  return (
    <Layout title="Sign in" description="Sign in to Edge Equation via email magic link.">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Sign In
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Magic link
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        No passwords. Enter the email you want to use; we&apos;ll send you a
        one-time link to sign in. The link expires in 15 minutes.
      </p>

      <div className="mt-10 max-w-xl">
        {state !== "sent" ? (
          <CardShell eyebrow="Email" headline="Send me a link">
            <form onSubmit={submit} className="space-y-5">
              <label className="block">
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
                  Email address
                </span>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="mt-2 w-full bg-ink-950 border border-edge-line rounded-sm px-4 py-3 font-mono text-edge-text placeholder:text-edge-textDim/50 focus:outline-none focus:border-edge-accent"
                />
              </label>
              <button
                type="submit"
                disabled={state === "sending"}
                className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-text transition-colors disabled:opacity-50"
              >
                {state === "sending" ? "Sending…" : "Send magic link"}
                <span>→</span>
              </button>
              {state === "error" && error && (
                <p className="font-mono text-sm text-edge-accent">{error}</p>
              )}
            </form>
          </CardShell>
        ) : (
          <CardShell eyebrow="Check your inbox" headline="We sent you a link">
            <p className="text-edge-textDim">
              If an account exists for <span className="font-mono text-edge-text">{email}</span>,
              a sign-in link is on its way. Click it within 15 minutes to
              complete sign-in. You can close this tab.
            </p>
          </CardShell>
        )}
      </div>
    </Layout>
  );
}
