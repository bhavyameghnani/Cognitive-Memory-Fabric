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

export interface MemoryDetail {
  memory: MemoryItem;
  versions: MemoryItem[];
  lifecycle: LifecycleEvent[];
}

export interface Finding { text: string; refs: string[] }
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
  validation: Record<string, unknown>;
}

export interface AskResult {
  request_id: string;
  client_ids: string[];
  report: AdvisorReport;
  retrieved_memory_ids: string[];
  agent_run: AgentRun | null;
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
