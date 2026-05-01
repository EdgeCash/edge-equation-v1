# X / Social Graphic Palette

Reference for AI image generation + manually-built X cards. The goal:
**a graphic that looks like it came off the website.** Visual cohesion
between the X post and the landing page builds trust and clicks.

## Brand promise the visual must reinforce

- **Honest. Numbers, not hype.**
- **Free + transparent track record.**
- **One sport at a time.** (MLB now; football later.)

If the graphic looks like a generic "lock of the day 🔒🔒🔒" promo, it
fails the brand. Restraint > flash.

## Color palette (hex codes)

Lifted directly from `website/tailwind.config.js` so what we ship in
graphics matches the site exactly.

### Surfaces (background → cards)

| Token | Hex | Use |
|---|---|---|
| `ink-950` | `#06080c` | Page background. Default canvas for graphics. |
| `ink-900` | `#0a0d13` | Card / panel background. Darker than gray. |
| `ink-800` | `#11151d` | Subtle elevation. |
| `ink-700` | `#1a202b` | Borders / dividers in dark panels. |

### Brand accent (used SPARINGLY)

| Token | Hex | Use |
|---|---|---|
| `edge-accent` (Electric Blue) | `#22d3ff` | Primary brand — eyebrows, italic emphasis, ELITE conviction. ONE loud color per graphic. |
| `edge-accentMuted` | `#0fa9c9` | Alternate Electric Blue. |
| `edge-accentSoft` | `#0e3b48` | Background fill behind Electric Blue chips. |

### Conviction tier colors

These colors carry SEMANTIC meaning. Don't repurpose them for
non-conviction context.

| Tier | Hex | When |
|---|---|---|
| ELITE | `#22d3ff` (Electric Blue) | Highest-conviction picks only. Reserved. |
| STRONG (over / NRFI) | `#10b981` (Deep Green) | Strong upside calls. |
| STRONG (fade / YRFI) | `#ef4444` (Red) | Strong fade / strong YRFI. |
| MODERATE / LEAN | `#f59e0b` (Amber) | Mid-tier conviction. |
| NO PLAY / neutral | `#64748b` (Slate) | Informational, no action. |

### Foreground / text

| Token | Hex | Use |
|---|---|---|
| `edge-text` | `#e6ecf2` | Primary text. High contrast on dark. |
| `edge-textDim` | `#8593a6` | Body / secondary text. |
| `edge-textFaint` | `#52607a` | Captions, eyebrows, fine print. |

## Typography

| Family | Use | CSS |
|---|---|---|
| **Fraunces** | Display headlines, italic emphasis | `font-display` |
| **Inter Tight** | Body copy | `font-body` |
| **JetBrains Mono** | Numbers, ticker text, timestamps, eyebrows | `font-mono` |

For X graphics where these specific fonts aren't available, sub:
- Fraunces → any humanist serif (Source Serif 4, EB Garamond)
- Inter Tight → any geometric sans (Inter, Söhne, Söhne Buch)
- JetBrains Mono → IBM Plex Mono / JetBrains Mono / Roboto Mono

## Layout patterns the site uses

The graphic should evoke (not necessarily copy) one of these
recurring patterns:

1. **Hero block** — giant Fraunces display headline, italic accent
   on the second line, mono eyebrow above. Lots of negative space.
2. **Card with corner ticks** — see `CardShell.tsx`. Four small
   `L`-shaped tick marks at the corners of a panel. Editorial feel.
3. **Conviction chip** — small `rounded-sm` pill, mono uppercase
   text, conviction-token background + matching foreground.
4. **Stat tile** — large mono number, small uppercase label below.

## Ratios + spacing

- **Twitter card aspect**: 1.91 : 1 (e.g., 1500 × 785).
- **Margin**: at least 8% of the longest edge as breathing room.
- **One eye-catch**: one Electric Blue element per graphic. Avoid
  multiple loud colors competing for attention.

## Voice of the copy on a graphic

Examples that pass the bar:

> ELITE  ·  KC @ BAL  ·  NRFI 67.3%
> Thursday's highest-conviction first-inning play.
> Free at edgeequation.com

> Free Daily MLB Picks · Highest Conviction First.
> Track record published openly.
> edgeequation.com

> Yesterday: 2-1 on STRONG · 1-0 on ELITE · +1.83u
> Today's board posts at 11:30 CT.

Examples that **fail** the bar (hype, opacity, bad-actor cosplay):

> 🔒 LOCK OF THE DAY 🔒  10-0 RUN!  100% GUARANTEED
> DM ME FOR MY TAILS · WIN BIG TONIGHT
> +EV PICKS · INSIDE INFO

## Don'ts

- Don't put Electric Blue and Red side-by-side at full saturation.
  They vibrate and read as a generic "win/loss" infographic.
- Don't use exclamation points. Honestly.
- Don't claim a record we haven't published on the public ledger.
- Don't show a probability without rounding. `67.3%` not `0.6731234`.
- Don't render dollar signs or cash imagery. We publish data, not
  bets. Everything stays in units (`+1.83u`) when stakes are shown.

## When in doubt

Open `/track-record` on the website, screenshot the tier-summary
strip, and use that as the visual reference. The graphic should look
like something that could ship as a section of the site — not a
poster pasted on top of it.
