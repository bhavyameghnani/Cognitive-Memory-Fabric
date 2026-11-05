import React, { useEffect, useState } from 'react';
import { Zap, Quote, History, ShieldAlert, Loader2 } from 'lucide-react';
import { api } from '../api';
import type { MemoryDetail, MemoryItem } from '../types';
import { STATUS_TONE, TYPES, fmtDate, nodeTitle, topicLabel, workingInfo } from '../lib/memory';

interface Props {
  memory: MemoryItem | null;
  advisorId: string;
  onSelect: (id: string) => void;
  onChanged: () => void;
}

export const MemoryDossier: React.FC<Props> = ({ memory, advisorId, onSelect, onChanged }) => {
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<'disputed' | 'expired' | null>(null);

  useEffect(() => {
    setDetail(null); setError(null); setConfirm(null); setReason('');
    if (!memory) return;
    let live = true;
    api.memory(memory.memory_id).then((d) => live && setDetail(d)).catch((e) => live && setError(String(e.message ?? e)));
    return () => { live = false; };
  }, [memory?.memory_id, memory?.status]);

  if (!memory) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-8 flex flex-col items-center justify-center text-center min-h-[300px]">
        <Zap className="w-8 h-8 text-slate-600 mb-2 stroke-[1.5]" />
        <p className="text-sm font-medium text-slate-300">Select a memory node</p>
        <p className="text-xs text-slate-500 max-w-xs mt-1">Click any node on the brain to see its evidence quotes, version history and audit trail.</p>
      </div>
    );
  }

  const t = TYPES[memory.memory_type];
  const wk = memory.memory_type === 'working' ? workingInfo(memory) : null;
  const lifecycleOk = memory.memory_type !== 'working' && ['active', 'unconfirmed', 'disputed'].includes(memory.status);

  const submit = async () => {
    if (!confirm) return;
    setBusy(true); setError(null);
    try {
      await api.lifecycle(memory.memory_id, { new_status: confirm, reason, actor: advisorId });
      setConfirm(null); setReason(''); onChanged();
    } catch (e: any) { setError(String(e.message ?? e)); } finally { setBusy(false); }
  };

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 backdrop-blur-xl p-5 flex flex-col gap-4">
      <div className="pb-3 border-b border-slate-800">
        <div className="flex flex-wrap items-center gap-x-2 text-xs font-mono text-slate-400">
          <span className="font-semibold" style={{ color: t.color }}>{t.name}</span><span>·</span>
          <span>{wk ? (wk.kind === 'tool' ? 'tool result' : 'task context') : topicLabel(memory.topic)}</span><span>·</span>
          <span className={`capitalize ${STATUS_TONE[memory.status]}`}>{memory.status}</span>
        </div>
        <h3 className="text-base font-semibold text-slate-100 mt-1 leading-snug">
          {memory.memory_type === 'episodic' ? memory.content : nodeTitle(memory)}
        </h3>
        {wk?.kind === 'tool' && <p className="text-[11px] text-slate-400 mt-1 font-mono break-words max-h-40 overflow-auto">{wk.detail}</p>}
        {memory.memory_type === 'semantic' && <p className="text-xs text-slate-400 mt-1">{memory.content}</p>}
      </div>

      {wk ? (
        <>
          <dl className="grid grid-cols-3 gap-3 text-xs">
            {wk.fields.map(([k, v]) => <Field key={k} k={k} v={v} />)}
            <Field k="Created by" v={memory.origin.replace(/_/g, ' ')} />
            <Field k="Session" v={memory.session_id ?? '—'} />
            <Field k="Expires" v={memory.expires_at ? memory.expires_at.slice(0, 16).replace('T', ' ') : '—'} />
          </dl>
          <p className="text-[11px] text-slate-500 leading-snug">Working memory is scratch context for one advisor request. It is never treated as something the client said and is never promoted to semantic memory; it expires automatically.</p>
        </>
      ) : (
      <dl className="grid grid-cols-3 gap-3 text-xs">
        <Field k="Certainty" v={memory.certainty} />
        <Field k="Confidence" v={`${Math.round(memory.confidence * 100)}%`} />
        <Field k="Origin" v={memory.origin.replace(/_/g, ' ')} />
        <Field k={memory.memory_type === 'episodic' ? 'Event date' : 'Valid from'} v={fmtDate(memory.event_date ?? memory.valid_from)} />
        <Field k="Valid to" v={memory.valid_to ? fmtDate(memory.valid_to) : memory.status === 'active' ? 'current' : '—'} />
        <Field k="Supporting episodes" v={memory.memory_type === 'semantic' ? String(memory.supporting_episodes) : '—'} />
      </dl>
      )}

      {memory.steps.length > 0 && (
        <Section icon={<Zap className="w-3.5 h-3.5 text-violet-400" />} title={`Procedure · ${memory.approval_status ?? 'n/a'}`}>
          <ol className="list-decimal list-inside space-y-1 text-xs text-slate-300">{memory.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </Section>
      )}

      {!wk && <Section icon={<Quote className="w-3.5 h-3.5 text-amber-400" />} title={`Evidence (${memory.evidence.length})`}>
        {memory.evidence.length === 0 && <p className="text-xs text-slate-500">No conversation evidence (curated or system-generated).</p>}
        <div className="space-y-2">
          {memory.evidence.map((e, i) => (
            <blockquote key={i} className="border-l-2 border-amber-500/60 pl-3 text-xs">
              <p className="text-slate-200 italic">“{e.excerpt}”</p>
              <p className="text-[10px] text-slate-500 font-mono mt-0.5">{e.interaction_id} · turn {e.turn_index} · {e.speaker}</p>
            </blockquote>
          ))}
        </div>
      </Section>}

      {detail && detail.versions.length > 1 && (
        <Section icon={<History className="w-3.5 h-3.5 text-emerald-400" />} title="Version history">
          <ol className="relative border-l border-slate-700 ml-1.5 space-y-2.5">
            {detail.versions.map((v) => (
              <li key={v.memory_id} className="ml-3">
                <span className={`absolute -left-[5px] w-2 h-2 rounded-full ${v.status === 'active' ? 'bg-emerald-400' : 'bg-slate-600'}`} />
                <button onClick={() => onSelect(v.memory_id)} className={`text-left text-xs cursor-pointer hover:text-white ${v.memory_id === memory.memory_id ? 'text-white font-semibold' : 'text-slate-300'}`}>
                  v{v.version_no}: {v.value_text} <span className="text-slate-500 font-mono">· {fmtDate(v.valid_from)} → {v.valid_to ? fmtDate(v.valid_to) : 'now'} · {v.status}</span>
                </button>
              </li>
            ))}
          </ol>
        </Section>
      )}

      <Section icon={<ShieldAlert className="w-3.5 h-3.5 text-slate-400" />} title="Lifecycle audit trail">
        {!detail && !error && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
        <ul className="space-y-1.5">
          {detail?.lifecycle.map((e) => (
            <li key={e.event_id} className="text-[11px] text-slate-400 leading-snug">
              <span className="font-mono text-slate-500">{e.created_at.slice(0, 16).replace('T', ' ')}</span>{' '}
              <span className="text-slate-300">{e.from_status ?? '∅'} → {e.to_status}</span> · {e.reason} <span className="text-slate-500">({e.actor})</span>
            </li>
          ))}
        </ul>
      </Section>

      {lifecycleOk && (
        <div className="pt-3 border-t border-slate-800 text-xs">
          {!confirm ? (
            <div className="flex items-center gap-2 text-slate-400">
              <span>Correct the record:</span>
              {memory.status !== 'disputed' && <button onClick={() => setConfirm('disputed')} className="px-2 py-1 rounded-lg border border-rose-500/40 text-rose-300 hover:bg-rose-500/10 cursor-pointer">Dispute</button>}
              <button onClick={() => setConfirm('expired')} className="px-2 py-1 rounded-lg border border-slate-600 text-slate-300 hover:bg-slate-800 cursor-pointer">Forget (expire)</button>
              <span className="text-slate-600 ml-auto">history is kept</span>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-slate-300">Mark this memory <b>{confirm}</b> as {advisorId}. It is suppressed from retrieval but the audit history stays.</p>
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (required)" className="w-full rounded-lg bg-slate-950 border border-slate-700 px-2.5 py-1.5 text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-blue-500" />
              <div className="flex gap-2">
                <button disabled={busy || reason.trim().length < 3} onClick={submit} className="px-3 py-1.5 rounded-lg bg-rose-600 text-white disabled:opacity-40 cursor-pointer">{busy ? 'Saving…' : 'Confirm'}</button>
                <button onClick={() => setConfirm(null)} className="px-3 py-1.5 rounded-lg text-slate-400 hover:text-white cursor-pointer">Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}
      {error && <p className="text-xs text-rose-400">{error}</p>}
    </div>
  );
};

const Field = ({ k, v }: { k: string; v: string }) => (
  <div><dt className="text-[10px] uppercase tracking-wide text-slate-500">{k}</dt><dd className="text-slate-200 capitalize mt-0.5">{v}</dd></div>
);

const Section = ({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) => (
  <section>
    <h4 className="flex items-center gap-1.5 text-xs font-semibold text-slate-300 mb-2">{icon}{title}</h4>
    {children}
  </section>
);
