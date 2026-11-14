# S3 results: start, stop and the brew record on the machine

Run on 2026-11-14 with the owner at the machine, firmware V12.0D.500, the
radio path of S0 to S2. Steps are those of `docs/PLAN.md`, S3, "Observed on
the machine".

## Step 3: started at the knob (done, 2026-11-14)

The step-1 recipe (25 ml at 20 °C), loaded from the dashboard at 19:47:17
while the machine was asleep (it woke and showed the recipe 2 s later),
then started with the machine's knob at 19:47:29.

| What | Result |
|---|---|
| Writes through the gate | 8102, 8104, 8004 for the load; no 8002 |
| The record | Opened by activity `35` (`opened_by: activity_35`), `hub_started: false`, carrying the recipe and its slug |
| Water | From 0.9 s to 25 ml; 40511 at 6.9 s, 40512 at 15.1 s; closed `finished` |
| After | Activity `36`, then home at 19:48:10 |

So a brew started at the machine is recorded like one started from the
page. The page offers Stop for any open brew whoever started it (the
brewing view of Home, and the browser tests' simulated knob start); on
this run nobody looked at the page during the brew, so that part is from
the code and the tests, not from the machine.

## Step 2: Stop (done, 2026-11-14)

Recipe: grinder off, 5 g, 1:20, one 100 ml pour at 20 °C and 3.0 ml/s.
Loaded and started from the dashboard; Stop pressed on the page. Five runs,
19:37 to 19:40; seconds are from the opening of each record.

| Run | Recipe | Water | Stop (40519 sent) | Machine home | Water when stopped |
|---|---|---|---|---|---|
| 1 | 100 ml | none | 18.4 s | 18.8 s | 0 ml |
| 2 | 100 ml | none | 34.9 s | 35.3 s | 0 ml |
| 3 | 25 ml (control) | from 22.1 s | 33.6 s, after the pour ended | 34.0 s | 25 ml |
| 4 | 100 ml | from 1.1 s | 13.0 s | 13.4 s | 47.7 ml |
| 5 | 100 ml | from 0.9 s | 12.2 s | 12.6 s | 45.0 ml |

**Stop works.** Each time: one 40519 through the gate, its echo within
0.4 s, activity `1` within another 0.5 s, no 40512 and no finished screen;
the record closed `stopped` with `stop_sent: true`. In runs 4 and 5 the
water count stopped rising at the stop and the next reading was 0. Stop
stays on the page.

**Runs 1 to 3 drew no water at first.** The owner, at the machine: the pump
did not take up water, and air came out of the spout. The machine reported
the pour as started (40510 at 0.6 s, as always) and the scale stayed under
1.5 g. During that time it sent 40522 again and again (six times in run 3,
between 7 and 16 s); in runs 4 and 5, which drew water at once, 40522 never
came. In run 3 the machine reported a pause and a resume (9010 and 40515 at
18.5 s with activity `31`, then 9011 and 40516 at 21.0 s with activity
`35`); the hub sent neither, and whether they came from the knob or from
the machine itself is not recorded. Water came one second after the
resume. Why the pump took air is not known; air in the line or a tank not
fully seated would fit, and neither was checked.

## Step 1: a harmless start (done, three times)

Recipe: grinder off, 5 g, 1:5, one 25 ml pour at 20 °C and 3.0 ml/s. Loaded
and started from the dashboard, three times in a row.

| What | Result |
|---|---|
| Writes through the gate, per run | 8102, 8104, 8004 for the load, then one 8002. Never a second 8002 |
| The machine's answer to 8002 | An 8002 echo, then 40502 within 30 ms |
| Water | About 25 ml of unheated water each time; the grinder did not run |
| A record per brew | Three records, each ended `finished` |
| The page | Followed the brew, with the two faults below |

**The sequence the machine reports**, the same in all three runs (seconds
after the brew opened):

| At | Signal | Meaning here |
|---|---|---|
| 0.0 | 8023 activity `35` | Brewing. About 0.4 s after the 8002 echo |
| 0.6 | 40510, pour 0 | The pour starts |
| 1.0 | first water reading above 0 | |
| 7.2 | 40511; water reads its full 25 ml | The pour ends |
| 13 to 15 | 40512 | The brew is finished; the record closes here |
| +4.5 | 8023 activity `36` | The screen after a brew |
| +20 | 40513, then 8023 activity `1` | Home again |

40522 arrived once, 4.9 s into the first run only; its meaning is unknown.

## What the run corrected

- **Brewing is activity 35**, not 34, at least with the grinder off. 34 has
  still not been seen; with the grinder on it may be the grinding phase. The
  hub treats both as a brew.
- **Water arrives in thousandths of a millilitre**: 25 ml was reported as
  `25000.0`. The official app divides the same value by 1000. The hub now
  converts where the reading enters; the three records of this run were
  converted by hand. On the page this had drawn a y axis with hundreds of
  labels; the axis now has at most five steps whatever arrives.
- **40513 is not only "abandoned"**: it also arrives when the machine leaves
  the screen after a finished brew. The record is already closed by then, so
  nothing changed, but the name in the code is narrower than the signal.
- **The machine sets its scale to zero about 5 s into the brew**, with water
  already in the cup. All three runs show it: the reading fell from 5.61 g
  to 0, from 4.95 g to 0.55 g and from 5.73 g to 0 between two samples, and
  went on rising from there, so the records ended at 16.9, 18.5 and 15.9 g
  for a 25 ml pour. With what was lost added back the cup held 22.5, 22.9
  and 21.7 g when the records closed; the last grams were still dripping.
  The hub now carries the weight across such a reset and lists each one in
  the record (`rezeroed`); the three records were corrected by hand with the
  same rule. 40522 arrived once, 0.3 s before the reset of the first run;
  whether it announces the reset is not known, since the other two runs had
  the reset without it.
- **The 25 s after a brew** (5 s still at 35, 20 s at 36) showed as "the
  machine is in use". They are now a state of their own, `finished`, and the
  page shows the final numbers and a cup, as the machine's own display does.

## Measured, not yet explained

- The water count went from 0.8 ml to 25 ml in 6.2 s: about 3.9 ml/s for a
  pour set to 3.0 ml/s. One short pour is not enough to say the rate is off;
  step 4 measures it on a real recipe.
- The 40512 came 6 to 8 s after the pour ended (13.3, 15.3 and 15.4 s).
- **40522, inferred: the pump is drawing no water.** It repeated through
  the dry starts of step 2 and never came in a start that drew water at
  once. It also came once in step 1. The page says nothing about it yet: a
  brew that pours air looks like a brew that has not started. Worth showing
  ("no water is reaching the spout") once a second dry start confirms it.
- During a pause made at the machine, the activity is `31` while the hub
  keeps the brew open; the page went on showing the brew, which is right,
  but it does not say "paused".

## Known and left

- A hub that connects in the 5 s a machine still reports 35 after a brew
  opens a record for it, which closes as finished when 36 arrives.
- After a brew closes, the state's `weight_g` is the scale's own reading
  again, lower than the record's last weight by what was carried over.

## Not observed

- No 9002 or 9008 (cup on, cup off) arrived during the run. Whether the cup
  was lifted is not recorded, so this says nothing yet about those events.
- Step 4 (a real brew) is still to do.
