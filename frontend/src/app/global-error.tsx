"use client";

/** Catches errors thrown by the root layout itself (providers, fonts), which `error.tsx` cannot see. */
export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: "4rem auto", maxWidth: 560, padding: "0 1rem" }}>
        <h1 style={{ fontSize: 24 }}>Unfold hit a problem</h1>
        <p>{error.digest ? `Reference ${error.digest}.` : "An unexpected error occurred."}</p>
        <button type="button" onClick={() => retry()} style={{ padding: "8px 14px", cursor: "pointer" }}>
          Try again
        </button>
      </body>
    </html>
  );
}
