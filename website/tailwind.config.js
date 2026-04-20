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
          accent: "#d7b572",        // warm gold — restrained, editorial
          accentMuted: "#a68a55",
          line: "#242932",
          text: "#e7e3d8",
          textDim: "#8a8a7a",
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
