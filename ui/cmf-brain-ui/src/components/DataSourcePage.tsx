import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, Brain, Database, Mail, MessageSquare, Phone, Users, ChevronDown, ChevronRight, PenLine, Cog } from 'lucide-react';
import { api } from '../api';
import type { DsOverview, DsScenarioDetail, DsStep, MemoryItem } from '../types';
import { TYPES } from '../lib/memory';
import { TableBrowser } from './TableBrowser';

const CH: Record<string, { icon: typeof Users; color: string }> = {
  Meetings: { icon: Users, color: '#3b82f6' }, Calls: { icon: Phone, color: '#22c55e' }, Email: { icon: Mail, color: '#f59e0b' }, Chat: { icon: MessageSquare, color: '#a78bfa' },
};
const fmt = (s: string) => s.replace('T', ' ').slice(0, 16);
const Section = ({ n, title, sub, children }: { n: number; title: string; sub: string; children: React.ReactNode }) => (
  <section className="flex flex-col gap-3">
    <div className="flex items-baseline gap-3"><span className="font-mono text-xs text-blue-400">STEP {n}</span><h2 className="text-lg font-bold text-slate-100">{title}</h2></div>
    <p className="text-sm text-slate-400 max-w-3xl -mt-1">{sub}</p>
    {children}
  </section>
);

function Badge({ channel }: { channel: string }) {
  const { icon: Icon, color } = CH[channel] ?? CH.Meetings;
  return <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium" style={{ background: color + '22', color }}><Icon className="w-3.5 h-3.5" />{channel}</span>;
}

function StepCard({ step, memories }: { step: DsStep; memories: MemoryItem[] }) {
  const [open, setOpen] = useState(step.sequence === 1);
  const original = step.source_id.startsWith('INT');   // data/conversations.csv; other channels need the multichannel adapter
  const { color } = CH[step.channel] ?? CH.Meetings;
  return (
    <div className="relative pl-8">
      <span className="absolute left-0 top-2 w-3 h-3 rounded-full border-2 border-slate-950" style={{ background: color }} />
      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <button onClick={() => setOpen(!open)} className="w-full flex flex-wrap items-center gap-3 p-3 text-left cursor-pointer">
          {open ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-500" />}
          <Badge channel={step.channel} />
          <span className="font-mono text-xs text-slate-500">{step.source_id} · {fmt(step.timestamp)}</span>
          <span className="text-sm font-medium text-slate-100 flex-1 min-w-[12rem]">{step.source.title}</span>
          <span className="text-xs text-slate-500">{step.source.meta}</span>
        </button>
        {open && (
          <div className="border-t border-slate-800 p-3 grid grid-cols-1 xl:grid-cols-12 gap-4">
            <div className="xl:col-span-7 min-w-0">
              <h4 className="text-xs font-semibold text-slate-300 mb-2">① Raw source: what was actually said / written</h4>
              <div className="max-h-80 overflow-auto rounded-lg border border-slate-800">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-slate-900 text-slate-400"><tr><th className="text-left p-2 w-8">#</th><th className="text-left p-2 w-32">Who</th><th className="text-left p-2">Text</th></tr></thead>
                  <tbody>{step.source.messages.map((m) => (
                    <tr key={m.turn} className="border-t border-slate-800/70 align-top">
                      <td className="p-2 font-mono text-slate-500">{m.turn}</td>
                      <td className="p-2 text-slate-300">{m.who}<div className="text-[10px] text-slate-500">{m.role}</div></td>
                      <td className="p-2 text-slate-200 whitespace-pre-wrap">{m.text}</td>
                    </tr>))}</tbody>
                </table>
              </div>
            </div>
            <div className="xl:col-span-5 flex flex-col gap-3 min-w-0">
              <div>
                <h4 className="text-xs font-semibold text-slate-300 mb-2">② What the CRM kept</h4>
                {step.crm.length === 0 ? <p className="text-xs text-slate-500">Nothing: no CRM record references this source.</p> : step.crm.map((c) => (
                  <div key={c.crm_record_id} className="rounded-lg border border-slate-800 p-2 mb-2 text-xs">
                    <div className="flex items-center gap-2 text-slate-400"><span className="font-mono">{c.crm_record_id}</span><span className="rounded bg-slate-800 px-1.5">{c.type}</span>
                      <span className="inline-flex items-center gap-1">{c.transfer.startsWith('manual') ? <PenLine className="w-3 h-3 text-amber-400" /> : <Cog className="w-3 h-3 text-slate-500" />}{c.transfer}</span></div>
                    <div className="text-slate-200 mt-1">{c.text}</div>
                  </div>))}
              </div>
              <div>
                <h4 className="text-xs font-semibold text-slate-300 mb-2">③ Memories extracted from it</h4>
                {memories.length === 0 ? <p className="text-xs text-slate-500">{original ? 'No memory in the store cites this source.'
                    : 'No memory cites this record. Email, chat and follow-up meetings are ingested only with CMF_MEMORY_CHANNELS=all and CMF_INGEST_MODE=llm_first.'}</p>
                  : memories.map((m) => {
                    const ev = m.evidence.find((e) => e.interaction_id === step.source_id);
                    return (
                      <div key={m.memory_id} className="rounded-lg border border-slate-800 p-2 mb-2 text-xs">
                        <div className="flex flex-wrap items-center gap-x-1.5">
                          <span className="font-semibold" style={{ color: TYPES[m.memory_type].color }}>{TYPES[m.memory_type].name}</span>
                          <span className="text-slate-500">·</span>
                          <span className={`font-semibold capitalize ${m.certainty === 'confirmed' || m.certainty === 'correction' ? 'text-emerald-300' : 'text-sky-300'}`}>{m.certainty}</span>
                          <span className="text-slate-500">· {Math.round(m.confidence * 100)}% · {m.status}</span>
                        </div>
                        <div className="text-slate-200 mt-0.5">{m.memory_type === 'semantic' ? m.title : m.content}</div>
                        {ev && <div className="text-slate-400 italic mt-1 border-l-2 border-amber-500/50 pl-2">“{ev.excerpt}” <span className="not-italic font-mono text-slate-500">turn {ev.turn_index}</span></div>}
                      </div>);
                  })}
              </div>
              <p className="text-[11px] text-slate-500">Link to the storyline: {step.link_basis.replace(/_/g, ' ')} · {step.origin}{step.note ? ` · ${step.note}` : ''}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function DataSourcePage() {
  const [ov, setOv] = useState<DsOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sid, setSid] = useState<string | null>(null);
  const [detail, setDetail] = useState<DsScenarioDetail | null>(null);

  useEffect(() => { api.datasource().then((o) => { setOv(o); setSid(o.scenarios[0]?.scenario_id ?? null); }).catch((e) => setErr(String(e.message ?? e))); }, []);
  useEffect(() => {
    if (!sid) return;
    setDetail(null);
    let live = true;
    api.scenario(sid).then((d) => { if (live) setDetail(d); }).catch((e) => { if (live) setErr(String(e.message ?? e)); });
    return () => { live = false; };
  }, [sid]);

  const clientIds = useMemo(() => [...new Set((ov?.scenarios ?? []).map((x) => x.client_id))].sort(), [ov]);
  const byStep = useMemo(() => {
    const m = new Map<string, MemoryItem[]>();
    detail?.memories.forEach((mem) => mem.evidence.forEach((e) => { const a = m.get(e.interaction_id) ?? []; if (!a.includes(mem)) a.push(mem); m.set(e.interaction_id, a); }));
    return m;
  }, [detail]);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-xl sticky top-0 z-30">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3.5 flex items-center justify-between">
          <h1 className="text-base font-bold tracking-tight flex items-center gap-2"><Database className="w-5 h-5 text-blue-400" />Learn about the data source</h1>
          <a href="#/" className="flex items-center gap-1.5 text-xs text-slate-300 hover:text-white"><ArrowLeft className="w-4 h-4" />Back to the memory brain</a>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8 flex flex-col gap-10">
        {err && <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-200 text-xs p-3">Could not load: {err}</div>}
        <p className="text-sm text-slate-300 max-w-3xl">All data here is <b>synthetic</b> (6 fictional clients, 3 advisors, Jan–Jun 2026). Real advisory history is fragmented across separate systems. This page follows one client story from the original communication, through the CRM, to the memory the system builds.</p>

        <Section n={1} title="Where the data comes from: four communication channels" sub="Each channel is captured by a different platform, with a different structure and ID scheme. Nothing is unified at the source.">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {(ov?.channels ?? []).map((c) => {
              const { icon: Icon, color } = CH[c.channel];
              return (
                <div key={c.channel} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                  <div className="flex items-center gap-2" style={{ color }}><Icon className="w-5 h-5" /><span className="font-semibold">{c.channel}</span></div>
                  <div className="mt-2 text-2xl font-bold tabular-nums">{c.records}<span className="text-xs font-normal text-slate-400 ml-1">{c.unit}</span></div>
                  {c.messages != null && <div className="text-xs text-slate-400">{c.messages} individual messages</div>}
                  <div className="mt-2 text-xs text-slate-300">{c.what}</div>
                  <div className="mt-1 text-[11px] text-slate-500 font-mono">{c.platform}</div>
                </div>);
            })}
          </div>
          <h3 className="text-sm font-semibold text-slate-200 mt-2">The actual data files: browse every row</h3>
          <TableBrowser groups={['Meetings', 'Calls', 'Email', 'Chat']} clients={clientIds} />
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span className="rounded-lg bg-slate-900 border border-slate-800 px-3 py-1.5">Raw channels</span><ArrowRight className="w-4 h-4" />
            <span className="rounded-lg bg-slate-900 border border-slate-800 px-3 py-1.5">CRM (manual + automatic notes)</span><ArrowRight className="w-4 h-4" />
            <span className="rounded-lg bg-blue-500/10 border border-blue-500/30 text-blue-200 px-3 py-1.5 inline-flex items-center gap-1.5"><Brain className="w-4 h-4" />Structured memory</span>
          </div>
          <p className="text-xs text-slate-500 max-w-3xl">Meetings and calls started as one file of {ov?.original_turns ?? '…'} transcript turns labelled only by <span className="font-mono">video / in-person / telephone</span>; telephone became Calls, the rest Meetings. Emails and chats were added as new, separately structured sources.</p>
        </Section>

        <Section n={2} title="What the CRM keeps: a thin, partly manual summary" sub="Advisors’ CRM holds one flat list per channel. Most entries are automatic log lines (“Meeting with client logged”); only a few carry real content, and those were typed by hand.">
          <div className="overflow-auto rounded-xl border border-slate-800">
            <table className="w-full text-xs">
              <thead className="bg-slate-900 text-slate-400"><tr><th className="text-left p-2">CRM artifact</th><th className="text-right p-2">Records</th><th className="text-right p-2">Automatic log</th><th className="text-right p-2">Manual note / summary</th></tr></thead>
              <tbody>{(ov?.crm ?? []).map((c) => (
                <tr key={c.artifact} className="border-t border-slate-800"><td className="p-2 font-mono">{c.artifact}</td><td className="p-2 text-right tabular-nums">{c.total}</td><td className="p-2 text-right tabular-nums">{c.automatic}</td><td className="p-2 text-right tabular-nums text-amber-300">{c.manual}</td></tr>))}</tbody>
            </table>
          </div>
          <h3 className="text-sm font-semibold text-slate-200 mt-2">The actual CRM records: browse every row</h3>
          <TableBrowser groups={['CRM']} clients={clientIds} />
        </Section>

        <Section n={3} title="Follow one story across channels" sub="Pick a storyline. Each card is one real source record. Open it to see the raw text, what the CRM kept of it, and the memories the system extracted.">
          <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
            <div className="md:col-span-4 flex flex-col gap-1.5 max-h-[32rem] overflow-auto pr-1">
              {(ov?.scenarios ?? []).map((s) => (
                <button key={s.scenario_id} onClick={() => setSid(s.scenario_id)}
                  className={`text-left rounded-lg border p-2.5 text-xs cursor-pointer ${sid === s.scenario_id ? 'border-blue-500/60 bg-blue-500/10' : 'border-slate-800 bg-slate-900/60 hover:border-slate-700'}`}>
                  <div className="font-mono text-slate-500">{s.scenario_id} · {s.client_id}</div>
                  <div className="text-slate-100 font-medium">{s.title}</div>
                  <div className="text-slate-500 mt-0.5">{s.record_count} records · {s.channels_present.replace(/\|/g, ', ')}</div>
                </button>))}
            </div>
            <div className="md:col-span-8 min-w-0">
              {detail ? (
                <>
                  <p className="text-sm text-slate-300 mb-4 rounded-lg border border-slate-800 bg-slate-900/60 p-3"><b className="text-slate-100">The story:</b> {detail.scenario.arc_summary}</p>
                  <div className="relative flex flex-col gap-3 before:absolute before:left-[5px] before:top-3 before:bottom-3 before:w-px before:bg-slate-800">
                    {detail.steps.map((s) => <StepCard key={s.sequence} step={s} memories={byStep.get(s.source_id) ?? []} />)}
                  </div>
                </>
              ) : <div className="text-sm text-slate-500 p-6">{sid ? 'Loading…' : 'No storylines available.'}</div>}
            </div>
          </div>
        </Section>

        {detail && (
          <Section n={4} title="The processed result: memory records for this story" sub="Structured memories extracted from the transcripts above. Each cites the exact turn it came from, so every fact can be traced back to the raw text.">
            {detail.memories.length === 0 ? <p className="text-sm text-slate-500">No memories in the connected store cite this story’s sources (this client may not be ingested yet).</p> : (
              <div className="overflow-auto rounded-xl border border-slate-800">
                <table className="w-full text-xs">
                  <thead className="bg-slate-900 text-slate-400"><tr><th className="text-left p-2">Type</th><th className="text-left p-2">Status</th><th className="text-left p-2">Memory</th><th className="text-left p-2">Value</th><th className="text-left p-2">Evidence (source · turn)</th></tr></thead>
                  <tbody>{detail.memories.map((m) => (
                    <tr key={m.memory_id} className="border-t border-slate-800 align-top">
                      <td className="p-2 font-semibold whitespace-nowrap" style={{ color: TYPES[m.memory_type].color }}>{m.memory_type}</td>
                      <td className="p-2 text-slate-300">{m.status}</td>
                      <td className="p-2 text-slate-100">{m.title}<div className="text-slate-500">{m.topic}{m.subject ? ` · ${m.subject}` : ''}</div></td>
                      <td className="p-2 text-slate-300">{m.value_text ?? '—'}</td>
                      <td className="p-2 text-slate-400">{m.evidence.map((e) => (<div key={e.interaction_id + e.turn_index} className="mb-1"><span className="font-mono text-slate-300">{e.interaction_id} · T{e.turn_index}</span> “{e.excerpt}”</div>))}</td>
                    </tr>))}</tbody>
                </table>
              </div>)}
          </Section>
        )}
      </main>
    </div>
  );
}
