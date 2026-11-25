// View-model for a report returned by POST /api/ask.  PURE: it only reorganises what the backend already verified (nothing is generated, reworded or promoted here).
// The point of this module is honesty about WHAT KIND of report the reader has: a verified model narrative, a code-written chronology, a degraded or fallback report, or memory only.
import type { AskResult, EvidenceItem, Finding, TimelineClaim } from '../types';

export const STORY_START = ['In meeting', 'Later, in meeting', 'Continuing in meeting', 'On the same day, in meeting'];

export type Completeness = 'complete_narrative' | 'partial_narrative' | 'chronology_only' | 'degraded' | 'fallback' | 'memory_only';
export type NarrativeState = 'verified' | 'failed' | 'not_attempted';
export type Tone = 'ok' | 'warn' | 'info' | 'bad';

export interface Banner { tone: Tone; title: string; detail: string }
export interface Segment { kind: 'text' | 'cite'; text: string; meta?: string; ref?: string }
export interface Development { date: string | null; where: string; who: string; wording: string; badges: string[]; ref: string | null }
export interface CorrectionPair { original: string; originalWhere: string; correction: string; correctionWhere: string; date: string | null }
export interface Glance { claims: number; meetings: number; from: string | null; to: string | null; clientStatements: number; advisorStatements: number; tentative: number; corrections: number; decisions: number; unresolved: number }

export interface ReportView {
  completeness: Completeness;
  label: string;
  banners: Banner[];
  narrativeState: NarrativeState;
  narrative: Finding[];
  story: Finding[];
  storyNote: string | null;
  /** How much of the history the story covers: it is built from a handful of lines and can stop well before the last meeting, so the reader is told. */
  storyCoverage: { meetings: number; of: number; from: string | null; to: string | null } | null;
  chronology: Finding[];
  narratedRefs: Set<string>;
  sourceRecords: Finding[];
  otherFindings: Finding[];
  glance: Glance | null;
  developments: Development[];
  corrections: CorrectionPair[];
  correctionStatements: Development[];
  contradictions: string[];
  current: { text: string; where: string; date: string | null; ref: string | null }[];
  uncertainties: string[];
  openQuestions: string[];
  systemNotes: string[];
  singleClientNote: string | null;
  summaryIsBoilerplate: boolean;
}

const KIND: Record<string, string> = { client_statement: "client's own words", advisor_reported: "advisor's account of the client", advisor_interpretation: "advisor's interpretation or recommendation", unattributed: 'speaker not attributable' };
export const kindLabel = (k: string) => KIND[k] ?? k.replace(/_/g, ' ');

const REASONS: Record<string, string> = {
  model_call_timeout: 'the language model did not answer in time',
  provider_timeout: 'the language model did not answer in time',
  partial_response: 'the language model stopped at its output limit before finishing',
  report_agent_fallback: 'the report model did not finish, so the report was assembled by code instead',
  max_llm_calls: 'the request reached its limit on model calls',
  max_runtime_seconds: 'the request reached its time limit',
  max_total_tokens: 'the request reached its token limit',
  max_tool_calls: 'the request reached its limit on tool calls',
};
export const reasonText = (r: string) => REASONS[r] ?? r.replace(/_/g, ' ');

/** Split report text into plain text and citation chips: "(INT-049 turn 4, recorded speaker Client) [E4]" -> a chip carrying the meeting/turn/speaker and the evidence handle. */
export function splitCitations(text: string): Segment[] {
  const out: Segment[] = [];
  const rx = /(?:\s*\(([^()]*recorded speaker[^()]*)\))?\s*\[(E\d+)\]/g;
  let last = 0;
  for (let m = rx.exec(text); m; m = rx.exec(text)) {
    if (m.index > last) out.push({ kind: 'text', text: text.slice(last, m.index) });
    out.push({ kind: 'cite', text: m[2], meta: m[1]?.replace(/,\s*recorded speaker\s*/i, ' · ').replace(/\s+turn\s+/i, ' turn '), ref: m[2] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

const isStory = (f: Finding) => f.origin === 'rendered' && STORY_START.some((s) => f.text.startsWith(s));
const where = (c: TimelineClaim) => (c.interaction_id ? `${c.interaction_id} turn ${c.turn_index}` : 'stored record');
const sourceTurns = (tl: TimelineClaim[]) => tl.filter((c) => c.source === 'source_turn');
const snippet = (s: string, n = 240) => { const t = s.replace(/\s+/g, ' ').trim(); return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + '…'; };

function glance(tl: TimelineClaim[]): Glance | null {
  const c = sourceTurns(tl).filter((x) => x.relevant);
  if (c.length === 0) return null;
  const dates = c.map((x) => x.event_date).filter((d): d is string => !!d).sort();
  return {
    claims: c.length, meetings: new Set(c.map((x) => x.interaction_id)).size, from: dates[0] ?? null, to: dates[dates.length - 1] ?? null,
    clientStatements: c.filter((x) => x.statement_kind === 'client_statement').length,
    advisorStatements: c.filter((x) => x.statement_kind === 'advisor_reported' || x.statement_kind === 'advisor_interpretation').length,
    tentative: c.filter((x) => x.modality === 'tentative' || x.modality === 'hypothetical').length,
    corrections: c.filter((x) => x.relations.some((r) => r.type === 'corrected_by')).length,
    decisions: c.filter((x) => x.confirmed_client_decision || (x.modality === 'decision' && x.statement_kind === 'client_statement')).length,
    unresolved: c.filter((x) => x.temporal_status === 'unresolved' || x.temporal_status === 'disputed').length,
  };
}

function developments(tl: TimelineClaim[]): Development[] {
  const topical = (c: TimelineClaim & { mentions_topic?: boolean | null; memory_refs?: string[] }) => !!(c.mentions_topic || (c.memory_refs && c.memory_refs.length));
  const picked = sourceTurns(tl).filter((c) => c.relevant && (
    c.relations.length > 0 || c.temporal_status === 'current' || c.temporal_status === 'corrected' || c.temporal_status === 'superseded' || c.confirmed_client_decision ||
    (['decision', 'correction', 'withdrawn'].includes(c.modality) && topical(c))));
  return picked.sort((a, b) => (a.event_date ?? '').localeCompare(b.event_date ?? '') || (a.interaction_id ?? '').localeCompare(b.interaction_id ?? '') || (a.turn_index ?? 0) - (b.turn_index ?? 0)).slice(0, 12).map((c) => ({
    date: c.event_date, where: where(c), who: kindLabel(c.statement_kind), wording: snippet(c.wording), ref: c.evidence_refs[0] ?? null,
    badges: [c.modality !== 'statement' ? c.modality : '', c.temporal_status !== 'historical' ? c.temporal_status : '', c.confirmed_client_decision ? 'verified, confirmed client decision' : ''].filter(Boolean),
  }));
}

function corrections(tl: TimelineClaim[]): CorrectionPair[] {
  const by = new Map(tl.map((c) => [c.claim_id, c]));
  const out: CorrectionPair[] = [];
  for (const c of sourceTurns(tl)) {
    for (const r of c.relations) {
      if (r.type !== 'corrects') continue;
      const o = by.get(r.claim_id);
      out.push({ correction: snippet(c.wording, 200), correctionWhere: where(c), date: c.event_date,
                 original: o && o.source === 'source_turn' ? snippet(o.wording, 200) : 'an earlier stored record whose source turn was not retrieved', originalWhere: o && o.source === 'source_turn' ? where(o) : 'not retrieved' });
    }
  }
  return out.slice(0, 8);
}

/** Source turns the classifier marked as worded as a correction, for the cases where the record does not link them to one specific earlier statement. */
function correctionStatements(tl: TimelineClaim[]): Development[] {
  return sourceTurns(tl).filter((c) => c.relevant && c.modality === 'correction' && c.relations.length === 0)
    .sort((a, b) => (a.event_date ?? '').localeCompare(b.event_date ?? '') || (a.turn_index ?? 0) - (b.turn_index ?? 0)).slice(0, 8)
    .map((c) => ({ date: c.event_date, where: where(c), who: kindLabel(c.statement_kind), wording: snippet(c.wording), badges: ['worded as a correction'], ref: c.evidence_refs[0] ?? null }));
}

function contradictions(r: AskResult, tl: TimelineClaim[]): string[] {
  const out: string[] = [];
  const by = new Map(tl.map((c) => [c.claim_id, c]));
  for (const c of sourceTurns(tl)) for (const rel of c.relations) if (rel.type === 'contradicts') {
    const o = by.get(rel.claim_id);
    out.push(`${where(c)} conflicts with ${o ? where(o) : 'another record'} and the conflict is unresolved.`);
  }
  for (const s of r.agent_run?.memory?.contradictions ?? []) out.push(String(s));
  for (const c of (r.harness?.conflicts ?? []) as unknown[]) {
    const o = c as Record<string, unknown>;
    out.push(typeof c === 'string' ? c : String(o.summary ?? o.description ?? o.detail ?? o.reason ?? 'a temporal conflict was flagged (see the trace)'));
  }
  return [...new Set(out)];
}

/** Separate what a reader should act on (unresolved items, advisor statements the client never confirmed, claims without a verified current position) from the system's own bookkeeping
 *  (what was not retrieved, what the verifier withheld, memories that are unverified).  Nothing is dropped: both lists are shown, the second one collapsed. */
const SYSTEM_NOTE = /^(Missing information|Tool \S+ |CLIENT-\d+: \d+ stored|\d+ of \d+ evidence items|\d+ source turns were retrieved|\d+ timeline claims|Withheld |\[E\d+\] |No model-written narrative|Unverified memory without)/;
export function splitUncertainties(items: string[]): { open: string[]; system: string[] } {
  const open: string[] = [], system: string[] = [];
  for (const s of items) (SYSTEM_NOTE.test(s) ? system : open).push(s);
  return { open, system };
}

export function buildView(r: AskResult): ReportView {
  const rep = r.report;
  const tl = rep.timeline ?? [];
  const hc = rep.historical_context ?? [];
  const narrative = hc.filter((f) => (f.origin === 'narrative_checked' || f.origin === 'synthesis_checked') && f.window !== -1);
  const chronology = hc.filter(isStory);
  const sourceRecords = hc.filter((f) => f.origin === 'rendered' && !isStory(f));
  const otherFindings = [...rep.key_findings.filter((f) => f.origin !== 'rendered'), ...hc.filter((f) => !f.origin), ...rep.financial_analysis.filter((f) => f.origin !== 'rendered' && f.origin !== 'tool_code')];
  const story = rep.story ?? [];
  const sstat = rep.validation.story;
  const storyNote = story.length > 0 ? null : sstat?.attempted ? (sstat.error ? 'A readable story could not be produced this time (the model call failed). The verified narrative and chronology below are unaffected.' : 'A readable story was attempted but code removed every sentence (each added something the record does not say), so none is shown.') : null;
  const sev = story.flatMap((f) => f.refs).map((r) => rep.evidence.find((e) => e.ref === r)).filter((e): e is EvidenceItem => !!e);
  const sdates = sev.map((e) => e.event_date).filter((d): d is string => !!d).sort();
  const gl = glance(tl);
  const storyCoverage = story.length > 0 ? { meetings: new Set(sev.map((e) => e.interaction_id).filter(Boolean)).size, of: gl?.meetings ?? 0, from: sdates[0] ?? null, to: sdates[sdates.length - 1] ?? null } : null;
  const narratedRefs = new Set(narrative.flatMap((f) => f.refs));
  const nstat = rep.validation.narrative;
  const mode = String(rep.validation.mode ?? '');
  const reasons = r.harness?.degraded_reasons ?? [];
  const fallback = mode.startsWith('deterministic');
  const degraded = !!(r.harness?.outcome === 'degraded' || reasons.length);
  const hasSource = sourceTurns(tl).length > 0;
  const multi = (r.client_ids ?? []).length > 1;
  const narrativeState: NarrativeState = narrative.length > 0 ? 'verified' : nstat?.attempted ? 'failed' : 'not_attempted';

  let completeness: Completeness;
  if (fallback) completeness = 'fallback';
  else if (degraded) completeness = 'degraded';
  else if (narrativeState === 'verified') completeness = rep.coverage && rep.coverage.narrated.length < rep.coverage.relevant ? 'partial_narrative' : 'complete_narrative';     // verified prose that covers only part of the relevant claims is never called complete
  else if (hasSource) completeness = 'chronology_only';
  else completeness = 'memory_only';
  const label = { complete_narrative: 'Evidence-checked report', partial_narrative: `Evidence-checked report · partial coverage (${rep.coverage?.narrated.length ?? 0} of ${rep.coverage?.relevant ?? 0} relevant records written up)`, chronology_only: 'Chronology only (no verified narrative)', degraded: 'Partial report (degraded)', fallback: 'Fallback report (not model-written)', memory_only: 'Memory-based answer (no source turns)' }[completeness];

  const banners: Banner[] = [];
  if (fallback) {
    banners.push({ tone: 'bad', title: 'The report model did not complete; this is the code-assembled fallback',
      detail: `${reasons.length ? 'Cause: ' + reasons.map(reasonText).join('; ') + '. ' : ''}Nothing below was written by the language model; every line is assembled by code from retrieved evidence. No narrative was generated.` });
  } else if (degraded) {
    banners.push({ tone: 'warn', title: 'Partial result', detail: `This request ran in degraded mode: ${reasons.map(reasonText).join('; ') || 'see the harness record'}. Treat the report as incomplete.` });
  }
  if (narrativeState === 'verified') {
    const n = nstat?.sentences_returned ?? 0, acc = narrative.reduce((s, f) => s + (f.narrative?.length ?? 0), 0);
    banners.push({ tone: 'ok', title: 'Narrative verified', detail: `${acc} of ${n || acc} model-written sentence${acc === 1 ? '' : 's'} passed code checks and the independent evidence verifier (each quotes its source turn, speaker and meeting). Sentences that did not pass were removed, not reworded.` });
  } else if (narrativeState === 'failed') {
    const codes = [...new Set((nstat?.sentences_rejected ?? []).flatMap((x) => x.problems))].slice(0, 5);
    banners.push({ tone: 'warn', title: 'No model-written narrative passed verification',
      detail: `${nstat?.error ? nstat.error + '. ' : ''}${nstat?.sentences_returned ? `The model wrote ${nstat.sentences_returned} sentence${nstat.sentences_returned === 1 ? '' : 's'} but none was accepted${codes.length ? ' (' + codes.join(', ').replace(/_/g, ' ') + ')' : ''}. ` : ''}The code-written chronology is shown instead; every statement in it quotes its source turn.` });
  } else if (!fallback) {
    banners.push({ tone: 'info', title: 'No narrative was attempted', detail: multi ? 'This request named more than one client. Source-turn retrieval, chronology and narrative are single-client only; this answer is built from memory records and tool results.' :
      hasSource ? 'No narrative was requested for this question.' : 'Narrative needs a single-client history or chronology question whose conversation turns were retrieved. This answer rests on memory records and tool results only.' });
  } else if (!multi) {
    banners.push({ tone: 'info', title: 'Narrative skipped', detail: 'Narrative generation is skipped when the report model itself fails.' });
  }
  if (multi && narrativeState !== 'not_attempted') banners.push({ tone: 'info', title: 'More than one client', detail: 'Source-turn chronology is single-client only.' });

  const cur = sourceTurns(tl).filter((c) => c.confirmed_client_decision || c.temporal_status === 'current').map((c) => ({ text: snippet(c.wording, 260), where: where(c), date: c.event_date, ref: c.evidence_refs[0] ?? null }));
  const unc = rep.uncertainties ?? [];
  return {
    completeness, label, banners, narrativeState, narrative, story, storyNote, storyCoverage, chronology, narratedRefs, sourceRecords, otherFindings,
    openQuestions: splitUncertainties(unc).open, systemNotes: splitUncertainties(unc).system,
    glance: gl, developments: developments(tl), corrections: corrections(tl), correctionStatements: correctionStatements(tl), contradictions: contradictions(r, tl), current: cur, uncertainties: unc,
    singleClientNote: multi ? 'Source-turn retrieval works for one client per request; for several clients each is answered from memory only.' : null,
    summaryIsBoilerplate: /^\d+ synthetic source records retrieved/.test(rep.summary ?? ''),
  };
}

export const evidenceFor = (rep: { evidence: EvidenceItem[] }, ref: string) => rep.evidence.find((e) => e.ref === ref);

/** Human message for a failed /api/ask call.  `kind` distinguishes what the browser knows: it cannot tell a slow server from a dead one except by how the request ended. */
export function describeFailure(e: { status?: number; message?: string; name?: string }, elapsedS: number, limitS: number): { title: string; detail: string } {
  if (e.name === 'AbortError') return elapsedS >= limitS ? { title: 'The request timed out in the browser', detail: `No answer arrived within ${Math.round(limitS / 60)} minutes. The server may still be working; it will finish (and be traced) on its own, but this page stopped waiting.` }
    : { title: 'Request cancelled', detail: 'You stopped waiting. The backend cannot be cancelled from the browser and may finish the request on its own.' };
  if (e.status === 401) return { title: 'Not authenticated', detail: 'The API refused the request. In this local setup the API must run with HARNESS_PROFILE=development on loopback.' };
  if (e.status === 403) return { title: 'Not permitted', detail: `${e.message ?? ''} Your identity may not read this client; nothing was retrieved and no model was called.`.trim() };
  if (e.status === 503) return { title: 'The backend could not complete the request', detail: `${e.message ?? ''} Usually the local language model (Ollama) is not running, was too slow, or the request exceeded its budget.`.trim() };
  if (e.status && e.status >= 500) return { title: 'Server error', detail: e.message ?? `HTTP ${e.status}` };
  return { title: 'Could not reach the backend', detail: `${e.message ?? 'The connection failed'}. If the question was already running, the server may still be working on it; the connection can be dropped by a browser or proxy after several minutes without a response.` };
}
