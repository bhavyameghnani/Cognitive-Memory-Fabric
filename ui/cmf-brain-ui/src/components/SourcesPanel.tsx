import React, { useMemo } from 'react';
import { Database, FileText, Mail, RefreshCw, ScrollText, Trash2, Users, Share2 } from 'lucide-react';
import type { BrainData } from '../types';
import { TYPES, TYPE_ORDER } from '../lib/memory';

// Light "Document & Enterprise Memory" card from the reference image. Only conversation transcripts exist as a real source today.
export const SourcesPanel: React.FC<{ data: BrainData }> = ({ data }) => {
  const stats = useMemo(() => {
    const rel = (k: string) => data.relationships.filter((r) => r.rel_type === k).length;
    const real = data.memories; // includes the curated GLOBAL procedures, which every client inherits
    return {
      meetings: data.interactions.length,
      ingested: data.interactions.filter((i) => i.ingestion === 'completed').length,
      superseded: data.counts.by_status.superseded ?? 0,
      disputed: data.counts.by_status.disputed ?? 0,
      expired: data.counts.by_status.expired ?? 0,
      unconfirmed: data.counts.by_status.unconfirmed ?? 0,
      supersedes: rel('supersedes'), corrects: rel('corrects'), supports: rel('supports'), derived: rel('derived_from'),
      byType: (t: string) => real.filter((m) => m.memory_type === t).length,
    };
  }, [data]);

  const sources = [
    { icon: Users, name: 'Meetings', sub: `${stats.meetings} transcripts · ${stats.ingested} ingested`, live: true },
    { icon: FileText, name: 'Documents', sub: 'reports, plans', live: false },
    { icon: Mail, name: 'Emails', sub: 'client communications', live: false },
    { icon: Database, name: 'CRM', sub: 'client records', live: false },
    { icon: ScrollText, name: 'Policies', sub: 'guidelines, research', live: false },
  ];

  return (
    <div className="rounded-2xl bg-slate-100 text-slate-800 p-4 flex flex-col gap-3 shadow-xl">
      <div className="text-center">
        <h3 className="text-sm font-bold text-slate-900">Document &amp; Enterprise Memory</h3>
        <p className="text-[11px] text-slate-500">From unstructured conversations to structured memories</p>
      </div>
      <div className="grid grid-cols-5 gap-1 rounded-xl bg-white p-2 border border-slate-200">
        {sources.map((s) => (
          <div key={s.name} className={`text-center px-0.5 ${s.live ? '' : 'opacity-40'}`}>
            <s.icon className="w-5 h-5 mx-auto text-slate-700" />
            <p className="text-[10px] font-semibold mt-1">{s.name}</p>
            <p className="text-[9px] text-slate-500 leading-tight">{s.live ? s.sub : 'not connected'}</p>
          </div>
        ))}
      </div>
      <p className="text-[11px] text-center text-slate-500">↓ Extract, understand and organize</p>

      <div className="flex flex-col gap-1.5">
        {TYPE_ORDER.map((t) => {
          const m = TYPES[t];
          return (
            <div key={t} className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 border" style={{ background: `${m.color}12`, borderColor: `${m.color}55` }}>
              <m.icon className="w-5 h-5 shrink-0" style={{ color: m.color }} />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-slate-900">{m.name} <span className="font-normal" style={{ color: m.color }}>({t === 'episodic' ? 'Events' : t === 'semantic' ? 'Facts' : t === 'procedural' ? 'Processes' : 'Current context'})</span></p>
                <p className="text-[10px] text-slate-500 truncate">{m.example}</p>
              </div>
              <span className="font-mono text-sm font-semibold tabular-nums" style={{ color: m.color }}>{stats.byType(t)}</span>
            </div>
          );
        })}
      </div>

      <div className="rounded-xl bg-white border border-slate-200 p-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold"><Share2 className="w-4 h-4 text-slate-600" />Temporal Association Graph</p>
        <p className="text-[10px] text-slate-500">Connects memories across time and relationships</p>
        <div className="grid grid-cols-4 gap-1.5 mt-2 text-center">
          {[['supersedes', stats.supersedes, '#ef4444'], ['corrects', stats.corrects, '#f97316'], ['supports', stats.supports, '#22c55e'], ['derived from', stats.derived, '#3b82f6']].map(([k, n, c]) => (
            <div key={k as string} className="rounded-lg bg-slate-50 border border-slate-200 py-1.5">
              <p className="font-mono text-sm font-semibold" style={{ color: c as string }}>{n}</p><p className="text-[9px] text-slate-500">{k}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-xl bg-white border border-slate-200 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold"><RefreshCw className="w-4 h-4 text-slate-600" />Consolidation &amp; Update</p>
          <p className="text-[10px] text-slate-500 mt-0.5">Episodes merged into coherent facts</p>
          <p className="mt-1.5 text-[11px] text-slate-700"><b className="font-mono">{stats.byType('semantic')}</b> semantic versions from <b className="font-mono">{stats.byType('episodic')}</b> episodes</p>
        </div>
        <div className="rounded-xl bg-white border border-slate-200 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold"><Trash2 className="w-4 h-4 text-slate-600" />Forgetting &amp; Correction</p>
          <p className="text-[10px] text-slate-500 mt-0.5">Suppressed, audit history kept</p>
          <p className="mt-1.5 text-[11px] text-slate-700"><b className="font-mono">{stats.superseded}</b> superseded · <b className="font-mono">{stats.disputed}</b> disputed · <b className="font-mono">{stats.expired}</b> expired · <b className="font-mono">{stats.unconfirmed}</b> unconfirmed</p>
        </div>
      </div>
    </div>
  );
};
