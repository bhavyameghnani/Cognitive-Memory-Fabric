import { useEffect, useState } from 'react';
import {
  ArrowDown, ArrowLeft, ArrowRight, Bot, Brain, Database, FileCheck, FileText, Filter, GitMerge, KeyRound, Landmark, Layers, Lock, Mail,
  MessageSquare, Monitor, Phone, Route, Scale, ScrollText, Search, ShieldCheck, Shuffle, Sparkles, UserCheck, Users, Workflow, Wrench, X,
} from 'lucide-react';
import { TYPES } from '../lib/memory';

/* One-page architecture explainer.  Every box is clickable and opens a panel with what it does, why it exists and where the code lives.
   Content mirrors README.md, docs/harness.md, docs/llm_first_ingestion.md and docs/evidence_layer_design.md: keep them in sync. */

type Kind = 'source' | 'code' | 'llm' | 'store' | 'human';
type Path = 'write' | 'read';
interface Node { title: string; sub: string; icon: typeof Users; kind: Kind; paths: Path[]; what: string; points: string[]; code: string[] }

const KIND: Record<Kind, { label: string; color: string }> = {
  source: { label: 'Raw data', color: '#94a3b8' },
  code: { label: 'Deterministic code', color: '#34d399' },
  llm: { label: 'LLM step', color: '#a78bfa' },
  store: { label: 'Database', color: '#60a5fa' },
  human: { label: 'Human in the loop', color: '#fb7185' },
};

const N: Record<string, Node> = {
  /* ---------------------------------------------------------------- 1. sources */
  meetings: { title: 'Meetings & calls', sub: '150 transcripts + 11 follow-ups', icon: Users, kind: 'source', paths: ['write'],
    what: 'Turn-by-turn transcripts of advisor–client meetings and phone calls. The backbone of the memory.',
    points: ['data/conversations.csv: 150 meetings, 6 clients × 25, one CSV row per turn (12–24 turns each)', 'Generated follow-up meetings (MTG-) in datasets/meetings with per-turn timestamps',
      'Calls vs meetings is decided by datasets/source_manifest.csv (62 calls, 88 meetings), not the raw channel column', 'Hand-authored by deterministic Python, no LLM: realistic changes of mind, corrections, hedges'],
    code: ['data/conversations.csv', 'datasets/meetings/', 'src/cmfgen/'] },
  email: { title: 'Email threads', sub: '21 threads (THR-)', icon: Mail, kind: 'source', paths: ['write'],
    what: 'Multi-message threads between advisor, client and third parties (e.g. a CPA).',
    points: ['Message-ID, In-Reply-To, References, to/cc and named attachments are kept', 'A thread can span weeks, so each message carries its own timestamp', 'Third-party senders are context only and can never be cited as the client'],
    code: ['datasets/emails/'] },
  chat: { title: 'Chat messages', sub: '27 conversations (CHT-)', icon: MessageSquare, kind: 'source', paths: ['write'],
    what: 'Short, informal messages. Many are deliberate non-decisions ("late-night musing; nothing due").',
    points: ['reply_to_message_id kept verbatim', 'Hardest channel for extraction: brevity and hedging', 'A manual read found no case where a musing was stored as a decision'],
    code: ['datasets/chats/'] },
  crm: { title: 'CRM records', sub: 'derived · not ingested', icon: ScrollText, kind: 'source', paths: [],
    what: 'What the advisor’s CRM kept: mostly automatic log lines, a few hand-typed notes.',
    points: ['Shown in "Learn about the data source" to illustrate the information loss CMF addresses', 'Never read by memory ingestion: a summary is not evidence', 'In the evidence layer it lives on a separate derived pathway and never enters turns or spans'],
    code: ['datasets/crm/', 'src/evidence/parsers/crm.py'] },
  ops: { title: 'Operational data', sub: 'accounts · holdings · goals', icon: Landmark, kind: 'source', paths: ['read'],
    what: 'Structured financial data for the same six clients: accounts, holdings, monthly snapshots, goals, liabilities, market data.',
    points: ['Deterministic generator, consistent with what the conversations say', 'Used only by the 13 MCP tools at question time, never written to memory', 'Every value is labelled synthetic in the tool provenance'],
    code: ['src/opdata/generate.py', 'data/operational/*.csv', 'docs/operational_data_dictionary.md'] },
  gov: { title: 'Governance & registry', sub: 'access control · speakers', icon: KeyRound, kind: 'source', paths: ['write', 'read'],
    what: 'Synthetic access-control fixtures and the speaker registry.',
    points: ['source_control.csv: records marked SYN-ACC-RESTRICTED (agent access: none) are never loaded', 'character_registry.csv: who each speaker is, their role and attribution confidence', 'decide() is deny-by-default'],
    code: ['datasets/governance/', 'datasets/character_registry.csv', 'datasets/governance_check.py'] },

  /* ---------------------------------------------------------------- 2. parsing */
  adapter: { title: 'Multichannel adapter', sub: 'one interface for every channel', icon: Shuffle, kind: 'code', paths: ['write', 'read'],
    what: 'Presents meetings, calls, email threads and chats through one unchanged Conversations interface: one interaction per meeting / thread / chat, one turn per message.',
    points: ['Because the interface is unchanged, the hardened integrity boundary and the verifiers check email and chat with their existing code',
      'Observation time = the cited message’s own timestamp', 'Speaker comes from the data, never from the model; third parties get speaker "Third party"',
      'Enabled by CMF_MEMORY_CHANNELS=all (default: the 150 original meetings only)', 'The evaluation answer key and the CRM are never read'],
    code: ['src/services/conversations.py'] },
  parsers: { title: 'Channel parsers', sub: 'structured evidence layer', icon: Layers, kind: 'code', paths: [],
    what: 'Deterministic, channel-aware parsing into a source-linked evidence store: container → segment → turn → span.',
    points: ['Email: thread reconstruction from In-Reply-To; quoted text detected only by explicit markers; the original body is always kept whole',
      'Chat: grouping by timing gaps and explicit replies; message boundaries never merged', 'Topic segments from a reviewable keyword lexicon (no fixed-size token chunks); rule-based dialogue acts',
      'Idempotent: content hash + parser version; re-ingest of an unchanged container is a no-op', 'SQLite FTS5 full-text search; no embeddings. Produces evidence only: memory does not consume it yet'],
    code: ['src/evidence/parsers/', 'src/evidence/store.py', 'sql/200_cmf_evidence_schema.sql', 'docs/evidence_layer_design.md'] },
  opsload: { title: 'Operational loader', sub: 'CSV → ops schema', icon: Database, kind: 'code', paths: ['read'],
    what: 'Validates the operational CSVs and loads them into the cmf_ops schema (SQLite or PostgreSQL).',
    points: ['Validation report in reports/operational_validation_report.md', 'Read-only for every agent'],
    code: ['src/opdata/validate.py', 'src/opdata/load_db.py', 'sql/001_cmf_ops_schema.sql'] },

  /* ---------------------------------------------------------------- 3a. rules ingestion */
  r1: { title: 'Salience filter', sub: 'regex cues pick turns', icon: Filter, kind: 'code', paths: [],
    what: 'Baseline only (not used in the current setup). Scores each turn with regex cues (decision, correction, hedge, numbers, topic keywords) and keeps the salient ones.',
    points: ['The original pipeline, now opt-in with CMF_INGEST_MODE=rules; llm_first is the default','Only selected snippets reach the model: cheap, but tuned to the scripted wording',
      'Natural rewording drops its recall from 85% to 66% on the same 60 meetings, which is why we moved extraction to the LLM'],
    code: ['src/agents/memory/rules.py', 'ingest.select_salient_turns'] },
  r2: { title: 'LLM extraction', sub: 'snippets → candidates', icon: Sparkles, kind: 'llm', paths: [],
    what: 'The model proposes structured MemoryCandidates (topic, subject, statement, value, certainty, excerpt) from the selected turns. Rule candidates are added alongside.',
    points: ['Structured output, schema-validated', 'The model never sees SQL or credentials and never writes to the database'],
    code: ['ingest.extract_memory_candidates', 'src/agents/memory/prompts.py'] },
  r3: { title: 'Validate & classify', sub: 'certainty guard · routing', icon: Scale, kind: 'code', paths: [],
    what: 'Checks every candidate against the transcript, applies the certainty guard and routes it to a memory type.',
    points: ['Excerpt must really be in the cited turn (near-misses repaired)', 'Certainty guard may only DOWNGRADE: a hedge can never become a decision', 'Classified episodic / procedural; semantic is never written here'],
    code: ['ingest.filter_memory_candidates', 'ingest.classify_memory'] },

  /* ---------------------------------------------------------------- 3b. llm-first ingestion */
  l1: { title: '① Extract', sub: 'whole conversation', icon: Sparkles, kind: 'llm', paths: ['write'],
    what: 'The extractor (gpt-4o-mini) reads every turn, with no regex pre-selection, and proposes MemoryCandidates in the same schema as the rules path.',
    points: ['Can reason across turns ("Any scholarships?" / "A small one.")', 'Robust to natural wording: 73% recall vs 66% for rules on rewritten meetings, 56% vs 23% on new simulated interactions'],
    code: ['src/agents/memory/llm_first.py · extract'] },
  l2: { title: '② Fact-check', sub: 'code · re-anchor', icon: Search, kind: 'code', paths: ['write'],
    what: 'Code checks facts; it never judges certainty.',
    points: ['Turn exists; excerpt is really in it; value appears in the excerpt; speaker from the data', 'A third party’s message can never be cited',
      'Regex helpers here only normalise: number/value parsing ("$600k", "63"), value-in-quote check, sentence splitting, and a keyword fallback if the model returns an invalid topic',
      'Re-anchor: if the cited sentence is a bare "Yes, let’s do that", primary evidence moves to the earlier sentence that states the content'],
    code: ['llm_first.fact_check', 'llm_first.reanchor'] },
  l3: { title: '③ Blind verify', sub: 'second model votes', icon: Bot, kind: 'llm', paths: ['write'],
    what: 'A different model (gpt-4.1-mini) labels each candidate’s certainty without seeing the extractor’s label, and says whether it is supported and worth remembering.',
    points: ['Using a different model than the extractor measured +5 points certainty accuracy', 'CMF_VERIFIER_MODEL (OpenAI backend)'],
    code: ['llm_first.verify'] },
  l4: { title: '④ Combine', sub: 'lower label wins', icon: GitMerge, kind: 'code', paths: ['write'],
    what: 'Final certainty = the LOWER of extractor and verifier. A regex hedge ("maybe", "leaning", "what if") vetoes both.',
    points: ['Three votes: extractor (LLM), verifier (LLM) and a regex certainty guard. The regex vote is advisory: it sets the confidence bucket, never the label, except for the hedge veto','Stored confidence = measured accuracy of the agreement bucket: all agree 0.96 · LLMs agree 0.82 · hedge veto 0.30', 'Disagreements are flagged "needs review"', 'Over-confidence is the costlier error, so it is downgrade-only: 0 over-confident memories in every eval set'],
    code: ['llm_first.combine'] },
  l5: { title: '⑤ Value check', sub: 'is it the client’s figure?', icon: Scale, kind: 'llm', paths: ['write'],
    what: 'Every confirmed number that could become a current value must be the client’s own figure: a decision, agreed plan, completed action or fact about them.',
    points: ['Unaccepted recommendations, advisor read-outs, what-ifs and corrected-away values stay unconfirmed', 'Names the kind of quantity (age, %, $/month…); it becomes part of the slot so $/month can never replace a number of days', 'Fails safe to unconfirmed'],
    code: ['llm_first.check_value'] },
  l6: { title: '⑥ Slot match', sub: 'which fact is it about?', icon: Route, kind: 'llm', paths: ['write'],
    what: 'Before a confirmed value enters a topic that already has current values of the same kind, the verifier says which one (if any) it updates.',
    points: ['Stops an unrelated value overwriting a current one', 'Stops one fact being split across two subjects'],
    code: ['llm_first.check_slot'] },

  rx: { title: 'Where regex still helps', sub: 'safety net, not the extractor', icon: Filter, kind: 'code', paths: ['write'],
    what: 'In LLM-first the models find and phrase every memory. Regex never decides what is a memory; it is kept only where it is precise and cheap.',
    points: ['Advisory third certainty vote → confidence bucket (all three agree: 0.96; only the two LLMs agree: 0.82)',
      'Hedge veto: "maybe", "leaning", "what if" found by regex but missed by both models forces the weaker label (two models can share a blind spot)',
      'Fact-check normalisation: numbers, units and values; checking the value really appears in the quoted sentence; sentence splitting; topic fallback',
      'Downstream, the integrity boundary’s support check is also deterministic lexical/numeric matching, not an LLM'],
    code: ['src/agents/memory/rules.py', 'llm_first.combine', 'llm_first.fact_check'] },

  /* ---------------------------------------------------------------- 3c. shared write gates */
  wv: { title: 'Write validator', sub: 'harness · independent re-check', icon: FileCheck, kind: 'code', paths: ['write'],
    what: 'An independent pre-persistence check on everything the Memory Orchestrator proposes. It may reject or downgrade, never upgrade.',
    points: ['Statement must be supported by the quoted sentence (word overlap)', 'Known cost: cross-turn LLM-first memories lose 7–10 recall points; proposed fix is to check against all cited evidence rows'],
    code: ['src/harness/memory_write_validator.py'] },
  ib: { title: 'Integrity boundary', sub: 'every durable write', icon: Lock, kind: 'code', paths: ['write', 'read'],
    what: 'The single chokepoint for every memory mutation. Fail-closed.',
    points: ['Identity: a sealed Principal (verified person or named service: ingestion, consolidation, orchestrator, ttl_expiry…), each limited to operations, types and clients',
      'Provenance: cites a real turn of the right client; observation time taken from that turn, never from the caller',
      'Verification: deterministic lexical / numeric / structural support (number, unit, negation, hedges, who acts, and vs or). Not semantic entailment; undecidable → unverified',
      'Human corrections take precedence; exactly one active version per (client, topic, subject) slot', '40 SQLite triggers block raw writes and make audit tables append-only'],
    code: ['src/agents/memory/integrity.py', 'src/agents/memory/support.py', 'sql/120_cmf_memory_integrity.sql'] },
  cons: { title: 'Consolidation', sub: 'episodes → versioned facts', icon: GitMerge, kind: 'code', paths: ['write'],
    what: 'The ONLY writer of semantic memory. Folds a slot’s confirmed episodes in time order into short, versioned values.',
    points: ['Example: retirement age 65 → 63. The old value is superseded (valid_to set), never overwritten', 'Deterministic and idempotent: re-running changes nothing', 'Out-of-order email evidence rebuilds the chain once; no version churn on re-runs', 'Fallback creator: if the earliest episode is rejected, the next confirmed one with the same value creates the version'],
    code: ['src/agents/memory/consolidate.py'] },

  /* ---------------------------------------------------------------- 4. stores */
  mem: { title: 'Memory store', sub: 'cmf_memory', icon: Brain, kind: 'store', paths: ['write', 'read'],
    what: 'Four kinds of memory, all evidence-linked to the exact turn they came from, with full lifecycle history.',
    points: ['Working: scratch notes for the current question, expire after 4 h', 'Episodic: specific events, each tied to a turn ("Mar 3: discussed retirement")', 'Semantic: stable facts, versioned and superseded, never overwritten',
      'Procedural: how the firm does things (curated, approved checklists)', 'Also: memory_evidence, relationships, lifecycle events, append-only conflicts and resolutions'],
    code: ['src/agents/memory/store.py', 'sql/100_cmf_memory_schema.sql'] },
  evdb: { title: 'Evidence store', sub: 'cmf_evidence', icon: Layers, kind: 'store', paths: [],
    what: 'Containers, turns, segments, spans, annotations and relations from the channel parsers. Separate database, permission-aware retrieval.',
    points: ['Annotations carry provenance (observed / rule / model), method and confidence', 'Reads go through EvidenceRetriever(requester) with per-container access checks'],
    code: ['src/evidence/', 'build/cmf_evidence.db'] },
  opsdb: { title: 'Operational DB', sub: 'cmf_ops (read-only)', icon: Database, kind: 'store', paths: ['read'],
    what: 'Financial tables the MCP tools query.', points: ['SQLite now, PostgreSQL-ready'], code: ['sql/001_cmf_ops_schema.sql'] },
  appdb: { title: 'Approvals & traces', sub: 'audit trail', icon: ScrollText, kind: 'store', paths: ['read'],
    what: 'Pending human approvals and per-request traces (stage timings, model calls, tokens, tool calls, verification outcomes).',
    points: ['Approvals are one-time and fingerprinted against data_version', 'Traces power the agent-performance panel in Ask CMF'],
    code: ['src/harness/approval_store.py', 'src/harness/trace_recorder.py'] },

  /* ---------------------------------------------------------------- 5. orchestration */
  req: { title: 'Request & identity', sub: 'API · permission gate', icon: KeyRound, kind: 'code', paths: ['read'],
    what: 'POST /api/ask. Identity comes from a signed token (AuthContext: actor, role, client scope, tool scope) and is checked before any model call or cache lookup.',
    points: ['Out-of-scope clients look exactly like missing ones', 'One shared Budget (time, tokens, model calls, tool calls) and one trace opened per request'],
    code: ['api/main.py', 'src/harness/permission_gate.py', 'src/harness/auth_tokens.py'] },
  planner: { title: 'Planner', sub: 'question → ExecutionPlan', icon: Workflow, kind: 'llm', paths: ['read'],
    what: 'Turns the advisor’s question into a validated, structured plan: clients, topics, task type, subtasks and execution order. Plans only; executes nothing.',
    points: ['17-topic vocabulary (retirement_age, asset_allocation, liquidity_reserve…)', 'Task hints: history, comparison, follow-up review, feasibility, calculation', 'Only told about the clients the caller may use', 'Replans without tools if tool discovery fails'],
    code: ['src/agents/planner/agent.py', 'src/agents/planner/topics.py'] },
  morch: { title: 'Memory Orchestrator', sub: 'retrieve the right slice', icon: Brain, kind: 'code', paths: ['read'],
    what: 'Reconstructs the slice of memory the plan needs: client → type → topic → status → temporal → relevance ranking → related memories.',
    points: ['Every query is scoped to the requested client', 'Episodes capped (default 8); the number omitted is reported', 'Harness then selects context within the token budget and detects temporal conflicts (stale or contradicting values)'],
    code: ['src/agents/memory/retrieve.py', 'src/harness/temporal_conflict.py'] },
  tools: { title: 'Tool Selector', sub: 'picks MCP calls', icon: Wrench, kind: 'llm', paths: ['read'],
    what: 'An LLM loop that decides which financial tools to call, with which arguments, given the plan and a memory summary.',
    points: ['Every call validated before execution (client scope, argument schema, budget)', 'Results digested before they reach the next agent', 'Deterministic fallback if the model step fails'],
    code: ['src/agents/tool_selector/'] },
  mcp: { title: 'MCP server', sub: '13 read-only tools', icon: Database, kind: 'code', paths: ['read'],
    what: 'Deterministic tools over the operational data. Each returns an envelope with data, provenance and limitations.',
    points: ['Profile, portfolio, portfolio history, goals, liabilities', 'Asset allocation, concentration, snapshot comparison, returns, liquidity ratio, goal funding gap', 'Market data, source evidence (verbatim turns)'],
    code: ['src/mcp_server/', 'src/schemas/tools.py'] },
  report: { title: 'Report Agent', sub: 'writes the answer', icon: FileText, kind: 'llm', paths: ['read'],
    what: 'Writes findings, each citing evidence. Has NO database or tool access: it sees only what the earlier agents hand it.',
    points: ['Each evidence item is classed verified / unverified / contradicted / open-conflict / ambiguous', 'Findings citing non-verified memory are withdrawn; every non-verified item is named in the uncertainties', 'Writes working memory for the request (4 h TTL)'],
    code: ['src/agents/report/'] },
  verify: { title: 'Evidence Verifier', sub: 'checks every finding', icon: ShieldCheck, kind: 'code', paths: ['read'],
    what: 'Deterministic verification of every finding before it leaves.',
    points: ['Handle exists, client in plan and in caller scope, quotation is real text of the original turn', 'Figure check: value, unit, period, modality, polarity, client',
      'Clause check: actors, negation, conditions, hearsay, and/or, completed vs planned', 'VERIFIED kept · AMBIGUOUS kept as [Qualified] · UNVERIFIED / CONTRADICTED withheld'],
    code: ['src/harness/evidence_verifier.py', 'src/harness/clause_support.py'] },
  approve: { title: 'Approval gate', sub: 'human sign-off', icon: UserCheck, kind: 'human', paths: ['read'],
    what: 'High-risk answers (suitability conclusions, sensitive writes, external or irreversible actions) are withheld until a supervisor approves.',
    points: ['The requester can release an approved answer once, before it expires (4 h)', 'If the evidence changed after approval, it is no longer releasable', 'A cache hit never bypasses it'],
    code: ['src/harness/approval.py'] },
  ui: { title: 'Brain UI', sub: 'advisor sees & corrects', icon: Monitor, kind: 'human', paths: ['read', 'write'],
    what: 'Brain map, per-memory dossier (evidence quotes, version history, audit trail) and Ask CMF with stage timings and retrieved-vs-cited memories.',
    points: ['Humans change a memory’s lifecycle or resolve conflicts (keep / dismiss / correct), always through the integrity boundary', 'A degraded run (fallback replaced a model step) shows an amber banner'],
    code: ['ui/cmf-brain-ui/'] },
};

const ROLE = ['planner', 'morch', 'tools', 'report'];

function Box({ id, sel, onSel, path, compact }: { id: string; sel: string | null; onSel: (id: string) => void; path: Path | 'all'; compact?: boolean }) {
  const n = N[id];
  const { color } = KIND[n.kind];
  const Icon = n.icon;
  const dim = path !== 'all' && !n.paths.includes(path);
  const on = sel === id;
  return (
    <button onClick={() => onSel(id)} title="Click for details"
      className={`group relative text-left rounded-xl border bg-slate-900/80 transition-all cursor-pointer ${compact ? 'px-2.5 py-2' : 'px-3 py-2.5'} ${on ? 'ring-2 ring-offset-2 ring-offset-slate-950' : 'hover:-translate-y-0.5 hover:bg-slate-900'} ${dim ? 'opacity-25' : ''}`}
      style={{ borderColor: color + (on ? 'ff' : '55'), boxShadow: on ? `0 0 0 2px ${color}` : undefined }}>
      <span className="absolute left-0 top-2 bottom-2 w-1 rounded-r" style={{ background: color }} />
      <div className="flex items-start gap-2 pl-1.5">
        <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color }} />
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-slate-100 leading-tight">{n.title}</div>
          <div className="text-[11px] text-slate-400 leading-snug">{n.sub}</div>
        </div>
      </div>
    </button>
  );
}

const Down = ({ label }: { label?: string }) => (
  <div className="flex flex-col items-center gap-0.5 py-1 text-slate-500">
    <ArrowDown className="w-5 h-5" />{label && <span className="text-[11px] font-mono text-slate-500 text-center">{label}</span>}
  </div>
);
const Right = () => <ArrowRight className="w-4 h-4 text-slate-500 shrink-0 self-center hidden lg:block" />;

function Band({ n, title, sub, accent, children }: { n: number; title: string; sub: string; accent: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900/30 p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 mb-3">
        <span className="font-mono text-xs font-semibold" style={{ color: accent }}>LAYER {n}</span>
        <h2 className="text-base font-bold text-slate-100">{title}</h2>
        <span className="text-xs text-slate-400">{sub}</span>
      </div>
      {children}
    </section>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const n = N[id];
  const { color, label } = KIND[n.kind];
  const Icon = n.icon;
  return (
    <aside className="fixed right-0 top-0 bottom-0 z-40 w-full sm:w-[30rem] border-l border-slate-800 bg-slate-950/95 backdrop-blur-xl shadow-2xl overflow-auto">
      <div className="p-5 flex flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: color + '22', color }}><Icon className="w-5 h-5" /></div>
            <div><h3 className="text-lg font-bold text-slate-100 leading-tight">{n.title}</h3><span className="text-xs font-medium" style={{ color }}>{label}</span></div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 cursor-pointer" aria-label="Close"><X className="w-5 h-5" /></button>
        </div>
        <p className="text-sm text-slate-200 leading-relaxed">{n.what}</p>
        <ul className="flex flex-col gap-2">
          {n.points.map((p) => <li key={p} className="text-[13px] text-slate-300 leading-relaxed pl-3 border-l-2" style={{ borderColor: color + '88' }}>{p}</li>)}
        </ul>
        <div>
          <h4 className="text-[11px] uppercase tracking-wide text-slate-500 mb-1.5">Where it lives</h4>
          <div className="flex flex-wrap gap-1.5">{n.code.map((c) => <code key={c} className="text-[11px] rounded-md bg-slate-900 border border-slate-800 px-2 py-1 text-slate-300">{c}</code>)}</div>
        </div>
        <p className="text-[11px] text-slate-500">Esc to close · click another box to switch</p>
      </div>
    </aside>
  );
}

const PRINCIPLES = [
  ['The model proposes, code disposes', 'LLMs only ever produce structured candidates. Code checks every fact and owns every database write.'],
  ['Downgrade only', 'Every check can lower certainty, none can raise it. Over-confidence is the costly error: 0 over-confident memories across all eval sets.'],
  ['Every claim cites a turn', 'Memories and report findings point at the exact sentence they came from; quotes are re-read from the source, never trusted from an agent.'],
  ['History, not overwrite', 'Semantic facts are versioned (65 → 63). Superseded values stay queryable; human corrections take precedence.'],
  ['Least privilege between agents', 'Each agent sees only what the previous one hands it. The Report Agent has no database or tool access.'],
  ['Fail closed', 'Missing identity, broken triggers, context overflow or an undecidable check → refuse or mark unverified, never guess.'],
];

const STATS = [['6', 'synthetic clients'], ['209', 'conversations, 4 channels'], ['4', 'memory types'], ['4', 'agents + harness'], ['13', 'read-only MCP tools'], ['0', 'over-confident memories']];

export function ArchitecturePage() {
  const [sel, setSel] = useState<string | null>(null);
  const [path, setPath] = useState<Path | 'all'>('all');
  useEffect(() => { const f = (e: KeyboardEvent) => { if (e.key === 'Escape') setSel(null); }; window.addEventListener('keydown', f); return () => window.removeEventListener('keydown', f); }, []);
  const B = (id: string, compact?: boolean) => <Box key={id} id={id} sel={sel} onSel={setSel} path={path} compact={compact} />;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-xl sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3.5 flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-base font-bold tracking-tight flex items-center gap-2"><Workflow className="w-5 h-5 text-violet-400" />Design & architecture</h1>
          <div className="flex items-center gap-4">
            <a href="#/data" className="text-xs text-slate-400 hover:text-white">Learn about the data source</a>
            <a href="#/" className="flex items-center gap-1.5 text-xs text-slate-300 hover:text-white"><ArrowLeft className="w-4 h-4" />Back to the memory brain</a>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6 flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <h2 className="text-2xl font-bold tracking-tight">How the Cognitive Memory Fabric works</h2>
          <p className="text-sm text-slate-300 max-w-4xl">Fragmented advisor–client communication goes in at the top. It is parsed into one shape, turned into evidence-linked, versioned memory, and used by four agents to answer an advisor’s question with every claim cited and checked. <b className="text-slate-100">Click any box</b> for details and the code behind it.</p>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {(Object.keys(KIND) as Kind[]).map((k) => <span key={k} className="inline-flex items-center gap-1.5 text-xs text-slate-300"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: KIND[k].color }} />{KIND[k].label}</span>)}
          </div>
          <div className="flex items-center gap-1 text-xs">
            <span className="text-slate-500 mr-1">Highlight:</span>
            {([['all', 'Everything'], ['write', 'Write path (ingestion)'], ['read', 'Read path (Ask CMF)']] as const).map(([k, l]) => (
              <button key={k} onClick={() => setPath(k)} className={`rounded-md px-2.5 py-1 cursor-pointer ${path === k ? 'bg-violet-500/20 text-violet-200 border border-violet-500/50' : 'border border-slate-700 text-slate-300 hover:border-slate-500'}`}>{l}</button>))}
          </div>
        </div>

        <div className="flex flex-col">
          {/* ---------------------------------------------------------------- 1 */}
          <Band n={1} title="Sources" sub="fragmented across systems · all synthetic · Jan–Jun 2026" accent="#94a3b8">
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2.5">
              {B('meetings')}{B('email')}{B('chat')}{B('crm')}{B('ops')}{B('gov')}
            </div>
          </Band>
          <Down label="raw CSV files" />

          {/* ---------------------------------------------------------------- 2 */}
          <Band n={2} title="Parsing & normalisation" sub="deterministic: IDs, order, timestamps, speakers never come from a model" accent="#34d399">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5">
              {B('adapter')}{B('parsers')}{B('opsload')}
            </div>
            <p className="mt-2 text-[11px] text-slate-500">Access filter applied here: restricted records dropped · third parties marked non-citable · CRM and the evaluation key never reach memory.</p>
          </Band>
          <Down label="one interaction per meeting / thread / chat · one turn per message" />

          {/* ---------------------------------------------------------------- 3 */}
          <Band n={3} title="Memory ingestion" sub="the write path · one conversation at a time · idempotent" accent="#a78bfa">
            <div className="grid grid-cols-1 lg:grid-cols-[2.2fr_1fr] gap-3">
              <div className="rounded-xl border-2 border-violet-500/50 bg-violet-500/[0.04] p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
                  <h3 className="text-sm font-semibold text-violet-200 flex items-center gap-2">LLM-first pipeline <span className="rounded bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 text-[10px] px-1.5 py-0.5 font-mono">ACTIVE</span></h3>
                  <code className="text-[10px] text-slate-500">CMF_INGEST_MODE=llm_first</code>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">{['l1', 'l2', 'l3', 'l4', 'l5', 'l6'].map((x) => B(x, true))}</div>
                <p className="mt-2 text-[11px] text-slate-400">The LLM reads the whole conversation and proposes every memory; a second, blind model votes on certainty; code checks the facts. Natural wording: 73% recall, 75% certainty accuracy; new simulated interactions: 56% / 57%. ~2 LLM calls per conversation.</p>
                <div className="mt-2.5 grid grid-cols-1 sm:grid-cols-[auto_1fr] gap-2 items-center rounded-lg border border-emerald-500/25 bg-emerald-500/[0.04] p-2">
                  {B('rx', true)}
                  <p className="text-[11px] text-slate-400"><b className="text-emerald-300">Regex is a safety net, not the extractor:</b> an advisory third certainty vote (sets confidence), a hedge-word veto, and number/value normalisation inside fact-check.</p>
                </div>
              </div>
              <div className="rounded-xl border border-dashed border-slate-700 p-3 opacity-80">
                <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2"><h3 className="text-xs font-semibold text-slate-300">Rules pipeline <span className="text-slate-500 font-normal">(baseline)</span></h3><code className="text-[10px] text-slate-500">CMF_INGEST_MODE=rules</code></div>
                <div className="flex flex-col gap-1.5">{B('r1', true)}<ArrowDown className="w-3.5 h-3.5 text-slate-600 self-center" />{B('r2', true)}<ArrowDown className="w-3.5 h-3.5 text-slate-600 self-center" />{B('r3', true)}</div>
                <p className="mt-2 text-[11px] text-slate-500">The original approach, kept for comparison. Regex picks turns first. Recall 91% on scripted wording but 66% on natural wording.</p>
              </div>
            </div>
            <div className="flex justify-center"><Down label="MemoryCandidates (same schema from both pipelines)" /></div>
            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-2.5 items-stretch">
              {B('wv')}<Right />{B('ib')}<Right />{B('cons')}
            </div>
            <p className="mt-2 text-[11px] text-slate-500">Episodic and procedural memories are written after the integrity boundary; semantic facts are written only by consolidation.</p>
          </Band>
          <Down label="evidence-linked, versioned memory" />

          {/* ---------------------------------------------------------------- 4 */}
          <Band n={4} title="Stores" sub="separate databases · append-only audit" accent="#60a5fa">
            <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr_1fr_1fr] gap-2.5">
              <div className="flex flex-col gap-2">
                {B('mem')}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
                  {([['Working', '4 h TTL', 'working'], ['Episodic', 'events, per turn', 'episodic'], ['Semantic', 'versioned facts', 'semantic'], ['Procedural', 'firm playbooks', 'procedural']] as const).map(([t, s, k]) => ({ t, s, c: TYPES[k].color })).map(({ t, s, c }) => (
                    <div key={t} className={`rounded-lg border px-2 py-1.5 ${path === 'all' || N.mem.paths.includes(path as Path) ? '' : 'opacity-25'}`} style={{ borderColor: c + '55', background: c + '10' }}>
                      <div className="text-[12px] font-semibold" style={{ color: c }}>{t}</div><div className="text-[10px] text-slate-400">{s}</div>
                    </div>))}
                </div>
              </div>
              {B('evdb')}{B('opsdb')}{B('appdb')}
            </div>
          </Band>
          <Down label="retrieved per question" />

          {/* ---------------------------------------------------------------- 5 */}
          <Band n={5} title="Agent orchestration" sub="the read path · POST /api/ask · stages follow the validated plan" accent="#f472b6">
            <div className="rounded-xl border-2 border-dashed border-pink-500/30 p-3 relative">
              <span className="absolute -top-2.5 left-3 bg-slate-950 px-2 text-[11px] font-mono text-pink-300">HARNESS: one budget · one trace · Strands LLM wrapper · cache · compression · timeouts</span>
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr_auto_1fr] gap-2 items-stretch mt-1">
                {B('req')}<Right />
                {ROLE.flatMap((r, i) => [
                  <div key={r} className="flex flex-col gap-1.5">
                    <span className="text-[10px] font-mono text-slate-500">AGENT {i + 1}</span>{B(r)}
                    {r === 'tools' && <><span className="text-center text-slate-500 text-xs">⇅ MCP</span>{B('mcp', true)}</>}
                  </div>,
                  i < ROLE.length - 1 ? <Right key={r + '>'} /> : null])}
              </div>
              <div className="flex justify-end pr-[8%]"><Down /></div>
              <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-2 items-stretch md:w-[75%] md:ml-auto">
                {B('verify')}<Right />{B('approve')}<Right />{B('ui')}
              </div>
            </div>
            <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] text-slate-500">
              <p>↺ The Report stage writes <b className="text-blue-300">working memory</b> (4 h) back to the store for follow-up questions.</p>
              <p>↺ Advisors correct memories and resolve conflicts in the UI; those writes go back through the <b className="text-emerald-300">integrity boundary</b>.</p>
            </div>
          </Band>
        </div>

        {/* ---------------------------------------------------------------- principles + stats */}
        <section className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-4">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/30 p-4">
            <h2 className="text-base font-bold mb-3">Design principles</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {PRINCIPLES.map(([t, d]) => <div key={t}><div className="text-sm font-semibold text-slate-100">{t}</div><p className="text-xs text-slate-400 leading-relaxed mt-0.5">{d}</p></div>)}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-900/30 p-4">
            <h2 className="text-base font-bold mb-3">At a glance</h2>
            <div className="grid grid-cols-2 gap-3">
              {STATS.map(([v, l]) => <div key={l}><div className="text-2xl font-bold tabular-nums text-slate-100">{v}</div><div className="text-[11px] text-slate-400">{l}</div></div>)}
            </div>
            <p className="mt-3 text-[11px] text-slate-500">Evaluation numbers come from synthetic data with an outcome-only answer key, judged blind by gpt-4.1. They are not production accuracy.</p>
          </div>
        </section>
      </main>

      {sel && <Detail id={sel} onClose={() => setSel(null)} />}
    </div>
  );
}
