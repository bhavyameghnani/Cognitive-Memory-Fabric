import React, { useMemo, useState } from 'react';
import { AlertTriangle, BadgeCheck, ChevronDown, ChevronRight, CircleAlert, Info, Quote, ShieldCheck } from 'lucide-react';
import type { AskResult, EvidenceItem, Finding } from '../types';
import { buildView, kindLabel, splitCitations, type Banner, type Development, type Tone } from '../lib/reportView';
import { AgentRunPanel } from './AgentRunPanel';

const TONE: Record<Tone, string> = {
  ok: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100',
  warn: 'border-amber-500/40 bg-amber-500/10 text-amber-100',
  info: 'border-sky-500/30 bg-sky-500/10 text-sky-100',
  bad: 'border-rose-500/40 bg-rose-500/10 text-rose-100',
};
const BADGE: Record<string, string> = {
  complete_narrative: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40', chronology_only: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  degraded: 'bg-amber-500/15 text-amber-300 border-amber-500/40', fallback: 'bg-rose-500/15 text-rose-300 border-rose-500/40', memory_only: 'bg-slate-500/15 text-slate-300 border-slate-500/40',
};

const Section: React.FC<{ title: string; hint?: string; count?: number; open?: boolean; children: React.ReactNode }> = ({ title, hint, count, open = true, children }) => {
  const [o, setO] = useState(open);
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/50">
      <button onClick={() => setO(!o)} className="w-full flex items-center gap-2 px-4 py-2.5 text-left cursor-pointer" aria-expanded={o}>
        {o ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-500" />}
        <h4 className="text-sm font-semibold text-slate-100">{title}</h4>
        {count != null && <span className="text-[11px] font-mono text-slate-500">{count}</span>}
        {hint && <span className="ml-auto text-[11px] text-slate-500 hidden sm:block truncate">{hint}</span>}
      </button>
      {o && <div className="px-4 pb-4 pt-1">{children}</div>}
    </section>
  );
};

const BannerBox: React.FC<{ b: Banner }> = ({ b }) => {
  const Icon = b.tone === 'ok' ? ShieldCheck : b.tone === 'info' ? Info : b.tone === 'bad' ? AlertTriangle : CircleAlert;
  return (
    <div className={`flex gap-2.5 rounded-lg border p-3 text-xs ${TONE[b.tone]}`} role={b.tone === 'bad' || b.tone === 'warn' ? 'alert' : 'status'}>
      <Icon className="w-4 h-4 shrink-0 mt-0.5" />
      <div><p className="font-semibold">{b.title}</p><p className="mt-0.5 opacity-90 leading-relaxed">{b.detail}</p></div>
    </div>
  );
};

/** Report text with its citations turned into chips; a chip opens the evidence item it cites. */
const Cited: React.FC<{ text: string; open: string | null; onOpen: (ref: string) => void }> = ({ text, open, onOpen }) => (
  <>
    {splitCitations(text).map((s, i) => s.kind === 'text' ? <React.Fragment key={i}>{s.text}</React.Fragment> : (
      <button key={i} onClick={() => onOpen(s.ref!)} title={`evidence ${s.ref}`} data-cite={s.ref}
        className={`ml-1 mr-0.5 px-1.5 py-0 rounded align-baseline font-mono text-[10px] cursor-pointer whitespace-nowrap ${open === s.ref ? 'bg-blue-500 text-white' : 'bg-blue-500/15 text-blue-300 hover:bg-blue-500/30'}`}>
        {s.meta ? `${s.meta} · ` : ''}{s.ref}
      </button>
    ))}
  </>
);

const EvidenceCard: React.FC<{ e: EvidenceItem; onSelectMemory: (id: string) => void }> = ({ e, onSelectMemory }) => (
  <div className="mt-2 rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 text-xs" data-evidence={e.ref}>
    <p className="font-mono text-blue-300">{e.ref} · {e.source_class.replace(/_/g, ' ')}{e.synthetic ? ' · synthetic' : ''}{e.evidence_class ? ` · ${e.evidence_class}` : ''}</p>
    <p className="text-slate-300 mt-1">{e.description}</p>
    {e.excerpt && <p className="italic text-slate-400 mt-1">“{e.excerpt}”</p>}
    {e.interaction_id && <p className="text-[10px] text-slate-500 font-mono mt-1">{e.interaction_id} · turn {e.turn_index}{e.speaker ? ` · recorded speaker ${e.speaker}` : ''}{e.event_date ? ` · ${e.event_date}` : ''}</p>}
    {e.memory_id && <button onClick={() => onSelectMemory(e.memory_id!)} className="mt-1.5 text-blue-300 underline underline-offset-2 hover:text-white cursor-pointer">open memory</button>}
  </div>
);

const Bullets: React.FC<{ items: string[]; tone?: string; limit?: number }> = ({ items, tone = 'text-slate-300', limit = 6 }) => {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, limit);
  return (
    <div>
      <ul className={`list-disc list-outside ml-4 space-y-1 text-xs leading-relaxed ${tone}`}>{shown.map((s, i) => <li key={i}>{s}</li>)}</ul>
      {items.length > limit && <button onClick={() => setAll(!all)} className="mt-2 text-[11px] text-blue-300 hover:text-white cursor-pointer underline underline-offset-2">{all ? 'show fewer' : `show all ${items.length}`}</button>}
    </div>
  );
};

const DevList: React.FC<{ items: Development[]; open: string | null; onOpen: (r: string) => void }> = ({ items, open, onOpen }) => (
  <ul className="space-y-2.5">
    {items.map((d, i) => (
      <li key={i} className="text-xs text-slate-300 leading-relaxed">
        <span className="font-mono text-[11px] text-slate-400">{d.date ?? 'undated'} · {d.where}</span>
        <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-500">{d.who}</span>
        {d.badges.map((b) => <span key={b} className="ml-1.5 text-[10px] px-1.5 rounded bg-slate-800 text-amber-300 border border-slate-700">{b}</span>)}
        <p className="mt-0.5 text-slate-200"><Quote className="inline w-3 h-3 mr-1 text-slate-500" />{d.wording}
          {d.ref && <button onClick={() => onOpen(d.ref!)} className={`ml-1 px-1.5 rounded font-mono text-[10px] cursor-pointer ${open === d.ref ? 'bg-blue-500 text-white' : 'bg-blue-500/15 text-blue-300 hover:bg-blue-500/30'}`}>{d.ref}</button>}</p>
      </li>
    ))}
  </ul>
);

export const ReportView: React.FC<{ result: AskResult; onSelectMemory: (id: string) => void }> = ({ result, onSelectMemory }) => {
  const rep = result.report;
  const v = useMemo(() => buildView(result), [result]);
  const [open, setOpen] = useState<string | null>(null);
  const toggle = (r: string) => setOpen((o) => (o === r ? null : r));
  const ev = (ref: string) => rep.evidence.find((e) => e.ref === ref);
  const opened = open ? ev(open) : undefined;
  const refsOf = (f: Finding) => f.refs;
  const para = (f: Finding, i: number, extra = '') => (
    <div key={i} className={extra}>
      <p className="leading-relaxed"><Cited text={f.text} open={open} onOpen={toggle} /></p>
      {opened && refsOf(f).includes(opened.ref) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
    </div>
  );
  const hasNarrative = v.narrative.length > 0;
  const summaryRefs = splitCitations(rep.summary).filter((s) => s.kind === 'cite').map((s) => s.ref!);
  const summaryBox = (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4" data-testid="summary">
      <h4 className="text-xs font-semibold text-slate-400 mb-1">{v.summaryIsBoilerplate ? 'Technical summary (as assembled by the backend: which turns were retrieved)' : 'Summary'}</h4>
      <p className={`${v.summaryIsBoilerplate ? 'text-[11px] text-slate-500' : 'text-sm text-slate-100'} leading-relaxed`}><Cited text={rep.summary} open={open} onOpen={toggle} /></p>
      {opened && summaryRefs.includes(opened.ref) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
    </div>
  );
  const g = v.glance;

  return (
    <div className="flex flex-col gap-3" data-report-kind={v.completeness}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold ${BADGE[v.completeness]}`} data-testid="report-kind">
          {v.completeness === 'complete_narrative' ? <BadgeCheck className="w-3.5 h-3.5" /> : <CircleAlert className="w-3.5 h-3.5" />}{v.label}
        </span>
        <span className="text-[11px] font-mono text-slate-500">{rep.request_id} · {result.client_ids.join(', ')}</span>
      </div>
      {v.banners.map((b, i) => <BannerBox key={i} b={b} />)}
      {v.singleClientNote && <BannerBox b={{ tone: 'info', title: 'Single-client source retrieval', detail: v.singleClientNote }} />}

      {!v.summaryIsBoilerplate && summaryBox}

      {v.story.length > 0 && (
        <section className="rounded-xl border border-amber-500/40 bg-amber-500/[0.05] p-4" data-testid="story">
          <h4 className="text-sm font-semibold text-amber-100 flex items-center gap-2"><Info className="w-4 h-4" />Story summary <span className="text-[10px] font-normal text-amber-200 border border-amber-500/40 rounded px-1.5">model-written · not independently verified</span></h4>
          <p className="mt-1 text-[11px] text-amber-200/80 leading-relaxed">A plain-English account written from the claims below. Code removes invented causes, decisions, intentions, wrong speakers and any figure, date or name that is not in the cited lines, but it cannot check that a paraphrase means what the source says. The verified quotations below are the evidence; where they differ, trust them.</p>
          {v.storyCoverage && (
            <p className="mt-1.5 text-[11px] font-semibold text-amber-200" data-testid="story-coverage">
              Covers {v.storyCoverage.meetings}{v.storyCoverage.of ? ` of ${v.storyCoverage.of}` : ''} meeting{v.storyCoverage.meetings === 1 ? '' : 's'}{v.storyCoverage.from ? ` (${v.storyCoverage.from}${v.storyCoverage.to && v.storyCoverage.to !== v.storyCoverage.from ? ' to ' + v.storyCoverage.to : ''})` : ''}.
              {v.storyCoverage.of > v.storyCoverage.meetings ? ' Developments in the other meetings are not in this summary: see the verified narrative and the chronology below.' : ''}
            </p>
          )}
          <div className="mt-2.5 flex flex-col gap-3 text-[14px] leading-7 text-slate-100">{v.story.map((f, i) => {
            const first = ev(f.refs[0]);                      // the caption is the evidence's own date and meeting, written by the backend, not by the model
            return <div key={i}>{first?.event_date && <p className="text-[10px] font-mono uppercase tracking-wide text-amber-300/70 mb-0.5">{first.event_date}{first.interaction_id ? ` · ${first.interaction_id}` : ''}</p>}{para(f, i)}</div>;
          })}</div>
        </section>
      )}
      {v.storyNote && <BannerBox b={{ tone: 'info', title: 'No story summary', detail: v.storyNote }} />}

      {hasNarrative && (
        <section className="rounded-xl border border-emerald-500/30 bg-emerald-500/[0.04] p-4" data-testid="narrative">
          <h4 className="text-sm font-semibold text-emerald-200 flex items-center gap-2"><BadgeCheck className="w-4 h-4" />Narrative <span className="text-[10px] font-normal text-emerald-300/70 border border-emerald-500/30 rounded px-1.5">model-written · verified</span></h4>
          <div className="mt-2 flex flex-col gap-3 text-[13px] text-slate-100">{v.narrative.map((f, i) => para(f, i))}</div>
        </section>
      )}

      {g && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center" data-testid="glance">
          {([[`${g.from ?? '?'} → ${g.to ?? '?'}`, `${g.meetings} meetings`], [String(g.clientStatements), "client's own statements"], [String(g.advisorStatements), "advisor's accounts / interpretations"],
             [String(g.tentative), 'tentative'], [String(g.decisions), 'decisions'], [String(g.corrections), 'linked corrections'], [String(g.unresolved), 'unresolved'], [String(g.claims), 'dated statements']] as const).map(([a, b]) => (
            <div key={b} className="rounded-lg border border-slate-800 bg-slate-900/60 py-2 px-1"><p className="text-sm font-semibold text-slate-100 tabular-nums">{a}</p><p className="text-[10px] text-slate-500 leading-tight">{b}</p></div>
          ))}
        </div>
      )}

      {v.current.length > 0 ? (
        <Section title="Current understanding" hint="verified and confirmed by the memory layer" count={v.current.length}>
          <DevList items={v.current.map((c) => ({ date: c.date, where: c.where, who: "client's own words", wording: c.text, badges: ['verified, confirmed'], ref: c.ref }))} open={open} onOpen={toggle} />
          {opened && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
        </Section>
      ) : v.glance && (
        <Section title="Current understanding" open={false}>
          <p className="text-xs text-slate-400">No retrieved statement is both verified and confirmed as the client's current position, so none is stated here. Read the developments and uncertainties below.</p>
        </Section>
      )}

      {v.developments.length > 0 && (
        <Section title="Important developments" hint="decisions, corrections and changes of status" count={v.developments.length} open={!hasNarrative}>
          <DevList items={v.developments} open={open} onOpen={toggle} />
          {opened && v.developments.some((d) => d.ref === opened.ref) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
        </Section>
      )}

      {(v.corrections.length > 0 || v.correctionStatements.length > 0) && (
        <Section title="Corrections" count={v.corrections.length + v.correctionStatements.length}>
          {v.corrections.map((c, i) => (
            <div key={i} className="text-xs text-slate-300 mb-3 leading-relaxed"><p className="font-mono text-[11px] text-slate-500">{c.date ?? ''} · {c.correctionWhere} corrects {c.originalWhere}</p>
              <p className="text-slate-400">Earlier: “{c.original}”</p><p className="text-slate-200">Correction: “{c.correction}”</p></div>
          ))}
          {v.correctionStatements.length > 0 && <><p className="text-[11px] text-slate-500 mb-1.5">Statements worded as corrections (the record does not link them to one specific earlier statement):</p><DevList items={v.correctionStatements} open={open} onOpen={toggle} /></>}
        </Section>
      )}

      {v.contradictions.length > 0 && (
        <Section title="Contradictions and conflicts" count={v.contradictions.length} open={false}><Bullets items={v.contradictions} tone="text-amber-200" /></Section>
      )}

      {v.otherFindings.length > 0 && (
        <Section title="Other findings the verifier accepted" count={v.otherFindings.length} open={false}>
          <div className="flex flex-col gap-2 text-xs text-slate-300">{v.otherFindings.map((f, i) => para(f, i))}</div>
        </Section>
      )}

      {v.openQuestions.length > 0 && <Section title="Unresolved and open questions" count={v.openQuestions.length} open><Bullets items={v.openQuestions} tone="text-amber-200/90" /></Section>}
      {v.systemNotes.length > 0 && <Section title="Retrieval, verification and coverage notes" count={v.systemNotes.length} open={false} hint="what was not retrieved, withheld or unverified"><Bullets items={v.systemNotes} tone="text-slate-400" limit={8} /></Section>}

      {v.chronology.length > 0 && (
        <Section title={hasNarrative ? 'Supporting chronology' : 'Chronology'} count={v.chronology.length} open={!hasNarrative}
          hint={hasNarrative ? 'code-written; every statement quotes its source turn' : 'code-written from the conversation record'}>
          <p className="text-[11px] text-slate-500 mb-2">{hasNarrative ? 'The same meetings in strict date order, with the original wording. Paragraphs already covered by the narrative above are marked.' : 'Strict date order, original wording, each statement attributed to its recorded speaker.'}</p>
          <div className="flex flex-col gap-3 text-xs text-slate-300">
            {v.chronology.map((f, i) => (
              <div key={i}>
                {f.refs.some((r) => v.narratedRefs.has(r)) && <span className="inline-block mb-0.5 text-[10px] px-1.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">also narrated above</span>}
                {para(f, i)}
              </div>
            ))}
          </div>
        </Section>
      )}

      {v.sourceRecords.length > 0 && (
        <Section title="Source records" count={v.sourceRecords.length} open={false} hint="what each retrieved turn records, not that it is true">
          <div className="flex flex-col gap-2 text-xs text-slate-400">{v.sourceRecords.map((f, i) => para(f, i))}</div>
        </Section>
      )}

      {rep.financial_analysis.some((f) => f.origin === 'tool_code' || f.origin === 'rendered') && (
        <Section title="Financial analysis (tool results)" open={false}>
          <div className="flex flex-col gap-2 text-xs text-slate-300">{rep.financial_analysis.map((f, i) => para(f, i))}</div>
        </Section>
      )}
      {v.summaryIsBoilerplate && summaryBox}
      {rep.suggested_followups.length > 0 && <Section title="Suggested follow-ups" open={false}><Bullets items={rep.suggested_followups} tone="text-emerald-300" /></Section>}
      {rep.limitations.length > 0 && <Section title="Limitations" open={false}><Bullets items={rep.limitations} tone="text-slate-400" /></Section>}
      {result.agent_run && <AgentRunPanel run={result.agent_run} onSelectMemory={onSelectMemory} />}
      <p className="text-[10px] text-slate-600">Provenance labels are set by the backend only: “verified narrative” = model prose that passed code checks and the independent evidence verifier; “code-written” = assembled by code from the cited turn. Kinds: {kindLabel('client_statement')}; {kindLabel('advisor_reported')}; {kindLabel('advisor_interpretation')}.</p>
    </div>
  );
};
