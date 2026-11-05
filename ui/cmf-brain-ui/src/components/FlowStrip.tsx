import React from 'react';
import { ArrowRight, Brain, FileText, UserRound } from 'lucide-react';
import type { AskResult } from '../types';

// Advisor Query -> CMF -> Response, as in the bottom of the reference image.
export const FlowStrip: React.FC<{ busy: boolean; result: AskResult | null }> = ({ busy, result }) => {
  const Arrow = () => <ArrowRight className={`w-5 h-5 shrink-0 ${busy ? 'text-blue-400 animate-pulse' : 'text-slate-600'}`} />;
  return (
    <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1.3fr_auto_1fr] items-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="flex items-center gap-3"><UserRound className="w-9 h-9 p-2 rounded-full bg-blue-600/80 text-white shrink-0" />
        <div className="min-w-0"><p className="text-xs font-semibold text-slate-200">Advisor Query</p><p className="text-[11px] text-slate-400 line-clamp-2">{result ? `“${result.report.question}”` : '“How should I prepare for the next portfolio review?”'}</p></div></div>
      <Arrow />
      <div className={`flex items-center gap-3 rounded-xl border px-3 py-2 ${busy ? 'border-blue-400 bg-blue-500/10' : 'border-blue-500/30 bg-blue-500/5'}`}><Brain className="w-9 h-9 p-2 rounded-full bg-slate-800 text-blue-300 shrink-0" />
        <div><p className="text-xs font-semibold text-slate-100">Cognitive Memory Fabric</p><p className="text-[11px] text-slate-400">Retrieves the right memories, understands the timeline, reasons with context</p></div></div>
      <Arrow />
      <div className="flex items-center gap-3"><FileText className="w-9 h-9 p-2 rounded-full bg-slate-800 text-slate-300 shrink-0" />
        <div className="min-w-0"><p className="text-xs font-semibold text-slate-200">Response to Advisor</p>
          <p className="text-[11px] text-slate-400">{result ? `${result.report.key_findings.length} findings · ${result.report.evidence.length} cited evidence` : 'Current context · history · evidence · reasoned recommendations'}</p></div></div>
    </div>
  );
};
