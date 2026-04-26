// Thin status strip — reads like an oscilloscope / engine readout.
// Used at the top of pages where it helps the engine feel "alive":
// a pulsing cyan dot, a state label, and one or two key facts.

type EngineState = "live" | "standby" | "stale";

type Fact = {
  label: string;
  value: string;
};

type EngineStatusBarProps = {
  state?: EngineState;
  facts?: Fact[];
  className?: string;
};

const STATE_TEXT: Record<EngineState, string> = {
  live: "Engine · Live",
  standby: "Engine · Standby",
  stale: "Engine · Stale",
};

const STATE_DOT: Record<EngineState, string> = {
  live: "bg-edge-accent",
  standby: "bg-edge-textDim",
  stale: "bg-ink-500",
};

export default function EngineStatusBar({
  state = "live",
  facts = [],
  className = "",
}: EngineStatusBarProps) {
  return (
    <div
      className={
        "flex flex-wrap items-center gap-x-6 gap-y-2 " +
        "border-y border-edge-line bg-ink-900/60 backdrop-blur " +
        "px-4 py-2.5 font-mono text-[10px] uppercase tracking-[0.22em] " +
        className
      }
    >
      <div className="flex items-center gap-2">
        <span className="relative inline-flex h-2 w-2">
          {state === "live" && (
            <span className="absolute inset-0 rounded-full bg-edge-accent animate-ping opacity-60" />
          )}
          <span
            className={"relative inline-flex h-2 w-2 rounded-full " + STATE_DOT[state]}
          />
        </span>
        <span className="text-edge-text">{STATE_TEXT[state]}</span>
      </div>

      {facts.map((f, i) => (
        <div key={i} className="flex items-center gap-2 text-edge-textDim">
          <span className="text-edge-accent">{f.label}</span>
          <span className="text-edge-text tabular-nums">{f.value}</span>
        </div>
      ))}

      {/* trailing chalk tick — keeps the strip from feeling dead at the end */}
      <span className="ml-auto text-edge-textDim/70">— EQ.v1</span>
    </div>
  );
}
