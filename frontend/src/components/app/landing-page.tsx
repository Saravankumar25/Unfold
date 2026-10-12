import { BookOpen, GitBranch, LockKeyhole, MessageSquareText, Network, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { ContinueSession } from "./continue-session";
import { LandingForm } from "./landing-form";
import { ThemeToggle } from "./theme-toggle";

const stages = ["Inspect repository", "Reconstruct workflows", "Map architecture", "Ground every claim"];

export function LandingPage() {
  return (
    <main className="theme-landing min-h-screen overflow-hidden">
      <div className="crease-field" aria-hidden />
      <header className="relative z-10 mx-auto flex max-w-[1240px] items-center justify-between px-5 py-6 sm:px-10 lg:px-16">
        <Link href="/" className="wordmark text-[22px] text-ink no-underline">Unfold</Link>
        <div className="flex items-center gap-2">
          <a href="#inside" className="hidden px-3 py-2 text-[13px] text-ink-soft hover:text-ink sm:block">What you get</a>
          <ThemeToggle />
        </div>
      </header>

      <section className="relative z-[1] mx-auto grid max-w-[1240px] gap-12 px-5 pb-16 pt-10 sm:px-10 lg:grid-cols-[1.08fr_.92fr] lg:px-16 lg:pb-24 lg:pt-20">
        <div>
          <p className="mb-6 flex items-center gap-3 font-mono text-[12px] uppercase tracking-[.16em] text-blueprint"><span className="h-px w-7 bg-blueprint" />repository → understanding</p>
          <h1 className="m-0 max-w-[760px] font-serif text-[clamp(54px,8.5vw,116px)] font-[390] leading-[.9] tracking-[-.035em]">
            Prepare your <span className="block font-[640] not-italic text-blueprint">project.</span>
          </h1>
          <p className="mb-0 mt-8 max-w-[570px] text-[clamp(16px,1.8vw,20px)] leading-[1.6] text-ink-soft">
            Unfold studies the codebase, reconstructs how it works, and turns it into a visual guide you can explain with confidence.
          </p>
          <div className="mt-8 flex flex-wrap gap-x-6 gap-y-3 font-mono text-[11px] uppercase tracking-[.08em] text-ink-faint">
            <span className="flex items-center gap-2"><LockKeyhole size={13} className="text-signal" />Static analysis only</span>
            <span className="flex items-center gap-2"><GitBranch size={13} className="text-signal" />Public repos & ZIPs</span>
            <span className="flex items-center gap-2"><BookOpen size={13} className="text-signal" />Evidence grounded</span>
          </div>
        </div>

        <LandingForm />
      </section>

      <div className="relative z-[1] border-y border-line bg-paper-dim/80">
        <div className="mx-auto flex max-w-[1112px] flex-wrap items-center gap-x-7 gap-y-3 px-5 py-4 font-mono text-[11px] text-ink-faint sm:px-10">
          <span className="text-signal">✛</span>{stages.map((stage, i) => <span key={stage} className="flex items-center gap-2"><b className="font-normal text-blueprint">0{i + 1}</b>{stage}</span>)}
        </div>
      </div>

      <section id="inside" className="relative z-[1] mx-auto max-w-[1112px] px-5 py-20 sm:px-10 lg:py-28">
        <div className="mb-12 flex flex-wrap items-end justify-between gap-6"><h2 className="m-0 max-w-[560px] font-serif text-[clamp(34px,5vw,58px)] font-[460] leading-[1.02]">Not another README generator.</h2><p className="m-0 max-w-[390px] text-[15px] leading-relaxed text-ink-soft">A connected learning environment built from the real repository—so every explanation has receipts.</p></div>
        <div className="grid border border-line md:grid-cols-3">
          {([
            [Network, "See the system", "Interactive architecture, dependency and workflow diagrams expose how the pieces connect."],
            [BookOpen, "Learn the reasoning", "A chaptered guide explains structure, tradeoffs, security, infrastructure and failure modes."],
            [MessageSquareText, "Question the code", "Ask follow-ups, rehearse interviews, and open the exact evidence behind each answer."],
          ] as [LucideIcon, string, string][]).map(([Icon, title, copy], i) => <article key={title} className="border-line bg-paper p-7 max-md:border-b md:border-r last:border-0 lg:p-9"><p className="m-0 font-mono text-[11px] text-signal">0{i + 1}</p><Icon className="my-8 text-blueprint" size={30} strokeWidth={1.4} /><h3 className="m-0 font-serif text-[23px] font-medium">{title}</h3><p className="mb-0 mt-3 text-[14px] leading-[1.65] text-ink-soft">{copy}</p></article>)}
        </div>
        <ContinueSession />
      </section>
      <footer className="relative z-[1] mx-auto flex max-w-[1112px] items-center justify-between border-t border-line px-5 py-8 text-[12px] text-ink-faint sm:px-10"><span className="wordmark text-[16px] text-ink">Unfold</span><span>Prepare your project.</span></footer>
    </main>
  );
}
