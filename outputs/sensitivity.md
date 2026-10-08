# Sensitivity analysis

Every configuration below rebuilds the full pipeline -- elevation sampling, metrics, every neighborhood-pair route, corridors and passes -- with one parameter changed from the baseline. The question is whether the findings survive the modelling choices.

Baseline: sample_spacing_m = 5, smooth_window_m = 25, gain_deadband_m = 0.5, dem_sigma_m = 3, point_rank = 0

## The headline (walking, all ordered neighborhood pairs)

| Configuration | Change | Flattest: extra distance | Flattest: climbing avoided | Shortest: mean climb | Flattest: mean climb | Grade-averse: mean steepest |
|---|---|---|---|---|---|---|
| baseline | the configuration used for every published figure | +7% | 19% | 560 ft | 448 ft | 9.4% |
| spacing_2.5m | denser DEM sampling | +7% | 19% | 561 ft | 449 ft | 8.8% |
| spacing_10m | coarser DEM sampling | +7% | 19% | 561 ft | 449 ft | 9.8% |
| window_12.5m | half the profile smoothing | +7% | 20% | 562 ft | 450 ft | 9.2% |
| window_50m | double the profile smoothing | +7% | 19% | 558 ft | 446 ft | 10.0% |
| deadband_0.25m | half the dead-band | +7% | 19% | 561 ft | 450 ft | 9.4% |
| deadband_1m | double the dead-band | +7% | 20% | 559 ft | 447 ft | 9.4% |
| sigma_0m | no spatial pre-filter on the DEM | +7% | 19% | 560 ft | 448 ft | 9.5% |
| sigma_6m | double the spatial pre-filter | +7% | 19% | 563 ft | 452 ft | 9.2% |
| point_rank_1 | second-nearest access intersection | +7% | 20% | 556 ft | 446 ft | 9.3% |
| point_rank_2 | third-nearest access intersection | +7% | 20% | 552 ft | 441 ft | 8.8% |

## Elevation model checks

| Configuration | Mattison Cove | Abner Pl | Lynn Dr | Mary George Ave | Eastside Trail climb/km | DeKalb Ave climb/km | Network climb/km | DEM RMS vs 1/3" |
|---|---|---|---|---|---|---|---|---|
| baseline | 23.8% | 21.2% | 21.4% | 21.2% | 12.7 m | 9.1 m | 17.0 m | 0.51 m |
| spacing_2.5m | 23.8% | 21.2% | 21.4% | 21.2% | 12.7 m | 9.1 m | 17.1 m | 0.51 m |
| spacing_10m | 23.9% | 20.9% | 21.1% | 21.1% | 12.4 m | 9.1 m | 17.0 m | 0.51 m |
| window_12.5m | 25.6% | 21.0% | 21.1% | 21.2% | 12.7 m | 9.1 m | 17.2 m | 0.51 m |
| window_50m | 23.3% | 21.3% | 21.2% | 21.1% | 12.7 m | 9.1 m | 16.8 m | 0.51 m |
| deadband_0.25m | 23.8% | 21.2% | 21.4% | 21.2% | 12.7 m | 9.1 m | 17.1 m | 0.51 m |
| deadband_1m | 23.8% | 21.2% | 21.4% | 21.2% | 12.7 m | 9.1 m | 16.9 m | 0.51 m |
| sigma_0m | 24.0% | 21.3% | 21.2% | 21.3% | 12.7 m | 9.2 m | 17.1 m | 0.51 m |
| sigma_6m | 23.9% | 21.1% | 21.2% | 21.1% | 12.5 m | 8.9 m | 17.0 m | 0.51 m |
| point_rank_1 | 23.8% | 21.2% | 21.4% | 21.2% | 12.7 m | 9.1 m | 17.0 m | 0.51 m |
| point_rank_2 | 23.8% | 21.2% | 21.4% | 21.2% | 12.7 m | 9.1 m | 17.0 m | 0.51 m |

The steep streets are the model's own steepest sustained blocks, and the figure is the steepest pitch on the street: there are no published gradients for Atlanta to hold them to, so these columns show how far a reading moves, not whether it is right.

## Corridors, passes and the BeltLine

Corridor overlap is measured on the street itself: the length-weighted share of corridor-material edges the run has in common with the baseline. Comparing corridor names would be misleading, since a merge boundary moving by one block renames a corridor without changing where it runs.

| Configuration | Corridors found | Corridor edges shared with baseline | Lead streets of the top 12 kept | Streets that enter the top 12 | Top corridor | Top pass | BeltLine trip: excess climb (flat / shortest) |
|---|---|---|---|---|---|---|---|
| baseline | 87 | 100% | 12/12 | &mdash; | Edgewood Avenue Northeast (4.8 km) | West End 1018 ft, 184 pairs | 37.1 m / 60.9 m |
| spacing_2.5m | 86 | 95% | 11/12 | Peachtree Road | Edgewood Avenue Northeast (4.4 km) | West End 1018 ft, 184 pairs | 37.3 m / 61.0 m |
| spacing_10m | 82 | 92% | 11/12 | Peachtree Road | Edgewood Avenue Northeast (4.8 km) | West End 1018 ft, 184 pairs | 36.3 m / 60.9 m |
| window_12.5m | 85 | 89% | 12/12 | &mdash; | Edgewood Avenue Northeast (4.6 km) | West End 1018 ft, 184 pairs | 37.3 m / 60.9 m |
| window_50m | 89 | 87% | 12/12 | &mdash; | Edgewood Avenue Northeast (4.4 km) | West End 1018 ft, 176 pairs | 37.1 m / 61.3 m |
| deadband_0.25m | 85 | 99% | 12/12 | &mdash; | Edgewood Avenue Northeast (4.8 km) | West End 1018 ft, 184 pairs | 37.6 m / 60.9 m |
| deadband_1m | 84 | 98% | 11/12 | Peachtree Road | Edgewood Avenue Northeast (4.8 km) | West End 1018 ft, 184 pairs | 37.1 m / 60.9 m |
| sigma_0m | 86 | 90% | 11/12 | Peachtree Road | Edgewood Avenue Northeast (4.3 km) | West End 1018 ft, 184 pairs | 37.4 m / 60.9 m |
| sigma_6m | 81 | 89% | 11/12 | Peachtree Road | Edgewood Avenue Northeast (4.7 km) | Oakland City 1019 ft, 184 pairs | 37.2 m / 61.0 m |
| point_rank_1 | 81 | 89% | 11/12 | Peachtree Road | Edgewood Avenue Northeast (4.5 km) | West End 1018 ft, 161 pairs | 37.1 m / 60.9 m |
| point_rank_2 | 82 | 85% | 11/12 | Peachtree Road | Edgewood Avenue Northeast (9.0 km) | West End 1018 ft, 207 pairs | 37.1 m / 60.9 m |

## Reading it

- The headline trade (extra distance for climbing avoided on the flattest route) ranges from +7% / 19% to +7% / 20% across every perturbation, against +7% / 19% at baseline.
- The corridor material stays 85-99% the same street, by length, under every perturbation, and 11-12 of the baseline's 12 lead streets keep their place. What moves is the exact extent and composite name of each corridor, most under **point_rank_2** (third-nearest access intersection, 85%), and a few borderline streets drift in and out at the margin: Peachtree Road.
- The dominant pass is in West End in 10 of 11 configurations.
- The BeltLine trip (Glenwood Avenue to Piedmont Park by bicycle) lands on a discovered corridor in 11 of 11 configurations, and its flat route always wastes less climbing than the shortest one: worst case 37.6 m against 60.9 m.
