# Known defects report

Each case is reproduced from the stored database (`reproduced = yes` means the system output shown is really in the database). The 'expected' text is a reviewer
assertion from the inspection phase and is **not ground truth**: it is to be confirmed by independent annotation (see `annotation_template.csv`).

## KD-01 - Boilerplate 'Sorry' triggers correction
Why it is suspect: The cue 'sorry, i|that' matches apologies and scheduling courtesy; no earlier statement is corrected.
Expected (pending annotation): No correction act and no corrects relation (social/apology).
Reproduced: 7/7

- `INT-023#T2` reproduced=yes  (Meetings, Sofia Alvarez)
  - original: Yes! Loan comparison and parental leave planning, perfect. Sorry, I'm squeezing this between meetings.
  - existing interpretation: answers -> INT-023#T1 [rule_v1:adjacent_question]; corrects -> INT-023#T1 [rule_v1:correction_cue+shared_token]   [target text: Hi Sofia, it's lovely to see you. How have things been since January 30? Today the topic is loan comparison and parental]
- `INT-045#T2` reproduced=yes  (Meetings, Sofia Alvarez)
  - original: Yes! Last check-in before the baby and keeping the employer match, perfect. Sorry, I'm squeezing this between meetings.
  - existing interpretation: answers -> INT-045#T1 [rule_v1:adjacent_question]; corrects -> INT-045#T1 [rule_v1:correction_cue+shared_token]   [target text: Hi Sofia. I'm glad we could fit this in. How are you? On February 26 we covered markets and buying the dip; today the to]
- `INT-065#T2` reproduced=yes  (Meetings, Sofia Alvarez)
  - original: Yes! Insurance estimate and how much cover is enough, perfect. Sorry, I'm squeezing this between meetings.
  - existing interpretation: corrects -> INT-065#T1 [rule_v1:correction_cue+shared_token]   [target text: Good morning, Sofia. Take your time. We've got plenty of it. Last time, on March 16, we focused on guardianship and your]
- `INT-113#T2` reproduced=yes  (Meetings, Sofia Alvarez)
  - original: Yes! Wills and guardianship follow-up, perfect. Sorry, I'm squeezing this between meetings.
  - existing interpretation: answers -> INT-113#T1 [rule_v1:adjacent_question]; corrects -> INT-113#T1 [rule_v1:correction_cue+shared_token]   [target text: Hello Sofia. Before the agenda, how are you doing? Since May 19, when we focused on saving for Lucia's education, the ne]
- `INT-139#T2` reproduced=yes  (Meetings, Sofia Alvarez)
  - original: Yes! The house fund and the childcare budget, perfect. Sorry, I'm squeezing this between meetings.
  - existing interpretation: answers -> INT-139#T1 [rule_v1:adjacent_question]; corrects -> INT-139#T1 [rule_v1:correction_cue+shared_token]   [target text: Hi Sofia. I'm glad we could fit this in. How are you? We last spoke on June 17, about reworked budget. For today I have ]
- `INT-012#T3` reproduced=yes  (Meetings, Priya Nair)
  - original: I'm sorry, that sounds unsettling. Has anything changed in your role or compensation?
  - existing interpretation: dialogue_act=['question', 'correction']
- `INT-015#T3` reproduced=yes  (Calls, Daniel Okafor)
  - original: I'm sorry, that's a lot to carry. How much of the cost falls to you each month?
  - existing interpretation: dialogue_act=['question', 'correction']

## KD-02 - Email corrections linked to the wrong earlier message
Why it is suspect: The rule links to the nearest earlier turn sharing a long word; the text names a dated earlier email (see diagnostics: date-implied targets).
Expected (pending annotation): Target = the earlier message the text explicitly names by date (human to confirm).
Reproduced: 5/5

- `EML-022` reproduced=yes  (Email, Daniel Okafor)
  - original: Lucas, A correction to my February 18 email, where I wrote that you're planning to sell part of your shares. As you told me today, you said you might and you haven't decided; you're waiting for my comparison. The comparison is overdue and I owe it to you. I'll send it by Friday, April 3. Daniel
  - existing interpretation: corrects -> EML-014 [rule_v1:correction_cue+shared_token]; replies_to -> EML-014 [in_reply_to_header]   [target text: I'll send it once I've re-read the letter.

Daniel]
- `EML-020` reproduced=yes  (Email, Daniel Okafor)
  - original: Rebecca, A correction to my March 12 email, where I wrote "following your acceptance of the offer". That was wrong. You told me you are inclined to accept; you have not signed the letter of intent and your lawyer is still negotiating. I have corrected my record today. The model is attached. Cash at closing is about $13.5 million after tax and fees. Daniel
  - existing interpretation: corrects -> EML-019 [rule_v1:correction_cue+shared_token]; replies_to -> EML-019 [in_reply_to_header]   [target text: Daniel, thanks. My lawyer says the redlines will take another week. Send me the cash-at-closing model as soon as you hav]
- `EML-039` reproduced=yes  (Email, Priya Nair)
  - original: Dear Kenji, A correction to my May 5 email. The $14,000 is not additional tuition. As you explained on today's call, it covers the program fee, flights and housing; Ren's normal tuition is paid separately and is already in your cash flow. I've corrected the file note and dated it today. My May 5 email is unchanged in the record. Best regards, Priya
  - existing interpretation: corrects -> EML-033 [rule_v1:correction_cue+shared_token]; replies_to -> EML-033 [in_reply_to_header]   [target text: Priya, thanks. I'll forward the breakdown as soon as the program office sends it. Ren is still waiting to hear whether h]
- `EML-068` reproduced=yes  (Email, Daniel Okafor)
  - original: Rebecca, A correction after yesterday's call. Version 1 assumed your salary stops at closing. You told me section six of the draft keeps you in a paid consulting role for the twelve months after closing, at your current salary. Version 2 reflects that: portfolio withdrawals begin only after those twelve months, and the spending account is larger than it needs to be. My recommendation is to resize 
  - existing interpretation: corrects -> EML-061 [rule_v1:correction_cue+shared_token]; replies_to -> EML-061 [in_reply_to_header]   [target text: Thanks. I haven't opened it properly. The escrow dispute is eating my week.

Rebecca]
- `EML-015` reproduced=yes  (Email, Priya Nair)
  - original: Dear Grace, A correction to my February 10 email. I wrote that the survivor payment looked like about half of Samuel's pension. I had read the company's summary too loosely. The form actually gives sixty percent, payable for life, plus an optional lump sum that I haven't evaluated yet. A one-page summary of the form is attached. Kind regards, Priya
  - existing interpretation: corrects -> EML-009 [rule_v1:correction_cue+shared_token]; replies_to -> EML-009 [in_reply_to_header]   [target text: Grace, please don't worry about the number yet. It comes from a summary, not from the plan's own letter. Once the letter]

## KD-03 - 'No apology needed' labelled rejection
Why it is suspect: 'No' opening a courtesy phrase is not a rejection of anything.
Expected (pending annotation): social
Reproduced: 1/1

- `CHM-0011` reproduced=yes  (Chat, Elena Rossi)
  - original: No apology needed! I hope you're getting some sleep. We can talk about house timing whenever you're ready. Nothing is due.
  - existing interpretation: dialogue_act=['rejection']

## KD-04 - Discourse openers labelled agreement
Why it is suspect: 'Good morning', 'Good question', 'Right now' open a turn; none agrees with the previous turn.
Expected (pending annotation): No agreement act (INT-008#T2 'Sure, but I reserve the right to disagree' is at most hedged agreement).
Reproduced: 4/4

- `INT-001#T1` reproduced=yes  (Meetings, Priya Nair)
  - original: Good morning, Kenji. I've put the numbers for today on one page. Shall we begin? Since this is the start of our review cycle, I'd like to take stock of where things are and what you want from this year. The agenda I have is retirement timing and income needs.
  - existing interpretation: dialogue_act=['question', 'proposal', 'agreement']
- `INT-003#T11` reproduced=yes  (Calls, Elena Rossi)
  - original: Good question. They adjust with inflation, which matters over a long retirement.
  - existing interpretation: dialogue_act=['agreement']
- `INT-003#T7` reproduced=yes  (Calls, Elena Rossi)
  - original: Right now about thirty-five percent of the portfolio is in equities. Is that still comfortable?
  - existing interpretation: dialogue_act=['question', 'agreement']
- `INT-008#T2` reproduced=yes  (Meetings, Lucas Moreau)
  - original: Sure, but I reserve the right to disagree with half of it.
  - existing interpretation: dialogue_act=['agreement']

## KD-05 - 'I'll have to think about that' labelled commitment
Why it is suspect: It is a deferral, not an undertaking to do a specific thing.
Expected (pending annotation): deferral
Reproduced: 1/1

- `INT-033#T12` reproduced=yes  (Calls, Peg Sullivan)
  - original: I'll have to think about that.
  - existing interpretation: dialogue_act=['commitment']

## KD-06 - 'The decision is yours' labelled decision
Why it is suspect: It hands the decision to the client; nobody has decided.
Expected (pending annotation): No decision (advisor declines to decide).
Reproduced: 1/1

- `EML-017` reproduced=yes  (Email, Priya Nair)
  - original: Grace, good question, and I would rather answer it in person. The survivor pension is one input. I haven't recommended for or against an annuity, and the decision is yours. We'll talk before you decide. Priya
  - existing interpretation: dialogue_act=['decision', 'commitment']

## KD-07 - Advisor recaps read as client decisions
Why it is suspect: The speaker is the advisor reporting what the client decided; the label has no maker or status. EML-001's recap was later corrected by EML-028 as overstating what the client said.
Expected (pending annotation): decision_status = reported_by_other; the decision-maker and confirmation need independent evidence (the client's own turn).
Reproduced: 5/5

- `INT-010#T17` reproduced=yes  (Calls, Priya Nair)
  - original: Nothing from you today. If I think a trade is warranted, I'll bring it to you first, and nothing happens without your agreement. Let me record the outcomes. You've decided to proceed with the agreed portfolio review. The working plan I'm recording is a growth-oriented portfolio at about 75 percent equities. From your side, you'll send over the plan summary for your Northgate deferred compensation 
  - existing interpretation: dialogue_act=['decision', 'commitment']
- `INT-022#T23` reproduced=yes  (Meetings, Priya Nair)
  - original: To summarise today in order: You've decided to keep the mortgage as it is and hold the cash as a cushion, rather than pay the mortgage down early. The working plan I'm recording is a working assumption of six months of base salary as severance for liquidity planning, to be replaced if the real terms become known. I'll book us for Monday, February 9, if that suits you. I'll put a written summary in
  - existing interpretation: dialogue_act=['decision']
- `EML-001` reproduced=yes  (Email, Elena Rossi)
  - original: Hi Sofia, Following Friday's call about buying a home and where the down payment should sit. You've decided to buy within two years, so I'd suggest we build a separate house fund and keep it in cash or short-term holdings rather than in equities. Your 401(k) and the employer match stay as they are. Warm regards, Elena
  - existing interpretation: dialogue_act=['decision', 'suggestion']
- `EML-024` reproduced=yes  (Email, Elena Rossi)
  - original: Dear Peg, Following our call yesterday, I'm writing it down in plain words so you have it. 1. You have decided not to lend Brian $80,000, because of the cost of Frank's care. 2. You will call Brian yourself this weekend. 3. You'd also like to help Brian with a few hundred dollars toward his equipment. Kind regards, Elena
  - existing interpretation: dialogue_act=['decision']
- `EML-030` reproduced=yes  (Email, Priya Nair)
  - original: Dear Grace, Following today's call: you have decided to keep the mortgage in place for now. The insurance payout stays in the money-market and short-term Treasury holdings while we plan. Kind regards, Priya
  - existing interpretation: dialogue_act=['decision']

## KD-08 - Modal 'will'/'may' and in-word cues trigger topic or temporal labels
Why it is suspect: 'will ' (modal) fires estate_and_trusts; 'May' matches the modal 'may'; 'rent' matches 'current/different'; 'son ' matches 'reason/comparison'.
Expected (pending annotation): No such topic / no date reference in these turns.
Reproduced: 4/4

- `INT-034#T11` reproduced=yes  (Meetings, Daniel Okafor)
  - original: Not today. I'm comfortable with where it sits, and any proposal will come to you in writing before I trade. So, to be clear: You're still weighing selling some Moreau Labs founder shares in the secondary offer, which I've recorded as an idea and not an instruction. From my side, I'll compare selling none, half or all of the offered founder shares, including the tax. Does Monday, February 23 work f
  - existing interpretation: topic cue scores={'portfolio_allocation': 2, 'estate_and_trusts': 1, 'tax': 1}; segment topic=portfolio_allocation
- `EML-051` reproduced=yes  (Email, Elena Rossi)
  - original: Dear Peg, Following today's call: you have decided against Maplewood Commons for now, because Frank wants to stay at home. You may revisit it if his needs change. No deposit will be paid. Kind regards, Elena
  - existing interpretation: temporal_ref=['today', 'may']
- `INT-141#T17` reproduced=yes  (Meetings, Priya Nair)
  - original: My notes from today read as follows. No decisions were needed today. We went through follow-ups on forms and estate documents and everything is on track. From your side, you'll get Mariko's Social Security statement to complete the survivor analysis and send over your current will and the beneficiary designations on your retirement accounts. Let's pencil in Thursday, June 25 for our next meeting. 
  - existing interpretation: topic cue scores={'retirement_planning': 3, 'estate_and_trusts': 3, 'housing_and_debt': 1, 'follow_up_commitments': 1, 'scheduling_and_admin': 1}; segment topic=retirement_planning
- `INT-026#T12` reproduced=yes  (Meetings, Sofia Alvarez)
  - original: It doesn't bother me much. Is there any reason it should?
  - existing interpretation: topic cue scores={'family_and_education': 1}; segment topic=family_and_education

## KD-09 - 'portal' alone triggers scheduling_and_admin
Why it is suspect: A document upload portal is not scheduling or administration of the engagement.
Expected (pending annotation): Topic judged by the content of the turn (human).
Reproduced: 1/1

- `INT-107#T11` reproduced=yes  (Meetings, Priya Nair)
  - original: For the record, here is what we established. We agreed to hold off on the deferred compensation payout election until the tax comparison is ready. From your side, you'll ask Hannah Park to estimate the tax on the three payout options. Let's pencil in Friday, May 29 for our next meeting. I'll put a written summary in the portal today.
  - existing interpretation: topic cue scores={'tax': 2, 'family_and_education': 1, 'scheduling_and_admin': 1}; segment topic=tax

## KD-10 - Questions linked to answers across segment boundaries
Why it is suspect: The answers relation ignores segmentation, and segmentation splits question from answer.
Expected (pending annotation): A question and its answer belong to one exchange (human to confirm).
Reproduced: 3/3

- `INT-003#T11` reproduced=yes  (Calls, Elena Rossi)
  - original: Good question. They adjust with inflation, which matters over a long retirement.
  - existing interpretation: answers -> INT-003#T10; segments SEG-INT-003-04 vs SEG-INT-003-03
- `INT-003#T13` reproduced=yes  (Calls, Elena Rossi)
  - original: Nothing from you today. If I think a trade is warranted, I'll bring it to you first, and nothing happens without your agreement. Here's what I'm taking away. Please stop me if I've got anything wrong. The working plan I'm recording is preserving capital while covering the gap between about six thousand three hundred in pension and Social Security and spending of about eight thousand nine hundred a
  - existing interpretation: answers -> INT-003#T12; segments SEG-INT-003-05 vs SEG-INT-003-04
- `INT-009#T15` reproduced=yes  (Calls, Elena Rossi)
  - original: Not today. I'm comfortable with where it sits, and any proposal will come to you in writing before I trade. I'd like to make sure we're on the same page. The working plan I'm recording is treating the survivor amount of Frank's pension as unconfirmed, with you believing it is about half. I'll book us for Monday, January 26, if that suits you. I'll put a written summary in the portal today.
  - existing interpretation: answers -> INT-009#T14; segments SEG-INT-009-05 vs SEG-INT-009-04

## KD-11 - Amount regex truncates '$1.46 million' to '$1.46 m'
Why it is suspect: Regex alternation matches 'm' before 'million', so the extracted amount loses its unit.
Expected (pending annotation): The full amount including 'million' (extraction accuracy).
Reproduced: 1/1

- `EML-012` reproduced=yes  (Email, Daniel Okafor)
  - original: Lucas, Following up on yesterday's call. - The liquid portfolio is about $1.15 million. Your startup stake is a large part of your net worth but can't easily be sold. - You're planning to sell part of your Moreau Labs shares in the tender, so I'll compare three cases (none, half, or all of the offered shares) and check the tax treatment with your accountant. The deadline is in the offer letter; I'
  - existing interpretation: amount_ref=['$1.15 m']

## KD-12 - Question label from an embedded greeting question
Why it is suspect: 'Shall we begin?' inside a long agenda opener yields question + proposal + agreement.
Expected (pending annotation): social/agenda opening
Reproduced: 1/1

- `INT-001#T1` reproduced=yes  (Meetings, Priya Nair)
  - original: Good morning, Kenji. I've put the numbers for today on one page. Shall we begin? Since this is the start of our review cycle, I'd like to take stock of where things are and what you want from this year. The agenda I have is retirement timing and income needs.
  - existing interpretation: dialogue_act=['question', 'proposal', 'agreement']

## KD-13 - Acts the rules cannot see
Why it is suspect: No request act; a question without '?' is missed; 'Nothing is decided' is not marked as deferral.
Expected (pending annotation): question / request / deferral respectively.
Reproduced: 3/3

- `CHM-0056` reproduced=yes  (Chat, Peg Sullivan)
  - original: Did I say the aide comes five days now since Frank fell
  - existing interpretation: dialogue_act=['information'] (no 'question')
- `CHM-0076` reproduced=yes  (Chat, Priya Nair)
  - original: Fine. Please send me the signed copy once it's in.
  - existing interpretation: dialogue_act=['information'] (no 'question')
- `CHM-0008` reproduced=yes  (Chat, Daniel Okafor)
  - original: Understood. Nothing is decided.
  - existing interpretation: dialogue_act=['agreement'] (no 'deferral_unresolved')
