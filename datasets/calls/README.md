# Calls - Telephony platform

**The original conversations are in `data/conversations.csv`. It is not copied, regenerated, renamed or modified.**
Only its 62 `telephone` interactions are Calls; the other 88 are Meetings (see `../source_manifest.csv`).

| Property | Value |
|---|---|
| Channel label | `Calls` |
| Source platform label | `Telephony platform` |
| Source file | `data/conversations.csv` (repository root, `data/`) |
| Original size | 2,199 turns in 150 interactions (`INT-001` .. `INT-150`), 2026-01-02 .. 2026-06-30 |
| Calls among them | 62 interactions (`channel = telephone`), listed in `calls_metadata.csv` |
| Integrity lock | `calls/conversations.sha256` (SHA-256 of the file when the dataset was built); `validate_datasets.py` also compares the file with git `HEAD` |

## Existing schema (preserved as-is)

| Column | Type | Notes |
|---|---|---|
| `interaction_id` | string `INT-###` | one per complete interaction; chronological |
| `client_id` | string `CLIENT-00#` | 6 clients |
| `advisor_id` | string `ADVISOR-00#` | 3 advisors; each client keeps one advisor |
| `interaction_datetime` | ISO 8601, no zone | identical on all rows of an interaction |
| `channel` | `telephone` / `video` / `in-person` | 62 / 61 / 27 interactions: **the only modality field** |
| `turn_index` | integer | 1-based, contiguous per interaction |
| `speaker` | `Advisor` / `Client` | role only; names appear inside utterances |
| `utterance` | text | consecutive same-speaker turns are merged |
| `meeting_context` | text | agenda label |
| `source_type` | constant `synthetic_meeting` | generic label on every row; says nothing about modality |
| `consent_status` | constant `synthetic` | |

A turn is addressed as `<interaction_id>#T<turn_index>` (e.g. `INT-049#T4`).

## `calls_metadata.csv` (new, 62 rows: only the `telephone` interactions)

References the original records without altering them: `call_source_id`, `client_id`, `advisor_id`,
`interaction_datetime`, `turn_count`, `first_turn_ref`, `last_turn_ref`, `meeting_context`,
`original_channel_value`, `original_source_type`, `original_consent_status`, `channel = Calls`,
`source_platform = Telephony platform`, `platform_basis`, `source_file`, `referenced_by_new_records`,
`linked_scenario_ids`, `synthetic`, `generation_batch`.
The 88 video / in-person interactions are described in `../meetings/original_meetings_metadata.csv` instead.
