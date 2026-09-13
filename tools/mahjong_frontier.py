"""Reproduce geometry-only frontier sampling and random-placement formula checks.

Run from any directory with Python 3.10+: python tools/mahjong_frontier.py
No dependencies. This does not estimate winning moves or player win rates.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import random
import statistics


def garden():
    positions = []
    for row, length in enumerate([3, 4, 5, 6, 5, 4, 3]):
        positions.extend((6 - length + 2 * col, 2 * row, 0) for col in range(length))
    for row, length in enumerate([2, 3, 4, 4, 3, 2]):
        positions.extend((6 - length + 2 * col, 1 + 2 * row, 1) for col in range(length))
    return positions + [(5, 5, 2), (5, 7, 2)]


def rectangles(sizes):
    positions = []
    for layer, size in enumerate(sizes):
        offset = (8 - size) // 2
        positions.extend(
            (2 * x, 2 * y, layer)
            for y in range(offset, offset + size)
            for x in range(offset, offset + size)
        )
    return positions


def blocker_masks(positions):
    """Exactly GardenLayout.isGeometricallyFree's half-tile overlap rules."""
    masks = []
    for i, (x, y, layer) in enumerate(positions):
        above = left = right = 0
        for j, (other_x, other_y, other_layer) in enumerate(positions):
            if other_layer > layer and abs(x - other_x) < 2 and abs(y - other_y) < 2:
                above |= 1 << j
            if other_layer == layer and abs(y - other_y) < 2:
                if other_x - x == -2:
                    left |= 1 << j
                if other_x - x == 2:
                    right |= 1 << j
        masks.append((1 << i, above, left, right))
    return masks


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def describe(values):
    return {
        "min": min(values),
        "p05": percentile(values, 0.05),
        "p25": percentile(values, 0.25),
        "median": percentile(values, 0.5),
        "p75": percentile(values, 0.75),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "mean": round(statistics.mean(values), 4),
        "observations": len(values),
    }


def sample_geometry(positions, seeds):
    masks = blocker_masks(positions)
    n = len(positions)
    values = []
    intervals = [[], [], [], []]
    opening = None
    for seed in range(seeds):
        rng = random.Random(seed)
        state = (1 << n) - 1
        for pick in range(n):
            available = [
                bit for bit, above, left, right in masks
                if state & bit and not state & above
                and (not state & left or not state & right)
            ]
            assert available, "Geometry cannot be cleared"
            if opening is None:
                opening = len(available)
            if pick < 0.8 * n and n - pick >= 12:
                values.append(len(available))
                intervals[min(3, int(pick / (0.2 * n)))].append(len(available))
            state ^= rng.choice(available)
    return {
        "N": n,
        "opening_F": opening,
        "pooled": describe(values),
        **{
            name: describe(interval)
            for name, interval in zip(
                ["first_20pct", "20to40pct", "40to60pct", "60to80pct"], intervals
            )
        },
    }


def even_counts(n, k):
    pairs_per_face, extras = divmod(n // 2, k)
    return [2 * (pairs_per_face + 1)] * extras + [2 * pairs_per_face] * (k - extras)


def expected_three_hand_matches(n, k, frontier):
    """Random even-balanced placement; first three face-blind picks are distinct.

    Distinct hand triples have probability proportional to the product of their
    original copy counts. After their removal, every frontier cell has the same
    remaining-population marginal. This is not a model of informed player states.
    """
    weight = weighted_matching_copies = 0
    for counts in itertools.combinations(even_counts(n, k), 3):
        product = math.prod(counts)
        weight += product
        weighted_matching_copies += product * (sum(counts) - 3)
    return frontier * weighted_matching_copies / weight / (n - 3)


def continuous_equal_copy_roots(n, frontier):
    # 2 <= 3 F (N/K - 1)/(N - 3) <= 3.
    return [
        round(frontier * n / (n - 3 + frontier), 3),
        round(3 * frontier * n / (2 * (n - 3) + 3 * frontier), 3),
    ]


def elementary_symmetric_4(counts):
    coefficients = [1, 0, 0, 0, 0]
    for count in counts:
        for degree in range(4, 0, -1):
            coefficients[degree] += count * coefficients[degree - 1]
    return coefficients[4]


def formulas():
    result = {
        "assumption": "Uniform random face placement; face-blind choices, no planted solution.",
        "N_and_F": [],
        "typical_geometry_K_intervals": [],
        "probability_first4_distinct": [],
        "three_distinct_picks_equal_copies_examples": [],
    }
    for n in [50, 116, 192]:
        for frontier in [8, 12, 16, 20, 24]:
            result["N_and_F"].append({
                "N": n,
                "F": frontier,
                "even_balanced_K_with_E_hand_matches_in_2to3": [
                    k for k in range(3, n // 2 + 1)
                    if 2 <= expected_three_hand_matches(n, k, frontier) <= 3
                ],
                "equal_copies_continuous_K_interval": continuous_equal_copy_roots(n, frontier),
                "single_pair_K": n // 2,
                "single_pair_E": round(3 * frontier / (n - 3), 5),
            })
        for k in [4, 6, 8, 10, 12, 16, 20, n // 2]:
            counts = even_counts(n, k)
            result["probability_first4_distinct"].append({
                "N": n,
                "K": k,
                "counts": counts,
                "finite_without_replacement": round(
                    math.factorial(4) * elementary_symmetric_4(counts)
                    / math.prod(range(n - 3, n + 1)), 6
                ),
                "uniform_iid": round(math.prod(range(k - 3, k + 1)) / k**4, 6),
            })
    for n, frontier in [(50, 13), (64, 16), (116, 25), (192, 27)]:
        result["typical_geometry_K_intervals"].append({
            "N": n,
            "F": frontier,
            "equal_copies_continuous_K_interval": continuous_equal_copy_roots(n, frontier),
            "even_balanced_exact_integer_K_interval": [
                k for k in range(3, n // 2 + 1)
                if 2 <= expected_three_hand_matches(n, k, frontier) <= 3
            ],
            "warning": "Illustration only: early three-pick model uses a pooled geometric F, not a calibrated midgame model.",
        })
    examples = [
        (50, 25, 16), (116, 58, 16), (192, 96, 16), (192, 12, 16),
        (192, 16, 16), (192, 24, 16), (192, 32, 16), (192, 48, 16),
        (192, 96, 24), (50, 5, 16), (50, 25, 24),
    ]
    for n, k, frontier in examples:
        assert n % k == 0
        copies, remaining = n // k, n - 3
        matching = 3 * (copies - 1)
        denominator = math.comb(remaining, frontier)
        pmf = [
            math.comb(matching, j) * math.comb(remaining - matching, frontier - j) / denominator
            if 0 <= frontier - j <= remaining - matching else 0
            for j in range(min(matching, frontier) + 1)
        ]
        result["three_distinct_picks_equal_copies_examples"].append({
            "N": n, "K": k, "m": copies, "F": frontier,
            "E": round(frontier * matching / remaining, 6),
            "P0": round(pmf[0], 6),
            "P1": round(pmf[1], 6),
            "P2or3": round(sum(pmf[2:4]), 6),
        })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=500)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "docs/balance-experiments/frontier_analysis.json")
    args = parser.parse_args()
    assert args.seeds > 0
    layouts = {
        "garden50": garden(),
        "flat64": rectangles([8]),
        "stepped116": rectangles([8, 6, 4]),
        "solid192": rectangles([8, 8, 8]),
        "solid256": rectangles([8, 8, 8, 8]),
    }
    output = {
        "protocol": {
            "seeds": args.seeds,
            "seed_range": [0, args.seeds - 1],
            "face_blind_removal": "Choose uniformly among geometrically selectable tiles; ignore hand.",
            "positions": "Same half-tile overlap and left/right test as GardenLayout.isGeometricallyFree.",
            "sampling": "States before picks 0 through ceil(.8*N)-1, additionally remaining >=12.",
            "caution": "Pooled states are correlated within trajectories and do not represent winning play.",
        },
        "geometry": {name: sample_geometry(positions, args.seeds) for name, positions in layouts.items()},
        "uniform_random_placement": formulas(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    for name, result in output["geometry"].items():
        print(name, "opening", result["opening_F"], "pooled", result["pooled"])
    print(json.dumps(output["uniform_random_placement"]["typical_geometry_K_intervals"], indent=2))


if __name__ == "__main__":
    main()
