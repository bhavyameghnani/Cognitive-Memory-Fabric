import React from 'react';
import { Activity, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import type { AgentRun } from '../types';
import { TYPES } from '../lib/memory';

const STAGES = [['planner', 'Planner'], ['memory', 'Memory'], ['tool_selector', 'Tools'], ['report', 'Report']] as const;
const GROUP_LABEL: Record<string, string> = { current: 'current facts', history: 'superseded', episodes: 'episodes', procedures: 'procedures', working: 'working', unresolved: 'unresolved' };

const fmt = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

export const AgentRunPanel: React.FC<{ run: AgentRun; onSelectMemory: (id: string) => void }> = ({ run, onSelectMemory }) => {
  const total = Math.max(run.timings_ms.total ?? 0, 1);
  const groups = Object.entries(run.memory.by_group).filter(([, n]) => n > 0);
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 flex flex-col gap-3.5" aria-label="Agent performance">
      <h4 className="text-xs font-semibold text-slate-200 flex items-center gap-2"><Activity className="w-3.5 h-3.5 text-blue-400" />How the agents performed
        <span className="ml-auto font-mono text-[10px] text-slate-500">{fmt(run.timings_ms.total ?? 0)} total</span></h4>

      {run.degraded.length > 0 && (
        <div className="flex gap-2 text-[11px] rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-200 p-2">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /><div><p className="font-medium">Degraded run: a fallback replaced a model step</p>
            <p className="font-mono text-amber-300/80 mt-0.5">{run.degraded.join(' · ')}</p></div></div>
      )}

      <div>
        <div className="flex h-2 rounded-full overflow-hidden bg-slate-800" role="img" aria-label="Time spent per stage">
          {STAGES.map(([k], i) => <div key={k} style={{ width: `${((run.timings_ms[k] ?? 0) / total) * 100}%`, opacity: 0.45 + i * 0.18 }} className="bg-blue-500" />)}
        </div>
        <div className="grid grid-cols-4 gap-1 mt-1.5">
          {STAGES.map(([k, label]) => <div key={k} className="text-[10px]"><p className="text-slate-400">{label}</p><p className="font-mono text-slate-200">{fmt(run.timings_ms[k] ?? 0)}</p></div>)}
        </div>
      </div>

      <div>
        <h5 className="text-[11px] font-semibold text-slate-300 mb-1">Plan{run.plan.task_type ? ` · ${run.plan.task_type}` : ''}</h5>
        <ol className="space-y-0.5">
          {run.plan.subtasks.map((t) => <li key={t.id} className="text-[11px] text-slate-400"><span className="font-mono text-slate-500">{t.id}</span> <span className="text-blue-300">{t.agent}</span> · {t.description}</li>)}
        </ol>
      </div>

      <div>
        <h5 className="text-[11px] font-semibold text-slate-300 mb-1">Memories used · {run.memory.total} retrieved, {run.memory.cited} cited in the answer</h5>
        <p className="text-[10px] text-slate-500 mb-1.5">{groups.map(([g, n]) => `${n} ${GROUP_LABEL[g] ?? g}`).join(' · ') || 'none retrieved'}</p>
        <div className="flex flex-wrap gap-1">
          {run.memory.items.map((m) => {
            const color = m.memory_type ? TYPES[m.memory_type].color : '#94a3b8';
            return (
              <button key={m.memory_id} onClick={() => onSelectMemory(m.memory_id)} title={`${m.memory_id}${m.cited ? ' · cited' : ' · retrieved, not cited'}`}
                className={`text-[10px] px-1.5 py-0.5 rounded border cursor-pointer text-left hover:text-white ${m.cited ? 'text-slate-100' : 'text-slate-500 border-dashed'}`}
                style={{ borderColor: m.cited ? color : undefined }}>
                <span style={{ color }}>●</span> {m.topic ?? m.memory_type}{m.label ? `: ${m.label.slice(0, 28)}` : ''}
              </button>
            );
          })}
        </div>
        {run.memory.contradictions.length > 0 && <p className="text-[10px] text-amber-300 mt-1.5">{run.memory.contradictions.length} contradiction(s) flagged in memory</p>}
      </div>

      <div>
        <h5 className="text-[11px] font-semibold text-slate-300 mb-1">Tool calls · {run.tools.status ?? 'n/a'}{run.tools.fallback_used ? ' · fallback plan' : ''}</h5>
        {run.tools.executions.length === 0 ? <p className="text-[10px] text-slate-500">{run.tools.stop_reason || 'no tools called'}</p> : (
          <ul className="space-y-0.5">
            {run.tools.executions.map((e, i) => (
              <li key={i} className="text-[11px] text-slate-400 flex items-center gap-1.5" title={e.error ?? e.rationale}>
                {e.status === 'success' ? <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" /> : <XCircle className="w-3 h-3 text-rose-400 shrink-0" />}
                <span className="font-mono text-slate-300">{e.tool}</span><span className="ml-auto font-mono text-[10px] text-slate-500">{e.status} · {fmt(e.duration_ms)}</span>
              </li>
            ))}
          </ul>
        )}
        {run.tools.missing_information.length > 0 && <p className="text-[10px] text-amber-300 mt-1">Missing: {run.tools.missing_information.join('; ')}</p>}
      </div>
    </section>
  );
};
