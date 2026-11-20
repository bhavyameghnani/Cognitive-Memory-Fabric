"""The ONLY sanctioned way to enumerate dataset files for source discovery / ingestion / memory extraction.

Layout rule: evaluation ground truth (scenarios.csv, scenario_links.csv, routine_records.csv, access_scenarios.csv) lives in datasets/evaluation/ and is never
returned by discover_source_files(). Tests and evaluation code may read it through EVALUATION_FILES; ingestion code
must not. Standard library only; read-only.
"""
from pathlib import Path

DATASETS = Path(__file__).resolve().parent
ROOT = DATASETS.parent

# original conversations (kept outside datasets/): channel mapping is in datasets/source_manifest.csv
ORIGINAL_SOURCE = ROOT / "data" / "conversations.csv"

SOURCE_FILES = {   # channel -> files holding source-native records (original evidence)
    "Meetings": ["meetings/meetings.csv", "meetings/meeting_turns.csv", "meetings/meeting_metadata.csv",
                 "meetings/original_meetings_metadata.csv"],
    "Email": ["emails/emails.csv", "emails/email_threads.csv"],
    "Calls": ["calls/calls_metadata.csv"],
    "Chat": ["chats/chats.csv", "chats/chat_conversations.csv"],
}
CRM_FILES = ["crm/crm_meetings.csv", "crm/crm_email.csv", "crm/crm_calls.csv", "crm/crm_chat.csv"]   # derived, not evidence
MANIFEST = "source_manifest.csv"
# synthetic source-control metadata (retention, access, grants): control data ABOUT records, not evidence and not ground truth
GOVERNANCE_FILES = ["governance/source_control.csv", "governance/retention_policies.csv",
                    "governance/access_policies.csv", "governance/access_grants.csv"]
EVALUATION_FILES = ["evaluation/scenarios.csv", "evaluation/scenario_links.csv", "evaluation/routine_records.csv",
                    "evaluation/access_scenarios.csv"]
FORBIDDEN_NAMES = {"scenarios.csv", "scenario_links.csv", "routine_records.csv", "access_scenarios.csv"}


def is_evaluation_path(path) -> bool:
    p = Path(path)
    return p.name.lower() in FORBIDDEN_NAMES or "evaluation" in {x.lower() for x in p.parts}


def assert_no_evaluation_data(paths):
    """Raise if any path is an evaluation-ground-truth file."""
    bad = [str(p) for p in paths if is_evaluation_path(p)]
    if bad:
        raise ValueError(f"evaluation ground truth must not enter source ingestion: {bad}")
    return list(paths)


def discover_source_files(include_crm=False, include_governance=False):
    """Absolute paths of dataset files an ingestion pipeline may read (original data/conversations.csv included).
    CRM (derived) and governance (control metadata) files are opt-in; evaluation files are never returned."""
    files = [ORIGINAL_SOURCE] + [DATASETS / f for ch in SOURCE_FILES.values() for f in ch] + [DATASETS / MANIFEST]
    if include_crm:
        files += [DATASETS / f for f in CRM_FILES]
    if include_governance:
        files += [DATASETS / f for f in GOVERNANCE_FILES]
    return assert_no_evaluation_data(files)
