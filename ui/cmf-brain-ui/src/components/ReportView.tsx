import React, { useMemo, useState } from 'react';
import { AlertTriangle, BadgeCheck, ChevronDown, ChevronRight, CircleAlert, Info, Quote, ShieldCheck } from 'lucide-react';
import type { AskResult, EvidenceItem, Finding } from '../types';
import { buildView, splitCitations, type Banner, type Development, type Tone } from '../lib/reportView';
import { buildClientReport, type Excerpt, type Para, type PeriodSection, type Sentence } from '../lib/clientReport';
import { AgentRunPanel } from './AgentRunPanel';

const TONE: Record<Tone, string> = {
  ok: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100',
  warn: 'border-amber-500/40 bg-amber-500/10 text-amber-100',
  info: 'border-sky-500/30 bg-sky-500/10 text-sky-100',
  bad: 'border-rose-500/40 bg-rose-500/10 text-rose-100',
};
const BADGE: Record<string, string> = {
  complete_narrative: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40', partial_narrative: 'bg-amber-500/15 text-amber-300 border-amber-500/40', chronology_only: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
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

const EXCERPT_LABEL: Record<Excerpt['state'], string> = {
  rejected: 'not written up: the checks rejected the wording',
  not_cited: 'not used in the write-up',
  window_failed: 'its period could not be written up',
  withheld: 'withheld by a later safeguard',
};
const STATUS_TONE: Record<string, string> = {
  ok: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40', none: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40', partial: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  empty: 'bg-rose-500/15 text-rose-300 border-rose-500/40', failed: 'bg-rose-500/15 text-rose-300 border-rose-500/40', skipped: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
};
const STATUS_LABEL: Record<string, string> = { ok: 'written up', none: 'written up', partial: 'partly written up', empty: 'not written up', failed: 'not written up', skipped: 'not written up' };

const Tile: React.FC<{ a: string; b: string; warn?: boolean }> = ({ a, b, warn }) => (
  <div className={`rounded-lg border py-2 px-1 text-center ${warn ? 'border-amber-500/40 bg-amber-500/[0.06]' : 'border-slate-800 bg-slate-900/60'}`}>
    <p className="text-sm font-semibold text-slate-100 tabular-nums">{a}</p><p className="text-[10px] text-slate-500 leading-tight">{b}</p>
  </div>
);

export const ReportView: React.FC<{ result: AskResult; onSelectMemory: (id: string) => void }> = ({ result, onSelectMemory }) => {
  const rep = result.report;
  const v = useMemo(() => buildView(result), [result]);
  const cr = useMemo(() => buildClientReport(result), [result]);
  const [open, setOpen] = useState<string | null>(null);
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const toggle = (r: string) => setOpen((o) => (o === r ? null : r));
  const flip = (k: string) => setShown((s) => ({ ...s, [k]: !s[k] }));
  const opened = open ? rep.evidence.find((e) => e.ref === open) : undefined;
  const chip = (ref: string | null) => ref ? <button key={ref} onClick={() => toggle(ref)} data-cite={ref} className={`ml-0.5 px-1 rounded align-baseline font-mono text-[9px] cursor-pointer ${open === ref ? 'bg-blue-500 text-white' : 'bg-blue-500/10 text-blue-300/80 hover:bg-blue-500/30'}`}>{ref}</button> : null;
  const card = (ref: string | null) => (opened && ref && opened.ref === ref ? <EvidenceCard e={opened} onSelectMemory={onSelectMemory} /> : null);
  const sentences = (ss: Sentence[]) => ss.map((s, i) => <React.Fragment key={i}>{s.text}{s.refs.map((r) => chip(r))}{' '}</React.Fragment>);
  const evidenceList = (p: Para) => (
    <ul className="mt-2 space-y-2 border-l border-slate-700 pl-3" data-testid="para-evidence">
      {p.evidence.map((e) => (
        <li key={e.claimId} className="text-xs text-slate-400 leading-relaxed">
          <span className="font-mono text-[10px] text-slate-500">{e.date ?? 'undated'} · {e.where} · {e.who}</span>
          <p className="text-slate-300"><Quote className="inline w-3 h-3 mr-1 text-slate-600" />{e.wording}{chip(e.ref)}</p>
          {card(e.ref)}
        </li>
      ))}
    </ul>
  );
  const prose = (p: Para, key: string, big = false) => (
    <div key={key} data-testid="prose">
      <p className={`${big ? 'text-[15px] leading-8' : 'text-[14px] leading-7'} text-slate-100`}>{sentences(p.sentences)}</p>
      {p.evidence.length > 0 && (
        <button onClick={() => flip(key)} className="mt-1 text-[11px] text-slate-500 hover:text-slate-200 cursor-pointer underline underline-offset-2" aria-expanded={!!shown[key]}>
          {shown[key] ? 'Hide supporting evidence' : `Supporting evidence (${p.evidence.length} source turn${p.evidence.length === 1 ? '' : 's'})`}
        </button>
      )}
      {shown[key] && evidenceList(p)}
    </div>
  );
  const excerptList = (items: Excerpt[]) => (
    <ul className="space-y-2" data-testid="excerpts">
      {items.map((x) => (
        <li key={x.claimId} className="text-xs text-slate-400 leading-relaxed">
          <span className="font-mono text-[10px] text-slate-500">{x.date ?? 'undated'} · {x.where} · {x.who}</span>
          {x.badges.map((b) => <span key={b} className="ml-1.5 text-[10px] px-1.5 rounded bg-slate-800 text-amber-300 border border-slate-700">{b}</span>)}
          <span className="ml-1.5 text-[10px] px-1.5 rounded bg-slate-800 text-slate-500 border border-slate-700">{EXCERPT_LABEL[x.state]}</span>
          <p className="text-slate-300 mt-0.5"><Quote className="inline w-3 h-3 mr-1 text-slate-600" />{x.wording}{chip(x.ref)}</p>
          {card(x.ref)}
        </li>
      ))}
    </ul>
  );
  const sectionView = (s: PeriodSection, n: number) => (
    <section key={s.index} className="rounded-xl border border-slate-800 bg-slate-900/50 p-4" data-testid="period" data-status={s.status}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-slate-100">{n > 1 ? `${s.index + 1}. ` : ''}{s.title}</h4>
        <span className={`ml-auto text-[10px] px-2 py-0.5 rounded-full border ${STATUS_TONE[s.status]}`}>{STATUS_LABEL[s.status]}</span>
      </div>
      {s.gap && <div className="mt-2"><BannerBox b={{ tone: 'warn', title: 'Gap in this part of the history', detail: s.gap }} /></div>}
      {s.paragraphs.length > 0 && <div className="mt-2 flex flex-col gap-3" data-testid="verified-paragraphs">{s.paragraphs.map((p, i) => prose(p, `s${s.index}p${i}`))}</div>}
      {s.excerpts.length > 0 && (
        <div className="mt-3">
          <button onClick={() => flip(`ex${s.index}`)} className="text-[11px] text-slate-500 hover:text-slate-200 cursor-pointer underline underline-offset-2" aria-expanded={!!shown[`ex${s.index}`]} data-testid="further-records">
            {shown[`ex${s.index}`] ? 'Hide' : 'Show'} {s.excerpts.length} further record{s.excerpts.length === 1 ? '' : 's'} from this period (recorded wording, not written up)
          </button>
          {shown[`ex${s.index}`] && <div className="mt-2">{excerptList(s.excerpts)}</div>}
        </div>
      )}
    </section>
  );
  const c = cr.coverage;
  const notNarrated = c.relevant != null ? c.relevant - (c.narrated ?? 0) : 0;
  const summaryRefs = splitCitations(rep.summary).filter((s) => s.kind === 'cite').map((s) => s.ref!);

  return (
    <div className="flex flex-col gap-3" data-report-kind={v.completeness}>
      {/* 1. client and reporting period */}
      <header className="rounded-xl border border-slate-700 bg-slate-900/70 p-4" data-testid="report-header">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold text-slate-50">Client report · {cr.header.clientId || 'client'}</h3>
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold ${BADGE[v.completeness]}`} data-testid="report-kind">
            {v.completeness === 'complete_narrative' ? <BadgeCheck className="w-3.5 h-3.5" /> : <CircleAlert className="w-3.5 h-3.5" />}{v.label}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-400" data-testid="report-period">{cr.header.from ? <>Reporting period <span className="font-mono text-slate-200">{cr.header.from}</span> to <span className="font-mono text-slate-200">{cr.header.to}</span></> : 'No dated source turns were retrieved'} · <span className="font-mono">{cr.header.requestId}</span></p>
        <p className="mt-1 text-xs text-slate-500">Question: {rep.question}</p>
      </header>
      {v.banners.filter((b) => b.title !== 'Narrative verified').map((b, i) => <BannerBox key={i} b={b} />)}
      {v.singleClientNote && <BannerBox b={{ tone: 'info', title: 'Single-client source retrieval', detail: v.singleClientNote }} />}

      {/* 2. executive summary */}
      {cr.executive ? (
        <section className="rounded-xl border border-slate-700 bg-slate-900/70 p-4" data-testid="executive">
          <h4 className="text-sm font-semibold text-slate-100 flex items-center gap-2"><BadgeCheck className="w-4 h-4 text-emerald-400" />Executive summary</h4>
          <div className="mt-2">{prose(cr.executive, 'exec', true)}</div>
        </section>
      ) : cr.overviewVerified.length > 0 && (
        <section className="rounded-xl border border-slate-700 bg-slate-900/70 p-4" data-testid="overview">
          <h4 className="text-sm font-semibold text-slate-100">Overview</h4>
          <p className="text-[11px] text-slate-500 mt-0.5">No written executive summary passed the checks for this request; this is the record's own overview, written by code (counts and dates only).</p>
          <ul className="mt-2 space-y-1 text-sm text-slate-200 list-disc pl-5" data-testid="overview-verified">{cr.overviewVerified.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </section>
      )}

      {/* 3. chronological sections */}
      {cr.sections.length > 0 && (
        <div className="flex flex-col gap-2.5" data-testid="chronology-sections">
          <h4 className="text-sm font-semibold text-slate-200 mt-1">How the history developed</h4>
          {cr.sections.map((s) => sectionView(s, cr.sections.length))}
        </div>
      )}

      {/* 4. corrections and changes */}
      {(cr.changes.length > 0 || cr.wordedCorrections.length > 0) && (
        <Section title="Corrections and changes" count={cr.changes.length + cr.wordedCorrections.length} hint="original position, then the corrected position">
          <div data-testid="corrections">
            {cr.changes.map((p, i) => (
              <div key={i} className="mb-3 text-xs leading-relaxed rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                <p className="font-mono text-[11px] text-slate-500">{p.date ?? 'undated'} · {p.corrected.where} corrects {p.original ? p.original.where : 'an earlier record whose source turn was not retrieved'}</p>
                {p.explanation && p.explanation.length > 0 && <p className="mt-1.5 text-[14px] leading-7 text-slate-100" data-testid="correction-explanation">{sentences(p.explanation)}</p>}
                <p className="mt-1.5 text-slate-400"><span className="text-[10px] uppercase tracking-wide text-slate-500">Original position · {p.original?.who ?? 'not retrieved'}{p.original?.date ? ` · ${p.original.date}` : ''}</span><br />{p.original ? <>“{p.original.wording}”{chip(p.original.ref)}{card(p.original.ref)}</> : 'The corrected statement is not among the retrieved turns.'}</p>
                <p className="mt-1.5 text-slate-200"><span className="text-[10px] uppercase tracking-wide text-emerald-300/80">Corrected position · {p.corrected.who} · {p.corrected.date ?? 'undated'}</span><br />“{p.corrected.wording}”{chip(p.corrected.ref)}{card(p.corrected.ref)}</p>
                <p className="mt-1.5 text-[10px] text-slate-500">{p.basis === 'shared_wording' ? `Linked by the words both turns share (“${p.phrase}”), not by a stored memory.` : 'Linked by the memory layer’s own correction record.'}</p>
              </div>
            ))}
            {cr.wordedCorrections.length > 0 && (
              <>
                <p className="text-[11px] text-slate-500 mb-1.5">Statements worded as corrections that the record does not link to one specific earlier statement:</p>
                <DevList items={cr.wordedCorrections.map((x) => ({ date: x.date, where: x.where, who: x.who, wording: x.wording, ref: x.ref, badges: ['worded as a correction'] }))} open={open} onOpen={toggle} />
                {opened && cr.wordedCorrections.some((x) => x.ref === opened.ref) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
              </>
            )}
          </div>
        </Section>
      )}

      {/* 5. current position and outcome: only what verified evidence establishes */}
      {(cr.established.length > 0 || cr.header.from) && (
        <Section title="Current position and outcome" hint={cr.established.length ? 'verified and confirmed' : 'none established'}>
          <div data-testid="current-position" className="flex flex-col gap-3">
            {cr.established.length > 0 ? (
              <DevList items={cr.established.map((x) => ({ date: x.date, where: x.where, who: x.who, wording: x.wording, ref: x.ref, badges: ['verified, confirmed'] }))} open={open} onOpen={toggle} />
            ) : (
              <>
                <p className="text-sm text-slate-200 leading-relaxed">No retrieved statement is both verified and confirmed as the client’s current position, so none is stated as established.</p>
                {cr.latestUnestablished && (
                  <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.05] p-3" data-testid="latest-position">
                    <p className="text-[11px] font-semibold text-amber-200 mb-1">Latest stated position ({cr.latestUnestablished.date ?? 'undated'}) · not established as final</p>
                    {cr.latestExplanation && cr.latestExplanation.length > 0 && <p className="text-[14px] leading-7 text-slate-100 mb-1.5">{sentences(cr.latestExplanation)}</p>}
                    <DevList items={[{ date: cr.latestUnestablished.date, where: cr.latestUnestablished.where, who: cr.latestUnestablished.who, wording: cr.latestUnestablished.wording, ref: cr.latestUnestablished.ref, badges: ['not established as current'] }]} open={open} onOpen={toggle} />
                  </div>
                )}
              </>
            )}
            {cr.outcome && (cr.outcome.not_established.length > 0 || cr.outcome.established.length > 0) && (
              <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3" data-testid="outcome">
                <p className="text-[11px] font-semibold text-slate-300 mb-1">Signing and completion (written by code from the retrieved turns)</p>
                {[...cr.outcome.established, ...cr.outcome.not_established].map((x, i) => <p key={i} className="text-sm text-slate-100 leading-relaxed">{x}</p>)}
                {cr.outcome.mentions.length > 0 && (
                  <>
                    <button onClick={() => flip('outcome')} className="mt-1 text-[11px] text-slate-500 hover:text-slate-200 cursor-pointer underline underline-offset-2" aria-expanded={!!shown.outcome}>{shown.outcome ? 'Hide' : 'Show'} the {cr.outcome.mentions.length} turns that mention signing, closing or completion</button>
                    {shown.outcome && (
                      <ul className="mt-2 space-y-1.5 border-l border-slate-700 pl-3" data-testid="outcome-mentions">
                        {cr.outcome.mentions.map((m, i) => <li key={i} className="text-xs text-slate-400"><span className="font-mono text-[10px] text-slate-500">{m.date ?? 'undated'} · {m.where} · {m.speaker} · {m.kind}</span><p className="text-slate-300">“{m.excerpt}”{chip(m.ref)}</p>{card(m.ref)}</li>)}
                      </ul>
                    )}
                  </>
                )}
              </div>
            )}
            {opened && [...cr.established, ...(cr.latestUnestablished ? [cr.latestUnestablished] : [])].some((x) => x.ref === opened.ref) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
          </div>
        </Section>
      )}

      {/* 6. open questions, contradictions, uncertainties */}
      {(v.openQuestions.length > 0 || v.contradictions.length > 0) && (
        <Section title="Open questions, contradictions and uncertainties" count={v.openQuestions.length + v.contradictions.length} open={false}>
          <div data-testid="open-items" className="flex flex-col gap-3">
            {v.contradictions.length > 0 && <div><p className="text-[11px] font-semibold text-amber-200 mb-1">Contradictions</p><Bullets items={v.contradictions} tone="text-amber-200" /></div>}
            {v.openQuestions.length > 0 && <div><p className="text-[11px] font-semibold text-amber-200 mb-1">Open questions and uncertainties</p><Bullets items={v.openQuestions} tone="text-amber-200/90" /></div>}
          </div>
        </Section>
      )}

      {/* 7. supporting evidence */}
      <div className="flex flex-col gap-2" data-testid="supporting">
        <h4 className="text-sm font-semibold text-slate-200 mt-1">Supporting evidence</h4>
        {cr.memoryOnly.length > 0 && (
          <Section title="Recorded in memory only (source turn not retrieved)" count={cr.memoryOnly.length} open={false} hint="not source-verified: never stated as what was said">
            <ul className="space-y-2 text-xs text-slate-400" data-testid="memory-only">
              {cr.memoryOnly.map((m) => <li key={m.claimId}><span className="font-mono text-[11px] text-slate-500">{m.date ?? 'undated'} · memory record · {m.status}</span><p className="text-slate-300 mt-0.5">“{m.wording}”{chip(m.ref)}</p>{card(m.ref)}</li>)}
            </ul>
          </Section>
        )}
        {v.chronology.length > 0 && (
          <Section title="Code-written chronology" count={v.chronology.length} open={false} hint="strict date order; every statement quotes its source turn">
            <div className="flex flex-col gap-3 text-xs text-slate-300">{v.chronology.map((f, i) => <p key={i} className="leading-relaxed"><Cited text={f.text} open={open} onOpen={toggle} /></p>)}</div>
            {opened && v.chronology.some((f) => f.refs.includes(opened.ref)) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
          </Section>
        )}
        {v.sourceRecords.length > 0 && (
          <Section title="Source records" count={v.sourceRecords.length} open={false} hint="what each retrieved turn records, not that it is true">
            <div className="flex flex-col gap-2 text-xs text-slate-400">{v.sourceRecords.map((f, i) => <p key={i} className="leading-relaxed"><Cited text={f.text} open={open} onOpen={toggle} /></p>)}</div>
            {opened && v.sourceRecords.some((f) => f.refs.includes(opened.ref)) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
          </Section>
        )}
        {v.otherFindings.length > 0 && (
          <Section title="Other findings the verifier accepted" count={v.otherFindings.length} open={false}>
            <div className="flex flex-col gap-2 text-xs text-slate-300">{v.otherFindings.map((f, i) => <p key={i} className="leading-relaxed"><Cited text={f.text} open={open} onOpen={toggle} /></p>)}</div>
          </Section>
        )}
        {rep.financial_analysis.some((f) => f.origin === 'tool_code' || f.origin === 'rendered') && (
          <Section title="Financial analysis (tool results)" open={false}>
            <div className="flex flex-col gap-2 text-xs text-slate-300">{rep.financial_analysis.map((f, i) => <p key={i} className="leading-relaxed"><Cited text={f.text} open={open} onOpen={toggle} /></p>)}</div>
          </Section>
        )}
        {v.systemNotes.length > 0 && <Section title="Retrieval, verification and coverage notes" count={v.systemNotes.length} open={false} hint="what was not retrieved, withheld or unverified"><Bullets items={v.systemNotes} tone="text-slate-400" limit={8} /></Section>}
        {v.story.length > 0 && (
          <Section title="Unverified model summary (optional)" count={v.story.length} open={false} hint="model-written · not independently verified">
            <div className="flex flex-col gap-2 text-xs text-amber-100/90" data-testid="story">{v.story.map((f, i) => <p key={i} className="leading-relaxed"><Cited text={f.text} open={open} onOpen={toggle} /></p>)}</div>
          </Section>
        )}
        {!cr.executive && !v.summaryIsBoilerplate && rep.summary && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4" data-testid="summary">
            <h4 className="text-xs font-semibold text-slate-400 mb-1">Model summary (checked by code; descriptive only)</h4>
            <p className="text-sm text-slate-100 leading-relaxed"><Cited text={rep.summary} open={open} onOpen={toggle} /></p>
            {opened && summaryRefs.includes(opened.ref) && <EvidenceCard e={opened} onSelectMemory={onSelectMemory} />}
          </div>
        )}
        {rep.suggested_followups.length > 0 && <Section title="Suggested follow-ups" open={false}><Bullets items={rep.suggested_followups} tone="text-emerald-300" /></Section>}
        {rep.limitations.length > 0 && <Section title="Limitations" open={false}><Bullets items={rep.limitations} tone="text-slate-400" /></Section>}
        {result.agent_run && <AgentRunPanel run={result.agent_run} onSelectMemory={onSelectMemory} />}
      </div>

      {/* 8. report metadata: coverage statistics are secondary */}
      <Section title="Report metadata" hint="retrieval and coverage statistics" open={false}>
        <div data-testid="coverage" className="flex flex-col gap-2">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {c.meetingsFound != null && <Tile a={`${c.meetingsRetrieved} of ${c.meetingsFound}`} b="meetings retrieved" warn={(c.meetingsNotRetrieved ?? 0) > 0} />}
            {c.relevant != null && <Tile a={`${c.narrated} of ${c.relevant}`} b="relevant records written up" warn={notNarrated > 0} />}
            {c.windows > 0 && <Tile a={`${c.windows - c.windowsIncomplete} of ${c.windows}`} b="periods fully written up" warn={c.windowsIncomplete > 0} />}
            <Tile a={String(c.memoryOnly)} b="memory-only records (not source-verified)" warn={c.memoryOnly > 0} />
          </div>
          {notNarrated > 0 && <p className="text-[11px] text-amber-200/90" data-testid="coverage-note">{notNarrated} relevant record{notNarrated === 1 ? '' : 's'} not written up ({c.rejected} rejected by a check, {c.notCited} not used, {c.withheld} withheld by a later safeguard, {c.windowFailed} in a period that was not written); each is still available as its recorded wording.</p>}
          <p className="text-[11px] text-slate-500" data-testid="quote-share">{Math.round(cr.quoteShare * 100)}% of the written-up prose is direct quotation{c.modelCalls != null ? ` · ${c.modelCalls} narrative model calls` : ''}.</p>
          {(cr.overviewVerified.length > 0 || cr.overviewUnresolved.length > 0) && (
            <div data-testid="overview-metadata">
              <ul className="space-y-1 text-xs text-slate-300 list-disc pl-5">{cr.overviewVerified.map((x, i) => <li key={i}>{x}</li>)}</ul>
              {cr.overviewUnresolved.length > 0 && <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/[0.06] p-2" data-testid="overview-unresolved"><div className="text-[11px] font-semibold text-amber-200 mb-1">Not established</div><ul className="space-y-1 text-xs text-amber-100 list-disc pl-5">{cr.overviewUnresolved.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
            </div>
          )}
        </div>
      </Section>
      <p className="text-[10px] text-slate-600">Provenance labels are set by the backend only: written-up prose was produced by the model and checked sentence by sentence by code against the cited source turns, and again by the independent evidence verifier; “code-written” = assembled by code from the cited turn; “memory only” = a stored memory whose source turn was not retrieved. Nothing on this page is reworded in the browser.</p>
    </div>
  );
};
