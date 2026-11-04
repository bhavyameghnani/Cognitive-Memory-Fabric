# Data Dictionary: `data/conversations.csv`

## What is an interaction?

One **interaction** is one complete meeting or conversation between a wealth advisor and a client, made up of
many back-and-forth turns. It is not a message, a sentence, an extracted fact or a memory record. Interaction
`INT-049` is a whole meeting of about 15-20 turns. The dataset has **150 interactions** (12-24 turns each, mean 14.7 after the revision).

IDs are assigned in chronological order across the whole dataset (ties broken by client ID), so `INT-001` is the
earliest meeting and `INT-150` the latest.

## Grain

One row per conversational turn. Consecutive turns by the same speaker are merged into a single turn.
The file is UTF-8, comma-delimited, with a header row, and fields containing commas, quotes or newlines are quoted
per RFC 4180.

## Columns

| Column | Type | Description |
|---|---|---|
| `interaction_id` | string `INT-###` | Unique ID of the complete meeting. |
| `client_id` | string `CLIENT-00#` | Stable fictional client identifier (six clients). |
| `advisor_id` | string `ADVISOR-00#` | Stable advisor identifier. Each client keeps the same advisor throughout. |
| `interaction_datetime` | ISO 8601, no time zone | Start of the meeting, e.g. `2026-01-08T10:00:00`. Identical on every row of an interaction. Simulated local time. |
| `channel` | `in-person`, `video`, `telephone` | Meeting channel. Constant within an interaction. |
| `turn_index` | integer | 1-based sequential position within the interaction. |
| `speaker` | `Advisor` or `Client` | Who is speaking. |
| `utterance` | text | The spoken content. |
| `meeting_context` | text | Brief description of the meeting's subject. This is an agenda label, not an extracted summary. |
| `source_type` | `synthetic_meeting` | Provenance of the source record. |
| `consent_status` | `synthetic` | Synthetic data, with no real client information. |

## Advisors and clients

| ID | Name | Role |
|---|---|---|
| ADVISOR-001 | Priya Nair | Advises CLIENT-001, CLIENT-004 |
| ADVISOR-002 | Daniel Okafor | Advises CLIENT-002, CLIENT-005 |
| ADVISOR-003 | Elena Rossi | Advises CLIENT-003, CLIENT-006 |

Names appear only inside utterances. The CSV carries IDs only. See `docs/dataset_design.md` for the client profiles.

## Deliberately absent

The CSV contains no `memory_type`, salience/confidence/importance scores, `supersedes`, extracted entities or
extracted events. Those are outputs of the future CMF pipeline. Original statements are never edited later:
corrections are made by new utterances in later meetings, and the earlier statement stays in the file.

## Side file (not source evidence)

`reports/generation_manifest.json` records the generator's authoring tags and action-item ledger per interaction.
It exists only for QC and for later scoring. It must not be given to the ingestion pipeline.
