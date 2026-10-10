"use client";

import { useEffect } from "react";
import { ErrorPanel } from "@/components/app/error-panel";

/** Last-resort boundary for an unexpected render error; API errors are handled where they happen. */
export default function Error({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main className="mx-auto w-full max-w-[640px] px-4 py-16 sm:px-6">
      <ErrorPanel error={error} title="This page hit a problem" onRetry={retry} />
    </main>
  );
}
