import type { Metadata } from "next";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Due Diligence Intelligence Platform",
  description:
    "Agentic AI that researches companies, extracts citation-verified risk signals, and synthesizes due-diligence reports.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <div className="flex min-h-screen flex-col">
          <header className="border-b border-border">
            <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
              <Link href="/" className="flex items-center gap-2 font-semibold">
                <ShieldCheck className="h-5 w-5 text-primary" />
                <span>Due Diligence Intelligence</span>
              </Link>
              <nav className="flex items-center gap-4 text-sm text-muted-foreground">
                <Link href="/" className="hover:text-foreground">
                  New Assessment
                </Link>
                <Link href="/runs" className="hover:text-foreground">
                  History
                </Link>
              </nav>
            </div>
          </header>
          <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
          <footer className="border-t border-border py-4 text-center text-xs text-muted-foreground">
            Agentic Due Diligence Platform · LangGraph · MCP · RAG
          </footer>
        </div>
      </body>
    </html>
  );
}
