"use client";

import { BookOpen, Menu, MessageSquarePlus, Send, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { EvidenceProvider, useEvidence } from "@/components/content/evidence-drawer";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api/endpoints";
import type { ChatMode, ChatSessionOut } from "@/lib/api/types";
import { useChatThread } from "@/lib/hooks/use-chat-thread";
import { useResource } from "@/lib/hooks/use-resource";
import { useSlow } from "@/lib/hooks/use-slow";
import { cn } from "@/lib/utils";
import { ChatMessages } from "./chat-thread";
import { ErrorPanel } from "./error-panel";
import { ExportButton } from "./export-button";
import { ProjectReadyGate, useProject } from "./project-provider";
import { ProjectTopbar } from "./project-topbar";

const prompts = ["Walk me through the architecture", "Trace the main request flow", "What would break first at 100× scale?", "Quiz me on this project"];

function Conversation() {
  const { project } = useProject();
  const search = useSearchParams();
  const sectionKey = search.get("section");
  const chats = useResource(`chats:${project!.id}`, (signal) => api.listChats(project!.id, signal));
  const [selected, setSelected] = useState<string | null>(null);
  const [sideOpen, setSideOpen] = useState(false);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<ChatMode>("default");
  const bottomRef = useRef<HTMLDivElement>(null);
  const { openEvidence } = useEvidence();
  const thread = useChatThread(project!.id, selected, (id) => {
    setSelected(id);
    chats.mutate((current) => [{ id, project_id: project!.id, title: null, message_count: 0, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }, ...(current ?? [])]);
  });
  const slow = useSlow(thread.pending);
  const wasPending = useRef(false);
  const reloadChats = chats.reload;
  useEffect(() => {
    if (wasPending.current && !thread.pending) reloadChats(); // the thread list shows the new title and count
    wasPending.current = thread.pending;
  }, [thread.pending, reloadChats]);
  const [autoSelected, setAutoSelected] = useState(false);
  if (!autoSelected && selected === null && chats.data?.length) {
    const first = chats.data[0]!.id;
    setAutoSelected(true);
    setSelected(first);
    thread.selectChat(first);
  }
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [thread.state.items]);
  async function submit(event?: FormEvent) { event?.preventDefault(); const value = text.trim(); if (!value || thread.pending || thread.loadingHistory) return; setText(""); await thread.send(value, mode, sectionKey); }
  function chooseChat(id: string | null) { setSelected(id); thread.selectChat(id); setSideOpen(false); }
  const sidebar = <aside className="flex h-full flex-col bg-panel"><div className="border-b border-line p-3"><Button className="w-full justify-center" onClick={() => chooseChat(null)}><MessageSquarePlus size={15} />New conversation</Button></div><div className="flex-1 overflow-y-auto p-2"><p className="px-2 font-mono text-[10px] uppercase tracking-[.12em] text-ink-faint">Recent threads</p>{chats.data?.map((chat: ChatSessionOut) => <button key={chat.id} onClick={() => chooseChat(chat.id)} className={cn("mb-1 w-full cursor-pointer rounded-s px-3 py-2.5 text-left text-[12.5px]", selected === chat.id ? "bg-paper-dim text-blueprint" : "text-ink-soft hover:bg-paper-dim")}><span className="block truncate font-medium">{chat.title || "Project conversation"}</span><span className="mt-0.5 block font-mono text-[9.5px] text-ink-faint">{chat.message_count} messages</span></button>)}</div></aside>;
  return <>
    <ProjectTopbar project={project!} current="chat" leading={<Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setSideOpen((v) => !v)} aria-label="Open conversations"><Menu size={17} /></Button>} actions={thread.chatId && thread.state.items.length ? <><ExportButton start={() => api.exportChat(thread.chatId!, "pdf")} filename={`${project!.name || "unfold"}-chat.pdf`} size="sm" /><ExportButton start={() => api.exportChat(thread.chatId!, "md")} filename={`${project!.name || "unfold"}-chat.md`} label="Markdown" size="sm" /></> : undefined} />
    <div className="grid h-[calc(100vh-var(--topbar-h)-42px)] min-h-[560px] lg:h-[calc(100vh-var(--topbar-h))] lg:grid-cols-[var(--sidebar-w)_1fr]">
      <div className="hidden border-r border-line lg:block">{sidebar}</div>
      {sideOpen ? <div className="fixed inset-0 z-40 lg:hidden"><button className="absolute inset-0 bg-ink/25" aria-label="Close menu" onClick={() => setSideOpen(false)} /><div className="absolute inset-y-0 left-0 w-[min(84vw,280px)] border-r border-line shadow-xl">{sidebar}<button className="absolute right-2 top-2 p-2" onClick={() => setSideOpen(false)} aria-label="Close"><X size={16} /></button></div></div> : null}
      <section className="flex min-h-0 min-w-0 flex-col bg-paper">
        <div className="flex-1 overflow-y-auto px-4 py-7 sm:px-7"><div className="mx-auto max-w-[760px]">
          {thread.historyError ? <ErrorPanel error={thread.historyError} onRetry={thread.reloadHistory} title="Couldn't load this conversation" /> : null}
          {!thread.state.items.length && !thread.loadingHistory ? <div className="py-[8vh] text-center"><span className="wordmark block text-[24px] text-ink">Unfold</span><h1 className="m-0 mt-7 font-serif text-[clamp(34px,6vw,52px)] font-[450]">Question the code.</h1><p className="mx-auto mb-0 mt-4 max-w-[520px] text-[14px] leading-relaxed text-ink-soft">Ask anything about {project!.name || "this project"}. Answers use the repository, graph, and guide—not general guesses.</p><div className="mx-auto mt-8 grid max-w-[620px] gap-2 sm:grid-cols-2">{prompts.map((p) => <button key={p} onClick={() => setText(p)} className="cursor-pointer rounded-s border border-line bg-panel px-4 py-3 text-left text-[12.5px] text-ink-soft hover:border-blueprint hover:text-blueprint">{p}</button>)}</div></div> : null}
          <ChatMessages items={thread.state.items} slow={slow} onOpenEvidence={openEvidence} onRetry={(q) => void thread.send(q, mode, sectionKey)} /><div ref={bottomRef} />
        </div></div>
        <div className="border-t border-line bg-panel px-4 pb-[calc(14px+env(safe-area-inset-bottom))] pt-3"><form onSubmit={submit} className="mx-auto max-w-[760px]"><div className="mb-2 flex items-center justify-between gap-2"><Select value={mode} onChange={(e) => setMode(e.target.value as ChatMode)} aria-label="Answer mode" className="w-[150px]"><option value="default">Answer normally</option><option value="simpler">Explain simpler</option><option value="deeper">Go deeper</option><option value="quiz">Quiz me</option><option value="interview">Interview me</option></Select>{sectionKey ? <span className="flex items-center gap-1 font-mono text-[10px] text-ink-faint"><BookOpen size={11} />Guide context attached</span> : null}</div><div className="flex items-end gap-2"><textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }} rows={1} placeholder="Ask about this project…" className="max-h-[120px] min-h-[44px] flex-1 resize-none rounded-[18px] border border-line-strong bg-paper px-4 py-3 text-[14px] outline-none focus:border-blueprint" /><Button type="submit" size="icon" variant="primary" aria-label="Send message" disabled={!text.trim() || thread.pending || thread.loadingHistory}><Send size={15} /></Button></div><p className="mb-0 mt-2 text-center font-mono text-[9.5px] text-ink-faint">Grounded answers can still be imperfect. Open the evidence to verify.</p></form></div>
      </section>
    </div>
  </>;
}

function ChatWorkspace() { const { project } = useProject(); return <EvidenceProvider projectId={project!.id}><Conversation /></EvidenceProvider>; }
export function ChatPage() { return <ProjectReadyGate>{() => <ChatWorkspace />}</ProjectReadyGate>; }
