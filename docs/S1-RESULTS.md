# S1 results — hub core on hardware

Run on 2026-11-14 with the hub at commit 64a62b8, started natively with
`uv run xbloom-hub`, against the same machine (firmware `V12.0D.500`) and
Android proxy as S0. mDNS advertising was off in the proxy app and the phone's
screen was on. Every load used the S0 recipe (grinder off, 5 g, one 25 ml pour
at 20 °C) with no beans and a dripper and empty cup in place.

## Attended steps

| | Step | Result |
|---|---|---|
| 1 | `POST /api/machine/load` | 200. Gate passed 8102, 8104, 8004. The hub reported `armed` (activity 31) within the request; the event stream showed idle → armed |
| 2 | `POST /api/machine/cancel` (8017) | 200. The machine answered 8017 and reported activity 1 0.8 s later; the owner saw the screen go to the recipe and back to home |
| 3 | Proxy app stopped and started | The hub noticed the drop within a second, reconnected to the proxy when it returned, and was back at `idle` 12.6 s after the drop, about 6 s after the proxy was reachable. The gate passed 8100 on the new session. Activity was 1 beforehand |
| 4 | `release`, official app, `acquire` | After `release` the hub stayed `released` and made no connection for the two minutes it was left. The official app connected to the machine and was closed. `acquire` brought the hub back to `idle` within 4 s. Activity was 1 beforehand |

Every write of the run, in order: 8100, 8102, 8104, 8004, 8017, 8100, 8100.
No start opcode, no brew, no error or refusal in the hub's log.

## What this settles

- **8017 cancels a loaded recipe.** It was unverified on hardware until this
  run. It was tried only from the armed state, which is the only state the
  hub sends it in.
- The hub reconnects by itself after the proxy restarts.
- `release` hands the machine to the official app and `acquire` takes it back.

## Found during the run

- With the phone's screen off the proxy reported no advertisements and the
  hub stayed `offline` with `machine_not_found` until the screen was turned
  on, then connected without a restart. Same limit as S0, item 5.
- The weight before the load read 0 g with a dripper and cup on the scale:
  the tare from an earlier load was still in effect. A cup check by weight
  cannot rely on the scale having an untared reading. Open question for S3.
- Stopping the hub waited on an open event stream. Fixed after the run with
  a 3-second graceful-shutdown limit: with the simulator and a stream open,
  the hub now stops 3.1 s after SIGTERM.
- xbloom-py logs the machine's serial number at INFO level. Hub logs must be
  treated as containing it.

## Not covered

Reconnecting with the phone's screen locked; a start or a real brew; a load
while the machine is asleep; 8017 from any state other than armed.
