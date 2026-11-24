// Run with:  npm test   (node --test, no extra dependencies).  These are AUTOMATED tests of the pure view-model; they do not exercise a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildView, describeFailure, splitCitations, splitUncertainties } from './reportView.ts';

const claim = (o: Record<string, unknown> = {}) => ({
  claim_id: 'C1', source: 'source_turn', wording: 'I was only exploring the possibility.', speaker: 'Client', statement_kind: 'client_statement', modality: 'tentative', confirmed_client_decision: false,
  verification: 'unverified', interaction_id: 'INT-049', turn_index: 4, event_date: '2026-03-04', evidence_refs: ['E4'], temporal_status: 'historical', relevant: true, relations: [], ...o,
});
const story = (text: string, refs: string[]) => ({ text, refs, origin: 'rendered' as const });
const narr = (text: string, refs: string[], n = 1) => ({ text, refs, origin: 'narrative_checked' as const, narrative: Array.from({ length: n }, () => ({ text: 's', sources: ['INT-049#4'] })) });

function result(over: { hc?: any[]; timeline?: any[]; validation?: any; harness?: any; clients?: string[]; unc?: string[]; summary?: string; mem?: any } = {}): any {
  return {
    request_id: 'REQ-1', client_ids: over.clients ?? ['CLIENT-002'], retrieved_memory_ids: [], released: true, agent_run: over.mem ? { memory: over.mem } : null,
    harness: over.harness ?? { outcome: 'completed', degraded_reasons: [] },
    report: { request_id: 'REQ-1', client_id: 'CLIENT-002', question: 'q', summary: over.summary ?? 'A summary.', key_findings: [], historical_context: over.hc ?? [], financial_analysis: [], evidence: [],
              assumptions: [], uncertainties: over.unc ?? [], limitations: [], suggested_followups: [], timeline: over.timeline ?? [claim()], validation: over.validation ?? { mode: 'llm' } },
  };
}
const STORY = 'Later, in meeting INT-049 (2026-03-04), in turn 4, the client said (the client\'s own words): "x" [E4].';

test('a verified narrative with a healthy harness is the only thing called complete', () => {
  const v = buildView(result({ hc: [narr('On 2026-03-04 the client said "x" (INT-049 turn 4, recorded speaker Client) [E4].', ['E4'], 2), story(STORY, ['E4'])], validation: { mode: 'llm', narrative: { attempted: true, sentences_returned: 3, paragraphs_accepted: 1 } } }));
  assert.equal(v.completeness, 'complete_narrative');
  assert.equal(v.narrativeState, 'verified');
  assert.equal(v.narrative.length, 1);
  assert.equal(v.chronology.length, 1);
  assert.ok(v.narratedRefs.has('E4'), 'the chronology paragraph can be marked as already narrated');
  assert.match(v.banners.map((b) => b.title).join('|'), /Narrative verified/);
  assert.match(v.banners[0].detail, /2 of 3/);
});

test('a degraded request is never presented as a complete narrative, even when a narrative exists', () => {
  const v = buildView(result({ hc: [narr('t [E4].', ['E4'])], harness: { outcome: 'degraded', degraded_reasons: ['partial_response'] } }));
  assert.equal(v.completeness, 'degraded');
  assert.notEqual(v.completeness, 'complete_narrative');
  assert.equal(v.banners[0].tone, 'warn');
  assert.match(v.banners[0].detail, /output limit/);
});

test('a deterministic fallback says plainly that no model wrote it and that no narrative was generated', () => {
  const v = buildView(result({ hc: [story(STORY, ['E4'])], validation: { mode: 'deterministic_model_failure' }, harness: { outcome: 'degraded', degraded_reasons: ['model_call_timeout', 'report_agent_fallback'] } }));
  assert.equal(v.completeness, 'fallback');
  assert.equal(v.banners[0].tone, 'bad');
  assert.match(v.banners[0].detail, /did not answer in time/);
  assert.match(v.banners[0].detail, /Nothing below was written by the language model/);
  assert.equal(v.narrative.length, 0);
});

test('a narrative that failed verification is explained, with the codes and without any rejected text', () => {
  const v = buildView(result({ hc: [story(STORY, ['E4'])], validation: { mode: 'llm', narrative: { attempted: true, sentences_returned: 4, paragraphs_accepted: 0, sentences_rejected: [{ paragraph: 0, sentence: 0, problems: ['quote_not_verbatim', 'decision_language'] }] } } }));
  assert.equal(v.completeness, 'chronology_only');
  assert.equal(v.narrativeState, 'failed');
  const b = v.banners.find((x) => /No model-written narrative/.test(x.title))!;
  assert.match(b.detail, /wrote 4 sentences but none was accepted/);
  assert.match(b.detail, /quote not verbatim/);
  assert.match(b.detail, /code-written chronology/);
});

test('a narrative call that errored is explained', () => {
  const v = buildView(result({ hc: [story(STORY, ['E4'])], validation: { mode: 'llm', narrative: { attempted: true, error: 'the narrative call failed; the deterministic chronology stands' } } }));
  assert.equal(v.narrativeState, 'failed');
  assert.match(v.banners.find((x) => /No model-written narrative/.test(x.title))!.detail, /narrative call failed/);
});

test('memory-only answers and multi-client requests are labelled, and multi-client never claims source retrieval', () => {
  const m = buildView(result({ timeline: [claim({ source: 'memory' })], validation: { mode: 'llm', narrative: { attempted: false } } }));
  assert.equal(m.completeness, 'memory_only');
  assert.equal(m.glance, null);
  assert.match(m.banners[0].detail, /memory records and tool results only/);
  const two = buildView(result({ clients: ['CLIENT-001', 'CLIENT-002'], timeline: [], validation: { mode: 'llm' } }));
  assert.match(two.banners[0].detail, /more than one client/i);
  assert.match(two.banners[0].detail, /single-client only/);
  assert.ok(two.singleClientNote);
});

test('an unverified record never becomes a "current understanding"', () => {
  assert.equal(buildView(result({ timeline: [claim({ modality: 'decision', verification: 'unverified' })] })).current.length, 0);
  const ok = buildView(result({ timeline: [claim({ modality: 'decision', confirmed_client_decision: true, verification: 'verified', temporal_status: 'current' })] }));
  assert.equal(ok.current.length, 1);
});

test('developments, corrections and contradictions come from recorded relations only', () => {
  const tl = [claim({ claim_id: 'C1', turn_index: 3, wording: 'I understood you wanted 60.', statement_kind: 'advisor_interpretation', temporal_status: 'corrected', relations: [{ type: 'corrected_by', claim_id: 'C2' }] }),
              claim({ claim_id: 'C2', turn_index: 4, wording: 'I was only exploring.', relations: [{ type: 'corrects', claim_id: 'C1' }, { type: 'contradicts', claim_id: 'C3' }] }),
              claim({ claim_id: 'C3', turn_index: 9, wording: 'We decided on 60.', interaction_id: 'INT-090' })];
  const v = buildView(result({ timeline: tl }));
  assert.equal(v.corrections.length, 1);
  assert.match(v.corrections[0].original, /wanted 60/);
  assert.match(v.corrections[0].correction, /only exploring/);
  assert.equal(v.contradictions.length, 1);
  assert.ok(v.developments.length >= 2);
  assert.equal(buildView(result({ timeline: [claim()] })).corrections.length, 0, 'no relation, no correction pair');
});

test('a correction whose original turn was not retrieved is stated as such, without inventing the original', () => {
  const tl = [claim({ claim_id: 'C2', relations: [{ type: 'corrects', claim_id: 'C27' }] }), claim({ claim_id: 'C27', source: 'memory', interaction_id: 'INT-027', turn_index: 8 })];
  const c = buildView(result({ timeline: tl })).corrections[0];
  assert.match(c.original, /not retrieved/);
  assert.doesNotMatch(c.original + c.originalWhere, /INT-027/);
});

test('the backend\'s listing summary is recognised so it can be shown as a technical note, not as the headline', () => {
  assert.equal(buildView(result({ summary: '83 synthetic source records retrieved; first 6 of 83 listed: INT-001 turn 1 ...' })).summaryIsBoilerplate, true);
  assert.equal(buildView(result({ summary: 'Kenji decided on 63.' })).summaryIsBoilerplate, false);
});

test('citations are split into text and chips, keeping meeting, turn and speaker', () => {
  const s = splitCitations('On 2026-03-04 the client said "x" (INT-049 turn 4, recorded speaker Client) [E4]. Then "y" [E5].');
  assert.deepEqual(s.filter((x) => x.kind === 'cite').map((x) => [x.ref, x.meta]), [['E4', 'INT-049 turn 4 · Client'], ['E5', undefined]]);
  assert.equal(s.map((x) => (x.kind === 'text' ? x.text : '')).join('').includes('[E'), false);
  assert.equal(splitCitations('no citation here').length, 1);
});

test('failures are described honestly: cancelled vs timed out vs refused vs unreachable', () => {
  assert.match(describeFailure({ name: 'AbortError' }, 30, 1620).title, /cancelled/i);
  assert.match(describeFailure({ name: 'AbortError' }, 1620, 1620).title, /timed out/i);
  assert.match(describeFailure({ status: 403, message: 'client_not_in_scope' }, 1, 1620).detail, /no model was called/);
  assert.match(describeFailure({ status: 401 }, 1, 1620).title, /authenticated/i);
  assert.match(describeFailure({ status: 503, message: 'LLMError: down' }, 5, 1620).detail, /Ollama/);
  assert.match(describeFailure({ message: 'Failed to fetch' }, 400, 1620).detail, /server may still be working/);
});

test("uncertainties are split into what to act on and the system's own bookkeeping, and nothing is dropped", () => {
  const items = ['Unresolved (CLIENT-002): she is still weighing the sale.', 'Missing information: Not retrieved: INT-002', 'Withheld unsupported statement (key findings): [text not shown]',
                 '[E73] business_sale 20000000: unverified - needs confirmation', 'Advisor-reported, not confirmed by the client [E84]: 2026-03-24', '60 of 89 evidence items were not shown to the model because of its context budget',
                 'The exact timeline of the acceptance is not confirmed.', 'Tool get_source_evidence (EX-1) did not return a result: rejected'];
  const { open, system } = splitUncertainties(items);
  assert.equal(open.length + system.length, items.length);
  assert.deepEqual(open, [items[0], items[4], items[6]]);
  assert.equal(system.length, 5);
  assert.deepEqual(buildView(result({ unc: items })).openQuestions, open);
});

test('the story is a separate, unverified list: it never changes the completeness label, and its absence is explained', () => {
  const withStory = result({ hc: [narr('t [E4].', ['E4'])], validation: { mode: 'llm', narrative: { attempted: true, sentences_returned: 1 }, story: { attempted: true, paragraphs_accepted: 1 } } });
  withStory.report.story = [{ text: 'In March Kenji said he was only exploring. [E4]', refs: ['E4'] }];
  const v = buildView(withStory);
  assert.equal(v.story.length, 1);
  assert.equal(v.storyNote, null);
  assert.equal(v.completeness, 'complete_narrative', 'the story neither raises nor lowers the status');
  assert.equal(v.narrative.length, 1, 'the verified narrative is still listed on its own');
  const failed = buildView(result({ hc: [narr('t [E4].', ['E4'])], validation: { mode: 'llm', narrative: { attempted: true, sentences_returned: 1 }, story: { attempted: true, error: 'the story call failed' } } }));
  assert.equal(failed.story.length, 0);
  assert.match(failed.storyNote ?? '', /could not be produced/);
  const stripped = buildView(result({ hc: [narr('t [E4].', ['E4'])], validation: { mode: 'llm', narrative: { attempted: true, sentences_returned: 1 }, story: { attempted: true, paragraphs_accepted: 0 } } }));
  assert.match(stripped.storyNote ?? '', /removed every sentence/);
  assert.equal(buildView(result({})).storyNote, null, 'no story attempted, nothing to explain');
});

test('the story says how much of the history it covers, so a story that stops early is not read as the whole history', () => {
  const r = result({ timeline: [claim({ claim_id: 'C1', interaction_id: 'INT-032', event_date: '2026-02-12' }), claim({ claim_id: 'C2', interaction_id: 'INT-035', event_date: '2026-02-17', evidence_refs: ['E5'] }),
                                claim({ claim_id: 'C3', interaction_id: 'INT-083', event_date: '2026-04-22', evidence_refs: ['E9'] })], hc: [narr('t [E4].', ['E4'])] });
  r.report.evidence = [{ ref: 'E4', interaction_id: 'INT-032', event_date: '2026-02-12' }, { ref: 'E5', interaction_id: 'INT-035', event_date: '2026-02-17' }];
  r.report.story = [{ text: 'Rebecca said X. [E4]', refs: ['E4'] }, { text: 'Rebecca said Y. [E5]', refs: ['E5'] }];
  const v = buildView(r);
  assert.deepEqual(v.storyCoverage, { meetings: 2, of: 3, from: '2026-02-12', to: '2026-02-17' });
  assert.equal(buildView(result({})).storyCoverage, null);
});
