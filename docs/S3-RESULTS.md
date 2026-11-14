# S3 results: start, stop and the brew record on the machine

Run on 2026-11-14 with the owner at the machine, firmware V12.0D.500, the
radio path of S0 to S2. Steps are those of `docs/PLAN.md`, S3, "Observed on
the machine".

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

## Known and left

- A hub that connects in the 5 s a machine still reports 35 after a brew
  opens a record for it, which closes as finished when 36 arrives.
- After a brew closes, the state's `weight_g` is the scale's own reading
  again, lower than the record's last weight by what was carried over.

## Not observed

- No 9002 or 9008 (cup on, cup off) arrived during the run. Whether the cup
  was lifted is not recorded, so this says nothing yet about those events.
- Steps 2 (stop), 3 (started at the knob) and 4 (a real brew) are still to do.
