# Validation report

## 1. Elevation: 1 m lidar vs independent 1/3 arc-second DEM

- Points compared: **4,000**
- Mean difference: **-0.00 m**, median **+0.00 m**
- RMS difference: **0.51 m**
- Within 2 m: **99.0%** of points

The two products are produced independently, so this level of agreement confirms the mosaic is correctly georeferenced and in metres above NAVD88. Residual scatter is expected: the 1/3 arc-second product averages over ~10 m and cannot resolve the street-scale relief the 1 m product captures.

## 2. The steepest streets the model finds

There is nothing to compare these against. San Francisco's steep streets have published gradients, and the pipeline this was ported from was checked against them (six of eight within five points, with the same smoothing and sampling settings used here). Atlanta has no such table that could be traced to a measurement, so this section is a list of readings, not a validation. Streets are ranked by the average gradient of a whole block of at least 80 m, the figure a lidar artefact cannot fake; the steepest pitch within the block is beside it.

| Street | Block average | Rise | Block length | Steepest pitch |
|---|---|---|---|---|
| Mattison Cove Northeast | 18.9% | 20 m | 106 m | 23.8% |
| Abner Place Northwest | 16.3% | 31 m | 187 m | 20.0% |
| Main Street Northwest | 16.1% | 14 m | 87 m | 24.1% |
| Forrest Terrace Southeast | 16.0% | 20 m | 125 m | 23.4% |
| Lynn Drive Southwest | 15.7% | 27 m | 171 m | 21.4% |
| Mary George Avenue Northwest | 15.2% | 33 m | 219 m | 20.8% |
| West Wesley Ridge | 15.1% | 32 m | 212 m | 22.6% |
| Noble Creek Drive Northwest | 15.0% | 15 m | 99 m | 20.0% |
| Whitewater Trail | 14.7% | 14 m | 97 m | 20.5% |
| Neal Street Northwest | 14.5% | 18 m | 127 m | 24.8% |
| Thornton Street Southwest | 14.3% | 21 m | 145 m | 16.8% |
| West Avenue Northwest | 14.3% | 14 m | 96 m | 19.2% |

The steepest-pitch column is the less trustworthy of the two. 42 of 35,223 drivable blocks (0.1%) carry a pitch of 30% or more somewhere along them, and most of those are steep for a few metres on a block that is otherwise gentle: a real kink (a ramp, a culvert), or ground that has changed since the lidar was flown in 2018, as in the subdivisions built since. They are left in rather than filtered, and they are why the route finder's *steepest* figure should be read as an upper bound.

Two families of artefact were removed before this table was made, because they sat on the streets that matter most. A street passing under a freeway or railway bridge read as a hump, the bare-earth surface there being interpolated from the embankments either side: Windsor Street under I-20 carried 11 m of climbing that does not exist. And the last metres of a street approaching a bridge fell away, because the mapped end of a bridge usually sits out over the cut. Both are now found from the street geometry and bridged (`network.find_dem_gaps`, `elevation.ABUTMENT_PAD_M`).

## 3. Known flat corridors

| Corridor | Km | Gain per km | Mean abs grade | Verdict | Discovered by the model? |
|---|---|---|---|---|---|
| The BeltLine (as a route) | 6.3 | 5.9 m | n/a | less wasted climbing | yes |
| BeltLine Eastside Trail | 3.6 | 12.8 m | 1.6% | flat | yes |
| BeltLine Westside Trail | 5.5 | 10.3 m | 1.6% | flat | yes |
| BeltLine Southside and Southeast trails | 8.4 | 6.3 m | 1.1% | flat | yes |
| Proctor Creek Greenway | 4.0 | 1.6 m | 0.8% | flat | yes |
| DeKalb Avenue (beside the Georgia Railroad) | 5.2 | 8.2 m | 1.5% | flat | yes |
| Marietta Street (beside the Western & Atlantic) | 6.8 | 5.2 m | 1.6% | flat | yes |
| Lee Street / Murphy Avenue (beside the railway south) | 9.6 | 8.7 m | 1.6% | flat | yes |
| Peachtree Street, Downtown to Midtown (the ridge road) | 6.1 | 12.2 m | 1.9% | flat | yes |

A corridor is called flat when its mean absolute gradient is under 2%. Gain per kilometre is shown for scale but depends on the direction each block was drawn in, so a railway grade climbing steadily one way shows a figure and the same grade drawn the other way shows none.

### The BeltLine

The BeltLine is also measured as a *route*, by asking the model the question the corridor answers: Glenwood Avenue at Bill Kennedy Way, to Piedmont Park at 10th Street and Monroe Drive, by bicycle. The direct way is Boulevard.

| | Distance | Climb | Net rise | Excess climb | Max grade |
|---|---|---|---|---|---|
| Shortest route | 6.03 km | 60.9 m | -39.4 m | **60.9 m** | 7.7% |
| Flat route | 6.30 km | 37.1 m | -39.4 m | **37.1 m** | 12.0% |

The trip ends 39 m lower than it starts, so none of the climbing is forced. The shortest route climbs 61 m it did not have to; the flat one 37 m, 39% less, for 5% more distance. Verdict: **less wasted climbing**.

## 4. Does the model route The BeltLine?

Bicycle routing from Glenwood Avenue at Bill Kennedy Way to Piedmont Park at 10th Street and Monroe Drive. The corridor was not named to the model.

| Objective | Distance | Climb | Max grade | Share on the corridor | Corridor streets used |
|---|---|---|---|---|---|
| shortest | 6027 m | 60.9 m | 7.7% | 0% | Atlanta Beltline Eastside Trail |
| balanced | 6307 m | 38.4 m | 10.0% | 90% | Atlanta Beltline Eastside Trail, Atlanta Beltline Southeast Trail, Krog Street Northeast |
| min_climb | 6301 m | 37.1 m | 12.0% | 90% | Atlanta Beltline Eastside Trail, Atlanta Beltline Southeast Trail, Bill Kennedy Way, Krog Street Northeast |
