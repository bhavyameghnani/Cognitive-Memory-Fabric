// Client-report view-model (Phase 2 layout, Phase 3 prose).  PURE: it arranges what the backend already produced and validated into the sections of an advisor-facing client report.  It never writes,
// rewords, infers or promotes anything:
//  - every sentence of prose comes from a finding the backend labelled: synthesis_checked (paraphrase validated proposition by proposition against the cited source turns), narrative_checked
//    (verified quotations) or the backend's own code-written overview and outcome statement;
//  - the sentences of a paragraph carry the evidence they rest on, and that evidence (the recorded wording of the cited turns) is SECONDARY: collapsed under the paragraph;
//  - a claim that is not in any validated sentence is shown as its own recorded wording, labelled as such, never paraphrased;
//  - a memory-only claim (its source turn was not retrieved) is kept apart and never shown as a source statement;
//  - a "current position" is shown only when the backend's timeline marks a claim as verified-and-confirmed (or current); otherwise the report says none is established.
import type { AskResult, Finding, NarrativeCoverage, OutcomeStatement, ReportOverview, TimelineClaim } from '../types';

export type ExcerptState = 'rejected' | 'not_cited' | 'window_failed' | 'withheld';
export interface Excerpt { claimId: string; date: string | null; where: string; who: string; wording: string; ref: string | null; state: ExcerptState; badges: string[] }
export type WindowStatus = 'ok' | 'partial' | 'empty' | 'failed' | 'skipped' | 'none';
export interface EvidenceLine { claimId: string; date: string | null; where: string; who: string; wording: string; ref: string | null }
export interface Sentence { text: string; refs: string[] }
/** One paragraph of validated prose: its sentences (plain text, citations as separate chips), the period it covers, and the evidence it rests on. `text` is the paragraph as the backend composed it. */
export interface Para { text: string; sentences: Sentence[]; from: string | null; to: string | null; evidence: EvidenceLine[]; kind: 'paraphrase' | 'quotation'; refs: string[] }
export interface PeriodSection {
  index: number; title: string; from: string | null; to: string | null; claimCount: number; narratedCount: number; status: WindowStatus; error: string | null;
  paragraphs: Para[]; excerpts: Excerpt[]; gap: string | null;
}
export interface ChangePair {
  date: string | null; basis: 'memory' | 'shared_wording'; phrase: string | null;
  original: { where: string; who: string; wording: string; ref: string | null; date: string | null } | null;
  corrected: { where: string; who: string; wording: string; ref: string | null; date: string | null };
  /** validated prose that explains the change (sentences that cite the correcting claim), or null */
  explanation: Sentence[] | null;
}
export interface PositionLine { date: string | null; where: string; who: string; wording: string; ref: string | null; note: string | null }
export interface MemoryOnlyLine { claimId: string; date: string | null; wording: string; ref: string | null; status: string }
export interface Header { clientId: string; requestId: string; from: string | null; to: string | null }
export interface CoverageSummary {
  meetingsRetrieved: number | null; meetingsFound: number | null; meetingsNotRetrieved: number | null;
  relevant: number | null; narrated: number | null; rejected: number; notCited: number; windowFailed: number; withheld: number; windows: number; windowsIncomplete: number; memoryOnly: number; modelCalls: number | null;
}
export interface ClientReport {
  header: Header; coverage: CoverageSummary; overviewVerified: string[]; overviewUnresolved: string[];
  /** the executive summary: validated prose over the whole history, or null when none passed the checks */
  executive: Para | null;
  sections: PeriodSection[]; changes: ChangePair[]; wordedCorrections: PositionLine[];
  established: PositionLine[]; latestUnestablished: PositionLine | null; latestExplanation: Sentence[] | null;
  outcome: OutcomeStatement | null;
  memoryOnly: MemoryOnlyLine[];
  /** share of the validated prose that sits inside quotation marks: a quotation dump is close to 1, a written report close to 0 */
  quoteShare: number;
}

const KIND: Record<string, string> = { client_statement: "client's own words", advisor_reported: "advisor's account of the client", advisor_interpretation: "advisor's interpretation or recommendation", unattributed: 'speaker not attributable' };
const kindLabel = (k: string) => KIND[k] ?? k.replace(/_/g, ' ');       // the same wording as reportView.ts (kept here so this module has no value import and runs under node --test)
const where = (c: TimelineClaim) => (c.interaction_id ? `${c.interaction_id} turn ${c.turn_index}` : 'stored record');
const keyOf = (c: TimelineClaim) => `${c.interaction_id}#${c.turn_index}`;
export const snippet = (s: string, n = 260) => { const t = (s ?? '').replace(/\s+/g, ' ').trim(); return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + '…'; };
const byOrder = (a: TimelineClaim, b: TimelineClaim) => (a.event_date ?? '').localeCompare(b.event_date ?? '') || (a.interaction_id ?? '').localeCompare(b.interaction_id ?? '') || (a.turn_index ?? 0) - (b.turn_index ?? 0);
const line = (c: TimelineClaim, note: string | null = null): PositionLine => ({ date: c.event_date, where: where(c), who: kindLabel(c.statement_kind), wording: snippet(c.wording), ref: c.evidence_refs[0] ?? null, note });

export function periodTitle(from: string | null, to: string | null): string {
  if (!from) return 'Undated';
  return !to || to === from ? from : `${from} to ${to}`;
}

function excerptOf(c: TimelineClaim, state: ExcerptState): Excerpt {
  const badges = [c.modality !== 'statement' ? c.modality : '', c.temporal_status !== 'historical' ? c.temporal_status : ''].filter(Boolean);
  return { claimId: c.claim_id, date: c.event_date, where: where(c), who: kindLabel(c.statement_kind), wording: snippet(c.wording), ref: c.evidence_refs[0] ?? null, state, badges };
}

/** Share of `text` (citation handles removed) that sits inside double quotation marks. */
export function quoteShareOf(text: string): number {
  const t = (text ?? '').replace(/\[E\d+\]/g, '');
  let inside = 0;
  for (const m of t.matchAll(/"([^"]*)"|“([^”]*)”/g)) inside += (m[1] ?? m[2] ?? '').length;
  return t.length === 0 ? 0 : inside / t.length;
}

function paraOf(f: Finding, byKey: Map<string, TimelineClaim>): Para {
  const sents = (f.narrative ?? []);
  const cited = new Map<string, TimelineClaim>();
  const sentences: Sentence[] = sents.length > 0
    ? sents.map((sn) => {
        const cs = sn.sources.map((k) => byKey.get(k)).filter((c): c is TimelineClaim => !!c);
        for (const c of cs) cited.set(c.claim_id, c);
        return { text: sn.text, refs: [...new Set(cs.map((c) => c.evidence_refs[0]).filter((r): r is string => !!r))] };
      })
    : [{ text: f.text, refs: f.refs }];
  const claims = [...cited.values()].sort(byOrder);
  const dates = claims.map((c) => c.event_date).filter((d): d is string => !!d).sort();
  return {
    text: f.text, sentences, from: dates[0] ?? null, to: dates[dates.length - 1] ?? null, refs: f.refs, kind: f.origin === 'synthesis_checked' ? 'paraphrase' : 'quotation',
    evidence: claims.map((c) => ({ claimId: c.claim_id, date: c.event_date, where: where(c), who: kindLabel(c.statement_kind), wording: snippet(c.wording, 420), ref: c.evidence_refs[0] ?? null })),
  };
}

function sections(findings: Finding[], tl: TimelineClaim[], cov: NarrativeCoverage | null | undefined, byKey: Map<string, TimelineClaim>): PeriodSection[] {
  const by = new Map(tl.map((c) => [c.claim_id, c]));
  if (!cov || cov.windows.length === 0) {                               // no windows (older report, fallback, or no narrative attempted): one section with whatever validated prose exists
    const dates = tl.filter((c) => c.source === 'source_turn' && c.relevant).map((c) => c.event_date).filter((d): d is string => !!d).sort();
    return findings.length === 0 ? [] : [{ index: 0, title: periodTitle(dates[0] ?? null, dates[dates.length - 1] ?? null), from: dates[0] ?? null, to: dates[dates.length - 1] ?? null, claimCount: 0, narratedCount: 0,
      status: 'none', error: null, paragraphs: findings.map((f) => paraOf(f, byKey)), excerpts: [], gap: null }];
  }
  const stateOf = new Map<string, ExcerptState>();
  for (const i of cov.rejected) stateOf.set(i, 'rejected');
  for (const i of cov.not_cited) stateOf.set(i, 'not_cited');
  for (const i of cov.window_failed) stateOf.set(i, 'window_failed');
  for (const i of cov.withheld ?? []) stateOf.set(i, 'withheld');
  const narrated = new Set(cov.narrated);
  return cov.windows.map((w) => {
    const ex = w.claim_ids.filter((i) => !narrated.has(i)).map((i) => by.get(i)).filter((c): c is TimelineClaim => !!c).sort(byOrder).map((c) => excerptOf(c, stateOf.get(c.claim_id) ?? 'not_cited'));
    const gap = w.status === 'failed' || w.status === 'skipped' || w.status === 'empty'
      ? `This period could not be written up (${w.status}${w.error ? ': ' + w.error : ''}). Its ${w.claim_ids.length} record${w.claim_ids.length === 1 ? '' : 's'} ${w.claim_ids.length === 1 ? 'is' : 'are'} listed below as recorded wording.` : null;
    return { index: w.index, title: periodTitle(w.first_date ?? null, w.last_date ?? null), from: w.first_date ?? null, to: w.last_date ?? null, claimCount: w.claim_ids.length,
      narratedCount: w.claim_ids.filter((i) => narrated.has(i)).length, status: w.status, error: w.error ?? null,
      paragraphs: findings.filter((f) => f.window === w.index).map((f) => paraOf(f, byKey)), excerpts: ex, gap };
  });
}

function changes(tl: TimelineClaim[], sentencesBySource: Map<string, Sentence[]>): ChangePair[] {
  const by = new Map(tl.map((c) => [c.claim_id, c]));
  const out: ChangePair[] = [];
  for (const c of tl.filter((x) => x.source === 'source_turn').sort(byOrder)) {
    for (const r of c.relations) {
      if (r.type !== 'corrects') continue;
      const o = by.get(r.claim_id);
      out.push({
        date: c.event_date, basis: r.basis === 'shared_wording' ? 'shared_wording' : 'memory', phrase: r.phrase ?? null,
        original: o && o.source === 'source_turn' ? { where: where(o), who: kindLabel(o.statement_kind), wording: snippet(o.wording), ref: o.evidence_refs[0] ?? null, date: o.event_date } : null,
        corrected: { where: where(c), who: kindLabel(c.statement_kind), wording: snippet(c.wording), ref: c.evidence_refs[0] ?? null, date: c.event_date },
        explanation: sentencesBySource.get(keyOf(c)) ?? null,
      });
    }
  }
  return out;
}

export function buildClientReport(r: AskResult): ClientReport {
  const rep = r.report;
  const tl = rep.timeline ?? [];
  const byKey = new Map(tl.filter((c) => c.source === 'source_turn').map((c) => [keyOf(c), c]));
  const ov: ReportOverview | null | undefined = rep.overview;
  const cov = rep.coverage ?? null;
  const prose = (rep.historical_context ?? []).filter((f) => f.origin === 'narrative_checked' || f.origin === 'synthesis_checked');
  const execF = prose.find((f) => f.window === -1);
  const verified = prose.filter((f) => f.window !== -1);
  const src = tl.filter((c) => c.source === 'source_turn' && c.relevant);
  const dates = src.map((c) => c.event_date).filter((d): d is string => !!d).sort();
  const established = tl.filter((c) => c.source === 'source_turn' && (c.confirmed_client_decision || c.temporal_status === 'current')).sort(byOrder).map((c) => line(c));
  const unestablished = tl.filter((c) => c.source === 'source_turn' && c.temporal_status === 'unresolved').sort(byOrder).pop();
  const linked = new Set(tl.filter((c) => c.relations.some((x) => x.type === 'corrects')).map((c) => c.claim_id));
  // validated sentences by the source turn they cite (to attach an explanation to a correction or to the latest position)
  const bySource = new Map<string, Sentence[]>();
  for (const f of verified) for (const sn of f.narrative ?? []) {
    const refs = [...new Set(sn.sources.map((k) => byKey.get(k)?.evidence_refs[0]).filter((x): x is string => !!x))];
    for (const k of sn.sources) bySource.set(k, [...(bySource.get(k) ?? []), { text: sn.text, refs }]);
  }
  const allText = verified.map((x) => x.text).join(' ');
  return {
    header: { clientId: ov?.client_id ?? (r.client_ids ?? []).join(', '), requestId: rep.request_id, from: ov?.first_date ?? dates[0] ?? null, to: ov?.last_date ?? dates[dates.length - 1] ?? null },
    coverage: {
      meetingsRetrieved: ov?.meetings_retrieved ?? null, meetingsFound: ov?.meetings_identified ?? null, meetingsNotRetrieved: ov?.meetings_not_retrieved ?? null,
      relevant: cov?.relevant ?? null, narrated: cov ? cov.narrated.length : null, rejected: cov?.rejected.length ?? 0, notCited: cov?.not_cited.length ?? 0, windowFailed: cov?.window_failed.length ?? 0, withheld: cov?.withheld?.length ?? 0,
      windows: cov?.windows.length ?? 0, windowsIncomplete: cov ? cov.windows.filter((w) => w.status !== 'ok').length : 0, memoryOnly: cov?.memory_only.length ?? tl.filter((c) => c.source === 'memory').length,
      modelCalls: cov?.model_calls ?? null,
    },
    overviewVerified: [...(ov?.verified ?? []), ...(ov?.narrative_verified ?? [])], overviewUnresolved: [...(ov?.unresolved ?? []), ...(ov?.narrative_unresolved ?? [])],
    executive: execF ? paraOf(execF, byKey) : null,
    sections: sections(verified, tl, cov, byKey), changes: changes(tl, bySource),
    wordedCorrections: tl.filter((c) => c.source === 'source_turn' && c.relevant && c.modality === 'correction' && !linked.has(c.claim_id)).sort(byOrder).map((c) => line(c, 'worded as a correction; not linked to one specific earlier statement')),
    established,
    latestUnestablished: established.length === 0 && unestablished ? line(unestablished, 'the latest decision-worded statement; the record does not establish it as the current position') : null,
    latestExplanation: established.length === 0 && unestablished ? bySource.get(keyOf(unestablished)) ?? null : null,
    outcome: rep.outcome ?? null,
    memoryOnly: tl.filter((c) => c.source === 'memory').sort(byOrder).map((c) => ({ claimId: c.claim_id, date: c.event_date, wording: snippet(c.wording, 220), ref: c.evidence_refs[0] ?? null, status: c.verification })),
    quoteShare: quoteShareOf(allText),
  };
}
