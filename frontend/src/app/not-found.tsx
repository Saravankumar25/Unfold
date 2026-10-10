import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto w-full max-w-[640px] px-4 py-24 sm:px-6">
      <p className="m-0 font-mono text-[12px] text-blueprint">404</p>
      <h1 className="m-0 mt-2 font-serif text-[34px] font-medium leading-tight">There is nothing here</h1>
      <p className="m-0 mt-3 text-[14.5px] leading-relaxed text-ink-soft">
        The page may have moved, or the project has expired and was removed.
      </p>
      <Link
        href="/"
        className="mt-6 inline-flex items-center rounded-s bg-blueprint px-4 py-2.5 text-[13.5px] font-semibold text-paper no-underline hover:bg-blueprint-deep"
      >
        Back to Unfold
      </Link>
    </main>
  );
}
