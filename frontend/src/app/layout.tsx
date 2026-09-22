import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";
import { AppProviders } from "@/components/app/app-providers";

const serif = Fraunces({ subsets: ["latin"], variable: "--font-fraunces", display: "swap" });
const sans = Space_Grotesk({ subsets: ["latin"], variable: "--font-grotesk", display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-plex-mono", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Unfold — Prepare your project", template: "%s · Unfold" },
  description: "Turn a public repository or ZIP into a visual, evidence-grounded project guide.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${serif.variable} ${sans.variable} ${mono.variable}`}>
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
