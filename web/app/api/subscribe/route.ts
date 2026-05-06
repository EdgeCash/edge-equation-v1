/**
 * POST /api/subscribe
 *
 * Public endpoint for the daily-digest signup form. Adds the
 * caller's email to the configured Resend audience.
 *
 * Soft-fails by design — when the Resend env vars aren't set
 * (preview deploys, dev), the endpoint returns a clean 503 so
 * the form can render an honest "Subscriptions temporarily
 * unavailable" message instead of pretending it worked.
 *
 * Required env vars (configured on Vercel):
 *   RESEND_API_KEY        Resend API key (server-side only)
 *   RESEND_AUDIENCE_ID    Audience the contact is added to
 *
 * Optional:
 *   RESEND_FROM_ADDRESS   Used by the daily Python sender, not here.
 *
 * Rate-limit posture: light — Resend's `contacts.create` is
 * idempotent on the email address (returns the existing contact
 * with no side-effect). We guard against trivial spam at the
 * input layer (basic email regex + 320-char cap).
 */

import { NextRequest, NextResponse } from "next/server";


export const dynamic = "force-dynamic";


const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_EMAIL_LEN = 320;        // RFC 3696 upper bound


export async function POST(req: NextRequest) {
  let payload: { email?: unknown } = {};
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, error: "Invalid JSON body." },
      { status: 400 },
    );
  }
  const raw = typeof payload.email === "string" ? payload.email.trim() : "";
  if (!raw) {
    return NextResponse.json(
      { ok: false, error: "Email is required." },
      { status: 400 },
    );
  }
  if (raw.length > MAX_EMAIL_LEN || !EMAIL_RE.test(raw)) {
    return NextResponse.json(
      { ok: false, error: "Please enter a valid email address." },
      { status: 400 },
    );
  }

  const apiKey = (process.env.RESEND_API_KEY || "").trim();
  const audienceId = (process.env.RESEND_AUDIENCE_ID || "").trim();
  if (!apiKey || !audienceId) {
    return NextResponse.json(
      {
        ok: false,
        error:
          "Subscriptions temporarily unavailable. Try again later.",
      },
      { status: 503 },
    );
  }

  try {
    const resp = await fetch(
      `https://api.resend.com/audiences/${audienceId}/contacts`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email: raw,
          unsubscribed: false,
        }),
      },
    );
    // Resend returns 200 on create AND on duplicate (idempotent).
    if (resp.status >= 400) {
      const errBody = await safeJson(resp);
      const reason =
        (errBody as { message?: string } | null)?.message
        ?? `Resend returned HTTP ${resp.status}`;
      return NextResponse.json(
        { ok: false, error: `Subscription failed — ${reason}` },
        { status: 502 },
      );
    }
  } catch (e) {
    return NextResponse.json(
      {
        ok: false,
        error:
          "Subscription failed — couldn't reach the email service. "
          + "Try again in a minute.",
      },
      { status: 502 },
    );
  }

  return NextResponse.json({
    ok: true,
    message:
      "You're in. The next daily card lands in your inbox by "
      + "11 AM CDT.",
  });
}


async function safeJson(resp: Response): Promise<unknown> {
  try {
    return await resp.json();
  } catch {
    return null;
  }
}
