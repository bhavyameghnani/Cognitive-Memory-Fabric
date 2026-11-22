# LLM-first on email, chat and generated meetings

59 records · extractor `gpt-4o-mini` · verifier `gpt-4.1-mini` · judge `gpt-4.1` · 2026-11-22 13:45

Answer key (evaluation only): `datasets/evaluation/scenario_links.csv` storyline notes for generated records, and the routine records that contain no decision. All numbers after the harness write validator.

- **Storyline points captured:** 0.521
- **Memories kept per routine record** (ideally 0): 0.0

| Channel | Records | Memories kept | Storyline points found | Routine records | Memories from routine |
|---|---|---|---|---|---|
| chat | 27 | 15 | 5/21 | 6 | 0 |
| email | 21 | 25 | 13/15 | 6 | 0 |
| meeting | 11 | 23 | 7/12 | 0 | 0 |

## Storyline points missed

- `CHT-002` (chat): client asks for comparison; maybes stay maybes
- `CHT-003` (chat): late-night musing about the house; nothing due
- `CHT-004` (chat): client anxious about annuity salesman; no action
- `CHT-005` (chat): deposit worry; nothing due
- `CHT-007` (chat): client wavers by chat; refuses to decide by text
- `CHT-008` (chat): recruiter call: exploratory only
- `CHT-009` (chat): client asks not to record four days as a plan
- `CHT-010` (chat): client felt rushed; nothing to decide
- `CHT-011` (chat): employer might allow remote; still a possibility
- `CHT-013` (chat): employer says no remote; off for the moment
- `CHT-014` (chat): client checks the correction was made; acceptance still pending
- `CHT-016` (chat): client double-checks the correction
- `CHT-017` (chat): reminder for summaries
- `CHT-019` (chat): client asks to stop; advisor restates no decision needed and the conditional call
- `CHT-021` (chat): CPA confirmation forwarded; submission planned; signed copy pending
- `CHT-022` (chat): pension papers to be uploaded via the secure portal
- `MTG-006` (meeting): two conditions restated; specialist declined; kept as maybe
- `MTG-007` (meeting): questions for the attorney; advisor explicitly not recommending
- `MTG-009` (meeting): advisor suggests house fund as one option; client says do not record as done
- `MTG-011` (meeting): client again says nothing decided about the equipment
- `MTG-011` (meeting): model rerun; survivor amount is a stress case only
- `THR-008` (email): advisor email labels it additional tuition; later corrected in same thread
- `THR-013` (email): forms requested; one form missing

## Sample of storyline points found

- `MTG-003` confirmed: Sofia's manager hinted at a four-day work week when she returns, but she hasn't asked for it and is undecided.  
  key: four-day week as scenario only; childcare fees to follow
- `THR-007` correction: Peg clarified that helping Brian with equipment was only a thought and nothing has been offered.  
  key: email lists equipment help as a wish; ambiguous client acknowledgement; correction May 8
- `THR-006` exploratory: Rebecca told Daniel she is inclined to accept the Lakeshore offer, but has not signed the letter of intent and her lawyer is still negotiating.  
  key: advisor email says offer accepted; correction and client confirmation of 'inclined' on Mar 24
- `CHT-020` confirmed: Rebecca confirmed that she paid the lawyer's forty thousand retainer and Mei Lin's fifteen thousand advance from the cash account.  
  key: client confirms both payments were made from the cash account
- `MTG-010` exploratory: Grace is open to the idea of holding Folake's share outright for now and revisiting it later with an attorney.  
  key: trust explained; attorney suggested; open
- `THR-009` exploratory: Lucas wants disability cover but is not buying anything yet.  
  key: advisor asks for summaries; client wants cover but buys nothing; partial summaries found
- `THR-004` confirmed: Grace has decided to keep the mortgage in place for now.  
  key: advisor email labels payout as reserved for mortgage; client's reply states a wish, not an action; later keep-mortgage confirmation; June correction
- `CHT-012` confirmed: Sofia Alvarez decided to hold off on choosing a 529 plan until the autumn.  
  key: advisor suggestion; client defers
- `CHT-001` confirmed: Kenji confirmed that the formal planning date is 65.  
  key: client asks for confirmation the corrected note says 65
- `THR-015` correction: Daniel corrected the assumption that Rebecca's salary stops at closing, noting she will remain in a paid consulting role for twelve months after closing at her current salary.  
  key: v1 model assumes no salary; correction and v2 model
- `THR-011` confirmed: Peg has decided against Maplewood Commons for now, because Frank wants to stay at home.  
  key: contract summary, hold, attorney list, final decision against for now
- `MTG-004` exploratory: Kenji is considering 63 as his retirement age, but he is not ready to say it is the answer yet.  
  key: 63 under serious consideration; planning date still 65; decision promised for May 1
- `CHT-015` confirmed: Priya confirmed that about $24,000 went into the global equity fund this morning as the first of six steps.  
  key: first step executed on Jun 1
- `THR-001` confirmed: Sofia is considering a purchase within about two years, with no commitment, and has decided not to start a house search until the autumn.  
  key: advisor email states decision; correction and softened autumn plan on Apr 13
- `THR-016` confirmed: Sofia confirmed to keep the $40,000 in the house fund as house money.  
  key: advisor email treats house fund as cushion; correction and client's explicit instruction
- `THR-002` exploratory: Kenji would like to retire at 60, despite the trade-offs and not recommended on current numbers.  
  key: advisor email overstates exploration as a wish to retire at 60; client reply is tentative
- `MTG-005` exploratory: Rebecca expressed uncertainty about opening a 529 plan and requested to be asked again once she knows where Ethan is applying.  
  key: two funding versions requested; 529 suggestion left open
- `MTG-008` exploratory: Kenji wants Mariko as the primary beneficiary, with Yuki and Ren equally as contingent beneficiaries, pending confirmation against the estate documents.  
  key: form review; beneficiary partly open; submission pending
- `THR-010` exploratory: Kenji is leaning toward five installments for his deferred compensation payout but will confirm on Wednesday's call.  
  key: advisor shares plan summary with the client's CPA; tentative client lean; decision recorded Jun 3
- `MTG-001` exploratory: Grace is feeling uncertain about choosing the annuity option and stated, 'I'm feeling like no. But I don't know.'  
  key: form walkthrough; tentative preference for monthly income; lump sum not evaluated

## Memories kept from routine records

