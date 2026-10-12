import type { Metadata, Viewport } from "next";
import { Fraunces, IBM_Plex_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";
import { AppProviders } from "@/components/app/app-providers";

const serif = Fraunces({ subsets: ["latin"], variable: "--font-fraunces", display: "swap" });
const sans = Space_Grotesk({ subsets: ["latin"], variable: "--font-grotesk", display: "swap" });
// optional: the mono text is small and secondary, so a late font is skipped rather than swapped in (a swap shifted the landing form, CLS 0.09)
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-plex-mono", display: "optional", adjustFontFallback: false, fallback: ["ui-monospace", "monospace"] });

const description = "Turn a public repository or ZIP into a visual, evidence-grounded project guide.";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.PUBLIC_WEB_ORIGIN || "http://localhost:3000"),
  title: { default: "Unfold — Prepare your project", template: "%s · Unfold" },
  description,
  applicationName: "Unfold",
  openGraph: { type: "website", siteName: "Unfold", title: "Unfold — Prepare your project", description },
  twitter: { card: "summary_large_image", title: "Unfold — Prepare your project", description },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfaf6" },
    { media: "(prefers-color-scheme: dark)", color: "#14161c" },
  ],
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
