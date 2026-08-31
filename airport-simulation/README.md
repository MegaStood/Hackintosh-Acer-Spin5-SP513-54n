# Pushback to Wheels-Up

A single-file, dependency-free simulation of **KMEG**, a fictional single-runway
airport, from first pushback to last wheels-up. One hundred aircraft start on
stand and must all reach the air, and none of them may ever touch.

Open `index.html` in any browser with WebGL. Nothing is fetched at runtime except
the two webfonts, and the page renders correctly without them.

## The rules

Two constraints drove every design decision:

**1. No collisions.** Three independent mechanisms keep aircraft apart, and a
fourth measures whether they worked.

| Mechanism | What it guarantees |
|---|---|
| **Node locks** | Every junction of the movement graph is a mutex. An aircraft may not enter a junction it does not own. Grants go to the longest-waiting requester, and the wait clock starts the moment an aircraft *wants* a junction — not when it happens to fall free — so the aircraft physically at the front of a queue always wins. |
| **In-trail separation** | Each aircraft projects its own path forward in 10 m steps and brakes to keep `hull + hull + 30 m` clear of anything standing on it. Because it tests the path rather than a heading cone, it holds through turns and merges. |
| **Runway exclusivity** | The runway is one resource, released only once the departure is airborne, then held closed for the wake-turbulence interval owed to the *next* departure's weight category. |
| **Conflict audit** | An independent pass measures true hull-to-hull distance for every pair each frame. `MIN HULL GAP` and `CONFLICTS` in the status bar are that measurement, not a claim. |

Two further rules stop the traffic model from eating itself:

- **Never block the box.** At a true merge (where a taxilane joins the collector),
  an aircraft may only enter if it can also *clear* the junction. An aircraft
  stopped inside a merge blocks the branch it is not even using, and that is what
  turns two busy queues into a deadlock.
- **Never reserve what you cannot reach.** An aircraft held up by traffic ahead
  does not take a lock it cannot get to, or the aircraft in front of it would be
  waiting on a junction owned by someone stuck behind.

Deadlock freedom comes from the layout: all movement flows one way (stand → taxilane
→ collector → taxiway ALPHA → runway 09), so the flow graph is acyclic and an
aircraft only ever waits on something ahead of it. The two rules above are what keep
the *geometric* waits acyclic too.

**2. The model is not transparent.** The fragment shader writes a literal
`alpha 1.0`, `gl.BLEND` is never enabled, and depth testing resolves every
surface. Coplanar decals (runway markings, apron slab joints, aircraft shadows)
use `gl.polygonOffset` rather than an alpha pass, and the grass is cut into
rectangles around every pavement footprint so no two differently coloured
surfaces are ever coplanar.

## The airfield

- Runway 09/27, 3200 × 45 m, with piano-key threshold, aiming points, touchdown
  zone stripes, blocky designators and edge lighting.
- Taxiway ALPHA, 170 m north of the centreline, one-way westbound to the 09 hold.
- Four apron taxilanes feeding an eastern collector; 100 nose-in stands (A01–D25)
  with lead-in lines, stop bars and jet bridges.
- Terminal, four concourse piers, control tower, maintenance hangars, radar head.

Departures run about 32/hour, so a full 100-aircraft morning takes a little over
three simulated hours — run it at 30× or higher.

## Controls

Drag to orbit, shift-drag or right-drag to pan, scroll to zoom. Camera presets:
**Tower** (from the cab, west down the runway), **Threshold**, **Apron**,
**Plan**, **Chase** (follows the next departure). Speed slider, pause, reset.

## Verifying it

`index.html` carries no build step, but the simulation is separable from the
renderer: concatenate its `<script>` blocks, stub `document` and the WebGL
context, and drive `step(SIM_DT)` in a loop. Doing that over ten randomised
fleet and schedule arrangements gives 100/100 departures every time, zero hull
overlaps, and a minimum hull-to-hull gap of 35–40 m.
