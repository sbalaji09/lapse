import "./globals.css";
import type { Metadata } from "next";
import { Caveat, Instrument_Sans, Source_Serif_4 } from "next/font/google";

const display = Source_Serif_4({ subsets: ["latin"], weight: ["500", "600"], variable: "--font-display" });
const sans = Instrument_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-sans" });
const hand = Caveat({ subsets: ["latin"], weight: ["500", "600"], variable: "--font-hand" });

export const metadata: Metadata = {
  title: "Lapse",
  description: "Find the Medicaid members a billing-code check will wrongly drop, and the one fact that keeps each covered.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${hand.variable}`}>
      <body>{children}</body>
    </html>
  );
}
