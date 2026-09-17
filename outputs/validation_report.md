# Validation report

## 1. Elevation: 1 m lidar vs independent 1/3 arc-second DEM

- Points compared: **4,000**
- Mean difference: **-0.02 m**, median **-0.00 m**
- RMS difference: **0.68 m**
- Within 2 m: **98.5%** of points

The two products are produced independently, so this level of agreement confirms the mosaic is correctly georeferenced and in metres above NAVD88. Residual scatter is expected: the 1/3 arc-second product averages over ~10 m and cannot resolve the street-scale relief the 1 m product captures.

## 2. Grades on known steep streets

| Street | Published | Computed | Difference | Verdict |
|---|---|---|---|---|
| Filbert Street | 31.5% | 32.9% | +1.4% | ok |
| 22nd Street | 31.5% | 32.6% | +1.1% | ok |
| Jones Street | 29.0% | 31.1% | +2.1% | ok |
| Bradford Street | 41.0% | 33.1% | -7.9% | under-reported |
| Prentiss Street | 37.0% | 32.9% | -4.1% | ok |
| Nevada Street | 35.0% | 25.2% | -9.8% | under-reported |
| Baden Street | 32.0% | 34.5% | +2.5% | ok |
| Duboce Avenue | 27.5% | 28.8% | +1.3% | ok |

**6 of 8** streets agree within 5 percentage points.

Nevada Street is the one substantial disagreement, and it is a classification issue rather than an elevation one: the pitch that gives Nevada Street its published 35% is tagged `steps` in OpenStreetMap, and this table deliberately measures only drivable classes. The stairway edge itself is computed at 34.6%, which matches the published figure closely. The model was left unchanged.

Where the computed value is lower, the cause is the smoothing chain rather than the elevation data, and the trade-off is deliberate. Published 'steepest street' figures are measured over the single steepest pitch, sometimes only 15-20 m long. Bradford Street, the steepest street in the city, illustrates the whole chain: sampled raw at 5 m it reads 41.4% against a published 41%; sampled raw at 10 m, 36.8% (which is why 5 m was adopted); with the 50 m Savitzky-Golay window applied within the edge, 36.9%; and as the pipeline actually computes it -- smoothed across whole street segments and reconciled at intersections -- 33.1%. So the smoothing costs roughly eight percentage points on the very shortest extreme pitches.

That cost is accepted because the alternative is worse. With a narrower window, localised lidar artefacts survived and pushed 22nd Street and Baden Street to the 60% plausibility ceiling, and smoothing edge-by-edge instead of segment-by-segment gave the two edges either side of an intersection different elevations for the same corner. Since the object of this project is to find *flat* routes, attenuating the peak of a 41% wall is a far cheaper error than inventing gradients on flat ground.

## 3. Known flat corridors

| Corridor | Km | Gain per km | Mean abs grade | Verdict | Discovered by the model? |
|---|---|---|---|---|---|
| The Wiggle (as a route) | 1.8 | 29.7 m | n/a | efficient climb | yes |
| Market Street (Embarcadero to Castro) | 11.0 | 8.3 m | 1.6% | flat | yes |
| Valencia Street | 3.0 | 6.0 m | 1.0% | flat | yes |
| Golden Gate Park (JFK / MLK drives) | 11.5 | 10.5 m | 1.8% | flat | yes |
| The Panhandle (Fell / Oak) | 4.6 | 7.9 m | 2.0% | flat | yes |
| Embarcadero | 8.0 | 1.1 m | 0.2% | flat | yes |
| Great Highway / western edge | 12.6 | 1.0 m | 0.2% | flat | no |
| Alemany / San Jose corridor | 24.3 | 12.0 m | 2.0% | flat | yes |

For scale, the steep streets in section 2 run at 20-40 m of climbing per kilometre of street, and the Embarcadero and the Great Highway -- the two genuinely level corridors in the city -- come out under 1 m/km.

### The Wiggle

The Wiggle is measured as a *route* rather than as a set of street names, because Duboce Avenue and Scott Street both climb hard outside the corridor itself, so any name-based average is meaningless. Routing the trip the Wiggle exists to serve -- Market Street at Duboce, to Haight Street at Masonic -- gives:

| | Distance | Climb | Net rise | Excess climb | Max grade |
|---|---|---|---|---|---|
| Shortest route | 1.78 km | 66.5 m | 47.3 m | **19.2 m** | 15.5% |
| Flat route (the Wiggle) | 1.79 km | 53.2 m | 47.3 m | **5.9 m** | 13.1% |

Both routes must gain the same 47 m. The shortest one throws away 19 m of extra climbing doing it; the flat one throws away 6 m. Verdict: **efficient climb**. The model reproduces the Wiggle without being told it exists.

## 4. Does the model route the Wiggle?

Bicycle routing from Market Street at Duboce to Haight Street at Masonic -- the trip the Wiggle exists to serve.

| Objective | Distance | Climb | Max grade | Wiggle streets used |
|---|---|---|---|---|
| shortest | 1783 m | 66.5 m | 15.5% | Haight Street, Waller Street, Webster Street |
| balanced | 2413 m | 49.8 m | 9.7% | Fell Street, Haight Street, Pierce Street, Scott Street, Waller Street, Webster Street |
| min_climb | 1790 m | 53.2 m | 13.1% | Haight Street, Pierce Street, Waller Street, Webster Street |

## 5. Notes on targets the model does *not* reproduce

- **Great Highway / western edge** measures as flat (about 1 m/km) but is *not* selected as an important corridor. This is a legitimate result, not a failure: the corridor metric rewards street that connects neighborhood pairs, and the Great Highway runs along the ocean edge with the city on only one side, so very few neighborhood pairs have any reason to use it. It is flat but not structurally useful.
- **Nevada Street** disagrees by 10 points because its published pitch is a stairway in OpenStreetMap; see section 2.
- **Bradford Street** disagrees by 8 points because of the smoothing chain; see section 2.
