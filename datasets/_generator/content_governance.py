"""Synthetic source-control metadata: retention policies, access policies, access grants, restricted records, and the
access-control evaluation scenarios.

EVERYTHING HERE IS A SYNTHETIC EXPERIMENTAL ASSUMPTION. None of it is a Microsoft, Exchange, telephony-provider, CRM-vendor
or regulatory policy, and no real retention period is modelled. Retention numbers are arbitrary review intervals chosen
only so that the channels differ from each other.
"""

ASSUMPTION = "synthetic experimental policy; not a vendor or regulatory rule"

# retention_policy_id -> (applies to, review interval in days, label)
RETENTION = {
    "SYN-RET-MTG": ("Meetings, original and generated", 270, "meeting transcripts and recordings: review after 270 days"),
    "SYN-RET-CAL": ("Calls", 120, "call transcripts: review after 120 days"),
    "SYN-RET-EML": ("Email threads", 365, "email threads: review after 365 days"),
    "SYN-RET-CHT": ("Chat conversations", 60, "short-lived chat: review after 60 days"),
    "SYN-RET-CRM-ACT": ("CRM Activity records", 540, "CRM activity log: own review interval, independent of its source"),
    "SYN-RET-CRM-NOTE": ("CRM Notes and Summaries", 720, "advisor-typed CRM text: own review interval, independent of its source"),
}

# access_policy_id -> (applies to, permitted roles, agent access, third-party grants, note)
ACCESS = {
    "SYN-ACC-MTG": ("Meetings", "client_self|assigned_advisor", "delegated", "honoured",
                    "participants may read; an explicit grant is needed for anyone else"),
    "SYN-ACC-CAL": ("Calls", "assigned_advisor", "delegated", "honoured",
                    "telephony transcripts are advisor-held in this experiment; the client is not a default reader"),
    "SYN-ACC-EML": ("Email threads", "client_self|assigned_advisor", "delegated", "honoured",
                    "a third-party correspondent reads a thread only through an explicit, dated grant"),
    "SYN-ACC-CHT": ("Chat conversations", "client_self|assigned_advisor", "delegated", "not_honoured",
                    "client-scoped channel: third-party grants are not honoured"),
    "SYN-ACC-RESTRICTED": ("modifier on flagged source records", "client_self|assigned_advisor", "none", "not_honoured",
                           "record-level restriction (named non-client employees), intersected with the channel policy so it can only "
                           "narrow access: no agent access, no third-party grants"),
    "SYN-ACC-CRM-ACT": ("CRM Activity records", "assigned_advisor", "delegated", "not_honoured",
                        "derived layer: own policy; never inherits the source record's readers or grants"),
    "SYN-ACC-CRM-NOTE": ("CRM Notes and Summaries", "assigned_advisor", "none", "not_honoured",
                         "derived layer: advisor-typed text, human assigned advisor only"),
}

# records under the record-level restriction. Basis: the discussion names non-client employees' compensation (retention
# pool). Chosen by hand in the generator; no scenario label is written into any source-control file.
RESTRICTED = {
    "INT-121": "names a non-client employee and discusses a possible retention pool",
    "THR-012": "email restating the retention-pool idea",
    "CHT-018": "chat about the lawyer's view of the retention pool",
    "MTG-012": "meeting restating that the retention pool is undecided",
}

# explicit access grants (valid_from may be "@record" = the record's own date)
GRANTS = [
    dict(grant_id="GRT-001", record_id="THR-010", principal_id="EXT-001", basis="INT-107|INT-046",
         note="client names Hannah Park as the CPA (INT-046) and says the advisor should have her run the estimate (INT-107)",
         valid_from="@record", valid_until="2026-12-31", status="active", revoked_at=""),
    dict(grant_id="GRT-002", record_id="THR-014", principal_id="EXT-002", basis="INT-018",
         note="client names Dennis Hall as the accountant and asks the advisor to contact him (INT-018)",
         valid_from="@record", valid_until="2026-06-30", status="active", revoked_at=""),
    dict(grant_id="GRT-003", record_id="MTG-008", principal_id="EXT-001", basis="INT-107|INT-046",
         note="SYNTHETIC LEDGER EVENT: sharing a meeting transcript with the CPA is modelled on the same client intent as "
              "GRT-001, but no utterance in the source authorises or revokes it",
         valid_from="@record", valid_until="", status="revoked", revoked_at="2026-06-24"),
]

AGENT = "AGENT-SYN-01"   # a system principal for tests only; it is not a character and never appears in source records

# (scenario_id, gap_category, requester, type, acting_for, record, request_date, expected, reason, rationale, support, grants)
SCENARIOS = [
    ("GOV-01", "authorized_external_sharing", "EXT-001", "external", "", "THR-010", "2026-06-12", "allow", "allow_active_grant",
     "The client named Hannah Park as the CPA and asked the advisor to have her run the estimate; an active dated grant covers exactly this thread.",
     "INT-046|INT-107|THR-010", "GRT-001"),
    ("GOV-02", "authorized_external_sharing", "EXT-002", "external", "", "THR-014", "2026-06-15", "allow", "allow_active_grant",
     "The client named Dennis Hall as the accountant and asked the advisor to contact him; the grant is still inside its window.",
     "INT-018|THR-014", "GRT-002"),
    ("GOV-03", "revoked_or_expired_authorization", "EXT-001", "external", "", "MTG-008", "2026-06-15", "allow", "allow_active_grant",
     "Before its revocation date the grant on the form-review meeting is in force.",
     "MTG-008", "GRT-003"),
    ("GOV-04", "revoked_or_expired_authorization", "EXT-001", "external", "", "MTG-008", "2026-06-30", "deny", "deny_grant_revoked",
     "The same grant was revoked on 2026-06-24; revocation applies from that date and the earlier allowance is not retroactive.",
     "MTG-008", "GRT-003"),
    ("GOV-05", "revoked_or_expired_authorization", "EXT-002", "external", "", "THR-014", "2026-07-15", "deny", "deny_grant_expired",
     "The grant's valid_until date (2026-06-30) has passed; nothing renewed it.",
     "THR-014", "GRT-002"),
    ("GOV-06", "authorized_external_sharing", "EXT-001", "external", "", "CHT-021", "2026-06-20", "deny", "deny_grants_not_honoured",
     "Authorisation for one thread does not extend to the client's chat; the chat policy does not honour third-party grants.",
     "CHT-021|INT-046", ""),
    ("GOV-07", "unauthorized_cross_client_disclosure", "EXT-001", "external", "", "THR-014", "2026-06-15", "deny", "deny_no_grant",
     "Hannah Park is CLIENT-001's CPA; THR-014 belongs to CLIENT-006 and her grant does not cover it.",
     "THR-014|INT-046", ""),
    ("GOV-08", "unauthorized_cross_client_disclosure", "CLIENT-002", "client", "", "MTG-001", "2026-06-10", "deny", "deny_other_client",
     "CLIENT-002 is not the client in MTG-001 (CLIENT-004).",
     "MTG-001", ""),
    ("GOV-09", "unauthorized_cross_client_disclosure", "CLIENT-005", "client", "", "THR-014", "2026-06-20", "deny", "deny_other_client",
     "CLIENT-005 is not the client in THR-014 (CLIENT-006), even though the same email thread names a third-party accountant.",
     "THR-014", ""),
    ("GOV-10", "advisor_outside_assignment", "ADVISOR-001", "advisor", "", "MTG-012", "2026-06-20", "deny", "deny_not_assigned_advisor",
     "ADVISOR-001 is assigned to CLIENT-001 and CLIENT-004; MTG-012 belongs to CLIENT-002 (ADVISOR-002).",
     "MTG-012", ""),
    ("GOV-11", "advisor_outside_assignment", "ADVISOR-003", "advisor", "", "CRM-EML-0011", "2026-06-20", "deny", "deny_not_assigned_advisor",
     "A CRM record for CLIENT-001 is not readable by an advisor assigned to other clients.",
     "THR-010", ""),
    ("GOV-12", "advisor_outside_assignment", "ADVISOR-002", "advisor", "", "INT-130", "2026-06-20", "allow", "allow_assigned_advisor",
     "Control case: the assigned advisor may read the client's own interaction.",
     "INT-130", ""),
    ("GOV-13", "agent_restricted_retrieval", AGENT, "agent", "ADVISOR-002", "INT-121", "2026-06-20", "deny", "deny_agent_blocked",
     "INT-121 carries a record-level restriction (named non-client employees); agents get no access even when the delegating advisor does.",
     "INT-121", ""),
    ("GOV-14", "agent_restricted_retrieval", AGENT, "agent", "ADVISOR-002", "INT-130", "2026-06-20", "allow", "allow_delegated_agent",
     "Control case: an unrestricted record of the delegating advisor's own client.",
     "INT-130", ""),
    ("GOV-15", "agent_restricted_retrieval", AGENT, "agent", "ADVISOR-001", "INT-130", "2026-06-20", "deny", "deny_not_assigned_advisor",
     "The agent holds only its delegator's rights; ADVISOR-001 is not assigned to CLIENT-002.",
     "INT-130", ""),
    ("GOV-16", "agent_restricted_retrieval", AGENT, "agent", "", "INT-130", "2026-06-20", "deny", "deny_missing_delegation",
     "An agent with no named delegator has no rights.",
     "INT-130", ""),
    ("GOV-17", "derived_crm_stricter_than_source", "EXT-001", "external", "", "CRM-EML-0011", "2026-06-12", "deny", "deny_grants_not_honoured",
     "The CPA may read THR-010 (GRT-001) but the CRM Activity record logged from it has its own policy and no grant.",
     "THR-010", "GRT-001"),
    ("GOV-18", "derived_crm_stricter_than_source", "CLIENT-001", "client", "", "CRM-EML-0011", "2026-06-12", "deny", "deny_role_not_permitted",
     "The client may read THR-010, but the derived CRM record is advisor-only; summarising does not widen access.",
     "THR-010", ""),
    ("GOV-19", "derived_crm_stricter_than_source", "ADVISOR-001", "advisor", "", "CRM-EML-0011", "2026-06-12", "allow", "allow_assigned_advisor",
     "Control case: the assigned advisor may read the CRM record.",
     "THR-010", ""),
    ("GOV-20", "derived_crm_stricter_than_source", AGENT, "agent", "ADVISOR-001", "CRM-CHT-0001", "2026-06-12", "deny", "deny_agent_blocked",
     "The delegated agent may read the source chat CHT-001 but the CRM Note typed from it is closed to agents.",
     "CHT-001", ""),
    ("GOV-21", "advisor_outside_assignment", "ADVISOR-002", "advisor", "", "INT-121", "2026-06-20", "allow", "allow_assigned_advisor",
     "A record-level restriction blocks agents and third parties, not the assigned human advisor.",
     "INT-121", ""),
    ("GOV-22", "missing_permission", "CLIENT-002", "client", "", "INT-121", "2026-06-20", "deny", "deny_role_not_permitted",
     "The Calls policy lists only the assigned advisor, and the restriction on INT-121 can only narrow that, so the client is denied "
     "even though it is the client's own call.",
     "INT-121", ""),
]

CATEGORIES = ["authorized_external_sharing", "unauthorized_cross_client_disclosure", "advisor_outside_assignment",
              "agent_restricted_retrieval", "derived_crm_stricter_than_source", "revoked_or_expired_authorization"]
