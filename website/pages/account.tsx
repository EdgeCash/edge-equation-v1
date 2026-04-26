import type { GetServerSideProps } from "next";
import Link from "next/link";
import { useState } from "react";

import CardShell from "@/components/CardShell";
import Layout from "@/components/Layout";
import StatTile from "@/components/StatTile";
import { api, apiBase, formatDate } from "@/lib/api";
import type { MeResponse } from "@/lib/types";


type Props = {
  me: MeResponse | null;
  error: string | null;
};


export const getServerSideProps: GetServerSideProps<Props> = async (ctx) => {
  const cookie = ctx.req.headers.cookie;
  try {
    const me = await api.me(cookie);
    return { props: { me, error: null } };
  } catch (e: unknown) {
    return {
      props: {
        me: null,
        error: e instanceof Error ? e.message : "unknown error",
      },
    };
  }
};


export default function Account({ me, error }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function startCheckout() {
    setBusy(true);
    setErr(null);
    try {
      const resp = await fetch(`${apiBase()}/stripe/create-checkout-session`, {
        method: "POST",
        credentials: "include",
      });
      if (!resp.ok) {
        const txt = await resp.text();
        throw new Error(`${resp.status}: ${txt.slice(0, 200)}`);
      }
      const { url } = await resp.json();
      if (url) window.location.href = url;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openPortal() {
    setBusy(true);
    setErr(null);
    try {
      const resp = await fetch(`${apiBase()}/stripe/create-portal-session`, {
        method: "POST",
        credentials: "include",
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const { url } = await resp.json();
      if (url) window.location.href = url;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    try {
      await fetch(`${apiBase()}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
      window.location.href = "/";
    } finally {
      setBusy(false);
    }
  }

  return (
    <Layout title="Account" description="Your Edge Equation account.">
      <div className="annotation mb-4 flex items-center gap-3">
        <span className="text-edge-accent">⌘</span>
        <span>Account</span>
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Your{" "}
        <span className="italic text-edge-accent chalk-underline accent-glow">
          account
        </span>
      </h1>

      {error && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{error}</p>
        </div>
      )}

      {!error && !me && (
        <div className="mt-10 max-w-xl">
          <CardShell eyebrow="Signed out" headline="You&rsquo;re not signed in">
            <p className="text-edge-textDim">
              <Link
                href="/login"
                className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
              >
                Go to sign-in
              </Link>{" "}
              to request a magic link.
            </p>
          </CardShell>
        </div>
      )}

      {me && (
        <div className="mt-10 space-y-10">
          <CardShell eyebrow="Profile" headline={me.user.email}>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
              <StatTile
                label="Subscription"
                value={me.has_active_subscription ? "Active" : "None"}
              />
              <StatTile
                label="Status"
                value={me.subscription?.status ?? "—"}
              />
              <StatTile
                label="Renews"
                value={formatDate(me.subscription?.current_period_end ?? null)}
              />
            </div>
            <div className="mt-8 flex flex-wrap gap-4">
              {!me.has_active_subscription && (
                <button
                  onClick={startCheckout}
                  disabled={busy}
                  className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-accentMuted transition-colors disabled:opacity-50"
                >
                  Start premium subscription →
                </button>
              )}
              {me.user.stripe_customer_id && (
                <button
                  onClick={openPortal}
                  disabled={busy}
                  className="font-mono text-xs uppercase tracking-[0.2em] text-edge-textDim hover:text-edge-accent transition-colors border-b border-transparent hover:border-edge-accent pb-1"
                >
                  Manage subscription
                </button>
              )}
              <button
                onClick={logout}
                disabled={busy}
                className="font-mono text-xs uppercase tracking-[0.2em] text-edge-textDim hover:text-edge-accent transition-colors border-b border-transparent hover:border-edge-accent pb-1"
              >
                Sign out
              </button>
            </div>
            {err && (
              <p className="mt-4 font-mono text-sm text-edge-accent">{err}</p>
            )}
          </CardShell>

          <section>
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-accent mb-4">
              Quick links
            </div>
            <div className="space-y-2">
              <Link
                href="/daily-edge"
                className="block border border-edge-line rounded-sm p-4 hover:border-edge-accent/60 transition-colors"
              >
                <div className="font-display text-lg tracking-tightest">Daily Edge</div>
                <div className="text-edge-textDim text-sm mt-1">
                  Today&apos;s public slate.
                </div>
              </Link>
              <Link
                href="/premium-edge"
                className="block border border-edge-line rounded-sm p-4 hover:border-edge-accent/60 transition-colors"
              >
                <div className="font-display text-lg tracking-tightest">Premium Edge</div>
                <div className="text-edge-textDim text-sm mt-1">
                  {me.has_active_subscription
                    ? "Full distributions + model notes."
                    : "Subscribe above to unlock."}
                </div>
              </Link>
            </div>
          </section>
        </div>
      )}
    </Layout>
  );
}
