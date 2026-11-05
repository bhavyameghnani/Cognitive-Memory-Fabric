import React, { useState } from 'react';
import { Send, Loader2, Sparkles, AlertTriangle } from 'lucide-react';
import { api } from '../api';
import type { AskResult, EvidenceItem, Finding } from '../types';
import { AgentRunPanel } from './AgentRunPanel';

interface Props {
  suggestions: string[];
  result: AskResult | null;
  onResult: (r: AskResult | null) => void;
  onBusy: (b: boolean) => void;
  onSelectMemory: (id: string) => void;
}

export const AdvisorQuery: React.FC<Props> = ({ suggestions, result, onResult, onBusy, onSelectMemory }) => {
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const run = async (text: string) => {
    if (text.trim().length < 3 || busy) return;
    setBusy(true); onBusy(true); setError(null); setOpen(null);
    try { onResult(await api.ask(text.trim())); } catch (e: any) { setError(String(e.message ?? e)); } finally { setBusy(false); onBusy(false); }
  };
  const report = result?.report;
  const ev = (ref: string): EvidenceItem | undefined => report?.evidence.find((e) => e.ref === ref);

  const Findings = ({ title, items }: { title: string; items: Finding[] }) => items.length === 0 ? null : (
    <div>
      <h5 className="text-xs font-semibold text-slate-300 mb-1.5">{title}</h5>
      <ul className="space-y-2">
        {items.map((f, i) => (
          <li key={i} className="text-xs text-slate-300 leading-relaxed">
            {f.text}{' '}
            {f.refs.map((r) => (
              <button key={r} onClick={() => setOpen(open === r ? null : r)} className={`ml-0.5 px-1 rounded font-mono text-[10px] cursor-pointer ${open === r ? 'bg-blue-500 text-white' : 'bg-blue-500/15 text-blue-300 hover:bg-blue-500/30'}`}>{r}</button>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
  const opened = open ? ev(open) : undefined;

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 backdrop-blur-xl p-5 flex flex-col gap-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2"><Sparkles className="w-4 h-4 text-blue-400" />Ask the Cognitive Memory Fabric</h3>
        <p className="text-xs text-slate-400 mt-0.5">Retrieves the right memories, understands the timeline and reasons with context. Answers cite their evidence.</p>
      </div>
      <form onSubmit={(e) => { e.preventDefault(); run(q); }} className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="How should I prepare for Kenji's next portfolio review?" className="flex-1 rounded-xl bg-slate-950 border border-slate-700 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-blue-500" />
        <button disabled={busy || q.trim().length < 3} className="px-3 rounded-xl bg-blue-600 text-white disabled:opacity-40 cursor-pointer" aria-label="Ask">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}</button>
      </form>
      <div className="flex flex-wrap gap-1.5">
        {suggestions.map((s) => <button key={s} onClick={() => { setQ(s); run(s); }} className="text-[11px] px-2 py-1 rounded-lg border border-slate-700 text-slate-300 hover:border-blue-500/60 hover:text-white text-left cursor-pointer">{s}</button>)}
      </div>

      {error && (
        <div className="flex gap-2 text-xs rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-200 p-3"><AlertTriangle className="w-4 h-4 shrink-0" />
          <div><p className="font-medium">CMF could not answer</p><p className="text-rose-300/80 mt-0.5 break-words">{error}</p><p className="text-rose-300/60 mt-1">The brain map still works; answering needs the LLM configured via CMF_LLM_*.</p></div></div>
      )}
      {busy && <p className="text-xs text-slate-400 flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" />Planning, retrieving memories, calling tools, writing the report…</p>}

      {report && !busy && (
        <div className="flex flex-col gap-4 border-t border-slate-800 pt-4">
          <p className="text-sm text-slate-100 leading-relaxed">{report.summary}</p>
          <p className="text-[11px] text-slate-500 font-mono">{result!.retrieved_memory_ids.length} memories retrieved · highlighted on the brain · {report.request_id}</p>
          <Findings title="Key findings" items={report.key_findings} />
          <Findings title="Historical context" items={report.historical_context} />
          <Findings title="Financial analysis" items={report.financial_analysis} />
          {opened && (
            <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 text-xs">
              <p className="font-mono text-blue-300">{opened.ref} · {opened.source_class.replace(/_/g, ' ')}{opened.synthetic ? ' · synthetic' : ''}</p>
              <p className="text-slate-300 mt-1">{opened.description}</p>
              {opened.excerpt && <p className="italic text-slate-400 mt-1">“{opened.excerpt}”</p>}
              {opened.interaction_id && <p className="text-[10px] text-slate-500 font-mono mt-1">{opened.interaction_id} · turn {opened.turn_index}</p>}
              {opened.memory_id && <button onClick={() => onSelectMemory(opened.memory_id!)} className="mt-1.5 text-blue-300 underline underline-offset-2 hover:text-white cursor-pointer">open memory</button>}
            </div>
          )}
          {report.uncertainties.length > 0 && <Bullets title="Uncertainties" items={report.uncertainties} tone="text-amber-300" />}
          {report.suggested_followups.length > 0 && <Bullets title="Suggested follow-ups" items={report.suggested_followups} tone="text-emerald-300" />}
          {report.limitations.length > 0 && <Bullets title="Limitations" items={report.limitations} tone="text-slate-400" />}
          {result!.agent_run && <AgentRunPanel run={result!.agent_run} onSelectMemory={onSelectMemory} />}
        </div>
      )}
    </div>
  );
};

const Bullets = ({ title, items, tone }: { title: string; items: string[]; tone: string }) => (
  <div>
    <h5 className={`text-xs font-semibold mb-1 ${tone}`}>{title}</h5>
    <ul className="list-disc list-inside space-y-0.5 text-xs text-slate-400">{items.map((s, i) => <li key={i}>{s}</li>)}</ul>
  </div>
);
