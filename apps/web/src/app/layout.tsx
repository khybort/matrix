import "./globals.css";
import type { Metadata } from "next";
import { JetBrains_Mono, Instrument_Serif } from "next/font/google";

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "700"],
  variable: "--font-jetbrains",
});

const instrument = Instrument_Serif({
  subsets: ["latin"],
  display: "swap",
  weight: "400",
  style: ["normal", "italic"],
  variable: "--font-instrument",
});

export const metadata: Metadata = {
  title: "matrix · trading terminal",
  description: "live state of the autonomous trading + self-improving research engine",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${jetbrains.variable} ${instrument.variable}`}>
      <body>{children}</body>
    </html>
  );
}
