// Structured content for the Learn section.
//
// Articles are kept as data so they can be linked, indexed, and rendered by a
// single [slug].tsx page. Bodies are typed blocks (paragraphs, headings,
// lists, callouts) instead of raw HTML — keeps the formatting consistent and
// the brand voice constrained.

export type LearnTrack =
  | "Foundations"
  | "Bankroll"
  | "Process"
  | "The Edge Equation Way";

export type LearnBlock =
  | { type: "p"; text: string }
  | { type: "h2"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] }
  | { type: "callout"; tone: "info" | "warn" | "elite"; title: string; text: string }
  | { type: "formula"; text: string }
  | { type: "takeaway"; text: string };

export interface LearnArticle {
  slug: string;
  track: LearnTrack;
  title: string;
  time: string;
  summary: string;
  body: LearnBlock[];
}

// -----------------------------------------------------------------------------
// Foundations
// -----------------------------------------------------------------------------

const FOUNDATIONS: LearnArticle[] = [
  {
    slug: "what-an-edge-actually-is",
    track: "Foundations",
    title: "What an edge actually is",
    time: "5 min read",
    summary:
      "The gap between a fair probability and an implied probability. Why a 53% pick at -110 is a real edge and a 60% pick at -200 isn't.",
    body: [
      {
        type: "p",
        text:
          "Most bettors talk about edges in vibes — a hot team, a sharp angle, a play they 'love.' We use a stricter definition. An edge is a number, and that number is the gap between two probabilities: the one we believe is fair, and the one the market is currently pricing.",
      },
      { type: "h2", text: "The two probabilities" },
      {
        type: "p",
        text:
          "Every betting line implies a probability. A coin-flip line at +100 implies the book thinks it's 50/50. A heavy favorite at -300 implies the book thinks the favorite wins 75% of the time (after a small vig adjustment). That's the implied probability — what the price is telling you.",
      },
      {
        type: "p",
        text:
          "Our fair probability is the number our model produces. It is what we think the true probability is, given the inputs. If the two numbers disagree, the gap is the edge.",
      },
      { type: "formula", text: "Edge = Fair Probability − Implied Probability" },
      { type: "h2", text: "Why a 53% pick at -110 is a real edge" },
      {
        type: "p",
        text:
          "A -110 line implies roughly a 52.4% probability. If our fair probability is 53%, the edge is small — about 0.6 percentage points — but it is positive, and over a large sample it pays. That's what the math says.",
      },
      {
        type: "p",
        text:
          "A 60% pick at -200, on the other hand, looks juicier. But -200 implies about 66.7%. The model says 60%. The edge is negative six points. The pick loses, on average, even though it wins more often than not.",
      },
      {
        type: "callout",
        tone: "warn",
        title: "Hit rate ≠ edge",
        text:
          "It is entirely possible to win more than half your bets and still lose money. Win rate is not the metric. Edge — fair probability minus implied probability, priced against the line you actually got — is the metric.",
      },
      { type: "h2", text: "How we use it" },
      {
        type: "p",
        text:
          "Every pick on the daily board carries an explicit edge. The conviction tier is a function of how big that edge is, how confident we are in the inputs, and how stable the market is around it. No edge, no play.",
      },
      {
        type: "takeaway",
        text:
          "An edge is a number, not a feeling. If you can't write it down, you don't have one.",
      },
    ],
  },
  {
    slug: "reading-american-odds",
    track: "Foundations",
    title: "Reading American odds without a calculator",
    time: "4 min read",
    summary:
      "A simple mental model for converting +110, -135, -180 to implied probabilities in your head. It will make every other lesson easier.",
    body: [
      {
        type: "p",
        text:
          "American odds are not designed for clear thinking. The plus and minus, the inconsistent scale, the way -110 and +110 are not symmetric — all of it makes it harder than it should be to know what a price is actually saying.",
      },
      {
        type: "p",
        text:
          "The good news: you can get to a usable estimate in your head with two short formulas.",
      },
      { type: "h2", text: "For favorites (negative odds)" },
      { type: "formula", text: "Implied % ≈ |odds| ÷ (|odds| + 100)" },
      {
        type: "p",
        text:
          "So -150 is about 150 / 250 = 60%. -110 is 110 / 210 ≈ 52.4%. -200 is 200 / 300 ≈ 66.7%.",
      },
      { type: "h2", text: "For underdogs (positive odds)" },
      { type: "formula", text: "Implied % ≈ 100 ÷ (odds + 100)" },
      {
        type: "p",
        text:
          "+150 is 100 / 250 = 40%. +110 is 100 / 210 ≈ 47.6%. +300 is 100 / 400 = 25%.",
      },
      { type: "h2", text: "Anchor points to memorize" },
      {
        type: "ul",
        items: [
          "+100 / -100 → 50%",
          "-110 → ~52%",
          "-150 → 60%",
          "-200 → ~67%",
          "+150 → 40%",
          "+200 → ~33%",
          "+300 → 25%",
        ],
      },
      {
        type: "callout",
        tone: "info",
        title: "Vig lives in the gap",
        text:
          "If a market is -110 / -110 on both sides, both sides imply 52.4%. That's 104.8% total — the extra 4.8% is the book's cut. The 'true' fair probability is closer to 50%. Always remember that the implied probabilities you read off a board are inflated by the vig.",
      },
      {
        type: "takeaway",
        text:
          "If you can map four or five common odds to their probabilities in your head, every other piece of analysis on this site will land faster.",
      },
    ],
  },
  {
    slug: "variance-not-vibes",
    track: "Foundations",
    title: "Variance, not vibes",
    time: "6 min read",
    summary:
      "Why a profitable bettor can lose for a month and a losing bettor can win for a week. What sample sizes actually mean.",
    body: [
      {
        type: "p",
        text:
          "Sports betting is a noisy game played on a short timeline. Most of the bettors you see on the timeline are not winning or losing because of skill. They are winning or losing because of variance — and they are confusing one for the other.",
      },
      { type: "h2", text: "What variance is" },
      {
        type: "p",
        text:
          "Variance is the random spread of outcomes around an expected value. If you flip a fair coin 100 times, you expect 50 heads. You will almost never get exactly 50. You'll get 47, or 54, or 43. Over thousands of flips the average creeps toward 50%, but any individual stretch can look wild.",
      },
      {
        type: "p",
        text:
          "The same is true of betting. A model with a real 3% edge will still lose plenty of weeks. A model with no edge will still win some. The numbers below show how long a 'lucky streak' can plausibly be in a coin-flip game:",
      },
      {
        type: "ul",
        items: [
          "10 plays: anyone can run 7-3.",
          "50 plays: a coin-flip bettor lands above 60% about 8% of the time.",
          "200 plays: signal starts to separate from noise — if you're still 60%, something's actually working.",
          "1,000+ plays: now we can talk about edge with a straight face.",
        ],
      },
      { type: "h2", text: "What this means for you" },
      {
        type: "p",
        text:
          "If a tout shows you a 30-day record, that record is almost meaningless. If a tout shows you a 'last week' record, it is fully meaningless. The window is so short that the result tells you nothing about the underlying skill.",
      },
      {
        type: "p",
        text:
          "Same goes for our results. A bad week from us means very little. A bad season would mean something. We hold ourselves to the longer window because the shorter one isn't a real test.",
      },
      {
        type: "callout",
        tone: "warn",
        title: "The chase trap",
        text:
          "Variance is what makes every losing bettor think 'I'm due.' You are never due. Each play is independent. The model that put you down 4 units doesn't owe you a win — it just keeps producing edges, and you keep sizing them properly, and the law of large numbers does the rest.",
      },
      { type: "h2", text: "How to live with it" },
      {
        type: "ol",
        items: [
          "Size every play the same way. Variance hurts less when no single bet can wreck you.",
          "Track at least 100 plays before drawing any conclusion about a model — yours or anyone else's.",
          "Ignore your last bet completely when sizing the next one.",
          "Read your equity curve weekly, not daily.",
        ],
      },
      {
        type: "takeaway",
        text:
          "Variance is not a problem to solve. It's the medium you swim in. Plan around it instead of trying to outrun it.",
      },
    ],
  },
];

// -----------------------------------------------------------------------------
// Bankroll
// -----------------------------------------------------------------------------

const BANKROLL: LearnArticle[] = [
  {
    slug: "unit-sizing-for-humans",
    track: "Bankroll",
    title: "Unit sizing for humans",
    time: "5 min read",
    summary:
      "How to size a unit based on your real bankroll, your real income, and the kind of variance you can actually live with.",
    body: [
      {
        type: "p",
        text:
          "A unit is the standard size of a bet, expressed as a percentage of your bankroll. Sizing every play in units, instead of in dollars, is the single most important habit a bettor can build.",
      },
      { type: "h2", text: "Step one: define the bankroll" },
      {
        type: "p",
        text:
          "Your bankroll is the money you have set aside specifically for sports betting. It is not your savings, your rent, or money you'd be sad to lose. It is dedicated capital — what poker players call 'play money.' If you can't afford a number on this line, the right number is zero.",
      },
      { type: "h2", text: "Step two: pick a unit size" },
      {
        type: "p",
        text:
          "A reasonable unit is between 1% and 2% of bankroll. We recommend 1%. That sounds small. It is supposed to.",
      },
      {
        type: "ul",
        items: [
          "$500 bankroll → $5 unit.",
          "$2,500 bankroll → $25 unit.",
          "$10,000 bankroll → $100 unit.",
        ],
      },
      { type: "h2", text: "Step three: bet in units, not dollars" },
      {
        type: "p",
        text:
          "From here on, every play is sized in units. A standard play might be 1 unit. A larger one, supported by Kelly math, might be 1.5 or 2 units. The dollar amount changes as your bankroll grows or shrinks; the unit math stays the same.",
      },
      {
        type: "callout",
        tone: "warn",
        title: "Don't chase your bankroll up",
        text:
          "Increase your unit size only when your bankroll has grown for a sustained stretch — not because you ran hot for a weekend. We re-baseline our own unit no more often than monthly, and only on the way up.",
      },
      { type: "h2", text: "What a bad week looks like" },
      {
        type: "p",
        text:
          "At a 1% unit, a -10 unit week is a 10% drawdown. Not pleasant, but recoverable. At a 5% unit — common among people who have not done this exercise — a -10 unit week is a 50% drawdown. That's career-ending. The unit size, more than any pick, decides which scenario you live in.",
      },
      {
        type: "takeaway",
        text:
          "The right unit size is the one that lets you keep playing through a bad month. Pick that one.",
      },
    ],
  },
  {
    slug: "kelly-half-kelly-and-the-cap",
    track: "Bankroll",
    title: "Kelly, half-Kelly, and why we cap it",
    time: "8 min read",
    summary:
      "Full Kelly is mathematically optimal and emotionally devastating. Here is why we run at half-Kelly with a 25% cap and you probably should too.",
    body: [
      {
        type: "p",
        text:
          "Kelly sizing is a formula for choosing a bet size that maximizes the long-run growth rate of a bankroll. It tells you, given an edge and odds, exactly what fraction of your bankroll to wager. Full Kelly is mathematically optimal in a textbook sense. In real life, almost no one should run it.",
      },
      { type: "h2", text: "The formula" },
      { type: "formula", text: "Kelly fraction = (bp − q) / b" },
      {
        type: "p",
        text:
          "Where b is the decimal odds minus one, p is your fair probability, and q is 1 − p. The output is a fraction of bankroll. A small edge gives a small fraction. A massive edge gives a massive fraction.",
      },
      { type: "h2", text: "Why full Kelly is brutal" },
      {
        type: "p",
        text:
          "Full Kelly assumes your model's probability is exactly correct. In reality, every model's estimate is itself uncertain. If your true edge is smaller than you think, full Kelly overbets. And full Kelly drawdowns are deep — a 50% drawdown is mathematically the median experience over a typical bettor's lifetime.",
      },
      {
        type: "p",
        text:
          "A 50% drawdown means your bankroll cuts in half before recovering. Most bettors quit before they recover. That is not optimal in any practical sense.",
      },
      { type: "h2", text: "Half-Kelly" },
      {
        type: "p",
        text:
          "Half-Kelly is exactly what it sounds like: bet half of what the formula says. You give up about a quarter of long-run growth for a meaningful drop in drawdown depth. Most professional bettors run at or below half-Kelly. We do too.",
      },
      { type: "h2", text: "The 25% cap" },
      {
        type: "p",
        text:
          "We add one more guardrail: the size of any single play, after the half-Kelly multiplier, is capped at 25% of the full-Kelly fraction. This sounds redundant. It is not. It exists for the rare cases where the formula spits out a huge number — usually because the modeled edge is implausibly large or the odds are unusual. The cap turns those into normal-sized plays. We will not bet 30% of a bankroll on one game, no matter what the formula says.",
      },
      {
        type: "callout",
        tone: "elite",
        title: "How we publish sizing",
        text:
          "Every pick on the daily and premium boards carries a half-Kelly fraction explicitly. You can apply it to your own bankroll, ignore it, scale it down further — but the math is on the page.",
      },
      { type: "h2", text: "Practical rule of thumb" },
      {
        type: "ul",
        items: [
          "Edge under 1%: don't bet, regardless of what Kelly says.",
          "Edge 1–3%: standard play, half-Kelly sized.",
          "Edge 3–5%: larger play, half-Kelly sized, hits the cap rarely.",
          "Edge over 5%: re-check the inputs. Edges that big are rare and often a sign of bad data.",
        ],
      },
      {
        type: "takeaway",
        text:
          "Kelly is a tool, not a religion. Half-Kelly with a cap is what survives contact with reality.",
      },
    ],
  },
  {
    slug: "drawdowns-are-part-of-the-deal",
    track: "Bankroll",
    title: "Drawdowns are part of the deal",
    time: "5 min read",
    summary:
      "Even a great model can go -10 units in a stretch. How to recognize a normal drawdown vs. a broken process.",
    body: [
      {
        type: "p",
        text:
          "Every winning bettor has bad weeks. The difference between a winning bettor and a former winning bettor is what they do during those weeks.",
      },
      { type: "h2", text: "What's normal" },
      {
        type: "p",
        text:
          "A model with a 2–3% edge, sized at half-Kelly, will see drawdowns of 5–10 units regularly and 15–20 units occasionally. That is not a model failure. That is the math.",
      },
      {
        type: "p",
        text:
          "If you simulated a true 53% bettor over 1,000 plays, the deepest drawdown across those plays would average somewhere in the range of 12–18 units, and would occasionally reach 30+. Normal. Painful, but normal.",
      },
      { type: "h2", text: "What's a warning sign" },
      {
        type: "p",
        text:
          "A drawdown is worth investigating — not panicking about — when one of these is true:",
      },
      {
        type: "ul",
        items: [
          "The realized hit rate is far below the modeled hit rate over a sample of 200+ plays.",
          "Specific input sources have changed (a stat feed, a line feed, an injury report) and you didn't update.",
          "The market structure shifted — for example, books recalibrated a market type and your old edges have gone to zero.",
          "Your discipline broke. You started chasing, sizing up after losses, or betting markets you don't model.",
        ],
      },
      {
        type: "p",
        text:
          "Notice that 'we lost a lot last week' is not on that list. Loss size alone is not a signal.",
      },
      {
        type: "callout",
        tone: "warn",
        title: "What not to do during a drawdown",
        text:
          "Do not increase your unit size to 'win it back.' Do not switch to a different model in mid-stream. Do not start betting markets the model wasn't built for. The drawdown is information about variance, not instructions to take more risk.",
      },
      { type: "h2", text: "How we handle them" },
      {
        type: "ol",
        items: [
          "We log every play — wins, losses, and skips — so the realized rate is always queryable.",
          "We compare realized vs. modeled hit rate by conviction tier, not overall.",
          "When a tier underperforms over a real sample, we re-fit. Not before.",
          "We don't change unit size based on recent results.",
        ],
      },
      {
        type: "takeaway",
        text:
          "Most drawdowns are weather, not climate. Treat them like weather: stay dry, don't redesign the house.",
      },
    ],
  },
];

// -----------------------------------------------------------------------------
// Process
// -----------------------------------------------------------------------------

const PROCESS: LearnArticle[] = [
  {
    slug: "the-case-for-no-play",
    track: "Process",
    title: "The case for 'no play'",
    time: "4 min read",
    summary:
      "Saying nothing is a position. Skipped bets are not missed opportunities — they are the discipline that makes the bets you do place mean something.",
    body: [
      {
        type: "p",
        text:
          "There is a feature of betting culture that says you should always have action. Three games tonight, three plays. Big slate, big card. The model has nothing on this game? Find one anyway.",
      },
      {
        type: "p",
        text:
          "We disagree. Strongly. The single biggest improvement most bettors can make is to start passing on more games.",
      },
      { type: "h2", text: "What 'no play' means" },
      {
        type: "p",
        text:
          "A no-play is a market where the model and the line agree, or where our inputs are too uncertain to trust the gap. It's not a bearish stance — it's the absence of a stance. We have nothing to say. We say nothing.",
      },
      { type: "h2", text: "Why this matters" },
      {
        type: "ol",
        items: [
          "Edges are scarce. If you bet every game, the average edge of your portfolio is near zero. If you bet only the games with real edge, your portfolio's average edge is real.",
          "Variance scales with volume. More plays means a wider distribution of outcomes. Fewer plays, sized correctly, means a tighter ride.",
          "Time and attention are limited. If you're betting twelve games tonight, you cannot have done careful work on any of them.",
        ],
      },
      {
        type: "callout",
        tone: "elite",
        title: "We publish skips",
        text:
          "On the daily and premium boards we explicitly call out games we passed on, and why. The skip is not a missing pick — it is a pick. The pick is 'do nothing.'",
      },
      { type: "h2", text: "When to skip" },
      {
        type: "ul",
        items: [
          "Edge under your minimum threshold (we use ~1%).",
          "A line that has moved sharply since you priced it.",
          "Inputs you don't have confidence in (a late scratch, weather you can't model, a market that just opened).",
          "A game that violates assumptions your model relies on.",
          "Any time you can't articulate the reason for the bet in one sentence.",
        ],
      },
      {
        type: "takeaway",
        text:
          "The bets you don't make are part of your strategy. Anyone with a betting account can place a bet. Knowing when not to is the harder skill.",
      },
    ],
  },
  {
    slug: "line-shopping-is-the-cheapest-edge",
    track: "Process",
    title: "Line shopping is the cheapest edge",
    time: "5 min read",
    summary:
      "The same play at -105 vs. -120 is a different bet. Holding multiple books is the single highest-ROI move most bettors never make.",
    body: [
      {
        type: "p",
        text:
          "If you bet $100 on -110 every night for a year, and your friend bets $100 on -105 every night for a year, and you both go 100-100, your friend made about $476 more than you did. That's the difference between betting at -110 and -105 across 200 plays. No model improvement. No new sport. No work. Just a better number.",
      },
      { type: "h2", text: "What line shopping is" },
      {
        type: "p",
        text:
          "Line shopping means having accounts at multiple sportsbooks and choosing the best available number on whichever side you want to bet. Different books move lines independently — they have different customers, different exposure, different risk teams. The same player prop or total can vary by 5–20 cents across books at any given moment.",
      },
      { type: "h2", text: "Why it works" },
      {
        type: "p",
        text:
          "The implied-probability gap between -110 and -105 is roughly two percentage points. That's bigger than the edge on most picks. Capturing it is, mathematically, equivalent to running a slightly stronger model.",
      },
      { type: "formula", text: "-110 implies 52.4%. -105 implies 51.2%. Δ ≈ 1.2 pp of edge captured for free." },
      { type: "h2", text: "Practical setup" },
      {
        type: "ol",
        items: [
          "Open accounts at three to five regulated books available in your jurisdiction.",
          "Fund each one only with money you've already counted in your bankroll.",
          "Before placing a bet, check the line at every book you have access to.",
          "Place the bet at the best available price.",
          "Log the line you took, not the line you saw first.",
        ],
      },
      {
        type: "callout",
        tone: "warn",
        title: "Don't bet a market just because one book has a soft line",
        text:
          "Line shopping helps you get a better price on a bet you already wanted to make. It is not a license to chase any outlier number. If five books have a market at -110 and one has it at -103, the most likely explanation is stale data at the outlier — and books take their stale lines down before you can hammer them.",
      },
      { type: "h2", text: "How we treat lines" },
      {
        type: "p",
        text:
          "Our boards quote a representative line at the time we ran the slate. If you bet at a worse price, your realized edge is worse than ours. If you bet at a better price, it is better. We track our records against the line we actually quoted; we recommend you track yours against the line you actually got.",
      },
      {
        type: "takeaway",
        text:
          "Most bettors lose to vig. Line shopping is the simplest way to claw some of that vig back.",
      },
    ],
  },
  {
    slug: "how-to-read-a-slate",
    track: "Process",
    title: "How to read a slate without getting sucked in",
    time: "6 min read",
    summary:
      "A practical workflow for going from 'ten games tonight' to 'two bets, sized correctly, recorded properly' without falling for the parlay screen.",
    body: [
      {
        type: "p",
        text:
          "A full slate is a great way to lose money. Ten games look like ten chances. They're not — they're ten temptations. Most of them you should not bet.",
      },
      {
        type: "p",
        text:
          "Here is the workflow we use. It applies whether you're using our board or running your own model.",
      },
      { type: "h2", text: "Step 1: filter by tier first" },
      {
        type: "p",
        text:
          "Open the board. Ignore the games. Look only at the highest-conviction tier — Electric Blue, then Deep Green and Red, then everything else. Most days, the Electric Blue list is empty or has one play. That is by design.",
      },
      { type: "h2", text: "Step 2: confirm the inputs are still valid" },
      {
        type: "p",
        text:
          "Lines move. Lineups change. Weather updates. Before placing any bet, confirm that the inputs the model used are still the inputs that exist in the world. If a starting pitcher has been scratched since the slate was published, the pick is dead. Skip it.",
      },
      { type: "h2", text: "Step 3: line shop the survivors" },
      {
        type: "p",
        text:
          "For the picks that pass step 2, check at least two books and bet the best price. If the best available number is worse than the price the slate was graded at, recompute the edge. If the edge has gone negative or below your threshold, the pick is dead. Skip it.",
      },
      { type: "h2", text: "Step 4: size the survivors" },
      {
        type: "p",
        text:
          "Apply the half-Kelly fraction to your bankroll. That's your stake. Do not increase it because you 'love this one.' Do not decrease it because you got burned yesterday. Same math, every play.",
      },
      { type: "h2", text: "Step 5: log everything" },
      {
        type: "p",
        text:
          "Before you place the bet, write down the play, the line, the stake, and the modeled edge. After it settles, write down the result. Without this log, none of the lessons in the rest of this section apply to you — you have no data on yourself.",
      },
      {
        type: "callout",
        tone: "warn",
        title: "The parlay screen is a trap",
        text:
          "Same-game parlays exist because they are profitable for the books. The vig on a 4-leg SGP is often 25%+. If you find yourself gravitating to them on a slow night, that is a signal to do less, not more.",
      },
      { type: "h2", text: "What a clean session looks like" },
      {
        type: "ul",
        items: [
          "10 games on the slate.",
          "3 picks above your edge threshold after step 1.",
          "1 dies on a lineup change in step 2.",
          "1 dies on a bad price in step 3.",
          "1 placed at half-Kelly, logged, and forgotten until it settles.",
        ],
      },
      {
        type: "takeaway",
        text:
          "A clean session is mostly skips. The bet you place is the small visible part of a much larger filter.",
      },
    ],
  },
];

// -----------------------------------------------------------------------------
// The Edge Equation Way
// -----------------------------------------------------------------------------

const EE_WAY: LearnArticle[] = [
  {
    slug: "reading-the-conviction-tiers",
    track: "The Edge Equation Way",
    title: "Reading the conviction tiers",
    time: "3 min read",
    summary:
      "What Electric Blue actually means. Why a Lean isn't a recommendation. How to use the tiers in your own bankroll math.",
    body: [
      {
        type: "p",
        text:
          "Every pick on the board is tagged with one conviction tier. The tier is not a vibe — it is a fixed mapping from edge size, model agreement, and input quality. Same threshold, every day.",
      },
      { type: "h2", text: "The tiers" },
      {
        type: "ul",
        items: [
          "Electric Blue — Elite. Largest modeled edge with stable inputs. Rare by design.",
          "Deep Green — Strong NRFI / strong over read.",
          "Red — Strong YRFI / strong fade read.",
          "Amber — Moderate. Modest edge or noisier signal. Published for transparency.",
          "Slate — Lean. A small directional edge. Logged, not recommended.",
          "No Play — The model and the market agree. We pass.",
        ],
      },
      { type: "h2", text: "How to use them" },
      {
        type: "p",
        text:
          "If you size everything as 'one unit,' the tiers don't change much for you — they tell you which plays the model trusts most. If you scale your bet size by conviction, here is a sensible starting point:",
      },
      {
        type: "ol",
        items: [
          "Electric Blue → up to 1.5–2 units (still capped by the half-Kelly fraction on the pick).",
          "Deep Green / Red — strong directional → 1 unit.",
          "Amber → 0.5 unit, or skip if you're conservative.",
          "Slate / Lean → log it. Do not bet it unless you have a specific reason.",
          "No Play → do nothing.",
        ],
      },
      {
        type: "callout",
        tone: "info",
        title: "The tier is not the bet",
        text:
          "A tier label tells you how much the model trusts a number. Your bet size is your decision, made against your bankroll. We give you the math; we do not press the button for you.",
      },
      {
        type: "takeaway",
        text:
          "One color, one conviction. If you can read the board, you can read the day.",
      },
    ],
  },
  {
    slug: "how-to-use-the-daily-board",
    track: "The Edge Equation Way",
    title: "How to use the daily board",
    time: "4 min read",
    summary:
      "We post a slate. You don't have to bet all of it — and most days, you shouldn't. Here is how to filter the board to fit your bankroll and risk tolerance.",
    body: [
      {
        type: "p",
        text:
          "The daily board is the public output of the engine. It is free, and it will stay free. The picks are not the product — the reasoning is. Here is how to use the board without overplaying it.",
      },
      { type: "h2", text: "What you'll see" },
      {
        type: "ul",
        items: [
          "A list of picks with conviction tier, fair probability, edge, and half-Kelly fraction.",
          "A market type (totals, NRFI, moneyline, etc.) and the line we graded against.",
          "A timestamp for when the slate was generated.",
          "Stat tiles summarizing the slate (top edge, count by tier, etc.).",
        ],
      },
      { type: "h2", text: "How we recommend reading it" },
      {
        type: "ol",
        items: [
          "Skim the conviction tiers. Find the Electric Blue and strong directional picks first.",
          "Read the line we quoted. Compare to the line your book is showing right now.",
          "If the line moved against you past your threshold, skip the pick.",
          "If the line is still good, apply the half-Kelly fraction to your bankroll.",
          "Place the bet. Log the bet. Move on.",
        ],
      },
      {
        type: "callout",
        tone: "warn",
        title: "Don't bet the entire board",
        text:
          "On a typical day there are 3–8 picks across all tiers. Betting all of them is a portfolio decision — and it is rarely the right one. Most retail bettors should be picking from the top of the conviction stack only.",
      },
      { type: "h2", text: "What we do not provide" },
      {
        type: "ul",
        items: [
          "We do not tell you what fraction of your bankroll to risk overall in a day.",
          "We do not adjust picks for your state's available books or promotions.",
          "We do not chase. If yesterday went poorly, today's slate is unchanged.",
        ],
      },
      {
        type: "takeaway",
        text:
          "The board is a menu, not a meal plan. You pick what fits. Most days, that's one or two items.",
      },
    ],
  },
  {
    slug: "reading-the-grade-history-page",
    track: "The Edge Equation Way",
    title: "Reading the grade history page",
    time: "5 min read",
    summary:
      "Hit rate by tier, expected vs. realized, and what to look at before deciding whether to trust the model going forward.",
    body: [
      {
        type: "p",
        text:
          "We publish a grade history page so the model is forced to wear its own report card. Here is how to read it without fooling yourself.",
      },
      { type: "h2", text: "Hit rate by conviction tier" },
      {
        type: "p",
        text:
          "The most useful view is hit rate broken out by tier. Electric Blue should hit the most often; lower tiers should hit less, in roughly the proportion implied by their fair probabilities. If Electric Blue and Slate are hitting at the same rate over a real sample, something is wrong with the tiering — and we want you to be able to see that.",
      },
      { type: "h2", text: "Realized vs. modeled" },
      {
        type: "p",
        text:
          "Each tier has an expected hit rate based on the fair probabilities we published. The realized hit rate is what actually happened. Over small samples these will diverge wildly (see: variance). Over a full season, they should converge.",
      },
      {
        type: "callout",
        tone: "info",
        title: "What 'a real sample' means",
        text:
          "For Electric Blue, 50 plays is barely a signal. 200 is meaningful. 500+ starts to feel like evidence. Lower-volume tiers reach those thresholds faster; higher-conviction ones take longer because we publish fewer of them. Be patient with the small ones.",
      },
      { type: "h2", text: "Units returned" },
      {
        type: "p",
        text:
          "Hit rate is a useful stat. Units returned is a more honest one — it accounts for odds, sizing, and the actual price you would have realized. A model that wins 55% of its bets at -200 is unprofitable. A model that wins 48% of its bets at +110 might be a monster. Always look at both.",
      },
      { type: "h2", text: "What to ignore" },
      {
        type: "ul",
        items: [
          "Last week's record. Too noisy.",
          "Single-game results. Not data.",
          "Cherry-picked windows that someone else built.",
          "ROI calculations that don't match the line we quoted.",
        ],
      },
      { type: "h2", text: "What to look at" },
      {
        type: "ul",
        items: [
          "Hit rate by tier over a 200+ play window.",
          "Realized vs. modeled, with the gap explicitly shown.",
          "Units returned by tier, by sport, by market type.",
          "Drawdown depth and recovery time across the full record.",
        ],
      },
      {
        type: "takeaway",
        text:
          "The grade history page exists to make trust possible. Use it to decide whether to keep listening — and walk away when the numbers say walk away.",
      },
    ],
  },
];

export const LEARN_ARTICLES: LearnArticle[] = [
  ...FOUNDATIONS,
  ...BANKROLL,
  ...PROCESS,
  ...EE_WAY,
];

export function getArticle(slug: string): LearnArticle | undefined {
  return LEARN_ARTICLES.find((a) => a.slug === slug);
}

export function articlesByTrack(track: LearnTrack): LearnArticle[] {
  return LEARN_ARTICLES.filter((a) => a.track === track);
}
