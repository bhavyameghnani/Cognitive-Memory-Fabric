import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Brain, FolderTree, Sparkles, Shield } from 'lucide-react';
import { api, ApiError } from './api';
import type { AskResult, BrainData, ClientSummary, MemoryType } from './types';
import { Header } from './components/Header';
import { BrainVisualizer } from './components/BrainVisualizer';
import { MemoryDossier } from './components/MemoryDossier';
import { AdvisorQuery } from './components/AdvisorQuery';
import { SourcesPanel } from './components/SourcesPanel';
import { FlowStrip } from './components/FlowStrip';

type Tab = 'dossier' | 'ask' | 'sources';

export default function App() {
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [clientId, setClientId] = useState('CLIENT-001');
  const [data, setData] = useState<BrainData | null>(null);
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<MemoryType | 'all'>('all');
  const [showHistory, setShowHistory] = useState(true);
  const [interactionId, setInteractionId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('dossier');
  const [result, setResult] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const session = 1;                              // single session: the UI connects automatically on load, there is no token entry
  const [ready, setReady] = useState(0);              // set once the client list has been received; brain data is requested only after that
  const sessionRef = useRef(session);
  const brainReq = useRef(0);                        // generation of the latest brain request; a response is applied only if it is still the latest
  const [authError, setAuthError] = useState(false);

  const fail = useCallback((e: unknown) => {
    setOnline(false); setError(String((e as Error).message ?? e));
    setAuthError(e instanceof ApiError && (e.status === 401 || e.status === 403));
  }, []);

  useEffect(() => {
    if (!session) return;
    let live = true;
    api.clients().then((c) => {
      if (!live) return;
      setClients(c); setOnline(true);
      if (!c.some((x) => x.client_id === clientId) && c[0]) setClientId(c[0].client_id);   // keep the selection only if this token may see it
      setReady(session);
    }).catch((e) => { if (live) fail(e); });
    return () => { live = false; };
  }, [session, fail]);

  const load = useCallback((id: string) => {
    const req = ++brainReq.current;
    const stale = () => req !== brainReq.current;
    return api.brain(id).then((d) => { if (stale()) return; setData(d); setOnline(true); setError(null); }).catch((e) => { if (!stale()) fail(e); });
  }, [fail]);
  useEffect(() => {
    if (!session || ready !== session || clients.length === 0) return;   // no brain request before the authorised client list is known (or when it is empty)
    setData(null); setSelectedId(null); setResult(null); setInteractionId(null); setTypeFilter('all');
    load(clientId);
  }, [clientId, load, session, ready, clients]);

  // Ask CMF callbacks are bound to the session they were created in: a late answer, error-driven busy reset or report from an earlier token is dropped.
  const askSession = session;
  const onAskResult = useCallback((r: AskResult | null) => { if (askSession === sessionRef.current) setResult(r); }, [askSession]);
  const onAskBusy = useCallback((b: boolean) => { if (askSession === sessionRef.current) setAsking(b); }, [askSession]);

  const client = clients.find((c) => c.client_id === clientId);
  const selected = data?.memories.find((m) => m.memory_id === selectedId) ?? null;
  const highlighted = useMemo(() => (result && result.client_ids.includes(clientId) ? new Set(result.retrieved_memory_ids) : null), [result, clientId]);
  const select = (id: string) => { setSelectedId(id); setTab('dossier'); };
  const name = client?.first_name ?? clientId;
  const suggestions = [
    `Prepare a comprehensive report on ${name}'s planning history. Explain how plans and preferences evolved across meetings, identify important decisions and corrections, distinguish ${name}'s own statements from advisor interpretations, and highlight unresolved questions.`,
    `Give me the chronology of ${name}'s most important decisions, with the source conversation turns and any corrections.`,
    `How should I prepare for ${name}'s next portfolio review?`,
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      <Header clients={clients} clientId={clientId} onClient={setClientId} data={data} online={online} />
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-5 flex flex-col gap-5">
        {authError && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-200 text-xs p-3">
            Authentication failed: {error}. The API rejected unauthenticated access; run it with HARNESS_PROFILE=development on loopback.
          </div>
        )}
        {error && !authError && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-200 text-xs p-3">
            Could not load the memory store: {error}. Start the API: <code className="font-mono">PYTHONPATH=src .venv/bin/uvicorn api.main:app --port 8000</code>
          </div>
        )}
        {data && data.memories.filter((m) => m.client_id !== 'GLOBAL').length === 0 && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-100 text-xs p-3">
            No memories for {name} yet. Ingest this client's meetings: <code className="font-mono">python -m orchestration.cli ingest --client {clientId}</code> (needs the LLM), or point
            <code className="font-mono"> CMF_MEMORY_DATABASE_URL</code> at a populated database.
          </div>
        )}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
          <div className={`${tab === 'ask' ? 'lg:col-span-5' : 'lg:col-span-7'} min-w-0`}>
            {data ? (
              <BrainVisualizer memories={data.memories} relationships={data.relationships} interactions={data.interactions} selectedId={selectedId} onSelect={select}
                typeFilter={typeFilter} onFilter={setTypeFilter} highlighted={highlighted} onClearHighlight={() => setResult(null)}
                showHistory={showHistory} onToggleHistory={() => setShowHistory((v) => !v)} interactionId={interactionId} onSelectInteraction={setInteractionId} />
            ) : (
              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 h-[560px] flex items-center justify-center text-sm text-slate-500">{session && ready === session && clients.length === 0 ? 'No clients are available.' : 'Loading memory brain…'}</div>
            )}
          </div>
          <div className={`${tab === 'ask' ? 'lg:col-span-7' : 'lg:col-span-5'} flex flex-col gap-3 min-w-0`}>
            <div className="flex items-center gap-1 p-1 bg-slate-900/80 border border-slate-800 rounded-xl text-xs">
              {([['dossier', Brain, 'Memory Dossier'], ['ask', Sparkles, 'Ask CMF'], ['sources', FolderTree, 'Sources & Lifecycle']] as const).map(([k, Icon, label]) => (
                <button key={k} onClick={() => setTab(k)} className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg font-medium transition-all cursor-pointer ${tab === k ? 'bg-slate-800 text-slate-100 border border-slate-700/60' : 'text-slate-400 hover:text-slate-200'}`}>
                  <Icon className="w-3.5 h-3.5 text-blue-400" /><span>{label}</span>
                </button>
              ))}
            </div>
            {tab === 'dossier' && <MemoryDossier memory={selected} advisorId={client?.advisor_id ?? 'advisor'} onSelect={select} onChanged={() => load(clientId)} />}
            {tab === 'ask' && <AdvisorQuery key={session} suggestions={suggestions} result={result} onResult={onAskResult} onBusy={onAskBusy} onSelectMemory={select} />}
            {tab === 'sources' && data && <SourcesPanel data={data} />}
          </div>
        </div>
        <FlowStrip busy={asking} result={result} />
      </main>
      <footer className="border-t border-slate-900 py-4 text-xs text-slate-500">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 flex flex-wrap items-center gap-2"><Shield className="w-4 h-4 text-emerald-500" />Synthetic data only · memories are never deleted, only superseded or expired with an audit trail</div>
      </footer>
    </div>
  );
}
