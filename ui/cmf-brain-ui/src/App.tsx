import { useCallback, useEffect, useMemo, useState } from 'react';
import { Brain, FolderTree, Sparkles, Shield } from 'lucide-react';
import { api } from './api';
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

  useEffect(() => {
    api.clients().then((c) => { setClients(c); setOnline(true); if (!c.some((x) => x.client_id === 'CLIENT-001') && c[0]) setClientId(c[0].client_id); })
      .catch((e) => { setOnline(false); setError(String(e.message ?? e)); });
  }, []);

  const load = useCallback((id: string) => api.brain(id).then((d) => { setData(d); setOnline(true); setError(null); }).catch((e) => { setOnline(false); setError(String(e.message ?? e)); }), []);
  useEffect(() => {
    setData(null); setSelectedId(null); setResult(null); setInteractionId(null); setTypeFilter('all');
    load(clientId);
  }, [clientId, load]);

  const client = clients.find((c) => c.client_id === clientId);
  const selected = data?.memories.find((m) => m.memory_id === selectedId) ?? null;
  const highlighted = useMemo(() => (result && result.client_ids.includes(clientId) ? new Set(result.retrieved_memory_ids) : null), [result, clientId]);
  const select = (id: string) => { setSelectedId(id); setTab('dossier'); };
  const name = client?.first_name ?? clientId;
  const suggestions = [`How should I prepare for ${name}'s next portfolio review?`, `What is ${name}'s current retirement age and how has it changed?`, `Summarise ${name}'s asset allocation history`];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      <Header clients={clients} clientId={clientId} onClient={setClientId} data={data} online={online} />
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-5 flex flex-col gap-5">
        {error && (
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
          <div className="lg:col-span-7 min-w-0">
            {data ? (
              <BrainVisualizer memories={data.memories} relationships={data.relationships} interactions={data.interactions} selectedId={selectedId} onSelect={select}
                typeFilter={typeFilter} onFilter={setTypeFilter} highlighted={highlighted} onClearHighlight={() => setResult(null)}
                showHistory={showHistory} onToggleHistory={() => setShowHistory((v) => !v)} interactionId={interactionId} onSelectInteraction={setInteractionId} />
            ) : (
              <div className="rounded-2xl border border-slate-800 bg-slate-900/60 h-[560px] flex items-center justify-center text-sm text-slate-500">Loading memory brain…</div>
            )}
          </div>
          <div className="lg:col-span-5 flex flex-col gap-3 min-w-0">
            <div className="flex items-center gap-1 p-1 bg-slate-900/80 border border-slate-800 rounded-xl text-xs">
              {([['dossier', Brain, 'Memory Dossier'], ['ask', Sparkles, 'Ask CMF'], ['sources', FolderTree, 'Sources & Lifecycle']] as const).map(([k, Icon, label]) => (
                <button key={k} onClick={() => setTab(k)} className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg font-medium transition-all cursor-pointer ${tab === k ? 'bg-slate-800 text-slate-100 border border-slate-700/60' : 'text-slate-400 hover:text-slate-200'}`}>
                  <Icon className="w-3.5 h-3.5 text-blue-400" /><span>{label}</span>
                </button>
              ))}
            </div>
            {tab === 'dossier' && <MemoryDossier memory={selected} advisorId={client?.advisor_id ?? 'advisor'} onSelect={select} onChanged={() => load(clientId)} />}
            {tab === 'ask' && <AdvisorQuery suggestions={suggestions} result={result} onResult={setResult} onBusy={setAsking} onSelectMemory={select} />}
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
