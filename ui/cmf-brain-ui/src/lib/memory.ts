import { BookOpen, Calendar, Cog, Brain } from 'lucide-react';
import type { MemoryItem, MemoryType, RelType } from '../types';

export interface TypeMeta {
  name: string;
  region: string;
  blurb: string;
  example: string;
  color: string;
  glow: string;
  icon: typeof Brain;
  /** ellipse (cx, cy, rx, ry) in the 700x440 brain viewBox that nodes are laid out inside */
  zone: [number, number, number, number];
  label: [number, number];
}

// Palette follows the reference image: blue / amber / green / violet.
export const TYPES: Record<MemoryType, TypeMeta> = {
  working: {
    name: 'Working Memory', region: 'Prefrontal Cortex', icon: Brain, color: '#3b82f6', glow: 'rgba(59,130,246,0.16)',
    blurb: 'Current conversation, task context, short-term reasoning', example: 'Current query: prepare for next portfolio review',
    zone: [185, 170, 62, 70], label: [150, 70],
  },
  episodic: {
    name: 'Episodic Memory', region: 'Hippocampus', icon: Calendar, color: '#f59e0b', glow: 'rgba(245,158,11,0.16)',
    blurb: 'Specific events and experiences', example: 'Met on Mar 3, discussed retirement planning',
    zone: [395, 150, 118, 62], label: [395, 62],
  },
  semantic: {
    name: 'Semantic Memory', region: 'Temporal Lobe', icon: BookOpen, color: '#22c55e', glow: 'rgba(34,197,94,0.16)',
    blurb: 'Facts, concepts and stable knowledge about the client', example: 'Risk profile: moderate · Retire at 63',
    zone: [292, 296, 125, 22], label: [300, 340],
  },
  procedural: {
    name: 'Procedural Memory', region: 'Cerebellum', icon: Cog, color: '#a855f7', glow: 'rgba(168,85,247,0.16)',
    blurb: 'How things are done: advisor processes, guidelines', example: 'Portfolio review process, compliance guidelines',
    zone: [507, 357, 46, 36], label: [507, 418],
  },
};
export const TYPE_ORDER: MemoryType[] = ['working', 'episodic', 'semantic', 'procedural'];

export const REL_STYLE: Record<RelType, { color: string; label: string; dash?: string; base: number }> = {
  supersedes: { color: '#f87171', label: 'supersedes', base: 0.9 },
  corrects: { color: '#fb923c', label: 'corrects', dash: '5 3', base: 0.9 },
  supports: { color: '#4ade80', label: 'supports', base: 0.55 },
  derived_from: { color: '#60a5fa', label: 'derived from', base: 0.5 },
  explores: { color: '#fbbf24', label: 'explores', dash: '2 3', base: 0.25 },
  follows: { color: '#94a3b8', label: 'follows', dash: '1 4', base: 0 }, // timeline chain: only drawn for the focused node
  related_to: { color: '#94a3b8', label: 'related to', dash: '2 3', base: 0.2 },
};

/** Golden-angle spiral inside each type's ellipse; deterministic (ordered by date, then id) so nodes never jump on re-render. */
export function layoutNodes(memories: MemoryItem[]): Record<string, { x: number; y: number }> {
  const pos: Record<string, { x: number; y: number }> = {};
  for (const t of TYPE_ORDER) {
    const group = memories
      .filter((m) => m.memory_type === t)
      .sort((a, b) => (a.event_date ?? a.valid_from ?? '').localeCompare(b.event_date ?? b.valid_from ?? '') || a.memory_id.localeCompare(b.memory_id));
    const [cx, cy, rx, ry] = TYPES[t].zone;
    const n = group.length;
    if (n <= 8 && (t === 'semantic' || t === 'procedural')) {
      // few, important facts: an evenly spaced row (zig-zag) reads better than a spiral
      group.forEach((m, i) => {
        const x = t === 'semantic' ? cx - rx + (2 * rx * (i + 0.5)) / n : cx + (i - (n - 1) / 2) * 0;
        const y = t === 'semantic' ? cy + (i % 2 ? 5 : -5) : cy - ry * 0.6 + (i * (ry * 1.2)) / Math.max(n - 1, 1);
        pos[m.memory_id] = { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 };
      });
      continue;
    }
    group.forEach((m, i) => {
      const r = Math.sqrt((i + 0.6) / n);
      const a = i * 2.399963;
      pos[m.memory_id] = { x: Math.round((cx + Math.cos(a) * rx * r) * 10) / 10, y: Math.round((cy + Math.sin(a) * ry * r) * 10) / 10 };
    });
  }
  return pos;
}

export const STATUS_TONE: Record<string, string> = {
  active: 'text-emerald-400', unconfirmed: 'text-amber-400', superseded: 'text-slate-400', disputed: 'text-rose-400', expired: 'text-slate-500', candidate: 'text-sky-400',
};

export interface WorkingInfo { kind: 'task' | 'tool'; title: string; detail: string; fields: [string, string][] }

/** Working memories are system-written (planner task context or tool results): derive a readable view instead of showing "working context". */
export function workingInfo(m: MemoryItem): WorkingInfo {
  const c = m.content;
  const tool = c.match(/^\[tool result[^\]]*\]\s*Tool (\w+)\s+(\w+)\s*:?\s*([\s\S]*)$/);
  if (tool) {
    const [, name, outcome, rest] = tool;
    return { kind: 'tool', title: `Tool: ${name.replace(/_/g, ' ')}`, detail: rest.trim() || outcome, fields: [['Tool', name.replace(/_/g, ' ')],['Outcome', outcome]] };
  }
  const task = c.match(/^Task context:\s*([^;]*);\s*topics:\s*([^;]*);\s*intent:\s*([\s\S]*)$/);
  if (task) {
    return { kind: 'task', title: task[3].trim(), detail: c, fields: [['Task', task[1].replace(/_/g, ' ')], ['Topics', task[2].replace(/_/g, ' ')]] };
  }
  return { kind: 'task', title: c.slice(0, 80), detail: c, fields: [] };
}

export function nodeTitle(m: MemoryItem): string {
  if (m.memory_type === 'procedural') return (m.procedure_name ?? m.title).replace('[curated] ', '');
  if (m.memory_type === 'semantic') return `${m.topic.replace(/_/g, ' ')}: ${m.value_text ?? ''}`;
  if (m.memory_type === 'working') return workingInfo(m).title;
  return m.content;
}

export const fmtDate = (s: string | null | undefined) => (s ? s.slice(0, 10) : '—');
export const topicLabel = (t: string) => t.replace(/_/g, ' ');
