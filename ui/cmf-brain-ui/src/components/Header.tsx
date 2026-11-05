import React from 'react';
import { Brain } from 'lucide-react';
import type { BrainData, ClientSummary } from '../types';

interface Props { clients: ClientSummary[]; clientId: string; onClient: (id: string) => void; data: BrainData | null; online: boolean }

export const Header: React.FC<Props> = ({ clients, clientId, onClient, data, online }) => {
  const s = data?.counts.by_status ?? {};
  return (
    <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-xl sticky top-0 z-30">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3.5 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="relative flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 via-violet-500 to-amber-500 text-white shadow-md shadow-blue-500/20">
            <Brain className="w-5 h-5" />
            <span className={`absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-slate-950 ${online ? 'bg-emerald-400' : 'bg-rose-500'}`} />
          </div>
          <div>
            <h1 className="text-base font-bold text-slate-100 tracking-tight">Cognitive Memory Fabric <span className="text-xs text-blue-400 font-mono font-normal">/ Wealth Advisor</span></h1>
            <p className="text-xs text-slate-400">{online ? 'Memory store connected' : 'API offline — start api/main.py'} · synthetic data</p>
          </div>
        </div>
        <div className="hidden lg:flex items-center gap-5 text-xs text-slate-400 font-mono tabular-nums">
          <span><b className="text-emerald-400">{s.active ?? 0}</b> active</span>
          <span><b className="text-amber-400">{s.unconfirmed ?? 0}</b> unconfirmed</span>
          <span><b className="text-slate-300">{s.superseded ?? 0}</b> superseded</span>
          <span><b className="text-rose-400">{s.disputed ?? 0}</b> disputed</span>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Client
          <select value={clientId} onChange={(e) => onClient(e.target.value)} className="rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-slate-100 focus:outline-none focus:border-blue-500 cursor-pointer">
            {clients.map((c) => <option key={c.client_id} value={c.client_id}>{c.first_name ?? c.client_id} · {c.client_id} ({c.memories} memories)</option>)}
          </select>
        </label>
      </div>
    </header>
  );
};
