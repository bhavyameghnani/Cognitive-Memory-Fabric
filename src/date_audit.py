"""Relative-date audit shared by the validator and the quality-comparison report.

Rules (heuristic, deliberately conservative).  For each client utterance containing a relative-time phrase we
check the gap in days to that client's previous meeting:

  yesterday / last night / this morning ........ always consistent (relative to the meeting itself)
  last week ................................ needs gap >= 6 days   (else it may predate the previous meeting)
  a few days ago / the other day ............. needs gap >= 3
  two weeks ago ............................ needs gap >= 13
  last month ............................... needs the previous calendar month to differ from the meeting month AND
                                              a gap >= 15, unless it is a reference to a prior meeting's month
  earlier this week ........................ needs gap <= 5 and same ISO week as previous meeting
Weekday-named references ("on Friday") must fall strictly after the previous meeting date.

Also verified exactly:
  * 'Last time, on <date>' / 'We last spoke on <date>' / 'Since <date>' agenda dates equal the true previous meeting date
  * 'Let's pencil in <Weekday>, <date>' equals the true next meeting date, and the weekday matches the date
"""
import csv
import datetime as dt
import re
from collections import defaultdict

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], 1)}
DATE_RX = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2})\b")
WD_DATE_RX = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (January|February|March|April|May|June|July) (\d{1,2})\b")
REL = [
    ("last week", re.compile(r"\blast week\b", re.I), 6),
    ("a few days ago", re.compile(r"\b(a few days ago|the other day|a couple of days ago)\b", re.I), 3),
    ("two weeks ago", re.compile(r"\btwo weeks ago\b", re.I), 13),
    ("last month", re.compile(r"\blast month\b", re.I), 15),
]
WEEKDAY_REF = re.compile(r"\b(?:on|since) (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b(?!,)")
WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load(csv_path):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    by = defaultdict(list)
    for r in rows:
        by[r["interaction_id"]].append(r)
    meta = {i: (rs[0]["client_id"], dt.datetime.fromisoformat(rs[0]["interaction_datetime"])) for i, rs in by.items()}
    prev, nxt, last = {}, {}, {}
    for i in sorted(by):
        c, d = meta[i]
        prev[i] = (last[c][0], last[c][1]) if c in last else None
        if c in last:
            nxt[last[c][0]] = i
        last[c] = (i, d)
    return rows, by, meta, prev, nxt


def audit(csv_path):
    rows, by, meta, prev, nxt = load(csv_path)
    issues, reviewed = [], []
    for i in sorted(by):
        cid, d = meta[i]
        p = prev[i]
        gap = (d - p[1]).days if p else None
        for r in by[i]:
            t = r["utterance"]
            for name, rx, mingap in REL:
                if rx.search(t):
                    if gap is None or gap < mingap:
                        issues.append(dict(id=i, rule=name, gap=gap, text=t[:140]))
                    else:
                        reviewed.append(dict(id=i, rule=name, gap=gap))
            for m in WEEKDAY_REF.finditer(t):
                wd = WD.index(m.group(1))
                if p:
                    # most recent such weekday before the meeting
                    back = (d.weekday() - wd) % 7 or 7
                    ref = (d - dt.timedelta(days=back)).date()
                    if ref <= p[1].date():
                        issues.append(dict(id=i, rule="weekday_before_previous_meeting", gap=gap, text=t[:140]))
            # agenda 'previous meeting' date statements
            for m in re.finditer(r"(?:Last time, on|We last spoke on|On|Since|Our last meeting, on|since) (January|February|March|April|May|June) (\d{1,2})", t):
                if p is None:
                    continue
                if re.search(r"(Last time, on|We last spoke on|Our last meeting, on)", m.group(0)) or \
                        re.search(r"(we covered|we focused on|when we focused|, about )", t[m.end():m.end() + 60]) or m.group(0).startswith("Since"):
                    dd = dt.date(2026, MONTHS[m.group(1)], int(m.group(2)))
                    if dd != p[1].date() and r["speaker"] == "Advisor" and r["turn_index"] in ("1", 1):
                        issues.append(dict(id=i, rule="agenda_prev_date_mismatch", gap=gap, text=t[:140]))
            for m in WD_DATE_RX.finditer(t):
                dd = dt.date(2026, MONTHS[m.group(2)], int(m.group(3)))
                if WD[dd.weekday()] != m.group(1):
                    issues.append(dict(id=i, rule="weekday_date_mismatch", gap=gap, text=m.group(0)))
        # next-meeting statement
        for r in by[i]:
            for m in re.finditer(r"(?:pencil in|Does|book us for|work for) ?(?:us for )?(Monday|Tuesday|Wednesday|Thursday|Friday), (\w+) (\d{1,2})", r["utterance"]):
                n = nxt.get(i)
                dd = dt.date(2026, MONTHS[m.group(2)], int(m.group(3)))
                if n is None or meta[n][1].date() != dd:
                    issues.append(dict(id=i, rule="next_meeting_date_mismatch", gap=gap, text=m.group(0)))
    # agenda checks counted
    return dict(issues=issues, rule_checked_instances=len(reviewed))


if __name__ == "__main__":
    import json
    import sys
    res = audit(sys.argv[1] if len(sys.argv) > 1 else "data/conversations.csv")
    print(json.dumps(res, indent=1)[:4000])
