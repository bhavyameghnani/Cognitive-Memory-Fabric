# CMF multi-channel synthetic interaction dataset (v1)

A fragmented, source-native interaction history for the **same six clients and three advisors** already in
`data/conversations.csv`. **All records are synthetic.** No LLM was used: text is hand-authored and the CSVs are built
deterministically by `datasets/_generator/` (standard library only).

## 1. The architecture this dataset reproduces

Four independently captured communication channels feed a CRM layer. The CRM holds **one flat artifact per channel**
(Notes, Summaries, Activity records) and is **not** a unified, reconciled client history.

| Channel (exact label) | Source platform (exact label) | Source-native records |
|---|---|---|
| `Meetings` | `OneDrive and Teams` | turn-level transcripts |
| `Email` | `Exchange mailbox` | threaded messages with headers |
| `Calls` | `Telephony platform` | turn-level transcripts |
| `Chat` | `Messaging platform` | short messages in conversations |

The problems it lets you investigate: capture differs by channel (different structures and ID schemes); important
information is transferred to the CRM manually (and lossily); there is little reconciliation across channels;
retention and access differ (see Section 6). The platform labels are constants taken from the brief and enforced by
`validate_datasets.py`.

## 2. Actual channel mapping (Calls vs Meetings)

`data/conversations.csv` labels every row `source_type = synthetic_meeting`, which cannot tell a call from a meeting.
Its `channel` column is the only modality field. `source_manifest.csv` maps **every** original interaction ID using it:

| Original `channel` | Assigned channel | Interactions | Reason |
|---|---|---|---|
| `telephone` | `Calls` | 62 | voice-only |
| `video` | `Meetings` | 61 | video meeting; not inferred to be a phone call |
| `in-person` | `Meetings` | 27 | face-to-face meeting |
| anything else | `Unclassified` | 0 | none occurred: all 150 have a recognised modality, consistent across the turns of each interaction |

Nothing was inferred from conversational tone. Original IDs, timestamps and turns are untouched
(`data/conversations.csv` is locked by SHA-256 and compared with git `HEAD`).
**Platform caveat:** for the 88 Meetings the source does not record whether Teams, a OneDrive upload or something else
was used, so `OneDrive and Teams` is assumed from the channel taxonomy (`platform_basis` says so on every row).
Interactions that `channel = in-person` are the least certain fit for that platform.

### Totals

| Channel | Original (from `conversations.csv`) | Generated | Total |
|---|---|---|---|
| Meetings | 88 interactions | 12 meetings / 174 turns | 100 |
| Calls | 62 interactions | none | 62 |
| Email | none | 22 threads / 81 messages (16 storyline + 6 routine threads) | 22 threads |
| Chat | none | 28 conversations / 94 messages (22 storyline + 6 routine conversations) | 28 conversations |
| CRM (derived, separate) | none | 166 records in 4 flat artifacts: 91 Meetings, 47 Calls, 19 Email, 9 Chat (153 Activity, 7 Note, 6 Summary) | |

### Original conversations vs generated records

The 12 generated meetings are **new events**, not copies of any of the 88 original meetings. Each is a follow-up that
cites the original interaction(s) it relates to in `meeting_metadata.references_source_ids`. Validated: no generated
meeting starts within 2 hours of an original meeting of the same client (nearest is about 24 hours), none reuses an
original title, and the maximum 5-word-shingle overlap with any original of the same client is 0.009. No generated text
shares a 12-word run with a call or meeting utterance. Emails and chats have no originals.

## 3. Layout

```
datasets/
  README.md   source_manifest.csv   character_registry.csv   source_catalog.py   governance_check.py
  meetings/    meetings.csv meeting_turns.csv meeting_metadata.csv      (12 generated)
               original_meetings_metadata.csv                           (88 original, metadata only)
  calls/       calls_metadata.csv (62, metadata only)  conversations.sha256  README.md
  emails/      emails.csv email_threads.csv
  chats/       chats.csv chat_conversations.csv
  crm/         crm_meetings.csv crm_email.csv crm_calls.csv crm_chat.csv   (derived; NOT source evidence)
  governance/  source_control.csv retention_policies.csv access_policies.csv access_grants.csv   (synthetic control metadata)
  evaluation/  scenarios.csv scenario_links.csv routine_records.csv access_scenarios.csv   (ground truth; tests/eval only)
  validate_datasets.py   audit_dataset.py   _generator/
```

Run: `python -I datasets/validate_datasets.py` (24 checks, exit 0 = pass). `python -I datasets/audit_dataset.py` prints the
scenario-support and realism audit (read-only). Rebuild: `python -I datasets/_generator/build.py --overwrite-generated`
(only rewrites generated files; leaves a file untouched if the bytes would not change, and reports files locked by
another program instead of failing).

## 4. Provenance conventions

| Layer | Record ID | Finer granularity | Other keys |
|---|---|---|---|
| Original interaction | `INT-###` | `INT-####T<turn_index>` | `source_manifest.csv` gives its channel |
| Meeting | `MTG-###` | `MTG-###-T##` (turn) | `meeting_metadata`: recording and transcript refs (synthetic URIs) |
| Email | `EML-###` | message | `THR-###` thread; `message_id` `<EML-###.THR-###@exchange.synthetic.example>`; `in_reply_to`, `references` |
| Chat | `CHM-####` | message | `CHT-###` conversation; `reply_to_message_id` |
| CRM | `CRM-MTG/EML/CAL/CHT-####` | n/a | `source_refs` (the source records it was transferred from), `record_type` Note/Summary/Activity, `transfer_method`, `is_original_source_evidence = false` |

- `references_source_ids` on meetings, email threads and chat conversations point only to an **earlier**, **same-client**
  record.
- A CRM record is in the same channel and for the same client as every record in its `source_refs`, is created after
  them, and is never referenced by a source record or scenario (validated). CRM rows have no `source_platform` column.
- **Generated IDs are append-only.** Routine batch 1 took `THR-017..019` / `CHT-023..025`; routine batch 2 (the
  governance refinement pass) took `THR-020..022` / `CHT-026..028` and the matching `EML` / `CHM` numbers. CRM rows for the
  second batch are numbered after all earlier CRM rows, so the only chronological exception in a CRM file is that final
  appended run (validated). Comparing the build before and after batch 2: 0 existing rows changed, apart from
  `referenced_by_new_records` flipping to true for INT-127 (a new chat cites it) and the corrected basis text for MENT-011.

### The CRM layer is deliberately lossy

- Activity records exist for only a share of source records (Meetings 85%, Calls 70%, Email 50%, Chat 25%, sampled
  deterministically). Notes and Summaries (13) are short advisor-typed restatements; 5 of the 13 omit at least one
  dollar or percent figure that their source records contain.
- Some Notes copy an advisor assertion that the source later corrects, and the CRM is **not** updated (Rebecca "accepted",
  Grace "about half", Peg's equipment). Kenji's file note is corrected twice because the source records say the advisor
  did that (Mar 4, May 29). This models the missing reconciliation; it is not a claim about any real system.
- A CRM artifact never becomes an authoritative client fact: it carries `is_original_source_evidence = false`, cites
  the source records it came from, and the sources stay independently available. It also has its **own** access and
  retention policy (Section 6) and inherits nothing from its source.

## 5. Character and client attribution

- **No new characters.** Speakers and senders are the six clients, three advisors, and two third parties (Hannah Park,
  Dennis Hall) who are named *and described by role* in the original calls. IDs follow the CSV (`CLIENT-001..006`,
  `ADVISOR-001..003`; pairing unchanged: 001 with 001/004, 002 with 002/005, 003 with 003/006).
- **Every registry row now carries an explicit status** (`character_registry.csv`, columns `person_category`,
  `id_status`, `name_status`, `role_status`, `attribution_confidence`, `attribution_evidence`). The existing `role_basis`
  and `name_source` columns are unchanged. Nothing is silently upgraded: an inferred role can never be rated `high`
  (validated), and the evidence column carries the numbers the rating came from.

| Category | People | ID | Name | Role | Confidence |
|---|---|---|---|---|---|
| client | 6 | established in the CSV `client_id` column | first name **inferred** from conversation context (appears in 25 of 25 of the client's interactions, in none of anyone else's); surname from the repository docs only | from the CSV column | high |
| advisor | 3 | established in the CSV `advisor_id` column | first name **inferred** (Priya 22 of 50, Daniel 17 of 50, Elena 26 of 50 interactions, never in another advisor's) | from the CSV column | medium |
| external correspondent | 2 | assigned by the dataset | full name stated in the calls | stated (CPA, accountant) | high |
| mentioned-only, role **stated** | 3 (Dorothy, Mei Lin Chu, Luis) | assigned | stated | stated in an utterance | high |
| mentioned-only, role **inferred** | 15 | assigned | stated | never stated outright; inferred from context and corroborated by the repository design docs | medium 13, low 2 (Ethan, Camille: only 4 interactions each) |

- **Limitations (flagged, not silently resolved):**
  1. The original CSV labels speakers only `Advisor` / `Client`; names appear inside utterances. "Priya = ADVISOR-001"
     etc. is derived from first-name co-occurrence and validated. Advisor names appear in under half of their own
     interactions, which is why they are rated medium.
  2. A name inside an utterance never makes that person a speaker. The 18 mentioned-only people are never speakers,
     senders, recipients or participants anywhere (validated).
  3. Family relationships (spouse, children, partner) are almost never stated in the calls; they are inferred and
     agree with `docs/dataset_design.md`. That document is a design note, not source evidence. Samuel is the one case the
     design doc does not name (it says only that CLIENT-004 was widowed); `final_dataset_acceptance.md` refers to his
     pension. The earlier registry text that called this "confirmed in docs/dataset_design.md" was wrong and is corrected.
  4. Mei Lin Chu is stated as Rebecca's accountant; her relation to the lawyer in INT-126 is not stated and is not assumed.
  5. Hannah Park (EXT-001) and Dennis Hall (EXT-002) appear only as email correspondents, one thread each, copied to
     the client at the client's request (INT-107, INT-018).
- Emails and chats are advisor-client only; spouses and children never write.

## 6. Retention and access-control metadata (synthetic source control)

**Audit of what existed before this pass:** the channel files carried `data_classification = client-confidential`
(identical on every row), `client_id`/`client_scope`, `consent_status = synthetic`, participant lists, and the CRM files
carried `record_layer` and `is_original_source_evidence`. None carried an owner, permitted readers, a retention policy,
a retention start or review date, a grant or an expiry. Because those differ by channel, they live in a separate
`governance/` folder instead of extra columns on every channel schema.

**All of it is a synthetic experimental assumption.** It is not a Microsoft, Exchange, telephony, messaging or CRM
vendor policy, and not a legal or regulatory rule. The retention numbers are arbitrary review intervals chosen only so
that the channels differ; no real retention period is modelled and nothing expires or is deleted.

| File | Content |
|---|---|
| `governance/source_control.csv` | one row per source or CRM record (378 = 212 source + 166 CRM): layer, original-evidence flag, controlling platform, client scope, responsible advisor, `access_policy_id`, optional `restriction_policy_id`, `retention_policy_id`, `retention_start_date`, `review_date`, `access_inheritance` |
| `governance/retention_policies.csv` | 6 policies: Meetings 270 days, Calls 120, Email 365, Chat 60, CRM Activity 540, CRM Note/Summary 720 (review intervals only) |
| `governance/access_policies.csv` | 7 policies: permitted roles, agent access, whether third-party grants are honoured |
| `governance/access_grants.csv` | 3 explicit, dated, record-level grants to a third party: `valid_from`, `valid_until`, `status`, `revoked_at`, `basis_source_ids` |

- **Source channels differ:** Meetings readable by client and assigned advisor; **Calls by the assigned advisor only**
  (the client is *not* a default reader); Email by client and assigned advisor, plus explicit grants; Chat by client
  and assigned advisor, grants not honoured.
- **Record-level restriction:** four records about the retention pool (INT-121, THR-012, CHT-018, MTG-012) carry a
  restriction that is *intersected* with the channel policy. It can only narrow: no agent access, no third-party grants.
- **Derived CRM records have their own policy** (Activity: assigned advisor, agents allowed; Note/Summary: assigned
  advisor, no agents) and their own retention. They never inherit their source's readers or grants and are never broader
  than their source (validated). Summarising does not widen access. A CRM record may outlive its source's review date.
- **Grants** are exact-record and single-client. `GRT-001` (CPA on THR-010) and `GRT-002` (accountant on THR-014) rest on
  the client's own words (INT-046/INT-107, INT-018). `GRT-003` (CPA on MTG-008, revoked 2026-06-24) is a **synthetic
  ledger event**: no utterance in the source authorises or revokes it. It exists so revocation can be tested.
- `governance_check.py` is a small deny-by-default reference evaluator, used only to check that the expected outcomes can
  be reproduced from the control files. It is not production access control and not memory storage.
- Governance files are opt-in in `source_catalog.discover_source_files(include_governance=True)` and are never evaluation labels.

### Governance scenarios (`evaluation/access_scenarios.csv`, 22 cases)

The 23 original scenarios were left unchanged. Six carry the `governance` dimension, but only two (SCN-C1-03, SCN-C6-04)
involve a third party receiving information; the rest concern advisor suggestions versus client decisions. None covered
cross-client disclosure, out-of-assignment access, agents, CRM-stricter-than-source or revoked/expired authorization, so
those gaps were filled with 22 cases built on existing records and characters (no new source records):

| Gap category | Cases | Outcomes |
|---|---|---|
| Authorized sharing with an external professional | GOV-01, 02, 06 | allow, allow, deny (the grant does not reach the chat) |
| Unauthorized disclosure to another client | GOV-07, 08, 09 | deny x3 |
| Advisor outside their assignment | GOV-10, 11, 12, 21 | deny x2, allow x2 (controls) |
| Agent retrieving restricted information | GOV-13, 14, 15, 16 | deny, allow, deny, deny |
| Derived CRM stricter than its source | GOV-17, 18, 19, 20 | deny x3, allow |
| Revoked or expired authorization | GOV-03, 04, 05 | allow before revocation, deny after, deny after expiry |
| Missing permission (by policy) | GOV-22 | deny |

Each case has an expected outcome, a reason code, a rationale and supporting source records. Expected outcomes are hand
written and independently reproduced by the evaluator (check 21).

## 7. Evaluation ground truth is separated from source data

`evaluation/scenarios.csv` (23 storylines), `scenario_links.csv` (113 links), `routine_records.csv` (12) and
`access_scenarios.csv` (22) describe what the dataset plants. They are an **evaluation aid** only.

- They live in their own folder; `source_catalog.discover_source_files()` is the sanctioned way to enumerate source files and
  never returns them; `assert_no_evaluation_data()` raises if any of the four names or the `evaluation/` folder appears in a list.
- No source, CRM or governance file carries evaluation columns, no scenario id appears in source text, and no code under `src/`,
  `utils/` or `services/` references them (all validated).
- **Routine status is not written into the source.** Routine records are identified only in `evaluation/routine_records.csv`;
  no source file has a routine flag and no routine text calls itself routine (validated).
- `scenario_links.csv` has a `link_basis` column so that a shared topic is not mistaken for evidence: of 113 links, 23 are
  the origin record, 49 are an **explicit reference** (the generated record cites an earlier record of the scenario in
  `references_source_ids`) and 41 are **shared topic only** (original interactions cannot cite anything, so they are
  linked by the storyline author).
- `tests/test_multichannel_datasets.py` fails if an evaluation file is added to the ingestion set, copied out of
  `evaluation/`, or referenced by application code. Mutation-tested on a scratch copy.
- Memory extraction must read only `data/conversations.csv`, `meetings/`, `emails/`, `chats/` and (as a *derived* layer) `crm/`.

## 8. What the data is designed to exercise

| Dimension | Examples planted |
|---|---|
| **Context** | Kenji's retirement age spans calls, email, chat and a meeting; Peg's Maplewood decision spans 8 records. |
| **Certainty** | "inclined, not accepted" (Rebecca); "might sell" vs "decided to sell half" (Lucas); advisor suggestions (529 plan, half-sale) that are not client decisions. |
| **Currency** | 60% vs "about half" survivor pension; $14k program fee vs "extra tuition"; salary stops vs 12-month consulting role; four vs five aide days. Originals are never edited; corrections are new records. |
| **Provenance** | Stable IDs at record and turn level; the same fact in different channels with different wording; CRM notes vs the source they were typed from. |
| **Governance** | Client-scoped IDs; per-channel and per-record access and retention; dated, revocable third-party grants; derived CRM records with stricter access; agent delegation; no cross-client names. |

### Scenario audit (`audit_dataset.py`, heuristic text evidence per scenario)

Scenarios with at least one linked record whose own text supports the category (of 23): exploratory 20, confirmed
decision 22, correction 18, changed fact 19, unresolved 16, pending commitment 21, incomplete cross-reference 20.
The scenario table lists a correction in 11 scenarios (12 have none, on purpose); every scenario that claims one has
corrective text in a linked record. The advisor-vs-client regex found support in only 6; the authored design has about
10 (SCN-C1-01/02, C2-01/03, C3-01/03, C4-01/03, C5-01, C6-01), so that column undercounts. Scenarios SCN-C2-05 and
SCN-C6-04 are deliberately stable/uncontradicted. No dollar figure in generated text is absent from the same client's
original calls (digits or spelled-out words).

### Realism

91 of 150 original interactions are in no scenario. Routine records (identified only in the evaluation file): **12**,
6 email threads (12 messages) and 6 chat conversations (14 messages), at least one of each for every client, about 15%
of generated email and chat messages. Batch 2 added 3 email threads and 3 chats (6 + 6 messages): two receipt
acknowledgements, a scheduling proposal, a tentative "may need to move our next call", and two thank-yous after a
meeting/call that cite that earlier interaction without adding any fact. They carry no figures, decisions or
commitments; 2 of the 12 routine records cite an earlier record, the other 10 are context-free. By the audit's own text
signal, 73% of generated email messages and 90% of chat messages carry no memory-bearing signal.

## 9. Known synthetic assumptions and limitations

1. The original file labels all 150 interactions `synthetic_meeting`; modality comes from its `channel` field alone.
2. The Teams/OneDrive platform for the 88 original meetings is assumed (see Section 2).
3. Slide 5 was not available; channel and platform labels follow the brief's wording.
4. New records add details the calls do not contradict (e.g. Kenji's June 26 form deadline, Sofia's childcare place,
   Rebecca's 12 June payments, the Apr 17 tender deadline). They are plausible additions, not facts from the calls.
5. `recording_ref`, `transcript_ref` and `attachment_refs` are synthetic placeholders; no files exist.
6. The CRM is a modelling device: its gaps are by construction. It has far more Meetings activity rows than Chat.
7. Generated Meetings, Email and Chat are small (12 / 22 / 28 conversations): enough for the five-dimension tests, not
   for statistical evaluation; six client voices limit stylistic variety.
8. Tax and investment figures are illustrative; the originals already contain unverified figures (see
   `docs/final_dataset_acceptance.md`).
9. Time window is 2026-01-02 to 2026-06-30; commitments such as Priya's conditional pre-July call to Grace stay open.
10. `validate_datasets.py` warns about capitalised words not found in the calls (e.g. "Fridays", "Mum"); all were read and
    none is a stray name.
11. **Governance is entirely synthetic**: policies, retention intervals, grants and the roles that can read each channel are
    invented for the experiment. The revocation in `GRT-003` is a ledger event with no source utterance behind it. The agent
    principal `AGENT-SYN-01` is a test identity, not a character, and appears in no source record.
12. Retention is modelled as review dates only; 28 of 378 records (18 Calls, 10 Chat) are already past review at
    2026-06-30, and a passed review date does not change access.
13. Family and other relationships are mostly inferred (Section 5). Surnames of clients and advisors come from repository
    docs, not from the calls.
14. The expected governance outcomes are independently reproducible from the control files, but the memory scenarios'
    expected outcomes are described in prose (`arc_summary`) and are checked only by heuristic text evidence.
