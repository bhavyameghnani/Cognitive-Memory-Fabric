"""CLIENT-005 (Lucas Moreau) / ADVISOR-002 (Daniel Okafor). Authored synthetic content."""
CLIENT = 5

MEETINGS = [
    dict(key="c5_m1", client=5, start="2026-04-07T09:00", platform="Teams",
         subject="Tender offer: reading the comparison",
         refs=["INT-062", "THR:c5_t1"],
         turns="""
A: Lucas, you've had the comparison since Friday. Where are you?
C: I read it twice. Half is where I keep landing. But I haven't talked to my accountant about the tax.
A: Let me tell you what I think, including where I disagree with you. Selling nothing keeps all the upside, but you have tax payments coming, a reserve to build, and a single-company stake. I'd sell something. I lean toward half.
C: You always say sell something.
A: Because it's true. But my recommendation isn't your decision.
C: What would selling all do?
A: It cleans up the risk, but you give up the company's upside and probably pay more tax this year. I wouldn't recommend all.
C: Okay. And if I sell half I still have a stake.
A: A large one.
C: Give me until next week. When's the paperwork due?
A: The offer letter says April 17.
C: So decide by the 14th, sign by the 17th.
A: That works. Ask your accountant about the tax before then.
C: Yeah, yeah.
""".strip()),

    dict(key="c5_m2", client=5, start="2026-05-26T09:30", platform="OneDrive",
         subject="Lisbon: what would make it real",
         refs=["INT-096"],
         turns="""
A: You raised Lisbon on the 7th and I wrote it down as a possibility. Has anything changed?
C: Camille asked HR about working remotely. They said they'd get back to her.
A: And Moreau Labs?
C: I could run it from anywhere. But my two biggest customers like having me in the room.
A: Then there are two conditions: her employer saying yes, and the company not needing you in the room. Before anything else, a cross-border tax specialist would be worth a conversation.
C: I'm not paying a specialist for a maybe.
A: Fair. Then it stays a maybe and I won't plan around it.
C: Yes. If HR says yes, I'll tell you and we'll look at it properly.
A: I'll keep it recorded as a possibility with those two conditions.
C: Right. Send me a short note so I remember what I said.
A: I'll do that.
""".strip()),
]

EMAILS = [
    dict(key="c5_t1", client=5, subject="Tender offer - what I will compare",
         refs=["INT-034", "INT-062"],
         msgs=[
             dict(t="2026-02-18T09:10", f="A", to="C", body="""Lucas,

Following up on yesterday's call.

- The liquid portfolio is about $1.15 million. Your startup stake is a large part of your net worth but can't easily be sold.
- You're planning to sell part of your Moreau Labs shares in the tender, so I'll compare three cases (none, half, or all of the offered shares) and check the tax treatment with your accountant.

The deadline is in the offer letter; I'll send you the date.

Daniel"""),
             dict(t="2026-02-18T11:42", f="C", to="A", body="""ok thx. what's the deadline again?

Lucas"""),
             dict(t="2026-02-18T13:05", f="A", to="C", body="""I'll send it once I've re-read the letter.

Daniel"""),
             dict(t="2026-03-27T10:30", f="A", to="C", body="""Lucas,

A correction to my February 18 email, where I wrote that you're planning to sell part of your shares. As you told me today, you said you might and you haven't decided; you're waiting for my comparison. The comparison is overdue and I owe it to you. I'll send it by Friday, April 3.

Daniel"""),
             dict(t="2026-03-27T12:10", f="C", to="A", body="""yeah. don't let it slip pls

Lucas"""),
             dict(t="2026-04-03T16:40", f="A", to="C", att="tender_comparison.pdf", body="""Lucas,

The comparison is attached. In short:

- Sell none: you keep all the upside; the tax payments and reserve come from other sources.
- Sell half (about $75,000): covers your reserve and the next tax payments and leaves most of your stake intact.
- Sell all: removes the single-company risk, but you give up the upside and probably pay more tax this year.

My suggestion is half. That is a recommendation, not a decision. Please check the tax treatment with your accountant.

Daniel"""),
             dict(t="2026-04-03T21:20", f="C", to="A", body="""ok reading it. need to ask my accountant about tax first. not deciding yet

Lucas"""),
         ]),
    dict(key="c5_t2", client=5, subject="Disability cover - what to ask your broker",
         refs=["INT-109", "INT-136"],
         msgs=[
             dict(t="2026-05-19T15:30", f="A", to="C", body="""Lucas,

Following today's call. With irregular income, disability cover matters more for you than most. Next step: please send me your current health and disability policy summaries, and ask your broker to quote for disability cover. I'm not recommending a specific product.

Daniel"""),
             dict(t="2026-05-20T10:20", f="C", to="A", body="""will dig up the summaries. i want the cover but i'm not buying anything yet

Lucas"""),
             dict(t="2026-06-18T15:00", f="A", to="C", body="""Lucas,

As asked on today's call, a reminder: please send the policy summaries this week. You told me your broker says quotes will take about three weeks, so the sooner he has them the better. Nothing is bought or decided.

Daniel"""),
             dict(t="2026-06-19T09:00", f="C", to="A", body="""found two of the three. the health one i have to request from the insurer. will do

Lucas"""),
         ]),
]

CHATS = [
    dict(key="c5_c1", client=5, refs=["INT-034", "THR:c5_t1"], msgs="""
2026-03-20T13:10|C|yo any news on the tender comparison? deadline's coming up|
2026-03-20T13:40|A|Not done yet. I owe it to you. Target is early next week. I'll message when it's ready.|1
2026-03-20T13:42|C|ok. the maybes are still maybes btw|
2026-03-20T13:50|A|Understood. Nothing is decided.|3
""".strip()),
    dict(key="c5_c2", client=5, refs=["INT-073"], msgs="""
2026-04-14T17:25|C|signed the tender paperwork and sent it to the company. half, 75k like we said|
2026-04-14T17:50|A|Got it. Please forward the company's receipt for the file.|1
2026-04-14T17:52|C|will do. maybe tmrw|
""".strip()),
    dict(key="c5_c3", client=5, refs=["INT-096"], msgs="""
2026-05-08T08:45|C|re lisbon, i told camille what you said. her employer might allow remote, she's asking|
2026-05-08T09:10|A|Fine. It's still a possibility and it's not in your numbers.|1
""".strip()),
    dict(key="c5_c4", client=5, refs=["MTG:c5_m2"], msgs="""
2026-05-27T19:10|C|camille's employer said no remote for now. lisbon is off the table for the moment|
2026-05-27T19:35|A|Understood. I'll record it as off for now; it can come back. No budget changes.|1
2026-05-27T19:37|C|yeah. the puppy is expensive enough lol|
""".strip()),
    dict(key="c5_c5", client=5, refs=["INT-109"], msgs="""
2026-06-04T11:00|A|Reminder: policy summaries for the broker. He needs them for the disability quotes.|
2026-06-04T11:12|C|ugh yes. this week|1
""".strip()),
]

SCENARIOS = [
    dict(id="SCN-C5-01", client=5, title="Tender offer: might sell, mis-recorded intention, decision to sell half",
         dims="context|certainty|currency|provenance",
         arc="Feb 'might sell some'; advisor email says planning to sell; comparison overdue; corrected Mar 27; advisor suggests half; client decides Apr 14.",
         records=[("INT-034", "origin: might sell, not decided"),
                  ("c5_t1", "advisor email states planning to sell; correction, comparison and recommendation (half)"),
                  ("c5_c1", "client asks for comparison; maybes stay maybes"),
                  ("INT-062", "client corrects: has not decided"),
                  ("c5_m1", "advisor recommends half, client undecided; paperwork deadline discussed"),
                  ("INT-073", "client decides to sell half (about $75,000)"),
                  ("c5_c2", "client reports signing; receipt pending")]),
    dict(id="SCN-C5-02", client=5, title="Relocating to Lisbon: possibility, conditions, off for now",
         dims="certainty|currency",
         arc="Daydream-to-plan on May 7; two stated conditions; employer says no remote on May 27; off for now.",
         records=[("INT-096", "origin: possibility, not a plan"),
                  ("c5_c3", "employer might allow remote; still a possibility"),
                  ("c5_m2", "two conditions restated; specialist declined; kept as maybe"),
                  ("c5_c4", "employer says no remote; off for the moment")]),
    dict(id="SCN-C5-03", client=5, title="Disability cover: wanted, not bought",
         dims="certainty|context|provenance",
         arc="Client wants cover but decides nothing; policy summaries repeatedly pending; broker quotes take about three weeks.",
         records=[("INT-109", "origin: insurance for a self-employed founder"),
                  ("c5_t2", "advisor asks for summaries; client wants cover but buys nothing; partial summaries found"),
                  ("c5_c5", "reminder for summaries"),
                  ("INT-136", "follow-up: broker needs three weeks"),
                  ("INT-148", "half-year review: still pending")]),
]
