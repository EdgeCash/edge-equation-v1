import type { GetStaticPaths, GetStaticProps } from "next";
import Link from "next/link";

import Layout from "@/components/Layout";
import {
  LEARN_ARTICLES,
  getArticle,
  type LearnArticle,
  type LearnBlock,
} from "@/lib/learn-content";

type Props = {
  article: LearnArticle;
  prev: { slug: string; title: string } | null;
  next: { slug: string; title: string } | null;
};

export const getStaticPaths: GetStaticPaths = async () => ({
  paths: LEARN_ARTICLES.map((a) => ({ params: { slug: a.slug } })),
  fallback: false,
});

export const getStaticProps: GetStaticProps<Props> = async (ctx) => {
  const slug = ctx.params?.slug as string;
  const article = getArticle(slug);
  if (!article) return { notFound: true };
  const idx = LEARN_ARTICLES.findIndex((a) => a.slug === slug);
  const prev =
    idx > 0
      ? { slug: LEARN_ARTICLES[idx - 1].slug, title: LEARN_ARTICLES[idx - 1].title }
      : null;
  const next =
    idx < LEARN_ARTICLES.length - 1
      ? { slug: LEARN_ARTICLES[idx + 1].slug, title: LEARN_ARTICLES[idx + 1].title }
      : null;
  return { props: { article, prev, next } };
};

function Block({ block }: { block: LearnBlock }) {
  switch (block.type) {
    case "p":
      return (
        <p className="text-edge-textDim leading-relaxed text-[17px]">
          {block.text}
        </p>
      );
    case "h2":
      return (
        <h2 className="font-display text-2xl sm:text-3xl tracking-tightest leading-tight text-edge-text mt-10 mb-2">
          {block.text}
        </h2>
      );
    case "ul":
      return (
        <ul className="space-y-2 text-edge-textDim leading-relaxed text-[17px]">
          {block.items.map((item, i) => (
            <li key={i} className="flex gap-3">
              <span className="text-edge-accent font-mono text-sm pt-1">—</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol className="space-y-2 text-edge-textDim leading-relaxed text-[17px] counter-reset-list">
          {block.items.map((item, i) => (
            <li key={i} className="flex gap-4">
              <span className="font-mono text-edge-accent text-sm tabular-nums pt-0.5 min-w-[1.5rem]">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ol>
      );
    case "callout": {
      const tone =
        block.tone === "warn"
          ? "border-conviction-fade/40 bg-conviction-fadeSoft/40"
          : block.tone === "elite"
          ? "border-conviction-elite/40 bg-conviction-eliteSoft/40"
          : "border-edge-line bg-ink-900/60";
      const headTone =
        block.tone === "warn"
          ? "text-conviction-fade"
          : block.tone === "elite"
          ? "text-conviction-elite"
          : "text-edge-accent";
      return (
        <aside className={"my-2 rounded-sm border p-5 " + tone}>
          <div
            className={
              "font-mono text-[10px] uppercase tracking-[0.28em] mb-2 " + headTone
            }
          >
            {block.title}
          </div>
          <p className="text-edge-textDim leading-relaxed">{block.text}</p>
        </aside>
      );
    }
    case "formula":
      return (
        <pre className="font-mono text-sm sm:text-base text-edge-text bg-ink-900/80 border border-edge-line rounded-sm px-5 py-4 overflow-x-auto">
          {block.text}
        </pre>
      );
    case "takeaway":
      return (
        <aside className="mt-10 border-l-2 border-conviction-elite pl-5 py-1">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-elite mb-2">
            Takeaway
          </div>
          <p className="font-display text-xl sm:text-2xl tracking-tightest leading-snug text-edge-text">
            {block.text}
          </p>
        </aside>
      );
    default:
      return null;
  }
}

export default function LearnArticlePage({ article, prev, next }: Props) {
  return (
    <Layout title={article.title} description={article.summary}>
      <article className="max-w-prose">
        <Link
          href="/learn"
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1 inline-block"
        >
          ← All lessons
        </Link>

        <div className="mt-8 flex items-center gap-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent">
            {article.track}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
            · {article.time}
          </span>
        </div>

        <h1 className="mt-3 font-display font-light text-4xl sm:text-5xl tracking-tightest leading-[1.05]">
          {article.title}
        </h1>

        <p className="mt-5 text-edge-textDim text-lg leading-relaxed">
          {article.summary}
        </p>

        <div className="mt-12 space-y-5">
          {article.body.map((block, i) => (
            <Block key={i} block={block} />
          ))}
        </div>
      </article>

      <nav className="mt-20 max-w-prose grid gap-6 sm:grid-cols-2 border-t border-edge-line pt-10">
        {prev ? (
          <Link
            href={`/learn/${prev.slug}`}
            className="block border border-edge-line rounded-sm p-5 hover:border-edge-accent transition-colors"
          >
            <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
              ← Previous
            </div>
            <div className="mt-2 font-display text-lg tracking-tightest text-edge-text">
              {prev.title}
            </div>
          </Link>
        ) : (
          <div />
        )}
        {next ? (
          <Link
            href={`/learn/${next.slug}`}
            className="block border border-edge-line rounded-sm p-5 hover:border-edge-accent transition-colors text-right"
          >
            <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
              Next →
            </div>
            <div className="mt-2 font-display text-lg tracking-tightest text-edge-text">
              {next.title}
            </div>
          </Link>
        ) : (
          <div />
        )}
      </nav>

      <p className="mt-20 max-w-prose font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint leading-relaxed">
        Educational content. Nothing on this page is a recommendation to place
        a specific wager. 21+. Bet within your means.
      </p>
    </Layout>
  );
}
