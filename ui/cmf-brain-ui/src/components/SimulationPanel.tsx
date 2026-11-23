import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, Loader2, Mail, MessageSquare, Phone, PlayCircle, Users, Video } from 'lucide-react';
import { api, ApiError } from '../api';
import type { MemoryItem, SimIngestResult, SimItem } from '../types';

/** Demo simulation: the client's NEXT pre-generated interaction (data/simulation, never seen by the system until now) is ingested with the production
 *  pipeline. The brain reloads and the memories it formed are highlighted. Nothing here writes a memory directly: the server runs extraction, the blind
 *  verifier, the evidence policy, the write validator, the integrity boundary and consolidation, exactly as for every other interaction. */

const CHANNEL_ICON: Record<string, React.FC<{ className?: string }>> = { email: Mail, chat: MessageSquare, telephone: Phone, video: Video, 'in-person': Users };
const CERTAINTY_TONE: Record<string, string> = {
  confirmed: 'text-emerald-300 border-emerald-500/40', correction: 'text-sky-300 border-sky-500/40', exploratory: 'text-amber-300 border-amber-500/40',
  hypothetical: 'text-amber-300 border-amber-500/40', unconfirmed: 'text-slate-300 border-slate-600', recommendation: 'text-violet-300 border-violet-500/40',
};

interface Props {
  clientId: string;
  name: string;
  onIngested: (newMemoryIds: string[]) => void;
  onSelectMemory: (id: string) => void;
}

export const SimulationPanel: React.FC<Props> = ({ clientId, name, onIngested, onSelectMemory }) => {
  const [items, setItems] = useState<SimItem[] | null>(null);
  const [off, setOff] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SimIngestResult | null>(null);

  const refresh = useCallback(() => {
    api.simulation(clientId).then((q) => { setItems(q.items); setOff(false); })
      .catch((e) => { if (e instanceof ApiError && e.status === 404) setOff(true); else setError(String(e.message ?? e)); });
  }, [clientId]);
  useEffect(() => { setItems(null); setResult(null); setError(null); refresh(); }, [refresh]);

  const next = items?.find((i) => !i.ingested) ?? null;
  const done = items?.filter((i) => i.ingested) ?? [];

  const ingest = async () => {
    if (!next) return;
    setBusy(true); setError(null); setResult(null);
    try {
      const r = await api.simulateIngest(next.interaction_id);
      setResult(r);
      onIngested(r.new_memories.map((m) => m.memory_id));
      refresh();
    } catch (e) {
      setError(String((e as Error).message ?? e));
      refresh();
    } finally {
      setBusy(false);
    }
  };

  if (off) {
    return <Card><p className="text-xs text-slate-400">Simulation is not available: it runs only outside the production profile, and needs <code className="font-mono">data/simulation/sim_conversations.csv</code> (see <code className="font-mono">scripts/generate_synthetic_conversations.py</code>).</p></Card>;
  }
  if (!items) return <Card><p className="text-xs text-slate-500">Loading simulated interactions…</p></Card>;

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-sm font-semibold text-slate-100">Incoming interactions for {name}</h4>
          <span className="text-[11px] text-slate-500 font-mono">{done.length} ingested · {items.length - done.length} queued</span>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">Pre-generated synthetic interactions dated after the existing record. Each one is unknown to the memory until it is ingested, and they are ingested in date order.</p>
      </Card>

      {next ? (
        <Card>
          <div className="flex items-center gap-2 text-xs">
            <ChannelIcon channel={next.channel} />
            <span className="font-semibold text-slate-100">{next.meeting_context}</span>
          </div>
          <p className="mt-0.5 text-[11px] text-slate-500 font-mono">{next.interaction_id} · {next.interaction_datetime.slice(0, 10)} · {next.channel} · {next.turns} {next.channel === 'email' || next.channel === 'chat' ? 'messages' : 'turns'}</p>
          <ol className="mt-2 max-h-72 overflow-y-auto pr-1 space-y-1.5">
            {next.transcript.map((t) => (
              <li key={t.turn_index} className="text-[12px] leading-snug">
                <span className={`font-mono text-[10px] mr-1.5 ${t.speaker === 'Client' ? 'text-sky-400' : 'text-slate-500'}`}>{t.turn_index}·{t.speaker}</span>
                <span className="text-slate-300">{t.utterance}</span>
              </li>
            ))}
          </ol>
          <button onClick={ingest} disabled={busy}
            className="mt-3 w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-400 text-white text-sm font-medium py-2 cursor-pointer disabled:cursor-wait">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlayCircle className="w-4 h-4" />}
            {busy ? 'Ingesting: extraction, blind verification, evidence checks…' : 'Ingest into memory'}
          </button>
          {busy && <p className="mt-1.5 text-[11px] text-slate-500">Two model calls and the code checks usually take 10–30 seconds.</p>}
        </Card>
      ) : (
        <Card><p className="text-xs text-slate-400">All simulated interactions for {name} have been ingested. Re-copy the demo database to start again.</p></Card>
      )}

      {error && <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-200 text-xs p-3">Ingestion failed: {error}. The interaction stays queued and can be retried.</div>}

      {result && <IngestResult r={result} onSelectMemory={onSelectMemory} />}

      {done.length > 0 && (
        <Card>
          <h5 className="text-xs font-semibold text-slate-300 mb-1">Already ingested</h5>
          <ul className="space-y-0.5">
            {done.map((i) => (
              <li key={i.interaction_id} className="flex items-center gap-2 text-[11px] text-slate-400">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                <span className="font-mono">{i.interaction_id}</span><span>{i.interaction_datetime.slice(0, 10)}</span><span className="truncate">{i.meeting_context}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
};

const IngestResult: React.FC<{ r: SimIngestResult; onSelectMemory: (id: string) => void }> = ({ r, onSelectMemory }) => {
  const c = r.consolidation;
  const byType = (t: string) => r.new_memories.filter((m) => m.memory_type === t);
  return (
    <Card>
      <h5 className="text-xs font-semibold text-emerald-300">{r.interaction_id} ingested</h5>
      <p className="mt-0.5 text-[11px] text-slate-400">
        {r.candidates} candidate{r.candidates === 1 ? '' : 's'} proposed · {r.stored} stored as episodic memory
        {c ? ` · ${c.versions_created} new current value${c.versions_created === 1 ? '' : 's'} · ${c.relationships} relationship${c.relationships === 1 ? '' : 's'}` : ''}
        {r.llm_error ? ` · model error: ${r.llm_error}` : ''}
      </p>
      {r.new_memories.length === 0 && <p className="mt-2 text-[11px] text-slate-500">Nothing new was stored: every candidate was rejected by the checks or matched a memory that already exists. The candidate audit lists why.</p>}
      {(['semantic', 'episodic', 'procedural'] as const).map((t) => byType(t).length > 0 && (
        <div key={t} className="mt-2">
          <p className="text-[10px] uppercase tracking-wide text-slate-500">{t === 'semantic' ? 'Current values (consolidation)' : t === 'episodic' ? 'What happened (episodic)' : 'Procedure proposals'}</p>
          <ul className="mt-1 space-y-1">{byType(t).map((m) => <NewMemory key={m.memory_id} m={m} onSelect={onSelectMemory} />)}</ul>
        </div>
      ))}
      <p className="mt-2 text-[11px] text-slate-500">New memories are highlighted on the brain; click one to open its dossier and evidence.</p>
    </Card>
  );
};

const NewMemory: React.FC<{ m: MemoryItem; onSelect: (id: string) => void }> = ({ m, onSelect }) => (
  <li>
    <button onClick={() => onSelect(m.memory_id)} className="w-full text-left rounded-lg border border-slate-800 hover:border-slate-600 bg-slate-950/40 p-2 cursor-pointer">
      <div className="flex items-center gap-2 text-[11px]">
        <span className={`px-1.5 py-0.5 rounded border font-mono ${CERTAINTY_TONE[m.certainty] ?? 'text-slate-300 border-slate-600'}`}>{m.certainty}</span>
        <span className="text-slate-400">{m.topic.replace(/_/g, ' ')}</span>
        {m.value_text && <span className="font-mono text-slate-100">= {m.value_text}</span>}
        <span className="ml-auto font-mono text-slate-500">{Math.round(m.confidence * 100)}%</span>
      </div>
      <p className="mt-1 text-[12px] text-slate-300 leading-snug">{m.content}</p>
      {m.evidence[0] && <p className="mt-1 text-[11px] text-slate-500 italic">“{m.evidence[0].excerpt}” · {m.evidence[0].speaker}, turn {m.evidence[0].turn_index}</p>}
    </button>
  </li>
);

const ChannelIcon: React.FC<{ channel: string }> = ({ channel }) => {
  const Icon = CHANNEL_ICON[channel] ?? MessageSquare;
  return <Icon className="w-4 h-4 text-blue-400 shrink-0" />;
};

const Card: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">{children}</div>
);
