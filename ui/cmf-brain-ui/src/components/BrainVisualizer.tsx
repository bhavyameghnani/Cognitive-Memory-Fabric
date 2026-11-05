import React, { useMemo, useState } from 'react';
import { Brain, Clock, Eye, EyeOff } from 'lucide-react';
import type { Interaction, MemoryItem, MemoryType, Relationship } from '../types';
import { REL_STYLE, TYPES, TYPE_ORDER, fmtDate, layoutNodes, nodeTitle, workingInfo } from '../lib/memory';

interface Props {
  memories: MemoryItem[];
  relationships: Relationship[];
  interactions: Interaction[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  typeFilter: MemoryType | 'all';
  onFilter: (t: MemoryType | 'all') => void;
  /** memory ids retrieved by the last advisor query; when set, everything else dims */
  highlighted: Set<string> | null;
  onClearHighlight: () => void;
  showHistory: boolean;
  onToggleHistory: () => void;
  interactionId: string | null;
  onSelectInteraction: (id: string | null) => void;
}

// Hand-built side-view silhouette (faces left). Regions mirror the reference image.
const REGION_PATH: Record<MemoryType, string> = {
  working: 'M 118 255 C 92 160 165 66 292 54 C 318 120 306 205 284 276 C 220 292 150 288 118 255 Z',
  episodic: 'M 292 54 C 410 40 520 80 556 160 C 500 178 420 190 356 262 C 330 210 304 130 292 54 Z',
  semantic: 'M 140 296 C 200 340 345 352 452 316 C 482 282 448 252 384 258 C 300 262 196 250 140 296 Z',
  procedural: 'M 446 322 C 486 298 566 306 580 350 C 574 404 506 424 456 398 C 426 372 424 342 446 322 Z',
};
const OUTLINE =
  'M 108 252 C 84 150 168 52 300 44 C 436 34 552 84 578 188 C 590 244 568 286 524 298 C 586 318 596 390 540 418 C 500 436 450 422 428 392 C 410 372 408 346 428 326 C 360 346 270 350 208 334 C 140 320 114 292 108 252 Z';
const HIPPOCAMPUS = 'M 330 214 C 380 168 450 150 492 168 C 520 182 500 206 470 196 C 430 184 396 204 372 244';

const SIZE: Record<string, number> = { semantic: 7, procedural: 6.5, working: 4.5, episodic: 4.2 };

export const BrainVisualizer: React.FC<Props> = (p) => {
  const [hovered, setHovered] = useState<string | null>(null);
  const [period, setPeriod] = useState<'all' | 'Q1' | 'Q2' | 'Q3'>('all');

  const visible = useMemo(
    () => p.memories.filter((m) => p.showHistory || (m.status !== 'superseded' && m.status !== 'expired')),
    [p.memories, p.showHistory],
  );
  const pos = useMemo(() => layoutNodes(visible), [visible]);
  const byId = useMemo(() => new Map(p.memories.map((m) => [m.memory_id, m])), [p.memories]);
  // semantic/procedural facts are few but sit close together: stagger their labels above/below by x order
  const labelSlot = useMemo(() => {
    const slot: Record<string, number> = {};
    (['semantic', 'procedural'] as const).forEach((t) => visible.filter((m) => m.memory_type === t && pos[m.memory_id])
      .sort((a, b) => pos[a.memory_id].x - pos[b.memory_id].x).forEach((m, i) => { slot[m.memory_id] = i % 2; }));
    return slot;
  }, [visible, pos]);
  const interactionOf = (m: MemoryItem) => m.evidence[0]?.interaction_id ?? null;
  const focus = hovered ?? p.selectedId;

  const links = useMemo(
    () => p.relationships.filter((r) => pos[r.source_id] && pos[r.target_id]).map((r) => {
      const touches = focus === r.source_id || focus === r.target_id;
      const base = REL_STYLE[r.rel_type].base;
      return { ...r, id: `${r.source_id}>${r.target_id}>${r.rel_type}`, touches, opacity: touches ? 1 : base };
    }).filter((l) => l.opacity > 0),
    [p.relationships, pos, focus],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    visible.forEach((m) => { c[m.memory_type] = (c[m.memory_type] ?? 0) + 1; });
    return c;
  }, [visible]);

  const timeline = useMemo(() => p.interactions.filter((i) => {
    if (period === 'all') return true;
    const mth = parseInt(i.datetime.slice(5, 7), 10);
    return period === 'Q1' ? mth <= 3 : period === 'Q2' ? mth >= 4 && mth <= 6 : mth >= 7;
  }), [p.interactions, period]);

  const dimmed = (m: MemoryItem) => {
    if (p.typeFilter !== 'all' && m.memory_type !== p.typeFilter) return true;
    if (p.highlighted && !p.highlighted.has(m.memory_id)) return true;
    if (p.interactionId && interactionOf(m) !== p.interactionId) return true;
    return false;
  };
  const hoveredMem = hovered ? byId.get(hovered) : null;

  return (
    <div className="relative rounded-2xl border border-slate-800 bg-slate-900/60 backdrop-blur-xl p-5 overflow-hidden flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <Brain className="w-5 h-5 text-blue-400" />
            <h2 className="text-base font-semibold text-slate-100">Cognitive Memory Brain</h2>
            <span className="text-xs text-slate-400 font-mono tabular-nums">{visible.length} memories · {links.length} associations</span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">Advisor conversations consolidated into four memory systems</p>
        </div>
        <div className="flex flex-wrap items-center gap-1 p-1 bg-slate-950/80 border border-slate-800/80 rounded-xl text-xs">
          <button onClick={() => p.onFilter('all')} className={`px-3 py-1.5 rounded-lg font-medium transition-all cursor-pointer ${p.typeFilter === 'all' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'}`}>All</button>
          {TYPE_ORDER.map((t) => (
            <button key={t} onClick={() => p.onFilter(p.typeFilter === t ? 'all' : t)}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg font-medium transition-all capitalize cursor-pointer ${p.typeFilter === t ? 'bg-slate-800 text-slate-100 border border-slate-700/60' : 'text-slate-400 hover:text-slate-200'}`}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: TYPES[t].color }} />
              {t}<span className="text-[10px] text-slate-500 font-mono">({counts[t] ?? 0})</span>
            </button>
          ))}
        </div>
      </div>

      {p.highlighted && (
        <div className="mt-3 flex items-center justify-between text-xs rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-1.5 text-blue-200">
          <span>Showing the {p.highlighted.size} memories CMF retrieved for your last question</span>
          <button onClick={p.onClearHighlight} className="underline underline-offset-2 hover:text-white cursor-pointer">clear</button>
        </div>
      )}

      <div className="relative w-full flex items-center justify-center my-1 select-none">
        <div className="absolute inset-0 pointer-events-none flex items-center justify-center">
          <div className="w-80 h-60 bg-blue-500/10 rounded-full blur-3xl -ml-24" />
          <div className="w-64 h-48 bg-amber-500/10 rounded-full blur-3xl ml-16 -mt-10" />
          <div className="w-56 h-40 bg-violet-500/10 rounded-full blur-3xl ml-40 mt-24" />
        </div>
        <svg viewBox="80 24 530 426" className="relative w-full h-auto max-h-[460px]" role="img" aria-label="Brain map of client memories by memory type">
          <defs>
            {TYPE_ORDER.map((t) => (
              <radialGradient key={t} id={`g-${t}`} cx="50%" cy="50%" r="65%">
                <stop offset="0%" stopColor={TYPES[t].color} stopOpacity="0.34" />
                <stop offset="100%" stopColor={TYPES[t].color} stopOpacity="0.04" />
              </radialGradient>
            ))}
            <filter id="glow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="3" result="b" />
              <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>

          <path d={OUTLINE} fill="rgba(15,23,42,0.55)" stroke="rgba(148,163,184,0.35)" strokeWidth="1.5" />
          {TYPE_ORDER.map((t) => (
            <path key={t} d={REGION_PATH[t]} fill={`url(#g-${t})`} stroke={TYPES[t].color} strokeDasharray="4 3"
              strokeWidth={p.typeFilter === t ? 2.2 : 1} opacity={p.typeFilter !== 'all' && p.typeFilter !== t ? 0.35 : 0.9} className="transition-all duration-300" />
          ))}
          {/* sulci */}
          <g fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="1.5">
            <path d="M 150 120 Q 200 150 250 118 T 330 130 T 430 108" />
            <path d="M 160 214 Q 220 232 280 206 T 380 226 T 520 214" />
            <path d="M 205 280 Q 260 300 330 284 T 440 288" />
            <path d="M 470 340 Q 510 330 545 346 M 466 372 Q 510 360 552 376" />
          </g>
          <path d={HIPPOCAMPUS} fill="none" stroke={TYPES.episodic.color} strokeWidth="7" strokeLinecap="round" opacity="0.18" />
          <path d="M 520 392 C 530 420 526 436 518 440" stroke="rgba(148,163,184,0.3)" strokeWidth="9" fill="none" strokeLinecap="round" />

          {/* region captions */}
          {TYPE_ORDER.map((t) => (
            <text key={t} x={TYPES[t].label[0]} y={TYPES[t].label[1]} textAnchor="middle" fontSize="10" fontWeight="600" fill={TYPES[t].color} opacity={p.typeFilter !== 'all' && p.typeFilter !== t ? 0.3 : 0.95}
              style={{ letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              {TYPES[t].region}
            </text>
          ))}

          {/* association paths */}
          <g>
            {links.map((l) => {
              const a = pos[l.source_id], b = pos[l.target_id];
              const dx = b.x - a.x, dy = b.y - a.y;
              const d = `M ${a.x} ${a.y} Q ${(a.x + b.x) / 2 - dy * 0.16} ${(a.y + b.y) / 2 + dx * 0.16} ${b.x} ${b.y}`;
              const st = REL_STYLE[l.rel_type];
              const faded = (p.highlighted && !(p.highlighted.has(l.source_id) && p.highlighted.has(l.target_id))) || p.typeFilter !== 'all';
              return (
                <g key={l.id} opacity={faded && !l.touches ? 0.12 : 1}>
                  <path d={d} fill="none" stroke={st.color} strokeOpacity={l.opacity} strokeWidth={l.touches ? 2 : 1.2} strokeDasharray={st.dash} className={l.touches ? 'animate-flow' : ''} />
                  {l.touches && <circle r="2.6" fill="#fff"><animateMotion path={d} dur="2.2s" repeatCount="indefinite" /></circle>}
                </g>
              );
            })}
          </g>

          {/* memory nodes */}
          {visible.map((m) => {
            const o = pos[m.memory_id];
            if (!o) return null;
            const t = TYPES[m.memory_type];
            const sel = p.selectedId === m.memory_id, hov = hovered === m.memory_id;
            const hit = p.highlighted?.has(m.memory_id);
            const faded = dimmed(m);
            const hollow = m.status === 'unconfirmed' || m.status === 'candidate';
            const old = m.status === 'superseded' || m.status === 'expired';
            const r = (SIZE[m.memory_type] ?? 4) * (0.8 + m.confidence * 0.4);
            const label = (sel || hov || m.memory_type === 'semantic' || m.memory_type === 'procedural') && !faded;
            return (
              <g key={m.memory_id} transform={`translate(${o.x},${o.y})`} onClick={() => p.onSelect(m.memory_id)} onMouseEnter={() => setHovered(m.memory_id)} onMouseLeave={() => setHovered(null)}
                className="cursor-pointer transition-opacity duration-300" opacity={faded ? 0.14 : old ? 0.55 : 1}>
                <circle r={11} fill="transparent" />
                {(sel || hit) && <circle r={r + 7} fill={t.glow} stroke={t.color} strokeWidth="1.5" className="animate-pulse-slow" />}
                {m.status === 'disputed' && <circle r={r + 3} fill="none" stroke="#f43f5e" strokeWidth="1.5" />}
                <circle r={sel || hov ? r + 1.5 : r} fill={hollow ? '#0f172a' : t.color} stroke={t.color} strokeWidth={hollow ? 1.5 : 0}
                  strokeDasharray={old ? '2 2' : undefined} filter={hollow || old ? undefined : 'url(#glow)'} />
                {label && (
                  <text x={sel || hov || m.memory_type === 'procedural' ? r + 6 : 0} y={sel || hov || m.memory_type === 'procedural' ? 3.5 : labelSlot[m.memory_id] ? r + 13 : -(r + 7)}
                    textAnchor={sel || hov || m.memory_type === 'procedural' ? 'start' : 'middle'} fontSize={sel || hov ? 11 : 9} fontWeight={sel ? 700 : 500} fill={sel || hov ? '#fff' : '#cbd5e1'} paintOrder="stroke" stroke="#020617" strokeWidth="3" className="pointer-events-none">
                    {sel || hov ? truncate(nodeTitle(m), 40) : shortLabel(m)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        {hoveredMem && (
          <div className="absolute bottom-2 left-2 right-2 sm:left-auto sm:w-80 p-3 bg-slate-950/95 border border-slate-700/80 rounded-xl shadow-2xl backdrop-blur-md text-xs pointer-events-none">
            <div className="flex items-center justify-between text-slate-400 pb-1 border-b border-slate-800">
              <span className="font-semibold" style={{ color: TYPES[hoveredMem.memory_type].color }}>{TYPES[hoveredMem.memory_type].name}</span>
              <span className="font-mono">{fmtDate(hoveredMem.event_date ?? hoveredMem.valid_from)}</span>
            </div>
            <p className="text-slate-100 mt-1 line-clamp-3">{nodeTitle(hoveredMem)}</p>
            <div className="flex justify-between mt-2 pt-1 border-t border-slate-800/80 text-[11px] text-slate-400">
              {hoveredMem.memory_type === 'working'
                ? <><span>{workingInfo(hoveredMem).kind === 'tool' ? 'tool result' : 'task context'} · session</span><span className="font-mono text-blue-400">expires {hoveredMem.expires_at?.slice(11, 16) ?? '—'}</span></>
                : <><span className="capitalize">{hoveredMem.status} · {hoveredMem.certainty}</span><span className="font-mono text-blue-400">{Math.round(hoveredMem.confidence * 100)}% conf.</span></>}
            </div>
          </div>
        )}
      </div>

      {/* legend: region callouts as in the reference image */}
      <div className="pt-3 border-t border-slate-800/70 grid grid-cols-2 lg:grid-cols-4 gap-2">
        {TYPE_ORDER.map((t) => {
          const m = TYPES[t];
          const Icon = m.icon;
          return (
            <button key={t} onClick={() => p.onFilter(p.typeFilter === t ? 'all' : t)}
              className="text-left rounded-xl border p-2.5 transition-all cursor-pointer hover:brightness-125"
              style={{ borderColor: `${m.color}${p.typeFilter === t ? 'cc' : '44'}`, background: m.glow }}>
              <div className="flex items-center gap-1.5 text-xs font-semibold" style={{ color: m.color }}><Icon className="w-3.5 h-3.5" />{m.name}</div>
              <div className="text-[10px] text-slate-400">({m.region})</div>
              <p className="text-[11px] text-slate-300 mt-1 leading-snug">{m.blurb}</p>
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-3 text-[11px] text-slate-400">
        <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-slate-300" />solid = active</span>
        <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full border border-slate-300" />hollow = unconfirmed</span>
        <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full border border-dashed border-slate-400 opacity-60" />dashed = superseded</span>
        {(['supersedes', 'corrects', 'supports', 'derived_from'] as const).map((r) => (
          <span key={r} className="flex items-center gap-1.5"><span className="w-4 h-0.5" style={{ background: REL_STYLE[r].color }} />{REL_STYLE[r].label}</span>
        ))}
        <button onClick={p.onToggleHistory} className="ml-auto flex items-center gap-1.5 text-slate-300 hover:text-white cursor-pointer">
          {p.showHistory ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
          {p.showHistory ? 'Hide' : 'Show'} superseded history
        </button>
      </div>

      {/* meeting timeline */}
      <div className="pt-3 mt-3 border-t border-slate-800/80">
        <div className="flex items-center justify-between pb-2">
          <div className="flex items-center gap-2 text-xs text-slate-300 font-medium">
            <Clock className="w-3.5 h-3.5 text-blue-400" />
            <span>Client meetings ({timeline.length}) <span className="text-slate-500 font-normal">· click one to light up what it created</span></span>
          </div>
          <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-lg border border-slate-800 text-[11px]">
            {(['all', 'Q1', 'Q2', 'Q3'] as const).map((q) => (
              <button key={q} onClick={() => setPeriod(q)} className={`px-2 py-0.5 rounded font-medium cursor-pointer ${period === q ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}>{q === 'all' ? 'All' : q}</button>
            ))}
          </div>
        </div>
        <div className="flex items-stretch gap-2.5 overflow-x-auto pb-1">
          {timeline.map((i) => {
            const made = p.memories.filter((m) => interactionOf(m) === i.interaction_id).length;
            const on = p.interactionId === i.interaction_id;
            return (
              <button key={i.interaction_id} onClick={() => p.onSelectInteraction(on ? null : i.interaction_id)}
                className={`shrink-0 w-52 p-2.5 rounded-xl border text-left transition-all cursor-pointer ${on ? 'bg-blue-950/40 border-blue-500/80 ring-1 ring-blue-500/30' : 'bg-slate-950/70 border-slate-800/80 hover:border-slate-700'}`}>
                <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 pb-1 border-b border-slate-800/60">
                  <span className="font-semibold text-slate-300">{fmtDate(i.datetime)}</span><span>{i.channel}</span>
                </div>
                <p className="text-xs font-semibold text-slate-100 mt-1.5 line-clamp-1">{i.context}</p>
                <p className="text-[10px] text-slate-500 mt-1">{i.interaction_id} · {i.turns} turns · <span className="text-blue-400">{made} memories</span></p>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
};

const shortLabel = (m: MemoryItem) =>
  m.memory_type === 'semantic' ? `${m.topic.split('_')[0]} ${m.value_text ?? ''}` : truncate(nodeTitle(m), 19);
const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
