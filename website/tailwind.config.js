/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./pages/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Surfaces — dark, slightly cool
        ink: {
          950: "#06080c",
          900: "#0a0d13",
          800: "#11151d",
          700: "#1a202b",
          600: "#262d3b",
          500: "#3a4254",
        },
        // Brand palette
        edge: {
          // Electric Blue is the primary brand accent — reserved for ELITE
          // conviction items, headlines, and the only "loud" color on the site.
          accent: "#22d3ff",       // Electric Blue
          accentMuted: "#0fa9c9",
          accentSoft: "#0e3b48",   // for muted backgrounds / chips

          // Foreground
          text: "#e6ecf2",
          textDim: "#8593a6",
          textFaint: "#52607a",

          // Lines / borders
          line: "#1c2230",
          lineSoft: "#141a25",
        },
        // Conviction tiers — used by ConvictionBadge + ConvictionKey.
        // These are the *only* colors that should signal a conviction call.
        // Anything else stays neutral.
        conviction: {
          elite: "#22d3ff",        // Electric Blue — Elite play
          eliteSoft: "#0c2f3a",
          strong: "#10b981",       // Deep Green — Strong NRFI / Strong over
          strongSoft: "#0a2f25",
          fade: "#ef4444",         // Red — Strong YRFI / Strong fade
          fadeSoft: "#3a1212",
          moderate: "#f59e0b",     // Amber — Moderate / lean
          moderateSoft: "#3a2208",
          neutral: "#64748b",      // Slate — No play / informational
          neutralSoft: "#1c2230",
        },
      },
      fontFamily: {
        display: ["'Fraunces'", "ui-serif", "Georgia", "serif"],
        body: ["'Inter Tight'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        tightest: "-0.04em",
      },
      maxWidth: {
        prose: "68ch",
      },
      boxShadow: {
        "elite-glow": "0 0 0 1px rgba(34, 211, 255, 0.35), 0 8px 40px -12px rgba(34, 211, 255, 0.45)",
      },
    },
  },
  plugins: [],
};
