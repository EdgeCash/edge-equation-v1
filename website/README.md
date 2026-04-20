# Edge Equation — Web

The public-facing website. Next.js (TypeScript) + Tailwind CSS.

## Local dev

```bash
cd website
npm install
npm run dev
```

Open http://localhost:3000.

## Pages

- `/` — Home
- `/daily-edge` — Public Daily Edge card (placeholder; wired up in Phase 6B)
- `/premium-edge` — Premium teaser
- `/about` — Manifesto
- `/contact` — Email + social

## Stack

- Next.js 14 (pages router)
- React 18
- TypeScript 5
- Tailwind CSS 3 (dark theme via `darkMode: "class"`, background applied via `<body>`)
- Fraunces (display) + Inter Tight (body) + JetBrains Mono (accent), loaded from Google Fonts

## Environment

`NEXT_PUBLIC_API_URL` — reserved for Phase 6B. Currently unused.

## Deploy

Deployed via Vercel with the monorepo `vercel.json` at the repo root.
Root directory: `website/`.

## Design philosophy

Editorial, restrained, dark. Warm gold accent (`#d7b572`) on near-black
(`#08090b`). Serif display, monospace for labels and data, sans-serif
for body. Tick marks in card corners, tabular numerals, subtle radial
gradients in the background. Facts. Not Feelings.
