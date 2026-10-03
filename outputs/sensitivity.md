# Sensitivity analysis

Every configuration below rebuilds the full pipeline -- elevation sampling, metrics, 10,080 routes, corridors and passes -- with one parameter changed from the baseline. The question is whether the findings survive the modelling choices.

Baseline: sample_spacing_m = 5, smooth_window_m = 25, gain_deadband_m = 0.5, dem_sigma_m = 3, point_rank = 0

## The headline (walking, all 1,260 ordered pairs)

| Configuration | Change | Flattest: extra distance | Flattest: climbing avoided | Shortest: mean climb | Flattest: mean climb | Grade-averse: mean steepest |
|---|---|---|---|---|---|---|
| baseline | the configuration used for every published figure | +14% | 39% | 484 ft | 281 ft | 10.8% |
| spacing_2.5m | denser DEM sampling | +14% | 40% | 484 ft | 281 ft | 10.3% |
| spacing_10m | coarser DEM sampling | +14% | 39% | 489 ft | 284 ft | 10.8% |
| window_12.5m | half the profile smoothing | +15% | 39% | 486 ft | 283 ft | 10.2% |
| window_50m | double the profile smoothing | +14% | 40% | 486 ft | 282 ft | 10.8% |
| deadband_0.25m | half the dead-band | +14% | 39% | 484 ft | 282 ft | 10.8% |
| deadband_1m | double the dead-band | +14% | 40% | 484 ft | 280 ft | 10.8% |
| sigma_0m | no spatial pre-filter on the DEM | +14% | 40% | 485 ft | 281 ft | 11.3% |
| sigma_6m | double the spatial pre-filter | +14% | 39% | 482 ft | 281 ft | 10.7% |
| point_rank_1 | second-nearest access intersection | +14% | 40% | 484 ft | 277 ft | 10.6% |
| point_rank_2 | third-nearest access intersection | +14% | 40% | 477 ft | 273 ft | 10.0% |

## Elevation model checks

| Configuration | Filbert St | Jones St | 22nd St | Bradford St | Embarcadero climb/km | Valencia climb/km | Network climb/km | DEM RMS vs 1/3" |
|---|---|---|---|---|---|---|---|---|
| baseline | 32.9% | 31.1% | 32.6% | 33.1% | 1.1 m | 6.0 m | 21.3 m | 0.68 m |
| spacing_2.5m | 33.1% | 31.4% | 32.4% | 32.3% | 1.1 m | 6.0 m | 21.3 m | 0.68 m |
| spacing_10m | 32.2% | 30.0% | 32.3% | 33.3% | 1.0 m | 6.1 m | 21.4 m | 0.68 m |
| window_12.5m | 31.7% | 29.5% | 31.7% | 36.7% | 1.1 m | 6.1 m | 21.5 m | 0.68 m |
| window_50m | 33.3% | 33.1% | 33.9% | 29.4% | 1.1 m | 6.0 m | 21.2 m | 0.68 m |
| deadband_0.25m | 32.9% | 31.1% | 32.6% | 33.1% | 1.3 m | 6.0 m | 21.3 m | 0.68 m |
| deadband_1m | 32.9% | 31.1% | 32.6% | 33.1% | 1.1 m | 6.0 m | 21.2 m | 0.68 m |
| sigma_0m | 33.3% | 31.6% | 32.9% | 32.4% | 1.0 m | 6.1 m | 21.5 m | 0.68 m |
| sigma_6m | 32.4% | 30.7% | 31.9% | 34.5% | 0.9 m | 6.0 m | 21.2 m | 0.68 m |
| point_rank_1 | 32.9% | 31.1% | 32.6% | 33.1% | 1.1 m | 6.0 m | 21.3 m | 0.68 m |
| point_rank_2 | 32.9% | 31.1% | 32.6% | 33.1% | 1.1 m | 6.0 m | 21.3 m | 0.68 m |

Published: Filbert 31.5%, Jones 29%, 22nd 31.5%, Bradford 41%.

## Corridors, passes and the Wiggle

Corridor overlap is measured on the street itself: the length-weighted share of corridor-material edges the run has in common with the baseline. Comparing corridor names would be misleading, since a merge boundary moving by one block renames a corridor without changing where it runs.

| Configuration | Corridors found | Corridor edges shared with baseline | Lead streets of the top 12 kept | Streets that enter the top 12 | Top corridor | Top pass | Wiggle excess climb (flat / shortest) |
|---|---|---|---|---|---|---|---|
| baseline | 53 | 100% | 12/12 | &mdash; | Valencia Street (6.9 km) | Golden Gate Park 255 ft, 119 pairs | 5.9 m / 19.2 m |
| spacing_2.5m | 52 | 90% | 11/12 | 24th Street | Valencia Street (7.4 km) | Golden Gate Park 255 ft, 119 pairs | 6.0 m / 19.2 m |
| spacing_10m | 53 | 82% | 9/12 | California Street; Geary Boulevard; Hyde Street | Valencia Street (10.5 km) | Golden Gate Park 255 ft, 119 pairs | 6.0 m / 19.2 m |
| window_12.5m | 53 | 78% | 11/12 | Market Street | Valencia Street (6.3 km) | Golden Gate Park 255 ft, 119 pairs | 5.6 m / 19.0 m |
| window_50m | 52 | 71% | 9/12 | Market Street; Oak Street Cyclepath; Onondaga Avenue | Valencia Street (7.2 km) | Golden Gate Park 255 ft, 119 pairs | 5.8 m / 18.8 m |
| deadband_0.25m | 52 | 98% | 11/12 | Greenwich Street | Valencia Street (6.9 km) | Golden Gate Park 255 ft, 119 pairs | 5.9 m / 19.2 m |
| deadband_1m | 53 | 99% | 12/12 | &mdash; | Valencia Street (6.9 km) | Golden Gate Park 255 ft, 119 pairs | 5.9 m / 19.2 m |
| sigma_0m | 52 | 81% | 9/12 | 23rd Street; 24th Street; Divisadero Street | Valencia Street (6.7 km) | Golden Gate Park 255 ft, 119 pairs | 6.0 m / 19.5 m |
| sigma_6m | 58 | 80% | 8/12 | Alabama Street; Greenwich Street; Market Street; Steiner Street | Valencia Street (7.4 km) | Golden Gate Park 255 ft, 119 pairs | 5.1 m / 18.3 m |
| point_rank_1 | 45 | 86% | 10/12 | Divisadero Street; Oakdale Avenue | Valencia Street (7.1 km) | Golden Gate Park 255 ft, 119 pairs | 5.9 m / 19.2 m |
| point_rank_2 | 49 | 75% | 11/12 | Divisadero Street | Valencia Street (7.1 km) | Golden Gate Park 255 ft, 112 pairs | 5.9 m / 19.2 m |

## Reading it

- The headline trade (extra distance for climbing avoided on the flattest route) ranges from +14% / 39% to +15% / 40% across every perturbation, against +14% / 39% at baseline.
- The corridor material stays 71-99% the same street, by length, under every perturbation, and 8-12 of the baseline's 12 lead streets keep their place. What moves is the exact extent and composite name of each corridor, most under the profile smoothing window (**window_50m**, 71%), and a few borderline streets drift in and out at the margin: 23rd Street, 24th Street, Alabama Street, California Street, Divisadero Street, Geary Boulevard, Greenwich Street, Hyde Street, Market Street, Oak Street Cyclepath, Oakdale Avenue, Onondaga Avenue, Steiner Street.
- The dominant pass is in Golden Gate Park in 11 of 11 configurations.
- The Wiggle is discovered as a corridor in 11 of 11 configurations, and its flat route always wastes less climbing than the shortest one: worst case 6.0 m against 18.3 m.
