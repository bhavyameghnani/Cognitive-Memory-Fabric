import React, { useEffect, useRef, useState } from 'react';
import { Send, Loader2, Sparkles, AlertTriangle, Square } from 'lucide-react';
import { api, ApiError } from '../api';
import type { AskJob, AskResult, ExecutionStatus } from '../types';
import { describeFailure } from '../lib/reportView';
import { ReportView } from './ReportView';

interface Props {
  suggestions: string[];
  result: AskResult | null;
  onResult: (r: AskResult | null) => void;
  onBusy: (b: boolean) => void;
  onSelectMemory: (id: string) => void;
}

/** The browser never gives up on its own: the local model is slow and the user decides when to stop.  The backend's request budget (HARNESS_MAX_RUNTIME_SECONDS, 3600 s in this setup) is the only limit. */
const NO_LIMIT_S = Infinity;
const JOB_KEY = 'cmf.askJob';
const POLL_MS = 3000;
const MAX_MISSES = 20;
const STALL_MS = 30000;        // one status check that gets no answer for this long is abandoned and repeated on a fresh connection

// The backend answers once, at the end: it does not stream progress.  These stages are TYPICAL timings of the local model (qwen2.5:3b on CPU), shown as an estimate and labelled as one.
const STAGES: [number, string][] = [
  [0, 'Planner: understanding the question and the client'],
  [60, 'Memory: retrieving the client’s memories'],
  [90, 'Tool Selector: finding and reading the relevant meetings'],
  [150, 'Report Agent: writing the report'],
  [330, 'Report Agent: writing and verifying the narrative'],
  [480, 'Still working: the local model is slow on this machine'],
];
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

export const AdvisorQuery: React.FC<Props> = ({ suggestions, result, onResult, onBusy, onSelectMemory }) => {
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ title: string; detail: string } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const ctl = useRef<AbortController | null>(null);
  const started = useRef(0);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - started.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy]);

  // The job id survives a refresh, a tab switch and a closed window (sessionStorage), so the report is fetched when it is ready instead of being lost with a dropped connection.
  const save = (j: { id: string; q: string; started: number } | null) => { try { if (j) sessionStorage.setItem(JOB_KEY, JSON.stringify(j)); else sessionStorage.removeItem(JOB_KEY); } catch { /* storage unavailable: no resume */ } };
  // One status check that never answers must not freeze the page: a stalled connection is cut after STALL_MS and the check is repeated (the finished report stays on the server for an hour).
  const statusWithStallLimit = async (id: string, outer: AbortSignal): Promise<AskJob> => {
    const t = new AbortController();
    const timer = setTimeout(() => t.abort('stall'), STALL_MS);
    const relay = () => t.abort(outer.reason);
    outer.addEventListener('abort', relay, { once: true });
    try { return await api.askStatus(id, t.signal); }
    finally { clearTimeout(timer); outer.removeEventListener('abort', relay); }
  };
  const poll = async (id: string, since: number) => {
    const c = new AbortController();
    ctl.current = c; started.current = since; setElapsed(Math.floor((Date.now() - since) / 1000));
    setBusy(true); onBusy(true); setFailure(null); setNote(null);
    let misses = 0;
    try {
      for (;;) {
        await new Promise<void>((res, rej) => { const t = setTimeout(res, POLL_MS); c.signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); }, { once: true }); });
        let job: AskJob;
        try { job = await statusWithStallLimit(id, c.signal); misses = 0; setNote(null); }
        catch (e: any) {
          if (e?.name === 'AbortError' && c.signal.aborted) throw e;            // the user stopped waiting, or the page was left
          if (e instanceof ApiError && e.status === 404) { save(null); setFailure({ title: 'The backend no longer has this request', detail: 'It was probably restarted while the report was being written. Ask again.' }); return; }
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) { save(null); throw e; }
          misses += 1; setNote(`Connection hiccup (${misses}): no answer from the server, retrying on a new connection. The request itself is not lost.`);
          if (misses >= MAX_MISSES) { setFailure({ title: 'Lost contact with the backend', detail: 'The server did not answer for a minute. Your request is saved: refresh this page to keep waiting for it, or check that the backend is running.' }); return; }
          continue;
        }
        if (job.status === 'running') continue;
        save(null);
        if (job.status === 'done' && job.result) onResult(job.result);
        else setFailure(describeFailure({ status: job.http_status, message: job.detail }, Math.floor((Date.now() - since) / 1000), NO_LIMIT_S));
        return;
      }
    } catch (e: any) {
      if (e?.name === 'AbortError' && c.signal.reason === 'leave') return;        // the page was left: keep the saved job, say nothing
      if (e?.name === 'AbortError') { save(null); setFailure(describeFailure({ name: 'AbortError' }, Math.floor((Date.now() - since) / 1000), NO_LIMIT_S)); }
      else setFailure(describeFailure({ status: e instanceof ApiError ? e.status : undefined, message: e?.message, name: e?.name }, Math.floor((Date.now() - since) / 1000), NO_LIMIT_S));
    } finally { if (ctl.current === c) { ctl.current = null; setBusy(false); onBusy(false); } }    // a newer poll (resume after a remount) owns the busy state
  };

  useEffect(() => {                                   // resume a request that was running when the page was refreshed, reopened or left for another tab
    let saved: { id: string; q: string; started: number } | null = null;
    try { saved = JSON.parse(sessionStorage.getItem(JOB_KEY) ?? 'null'); } catch { saved = null; }
    if (saved?.id) { setQ(saved.q); void poll(saved.id, saved.started); }
    return () => ctl.current?.abort('leave');          // leaving the tab stops POLLING only; the saved job is picked up again on return
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async (text: string) => {
    if (text.trim().length < 3 || busy) return;
    onResult(null); setFailure(null);
    try {
      const { job_id } = await api.askStart(text.trim());
      const since = Date.now();
      save({ id: job_id, q: text.trim(), started: since });
      void poll(job_id, since);
    } catch (e: any) {
      setFailure(describeFailure({ status: e instanceof ApiError ? e.status : undefined, message: e?.message, name: e?.name }, 0, NO_LIMIT_S));
    }
  };
  const report = result?.report;
  const stage = [...STAGES].reverse().find(([from]) => elapsed >= from)![1];

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 backdrop-blur-xl p-5 flex flex-col gap-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2"><Sparkles className="w-4 h-4 text-blue-400" />Ask the Cognitive Memory Fabric</h3>
        <p className="text-xs text-slate-400 mt-0.5">Retrieves the right memories and conversation turns, builds the timeline and writes a report in which every statement cites its evidence. Name <b>one client</b> per question: source-turn chronology and narrative are single-client.</p>
      </div>
      <form onSubmit={(e) => { e.preventDefault(); run(q); }} className="flex gap-2 items-start">
        <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={3} disabled={busy} aria-label="Question"
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(q); } }}
          placeholder="Prepare a comprehensive report on Kenji's retirement planning history: how his plans evolved across meetings, key decisions and corrections, who said what, and what is unresolved."
          className="flex-1 rounded-xl bg-slate-950 border border-slate-700 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-blue-500 resize-y disabled:opacity-60" />
        <button disabled={busy || q.trim().length < 3} className="px-3 py-2 rounded-xl bg-blue-600 text-white disabled:opacity-40 cursor-pointer" aria-label="Ask" title="Ask (Ctrl+Enter)">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}</button>
      </form>
      <div className="flex flex-wrap gap-1.5">
        {suggestions.map((s) => <button key={s} disabled={busy} onClick={() => setQ(s)} className="text-[11px] px-2 py-1 rounded-lg border border-slate-700 text-slate-300 hover:border-blue-500/60 hover:text-white text-left cursor-pointer disabled:opacity-40" title="Fill the question box (then press Ask)">{s}</button>)}
      </div>

      {busy && (
        <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 text-xs text-slate-300" role="status" aria-live="polite" data-testid="progress">
          <div className="flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400" /><span className="font-semibold text-slate-100">Working… {mmss(elapsed)}</span>
            <button onClick={() => ctl.current?.abort()} className="ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded border border-slate-600 text-slate-300 hover:text-white hover:border-slate-400 cursor-pointer"><Square className="w-3 h-3" />Stop waiting</button></div>
          <div className="mt-2 h-1 rounded bg-slate-800 overflow-hidden"><div className="h-full bg-blue-500/70 transition-all" style={{ width: `${Math.min(97, (elapsed / 600) * 100)}%` }} /></div>
          {note && <p className="mt-2 text-amber-300" role="status">{note}</p>}
          <p className="mt-2 text-slate-200">Likely stage: {stage}</p>
          <p className="mt-1 text-slate-500">The backend does not report its stage while it works, so the stage above is an <b>estimate</b> from typical timings, not live status. With the local Qwen2.5:3B model a full report usually takes 3–6 minutes and can take longer; this page keeps waiting until the answer arrives or you press Stop waiting (the backend’s own request budget is one hour). The request runs as a background job: you can refresh or switch tabs and this page picks it up again. Stopping here does not cancel the backend.</p>
        </div>
      )}

      {failure && !busy && (
        <div className="flex gap-2 text-xs rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-200 p-3" role="alert" data-testid="failure"><AlertTriangle className="w-4 h-4 shrink-0" />
          <div><p className="font-medium">{failure.title}</p><p className="text-rose-300/80 mt-0.5 break-words leading-relaxed">{failure.detail}</p><p className="text-rose-300/60 mt-1">No report is shown for this attempt. The brain map still works.</p></div></div>
      )}

      {report && !busy && (
        <div className="flex flex-col gap-3 border-t border-slate-800 pt-4">
          {result!.released === false ? (
            <>
              <p className="text-sm text-slate-100 leading-relaxed">{report.summary}</p>
              {result!.execution && <WithheldStatus ex={result!.execution} approvalId={result!.approval?.approval_id} />}
              <p className="text-[11px] text-slate-500 font-mono">Memory details are withheld until the answer is approved · {report.request_id}</p>
            </>
          ) : (
            <>
              <p className="text-[11px] text-slate-500 font-mono">{result!.retrieved_memory_ids.length} memories retrieved · highlighted on the brain</p>
              <ReportView result={result!} onSelectMemory={onSelectMemory} />
            </>
          )}
        </div>
      )}
    </div>
  );
};

/** Shown only while an answer is withheld for approval: WHAT RAN (content-free), so a held answer is not a black box.  The answer itself stays withheld. */
const WithheldStatus = ({ ex, approvalId }: { ex: ExecutionStatus; approvalId?: string }) => (
  <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
    <p className="font-semibold text-amber-300">Held for approval{ex.harness.approval.category ? ` (${ex.harness.approval.category.replace(/_/g, ' ')})` : ''}{approvalId ? ` · ${approvalId}` : ''}</p>
    <ul className="mt-2 space-y-0.5 text-slate-300">
      {ex.agents.map((a) => (
        <li key={a.agent} className="flex gap-2 font-mono text-[11px]">
          <span className={a.ran ? 'text-emerald-400' : 'text-rose-400'}>{a.ran ? 'ran' : 'did not run'}</span>
          <span className="text-slate-200">{a.agent}</span>
          <span className="text-slate-500">{a.duration_ms != null ? `${(a.duration_ms / 1000).toFixed(1)}s` : ''}{a.llm_calls ? ` · ${a.llm_calls} model call${a.llm_calls > 1 ? 's' : ''}` : ''}{a.status ? ` · ${a.status}` : ''}{a.stop_reason ? ` (${a.stop_reason.replace(/_/g, ' ')})` : ''}</span>
        </li>
      ))}
    </ul>
    <p className="mt-2 font-mono text-[11px] text-slate-400">Harness: {ex.harness.active ? 'active' : 'off'}{ex.harness.profile ? ` · ${ex.harness.profile}` : ''}{ex.harness.outcome ? ` · ${ex.harness.outcome}` : ''} · permission checks {ex.harness.permissions.allowed} allowed / {ex.harness.permissions.denied} denied{ex.harness.degraded ? ' · degraded' : ''}</p>
    {!ex.client_identified && <p className="mt-1 text-amber-300/90">No client was identified in the question, so no client memory was searched. Name the client (for example "Kenji").</p>}
    <p className="mt-1 text-slate-500">{ex.note}</p>
  </div>
);
