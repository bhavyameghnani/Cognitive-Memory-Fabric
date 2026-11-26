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

// ---------------------------------------------------------------------------------------------------------------- Phase 4A: answer slots, facets, corrections
const ev = (o: Record<string, unknown> = {}) => ({ claim_id: 'C1', key: 'INT-002#4', date: '2026-01-02', where: 'INT-002 turn 4', speaker: "the client's own words", role: 'first_record', kind: null, excerpt: 'Lakeshore contacted me.', ref: 'E4', ...o });
const facet = (key: string, status: string, o: Record<string, unknown> = {}) => ({ key, label: key, status, statement: `${key} statement`, as_of: null, evidence: [], documents: [], ...o });
const txn = () => ({ decision: facet('decision', 'established'), signing: facet('signing', 'negative'), agreement: facet('agreement', 'referenced'), completion: facet('completion', 'unavailable'), record_through: '2026-06-30' });
const part = (role: string, status: string, o: Record<string, unknown> = {}) => ({ role, status, label: role, evidence: ev({ role }), note: null, ...o });

function with4a(extra: Record<string, unknown>): any {
  const r = result({ timeline: TL, coverage: cov(), hc: [] });
  Object.assign(r.report, extra);
  return r;
}

test('the answer block keeps its five slots in order, with the status the backend gave each', () => {
  const answer = { origin: 'code', subject_terms: ['sale'], not_applicable: [], slots: [
    { key: 'beginning', label: 'How it began', status: 'filled', reason: null, items: [{ label: 'first', status: 'filled', sentences: [{ text: 'On 2 January, Rebecca said x.', sources: ['INT-002#4'] }], evidence: [ev()], note: null }] },
    { key: 'acceptance', label: 'Accepted?', status: 'unavailable', reason: 'No retrieved turn records it.', items: [{ label: 'Accepted', status: 'unavailable', sentences: [], evidence: [], note: 'No retrieved turn records the client accepting the offer.' }] },
  ] };
  const r = buildClientReport(with4a({ answer }));
  assert.deepEqual(r.answer!.slots.map((s) => [s.key, s.status]), [['beginning', 'filled'], ['acceptance', 'unavailable']]);
  assert.deepEqual(r.answer!.slots[0].items[0].sentences, [{ text: 'On 2 January, Rebecca said x.', refs: ['E4'] }]);
  assert.match(r.answer!.slots[1].statusLabel, /no retrieved evidence/);                              // unavailable is worded as an absence of evidence, never as a negative finding
});

test('an unavailable slot and a negative slot are labelled differently', () => {
  const mk = (status: string) => ({ origin: 'code', subject_terms: [], not_applicable: [], slots: [{ key: 'acceptance', label: 't', status, reason: null, items: [] }] });
  assert.notEqual(buildClientReport(with4a({ answer: mk('negative') })).answer!.slots[0].statusLabel, buildClientReport(with4a({ answer: mk('unavailable') })).answer!.slots[0].statusLabel);
});

test('the four facets are shown separately, in a fixed order, each with its own status label', () => {
  const r = buildClientReport(with4a({ transaction: txn() }));
  assert.deepEqual(r.transaction!.map((f) => [f.key, f.status]), [['decision', 'established'], ['signing', 'negative'], ['agreement', 'referenced'], ['completion', 'unavailable']]);
  assert.match(r.transaction![2].statusLabel, /not established/);                                   // a document called signed only in a request is never "established"
  assert.match(r.transaction![3].statusLabel, /no retrieved evidence/);
});

test('a report without the Phase 4A fields still builds (older reports)', () => {
  const r = buildClientReport(result({ timeline: TL, coverage: cov(), hc: [] }));
  assert.equal(r.answer, null);
  assert.equal(r.transaction, null);
  assert.deepEqual(r.correctionRecords, []);
});

test('correction records keep the original record, what the client said and the advisor correction as three separate parts', () => {
  const rec = { date: '2026-03-24', basis: 'shared_wording', phrase: 'you had accepted the lakeshore offer',
    original: part('original', 'described', { label: 'The earlier record was not retrieved; the advisor’s later description of it', note: 'This is the advisor’s own account.', evidence: ev({ role: 'original', excerpt: 'my earlier note that you had accepted the Lakeshore offer was wrong', where: 'INT-058 turn 13', key: 'INT-058#13' }) }),
    client_said: part('client_said', 'retrieved', { evidence: ev({ role: 'client_said', excerpt: "No. I said I was inclined to accept.", where: 'INT-058 turn 4', key: 'INT-058#4', claim_id: 'C20' }) }),
    check: part('check', 'retrieved', { evidence: ev({ role: 'check', excerpt: 'my note says you had accepted. Is that correct?', where: 'INT-058 turn 3', key: 'INT-058#3', claim_id: 'C19' }) }),
    acknowledgement: null,
    correction: part('correction', 'retrieved', { evidence: ev({ role: 'correction', excerpt: 'I corrected my own record.', where: 'INT-058 turn 13', key: 'INT-058#13', claim_id: 'C21' }) }),
    missing: 'The earlier record itself was not among the retrieved evidence.' };
  const r = buildClientReport(with4a({ corrections: [rec] }));
  const x = r.correctionRecords[0];
  assert.equal(x.original.status, 'described');
  assert.notEqual(x.original.evidence!.excerpt, x.check!.evidence!.excerpt, 'the advisor’s question is never presented as the original record');
  assert.equal(x.client_said!.evidence!.role, 'client_said');
  assert.equal(x.correction.evidence!.role, 'correction');
  assert.match(x.missing ?? '', /not among the retrieved evidence/);
});

test('a correction shown inside a correction record is not listed again as an unlinked "worded as a correction" statement', () => {
  const tl = [...TL, claim({ claim_id: 'C30', modality: 'correction', interaction_id: 'INT-058', turn_index: 5, event_date: '2026-03-24', evidence_refs: ['E46'], speaker: 'Advisor', statement_kind: 'advisor_interpretation' })];
  const rec = { date: '2026-03-24', basis: 'shared_wording', phrase: 'p', original: part('original', 'unavailable'), client_said: null, check: null,
    acknowledgement: part('acknowledgement', 'retrieved', { evidence: ev({ claim_id: 'C30', role: 'acknowledgement', key: 'INT-058#5' }) }), correction: part('correction', 'retrieved'), missing: null };
  const r = result({ timeline: tl, coverage: cov(), hc: [] });
  r.report.corrections = [rec];
  assert.deepEqual(buildClientReport(r).wordedCorrections.map((w) => w.where), []);
  const r2 = result({ timeline: tl, coverage: cov(), hc: [] });
  assert.deepEqual(buildClientReport(r2).wordedCorrections.map((w) => w.where), ['INT-058 turn 5']);   // without the record it IS listed
});

test('a finding that something had not happened is labelled with its date, and the transaction slot never says "had not happened" as a whole', () => {
  const t = txn();
  (t.signing as any).as_of = '2026-03-24';
  (t.completion as any).status = 'pending';
  (t.completion as any).as_of = '2026-06-11';
  const answer = { origin: 'code', subject_terms: [], not_applicable: [], slots: [{ key: 'transaction', label: 'Signing, agreement execution and completion', status: 'negative', reason: null, items: [] }, { key: 'acceptance', label: 'a', status: 'negative', reason: null, items: [] }] };
  const r = buildClientReport(with4a({ transaction: t, answer }));
  assert.match(r.transaction![1].statusLabel, /not having happened as of 2026-03-24/);
  assert.match(r.transaction![3].statusLabel, /still to come as of 2026-06-11/);
  assert.doesNotMatch(r.answer!.slots[0].statusLabel, /had not happened/);
  assert.match(r.answer!.slots[1].statusLabel, /as of the dates shown/);
});

test('the focused "subject" slot carries the turns themselves, each with its speaker, exact wording and a promise note', () => {
  const answer = { origin: 'code', subject_terms: ['accountant'], not_applicable: ['acceptance', 'transaction'], slots: [{ key: 'subject', label: 'What the retrieved turns establish about the question', status: 'filled', reason: null, items: [
    { label: '9 April · INT-070 turn 3 · Advisor (the advisor’s interpretation or recommendation)', status: 'filled', sentences: [], evidence: [ev({ where: 'INT-070 turn 3', excerpt: 'Your accountant and I should speak before any agreement is final.', role: 'mention' })], note: null },
    { label: '9 April · INT-070 turn 11 · Advisor (the advisor’s interpretation or recommendation)', status: 'filled', sentences: [], evidence: [ev({ where: 'INT-070 turn 11', excerpt: 'From my side, I’ll arrange a call with your accountant Mei Lin Chu.', role: 'mention' })], note: 'This is what the speaker says they WILL do (a promise or a plan); no retrieved turn is cited here as showing that it was done.' }] }] };
  const r = buildClientReport(with4a({ answer }));
  assert.deepEqual(r.answer!.slots.map((s) => s.key), ['subject']);
  assert.deepEqual(r.answer!.notApplicable, ['acceptance', 'transaction']);
  assert.match(r.answer!.slots[0].items[1].note ?? '', /WILL do/);
  assert.equal(r.answer!.slots[0].items[0].evidence[0].excerpt, 'Your accountant and I should speak before any agreement is final.');
});
