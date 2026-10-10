import { LoadingLines } from "@/components/app/skeleton";

export default function Loading() {
  return (
    <main className="mx-auto w-full max-w-[640px] px-4 py-16 sm:px-6">
      <LoadingLines label="Loading…" lines={5} />
    </main>
  );
}
