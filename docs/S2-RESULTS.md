# S2 results — a recipe from Claude Code to the machine

Run on 2026-11-14 with the hub at aebd97b against the same machine and proxy
as S0 and S1, the phone's screen off. The assistant was a separate Claude Code
session opened in this repository by the owner; it had the `xbloom-recipe`
skill and the MCP tools and nothing else from the work that built them.

## What happened

| | Step | Result |
|---|---|---|
| 1 | Recipe from a hand-pour card (20 g; 80 °C to 60 g at 0:00, 95 °C to 180 g at 0:45, 80 °C to 300 g at 1:45) | Saved as written for 20 g, then, asked for an Omni version, scaled to 18 g at 1:15: pours of 54, 108, 108 ml with pauses 27, 29, 0 s, which keeps the starts at 0:00, 0:45 and 1:45. It reported the volumes the machine would be given, called the grind size a starting point and the pauses computed rather than timed, and put the card and the conversion in the recipe's notes |
| 2 | Load | The hub sent 8102, 8104, 8001. The machine's display showed grind `60`, ratio `15` and the start arrow. The assistant said the machine was waiting and that it had not started a brew |
| 3 | Cancel | The owner asked for the load to be cancelled; it was cancelled with `POST /api/machine/cancel` and the machine returned to home. The MCP `cancel_load` tool was not exercised on hardware, only on the simulator |

Every write of the run: 8100, 8102, 8104, 8001, 8017. Eleven MCP requests,
no error in the hub's log. No brew was started.

## Learned

- **Loading works while the machine is asleep.** It had been asleep for two
  minutes (activity 10); the load woke it and armed it.
- **The middle number on the display is the ratio, not the dose.** S0's note
  had it as the dose because that recipe's dose and ratio were both 5.
- **The assistant misread the scale.** It told the owner the cup might be
  missing because `weight_g` was 0, with the cup in place: the scale stays
  tared after a load. The skill and the `machine_state` description now say
  the weight does not show whether a cup is present (577d21e).
- Claude Code shows a project MCP server as pending until the owner approves
  it in an interactive session; after that the token helper ran without
  further setup.

## Not covered

A brew. Nothing in S0-S2 has started one, so the flow rate, the time a
temperature change takes, and whether the computed pauses give the intended
pour starts are all still unmeasured. `cancel_load` over MCP on hardware.
