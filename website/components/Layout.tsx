import Head from "next/head";
import { ReactNode } from "react";

import Header from "./Header";
import Footer from "./Footer";

type LayoutProps = {
  children: ReactNode;
  title?: string;
  description?: string;
};

export default function Layout({
  children,
  title = "Edge Equation",
  description = "Deterministic sports analytics. Facts. Not Feelings.",
}: LayoutProps) {
  const pageTitle =
    title === "Edge Equation" ? title : `${title} — Edge Equation`;
  return (
    <>
      <Head>
        <title>{pageTitle}</title>
        <meta name="description" content={description} />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#08090b" />
      </Head>
      <div className="min-h-screen flex flex-col">
        <Header />
        <main className="flex-1">
          <div className="mx-auto max-w-6xl px-6 py-16">{children}</div>
        </main>
        <Footer />
      </div>
    </>
  );
}
