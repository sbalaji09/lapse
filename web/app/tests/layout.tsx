import type { Metadata } from "next";
import { Caveat, Instrument_Sans, Source_Serif_4 } from "next/font/google";

const display = Source_Serif_4({ subsets: ["latin"], weight: ["500", "600"], variable: "--font-display" });
const sans = Instrument_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-sans" });
const hand = Caveat({ subsets: ["latin"], weight: ["500", "600"], variable: "--font-hand" });

export const metadata: Metadata = { title: "Lapse Demo QA" };

export default function TestsLayout({ children }: { children: React.ReactNode }) {
  return <div className={`${display.variable} ${sans.variable} ${hand.variable}`}>{children}</div>;
}
