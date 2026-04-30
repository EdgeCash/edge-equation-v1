import Head from "next/head";
import { ReactNode } from "react";

import Header from "./Header";
import Footer from "./Footer";
import TestingBanner from "./TestingBanner";

type LayoutProps = {
  children: ReactNode;
  title?: string;
  description?: string;
};

export default function Layout({
  children,
  title = "Edge Equation",
  description = "Facts. Not Feelings. Transparent data and reasoning to help you become a better bettor.",
}: LayoutProps) {
  const pageTitle =
    title === "Edge Equation" ? title : `${title} — Edge Equation`;
  return (
    <>
      <Head>
        <title>{pageTitle}</title>
        <meta name="description" content={description} />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#06080c" />
      </Head>
      <div className="min-h-screen flex flex-col">
        <TestingBanner />
        <Header />
        <main className="flex-1">
          <div className="mx-auto max-w-6xl px-6 py-16">{children}</div>
        </main>
        <Footer />
      </div>
    </>
  );
}
