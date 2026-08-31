# Pushback to Wheels-Up

A single-file, dependency-free simulation of **KMEG**, a fictional airport with
one runway worked in mixed mode. A hundred aircraft start on stand and must all
get airborne, while a steady stream of inbounds lands on the same strip of
tarmac. Nothing may ever touch.

Open `index.html` in any browser with WebGL. Nothing is fetched at runtime
except the two webfonts, and the page renders correctly without them.

## The rules

**1. No collisions.** Four mechanisms keep aircraft apart, and a fifth measures
whether they worked.

| Mechanism | What it guarantees |
|---|---|
| **Node locks** | Every junction of the movement graph is a mutex. Grants go to the longest-waiting requester, and the wait clock starts when an aircraft first *wants* a junction rather than when it happens to fall free — so the aircraft physically at the front of a queue always wins the tie, and never ends up holding a junction it cannot reach. |
| **In-trail separation** | Each aircraft projects its own path forward and brakes to keep `hull + hull + 30 m` clear of anything standing on it. Because it tests the path rather than a heading cone, it holds through turns and merges. The scan reaches as far as the aircraft could actually need to brake, which for a landing roll at 70 m/s is over a kilometre. |
| **Runway** | One exclusive resource contested by both flows. Arrivals outrank departures: a departure only gets position clearance if it can be airborne and clear before the next arrival needs the runway, and an arrival that cannot have it goes around. |
| **Vacate guarantee** | An arrival is only cleared to land when its rapid exit is clear, so a landing aircraft can always get off. Without it a blocked exit locks the runway, and with the runway locked nothing else on the field can drain either. |
| **Conflict audit** | An independent pass measures true hull-to-hull distance for every pair each frame. `MIN HULL GAP` and `CONFLICTS` in the status bar are that measurement, not a claim. |

Two further rules stop the traffic model from eating itself:

- **Never block the box.** At a merge, an aircraft may only enter if it can also
  *clear* it. An aircraft stopped inside a junction blocks the branch it is not
  even using, and that is what turns two busy queues into a deadlock.
- **Never reserve what you cannot reach.** An aircraft held up by traffic ahead
  does not take a lock it cannot get to.

**2. The model is not transparent.** The fragment shader writes a literal
`alpha 1.0`, `gl.BLEND` is never enabled, and depth testing resolves every
surface. Coplanar decals (runway markings, apron slab joints, aircraft shadows)
use `gl.polygonOffset` rather than an alpha pass, and the grass is cut into
rectangles around every pavement footprint so no two differently coloured
surfaces are ever coplanar.

## Layout: why the two flows never cross

```
             apron  z 282..1260   (4 taxilanes east, 100 stands)
 spur x=-1420 ^                                    v collector x=1760
 BRAVO  z=+170  <==== arrivals westbound ====        |
    rapid exits x=300, 900, 1450  ^ north off the runway
 RUNWAY z=0     ==== departures and arrivals eastbound ====
 ALPHA  z=-170  <==== departures westbound ====      <
```

ALPHA sits south of the runway and BRAVO north of it, and the departure
collector rounds the *east end* of the runway rather than crossing it. The
arrival spur enters the apron west of every stand, so no departure ever uses
those nodes. The two flows therefore share only the apron taxilanes — where
they run the same direction and the ordinary node locks already cover them.
No aircraft ever crosses an opposing stream, and the flow graph stays acyclic.

## How the runway is worked

Taxiing into position takes about half a minute, and doing that serially with
the runway is what caps a single-runway airport. So position clearance is a
second, weaker resource: an aircraft may occupy the threshold while the previous
movement is still rolling out, and takes the runway itself only when it is
actually free. That is line-up-and-wait, and it cut measured departure runway
occupancy from 64 s to 35 s.

Departures all turn north off the runway; the missed approach turns south and
climbs harder, so a go-around never overtakes a departure on the same track. An
arrival breaks off early — 4.2 km out, still 200 m up — if a departure is still
on the ground, rather than pressing in to the last gate.

The result is about **36 runway movements an hour**: 100 departures and around
45 arrivals in a four-hour morning. Run it at 30× or higher.

## Controls

Drag to orbit, shift-drag or right-drag to pan, scroll to zoom. Camera presets:
**Tower**, **Final**, **Threshold**, **Apron**, **Plan**, **Chase** (follows
whoever has the runway). Speed slider, pause, reset.

## Verifying it

`index.html` has no build step, but the simulation is separable from the
renderer: concatenate its `<script>` blocks, stub `document` and the WebGL
context, and drive `step(SIM_DT)` in a loop. Over eight randomised fleet and
schedule arrangements that gives 100/100 departures and 42–47 arrivals every
time, zero hull overlaps, a minimum hull-to-hull gap of 35–41 m — the in-trail
buffer, as designed — and 1.0–2.5 km between anything airborne.

Two numbers are worth reading carefully in the status bar:

- **`MIN HULL GAP`** is the *current* closest pair, measured centre-to-centre
  minus both hull radii (`max(length, wingspan) / 2`: 32 m heavy, 19 m medium,
  15.5 m light). It sits around 35–41 m because two floors set it: the 30 m
  in-trail buffer in a taxi queue, and the 105 m stand pitch, which leaves two
  parked heavies 41 m apart.
- **`CONFLICTS`** is cumulative, not instantaneous — the number of distinct
  pairs whose hulls have *ever* overlapped. It should read 0 for the whole run.

Go-arounds are part of the model, not a failure: two to five per run is the
vacate guarantee and the runway arbitration refusing to put an aircraft
somewhere it cannot safely go.
