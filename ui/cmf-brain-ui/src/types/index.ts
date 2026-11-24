export type MemoryType = 'working' | 'episodic' | 'semantic' | 'procedural';
export type Status = 'candidate' | 'active' | 'superseded' | 'disputed' | 'expired' | 'unconfirmed';
export type RelType = 'derived_from' | 'supersedes' | 'corrects' | 'explores' | 'supports' | 'follows' | 'related_to';

export interface EvidenceRef {
  interaction_id: string;
  turn_index: number;
  speaker: string;
  excerpt: string;
  role: string;
}

export interface MemoryItem {
  memory_id: string;
  client_id: string;
  memory_type: MemoryType;
  topic: string;
  subject: string;
  title: string;
  content: string;
  value_text: string | null;
  corrects_value: string | null;
  certainty: string;
  source_role: string | null;
  status: Status;
  confidence: number;
  event_date: string | null;
  valid_from: string | null;
  valid_to: string | null;
  version_no: number | null;
  supersedes_id: string | null;
  origin: string;
  supporting_episodes: number;
  evidence: EvidenceRef[];
  expires_at: string | null;
  session_id: string | null;
  approval_status: string | null;
  procedure_name: string | null;
  steps: string[];
}

export interface Relationship {
  source_id: string;
  target_id: string;
  rel_type: RelType;
  note: string;
}

export interface Interaction {
  interaction_id: string;
  datetime: string;
  channel: string;
  context: string;
  source_type: string;
  turns: number;
  ingestion: string | null;
}

export interface ClientSummary {
  client_id: string;
  first_name: string | null;
  advisor_id: string;
  interactions: number;
  age: string | null;
  occupation: string | null;
  risk_category: string | null;
  memories: number;
}

export interface BrainData {
  client_id: string;
  memories: MemoryItem[];
  relationships: Relationship[];
  interactions: Interaction[];
  counts: { by_type: Record<string, number>; by_status: Record<string, number> };
  conflicts?: { conflict_id: string; kind: string }[];
}

export interface LifecycleEvent {
  event_id: string;
  memory_id: string;
  from_status: string | null;
  to_status: string;
  reason: string;
  actor: string;
  related_memory_id: string | null;
  created_at: string;
}

export interface ConflictView {
  conflict_id: string;
  kind: string;
  label: string;
  severity: string;
  status: 'open' | 'resolved';
  needs_person: boolean;
  detected_at: string;
  other_id: string;
  other: { memory_type: string | null; value: string | null; status: string | null; certainty: string | null; date: string | null; text: string };
  decision: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_reason: string | null;
}

export interface Reasoning {
  created_by_episode: string | null;
  votes: { extractor: string; verifier: string; rules: string; final: string; bucket: string; bucket_text: string; confidence: number } | null;
  checks: string[];
  needs_review: boolean;
  ingestion: { origin: string; certainty_in: string; certainty_out: string; decision: string; reason: string } | null;
  mode: 'llm_first' | 'rules' | null;
}

export interface MemoryDetail {
  memory: MemoryItem;
  versions: MemoryItem[];
  lifecycle: LifecycleEvent[];
  conflicts: ConflictView[];
  reasoning: Reasoning;
}

/** `origin` is set only by backend code: "rendered" = assembled by code from one evidence item, "narrative_checked" = MODEL-written prose that code and the independent verifier checked sentence by sentence,
 *  absent = a finding the model wrote that the verifier accepted.  The UI never sets or changes it. */
export interface Finding { text: string; refs: string[]; origin?: 'rendered' | 'tool_code' | 'narrative_checked' | null; narrative?: { text: string; sources: string[] }[] | null }
export interface EvidenceItem {
  ref: string;
  source_class: string;
  description: string;
  interaction_id: string | null;
  turn_index: number | null;
  memory_id: string | null;
  excerpt: string | null;
  synthetic: boolean;
  topic: string | null;
  value: string | null;
  status: string | null;
  speaker?: string | null;
  event_date?: string | null;
  evidence_class?: string | null;
}

/** One dated, classified statement of the chronology (built by backend code from retrieved source turns). */
export interface TimelineClaim {
  claim_id: string;
  source: 'source_turn' | 'memory';
  wording: string;
  speaker: string | null;
  statement_kind: 'client_statement' | 'advisor_reported' | 'advisor_interpretation' | 'unattributed' | string;
  modality: string;
  confirmed_client_decision: boolean;
  verification: string;
  interaction_id: string | null;
  turn_index: number | null;
  event_date: string | null;
  evidence_refs: string[];
  temporal_status: 'current' | 'historical' | 'corrected' | 'superseded' | 'disputed' | 'unresolved' | string;
  relevant: boolean;
  mentions_topic?: boolean | null;
  memory_refs?: string[];
  relations: { type: 'corrects' | 'corrected_by' | 'contradicts' | string; claim_id: string }[];
}

export interface NarrativeStatus {
  attempted?: boolean;
  paragraphs_returned?: number;
  sentences_returned?: number;
  paragraphs_accepted?: number;
  sentences_rejected?: { paragraph: number; sentence: number | null; problems: string[] }[];
  error?: string;
}

export interface HarnessSummary {
  outcome?: string | null;
  degraded_reasons?: string[];
  profile?: string | null;
  budget?: { llm_calls?: number; elapsed_ms?: number; llm_calls_by_agent?: Record<string, number> } & Record<string, unknown>;
  conflicts?: unknown[];
}

export interface AdvisorReport {
  request_id: string;
  client_id: string;
  question: string;
  summary: string;
  key_findings: Finding[];
  historical_context: Finding[];
  financial_analysis: Finding[];
  evidence: EvidenceItem[];
  assumptions: string[];
  uncertainties: string[];
  limitations: string[];
  suggested_followups: string[];
  timeline?: TimelineClaim[];
  /** Model-written readable account: NOT verified (the backend never puts it through the evidence verifier and gives it no provenance label). Each sentence ends with the evidence handles it rests on. */
  story?: Finding[];
  validation: Record<string, unknown> & { mode?: string; model_error?: string; narrative?: NarrativeStatus; story?: NarrativeStatus; summary_replaced?: string | null };
}

export interface AskResult {
  request_id: string;
  client_ids: string[];
  report: AdvisorReport;
  retrieved_memory_ids: string[];
  agent_run: AgentRun | null;
  /** false while the answer is held for approval: the API then deliberately returns no memory ids */
  released?: boolean;
  /** present only while withheld: content-free run status (which agents ran, timings, permission checks, approval category) */
  execution?: ExecutionStatus | null;
  approval?: { approval_id?: string; status?: string; category?: string } | null;
  harness?: HarnessSummary | null;
  message?: string;
}

/** State of a background ask: `result` is exactly what POST /api/ask would have returned; `http_status`/`detail` are what it would have failed with. */
export interface AskJob { status: 'running' | 'done' | 'error'; elapsed_s: number; result?: AskResult; http_status?: number; detail?: string }

export interface ExecutionStatus {
  agents: { agent: string; ran: boolean; duration_ms: number | null; llm_calls: number; status?: string | null; stop_reason?: string | null }[];
  client_identified: boolean;
  harness: { active: boolean; profile: string | null; outcome: string | null; degraded: boolean;
             permissions: { allowed: number; denied: number; denied_codes: string[] }; approval: { category: string | null; status: string | null } };
  note: string;
}

export interface AgentRun {
  timings_ms: Partial<Record<'planner' | 'memory' | 'tool_selector' | 'report' | 'total', number>>;
  plan: { intent: string | null; task_type: string | null; topics: string[]; memory_categories: string[]; subtasks: { id: string; agent: string; description: string }[] };
  memory: {
    total: number; cited: number; by_group: Record<string, number>; contradictions: string[];
    items: { memory_id: string; client_id: string; group: string; memory_type: MemoryType | null; topic: string | null; label: string; status: string | null; cited: boolean }[];
  };
  tools: {
    status: string | null; stop_reason: string | null; iterations: number; fallback_used: boolean; missing_information: string[];
    executions: { tool: string; status: string; duration_ms: number; rationale: string; error: string | null }[];
  };
  report: { validation: Record<string, unknown>; evidence: number };
  degraded: string[];
}

export interface DsChannel { channel: string; platform: string; what: string; records: number; unit: string; messages: number | null }
export interface DsCrm { artifact: string; total: number; automatic: number; manual: number }
export interface DsScenario { scenario_id: string; client_id: string; advisor_id: string; title: string; arc_summary: string; record_count: string; channels_present: string }
export interface DsOverview { channels: DsChannel[]; crm: DsCrm[]; original_turns: number; scenarios: DsScenario[] }
export interface DsMessage { who: string; role: string; at: string; text: string; turn: number }
export interface DsCrmRecord { crm_record_id: string; type: string; created_at: string; subject: string; text: string; transfer: string }
export interface DsStep {
  sequence: number; channel: string; source_id: string; timestamp: string; origin: string; link_basis: string; note: string;
  source: { title: string; meta: string; messages: DsMessage[] }; crm: DsCrmRecord[];
}
export interface DsScenarioDetail { scenario: DsScenario; steps: DsStep[]; memories: MemoryItem[] }
export interface DsTablePage { key: string; file: string; label: string; columns: string[]; total: number; offset: number; rows: string[][] }
