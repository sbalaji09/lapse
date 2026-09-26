import type { Metadata } from "next";

export const metadata: Metadata = { title: "Lapse Demo QA" };

export default function TestsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
