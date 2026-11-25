// Run with:  npm test.  Automated tests of the pure client-report view-model (Phase 2, F); they do not exercise a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildClientReport, periodTitle } from './clientReport.ts';
import { buildView } from './reportView.ts';

const claim = (o: Record<string, unknown> = {}) => ({
  claim_id: 'C1', source: 'source_turn', wording: 'I was only exploring the possibility.', speaker: 'Client', statement_kind: 'client_statement', modality: 'statement', confirmed_client_decision: false,
  verification: 'no_memory', interaction_id: 'INT-049', turn_index: 4, event_date: '2026-03-04', evidence_refs: ['E4'], temporal_status: 'historical', relevant: true, relations: [], ...o,
});
const para = (text: string, window: number) => ({ text, refs: ['E4'], origin: 'narrative_checked' as const, narrative: [{ text: 's', sources: ['INT-049#4'] }], window });
const win = (index: number, ids: string[], status = 'ok', extra: Record<string, unknown> = {}) => ({
  index, first_date: '2026-01-02', last_date: '2026-02-12', claim_ids: ids, context_ids: [], status, attempts: 1, sentences_returned: 1, sentences_accepted: 1, sentences_rejected: [], quotes_repaired: 0, duration_ms: 1, error: null, ...extra,
});
const cov = (o: Record<string, unknown> = {}) => ({ window_size: 10, relevant: 4, narrated: ['C1', 'C2'], rejected: [], not_cited: ['C3'], window_failed: ['C4'], memory_only: ['C9'], unresolved: [], model_calls: 3,
  windows: [win(0, ['C1', 'C2', 'C3']), win(1, ['C4'], 'failed', { error: 'RuntimeError: timed out', first_date: '2026-03-04', last_date: '2026-03-04' })], ...o });

function result(over: { hc?: any[]; timeline?: any[]; coverage?: any; overview?: any } = {}): any {
  return {
    request_id: 'REQ-1', client_ids: ['CLIENT-002'], retrieved_memory_ids: [], released: true, agent_run: null, harness: { outcome: 'completed', degraded_reasons: [] },
    report: { request_id: 'REQ-1', client_id: 'CLIENT-002', question: 'q', summary: 's', key_findings: [], historical_context: over.hc ?? [], financial_analysis: [], evidence: [], assumptions: [], uncertainties: [],
              limitations: [], suggested_followups: [], timeline: over.timeline ?? [], coverage: over.coverage ?? null, overview: over.overview ?? null, validation: { mode: 'llm' } },
  };
}
const TL = [claim({ claim_id: 'C1', event_date: '2026-01-02', interaction_id: 'INT-002', turn_index: 4 }), claim({ claim_id: 'C2', event_date: '2026-02-12', interaction_id: 'INT-030', turn_index: 2 }),
  claim({ claim_id: 'C3', event_date: '2026-02-12', interaction_id: 'INT-030', turn_index: 5, evidence_refs: ['E9'] }), claim({ claim_id: 'C4', event_date: '2026-03-04', interaction_id: 'INT-049', turn_index: 4, evidence_refs: ['E12'] }),
  claim({ claim_id: 'C9', source: 'memory', wording: 'Rebecca is still weighing selling.', evidence_refs: ['MEM-1'], verification: 'unverified', interaction_id: 'THR-1', turn_index: 3 })];

test('each window becomes one chronological section holding only its own verified paragraphs', () => {
  const r = buildClientReport(result({ timeline: TL, coverage: cov(), hc: [para('first part', 0), para('second part', 1)] }));
  assert.equal(r.sections.length, 2);
  assert.deepEqual(r.sections.map((s) => s.paragraphs.map((p) => p.text)), [['first part'], ['second part']]);
  assert.deepEqual(r.sections.map((s) => s.title), ['2026-01-02 to 2026-02-12', '2026-03-04']);
});

test('a window that could not be written shows an explicit gap and its claims as their recorded wording, never silently omitted', () => {
  const r = buildClientReport(result({ timeline: TL, coverage: cov(), hc: [para('first part', 0)] }));
  const bad = r.sections[1];
  assert.equal(bad.status, 'failed');
  assert.match(bad.gap ?? '', /could not be written up \(failed: RuntimeError: timed out\)\. Its 1 record is listed below as recorded wording/);
  assert.deepEqual(bad.excerpts.map((x) => [x.claimId, x.state, x.wording]), [['C4', 'window_failed', 'I was only exploring the possibility.']]);
  assert.equal(bad.narratedCount, 0);
});

test('claims of a written window that the verified prose does not use are listed with the reason, not dropped', () => {
  const r = buildClientReport(result({ timeline: TL, coverage: cov(), hc: [para('first part', 0)] }));
  const ok = r.sections[0];
  assert.deepEqual(ok.excerpts.map((x) => [x.claimId, x.state]), [['C3', 'not_cited']]);
  assert.equal(ok.narratedCount, 2);
  assert.equal(ok.gap, null);
});

test('a claim whose verified sentence was withheld later is shown as its recorded wording with that reason', () => {
  const c = cov({ narrated: ['C1'], not_cited: [], window_failed: ['C4'], withheld: ['C2', 'C3'], withheld_reasons: { C2: 'x', C3: 'x' } });
  const r = buildClientReport(result({ timeline: TL, coverage: c, hc: [] }));
  assert.deepEqual(r.sections[0].excerpts.map((x) => [x.claimId, x.state]), [['C2', 'withheld'], ['C3', 'withheld']]);
  assert.equal(r.coverage.withheld, 2);
  assert.equal(r.coverage.narrated! + r.coverage.rejected + r.coverage.notCited + r.coverage.windowFailed + r.coverage.withheld, r.coverage.relevant);
});

test('coverage counts add up and memory-only claims are kept apart from source claims', () => {
  const r = buildClientReport(result({ timeline: TL, coverage: cov(), hc: [] }));
  const c = r.coverage;
  assert.equal(c.narrated! + c.rejected + c.notCited + c.windowFailed, c.relevant);
  assert.equal(c.windowsIncomplete, 1);
  assert.equal(c.memoryOnly, 1);
  assert.deepEqual(r.memoryOnly.map((m) => [m.claimId, m.status]), [['C9', 'unverified']]);
  assert.ok(!r.sections.some((s) => s.excerpts.some((x) => x.claimId === 'C9')), 'a memory-only claim is never shown as a source excerpt');
});

test('a correction shows the original and the corrected position, and says how they are linked', () => {
  const tl = [claim({ claim_id: 'C47', wording: 'my note says you had accepted the Lakeshore offer', statement_kind: 'advisor_interpretation', turn_index: 3, relations: [{ type: 'corrected_by', claim_id: 'C56', basis: 'shared_wording', phrase: 'you had accepted the lakeshore offer' }] }),
    claim({ claim_id: 'C56', wording: 'I have corrected my own record: you are inclined to accept and have not signed', statement_kind: 'advisor_interpretation', modality: 'correction', turn_index: 13,
           evidence_refs: ['E53'], relations: [{ type: 'corrects', claim_id: 'C47', basis: 'shared_wording', phrase: 'you had accepted the lakeshore offer' }] })];
  const r = buildClientReport(result({ timeline: tl }));
  assert.equal(r.changes.length, 1);
  const p = r.changes[0];
  assert.equal(p.original?.where, 'INT-049 turn 3');
  assert.equal(p.corrected.where, 'INT-049 turn 13');
  assert.equal(p.basis, 'shared_wording');
  assert.equal(p.phrase, 'you had accepted the lakeshore offer');
  assert.deepEqual(r.wordedCorrections, [], 'a linked correction is not repeated as an unlinked one');
});

test('a memory-linked correction is labelled as such and an unlinked correction wording is listed separately', () => {
  const tl = [claim({ claim_id: 'C3', turn_index: 3, relations: [{ type: 'corrected_by', claim_id: 'C4' }] }), claim({ claim_id: 'C4', turn_index: 4, modality: 'correction', relations: [{ type: 'corrects', claim_id: 'C3' }] }),
    claim({ claim_id: 'C8', turn_index: 8, modality: 'correction', interaction_id: 'INT-055' })];
  const r = buildClientReport(result({ timeline: tl }));
  assert.equal(r.changes[0].basis, 'memory');
  assert.deepEqual(r.wordedCorrections.map((x) => x.where), ['INT-055 turn 8']);
});

test('no current position is stated unless the backend marked a claim verified and confirmed', () => {
  const none = buildClientReport(result({ timeline: [claim({ claim_id: 'C92', modality: 'decision', temporal_status: 'unresolved', wording: "I've decided. I'm going to sell." })] }));
  assert.deepEqual(none.established, []);
  assert.match(none.latestUnestablished?.note ?? '', /does not establish it as the current position/);
  const some = buildClientReport(result({ timeline: [claim({ claim_id: 'C92', modality: 'decision', temporal_status: 'current', confirmed_client_decision: true })] }));
  assert.equal(some.established.length, 1);
  assert.equal(some.latestUnestablished, null);
});

test('the overview text is passed through unchanged and the period comes from it', () => {
  const ov = { origin: 'code', client_id: 'CLIENT-002', first_date: '2026-01-02', last_date: '2026-06-30', meetings_identified: 22, meetings_retrieved: 22, meetings_not_retrieved: 0, not_retrieved: [], source_turns: 150, claims: 157,
    corrections: 1, contradictions: 0, references_unresolved: 0, memory_only_claims: 7, verified: ['Client: CLIENT-002.'], unresolved: ['7 claims rest only on a stored memory'] };
  const r = buildClientReport(result({ timeline: TL, overview: ov }));
  assert.deepEqual([r.header.from, r.header.to, r.coverage.meetingsRetrieved, r.coverage.meetingsFound], ['2026-01-02', '2026-06-30', 22, 22]);
  assert.deepEqual([r.overviewVerified, r.overviewUnresolved], [ov.verified, ov.unresolved]);
});

test('a report without windows (an older or fallback report) still shows its verified prose in one section', () => {
  const r = buildClientReport(result({ timeline: TL, hc: [para('old style', 0)] }));
  assert.equal(r.sections.length, 1);
  assert.equal(r.sections[0].status, 'none');
  assert.equal(r.sections[0].paragraphs.length, 1);
  assert.equal(buildClientReport(result({ timeline: TL })).sections.length, 0);
});

test('period titles', () => {
  assert.equal(periodTitle(null, null), 'Undated');
  assert.equal(periodTitle('2026-01-02', '2026-01-02'), '2026-01-02');
  assert.equal(periodTitle('2026-01-02', '2026-02-12'), '2026-01-02 to 2026-02-12');
});

test('verified prose that covers only part of the relevant claims is labelled partial, never complete', () => {
  const partial = buildView(result({ timeline: TL, coverage: cov(), hc: [para('first part', 0)] }));
  assert.equal(partial.completeness, 'partial_narrative');
  assert.match(partial.label, /partial coverage \(2 of 4 relevant records written up\)/);
  const full = buildView(result({ timeline: TL, coverage: cov({ narrated: ['C1', 'C2', 'C3', 'C4'], not_cited: [], window_failed: [] }), hc: [para('first part', 0)] }));
  assert.equal(full.completeness, 'complete_narrative');
});

// ---------------------------------------------------------------------------------------------------- Phase 3: prose, executive summary, explanations, outcome
const syn = (sentences: { text: string; sources: string[] }[], window: number) => ({ text: sentences.map((x) => x.text).join(' '), refs: ['E4'], origin: 'synthesis_checked' as const, narrative: sentences, window });
const TLS = [claim({ claim_id: 'C1', event_date: '2026-01-02', interaction_id: 'INT-002', turn_index: 4 }), claim({ claim_id: 'C2', event_date: '2026-03-24', interaction_id: 'INT-058', turn_index: 13, evidence_refs: ['E53'],
  modality: 'correction', relations: [{ type: 'corrects', claim_id: 'C3', basis: 'shared_wording', phrase: 'you had accepted' }] }),
  claim({ claim_id: 'C3', event_date: '2026-03-24', interaction_id: 'INT-058', turn_index: 3, evidence_refs: ['E44'], relations: [{ type: 'corrected_by', claim_id: 'C2' }] }),
  claim({ claim_id: 'C4', event_date: '2026-04-22', interaction_id: 'INT-083', turn_index: 2, evidence_refs: ['E85'], modality: 'decision', temporal_status: 'unresolved', wording: "I've decided. I'm going to sell." })];

test('the executive summary is the window -1 finding and is kept out of the chronological sections', () => {
  const hc = [syn([{ text: 'On 2 January, Rebecca said that Lakeshore had contacted her.', sources: ['INT-002#4'] }], -1), syn([{ text: 'On 2 January, Rebecca said that Lakeshore had contacted her.', sources: ['INT-002#4'] }], 0)];
  const r = buildClientReport(result({ timeline: TLS, hc, coverage: cov({ narrated: ['C1'], relevant: 4, not_cited: [], window_failed: [], windows: [win(0, ['C1', 'C2', 'C3', 'C4'])] }) }));
  assert.equal(r.executive?.sentences.length, 1);
  assert.equal(r.sections[0].paragraphs.length, 1);
  assert.equal(r.executive?.kind, 'paraphrase');
});

test('a paragraph carries its sentences as plain text, the evidence they rest on and the period they cover', () => {
  const hc = [syn([{ text: 'On 24 March, the advisor corrected his earlier note.', sources: ['INT-058#13', 'INT-058#3'] }], 0)];
  const p = buildClientReport(result({ timeline: TLS, hc, coverage: cov({ windows: [win(0, ['C1', 'C2', 'C3', 'C4'])] }) })).sections[0].paragraphs[0];
  assert.deepEqual(p.sentences[0].refs.sort(), ['E44', 'E53']);
  assert.deepEqual(p.evidence.map((e) => e.where), ['INT-058 turn 3', 'INT-058 turn 13']);
  assert.deepEqual([p.from, p.to], ['2026-03-24', '2026-03-24']);
});

test('a correction is explained by the validated sentences that cite the correcting turn, and the pair stays visible', () => {
  const hc = [syn([{ text: 'On 24 March, the advisor corrected his earlier note, which had said that Rebecca had accepted the offer.', sources: ['INT-058#13'] }], 0)];
  const r = buildClientReport(result({ timeline: TLS, hc, coverage: cov({ windows: [win(0, ['C1', 'C2', 'C3', 'C4'])] }) }));
  assert.equal(r.changes[0].explanation?.length, 1);
  assert.match(r.changes[0].explanation![0].text, /corrected his earlier note/);
  assert.equal(r.changes[0].original?.where, 'INT-058 turn 3');
  assert.equal(buildClientReport(result({ timeline: TLS })).changes[0].explanation, null);
});

test('the latest stated position gets its explanation but is never called established', () => {
  const hc = [syn([{ text: 'On 22 April, Rebecca said she had decided to sell.', sources: ['INT-083#2'] }], 0)];
  const r = buildClientReport(result({ timeline: TLS, hc, coverage: cov({ windows: [win(0, ['C1', 'C2', 'C3', 'C4'])] }) }));
  assert.deepEqual(r.established, []);
  assert.equal(r.latestUnestablished?.where, 'INT-083 turn 2');
  assert.equal(r.latestExplanation?.[0].text, 'On 22 April, Rebecca said she had decided to sell.');
});

test('the outcome statement is passed through as the backend wrote it', () => {
  const outcome = { established: [], not_established: ['No retrieved source turn establishes that the letter of intent was signed or that the sale completed.'], mentions: [{ claim_id: 'C9', date: '2026-03-24', where: 'INT-058 turn 4', speaker: "the client's own words", excerpt: "I haven't signed the letter of intent", kind: 'hedged', ref: 'E45' }] };
  const base = result({ timeline: TLS });
  base.report.outcome = outcome;
  assert.deepEqual(buildClientReport(base).outcome, outcome);
  assert.equal(buildClientReport(result({ timeline: TLS })).outcome, null);
});

test('quote share tells a written report from a quotation dump', () => {
  const dump = [syn([{ text: 'On 2026-01-02 the client said "A private equity group called Lakeshore Partners contacted me out of the blue." [E2]', sources: ['INT-002#4'] }], 0)];
  const prose = [syn([{ text: 'On 2 January, Rebecca said that a private equity group called Lakeshore Partners had contacted her.', sources: ['INT-002#4'] }], 0)];
  assert.ok(buildClientReport(result({ timeline: TLS, hc: dump })).quoteShare > 0.5);
  assert.equal(buildClientReport(result({ timeline: TLS, hc: prose })).quoteShare, 0);
});
