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
        ink: {
          950: "#08090b",
          900: "#0d0f12",
          800: "#14171c",
          700: "#1e2229",
          600: "#2a2f38",
          500: "#3a4050",
        },
        edge: {
          accent: "#5BC0E8",        // logo cyan — controlled chaos
          accentMuted: "#A8DEF5",   // pale cyan for secondary accents
          line: "#242932",
          text: "#e7e3d8",
          textDim: "#8a8a7a",
          chalk: "#c9d3df",         // soft chalk-white for handwritten flourishes
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
    },
  },
  plugins: [],
};
