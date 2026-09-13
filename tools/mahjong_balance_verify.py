"""Independent, reproducible QA for mahjong_balance.py; standard library only.

Run: python tools/mahjong_balance_verify.py
The reference uses direct coordinate tests and a separately memoized recurrence.
All 2,520 four-pair assignments and all 256 remaining subsets are checked on
each small shape, including terminal and nonterminal losing states.
"""

from collections import Counter
from functools import lru_cache
import itertools
import json
from pathlib import Path
import time

from mahjong_balance import Deal, Geometry, LOSS, Solver, WIN, generate, shape_positions


def directly_free(positions, remaining, index):
    if not remaining & (1 << index):
        return False
    x, y, layer = positions[index]
    blocked_left = blocked_right = False
    for j, (xx, yy, other_layer) in enumerate(positions):
        if j == index or not remaining & (1 << j):
            continue
        if other_layer > layer and abs(xx - x) < 2 and abs(yy - y) < 2:
            return False
        if other_layer == layer and abs(yy - y) < 2:
            blocked_left |= xx == x - 2
            blocked_right |= xx == x + 2
    return not (blocked_left and blocked_right)


def direct_frontier(positions, remaining):
    return tuple(i for i in range(len(positions)) if directly_free(positions, remaining, i))


def verify_small_shape(name, positions, assignments):
    geometry = Geometry(positions)
    frontiers = [direct_frontier(positions, mask) for mask in range(256)]
    frontier_masks = [sum(1 << i for i in free) for free in frontiers]
    for mask in range(256):
        assert geometry.free(mask) == frontier_masks[mask], (name, mask, "frontier")
        for i in frontiers[mask]:
            child, free = geometry.remove(mask, frontier_masks[mask], i)
            assert child == mask & ~(1 << i)
            assert free == frontier_masks[child], (name, mask, i, "incremental frontier")

    results = Counter()
    for faces in assignments:
        parities = [0] * 256
        for mask in range(1, 256):
            index = (mask & -mask).bit_length() - 1
            parities[mask] = parities[mask & (mask - 1)] ^ (1 << faces[index])

        @lru_cache(None)
        def reference(mask):
            if parities[mask].bit_count() >= 4:
                return False
            if mask == 0:
                return True
            return any(reference(mask & ~(1 << i)) for i in frontiers[mask])

        solver = Solver(Deal(geometry, list(faces), [], 4), budget=100000)
        # Arbitrary face assignments have no certified witness to seed the cache.
        solver.cache = {0: WIN}
        for mask in range(256):
            expected = WIN if reference(mask) else LOSS
            actual = solver.query(mask, parities[mask], frontier_masks[mask])
            assert actual == expected, (name, faces, mask, actual, expected)
            results["states"] += 1
            results["winning_states" if expected == WIN else "losing_states"] += 1
            if expected == LOSS and parities[mask].bit_count() <= 3:
                results["nonterminal_losing_states"] += 1
        results["winning_initial_deals" if reference(255) else "losing_initial_deals"] += 1
    return dict(shape=name, assignments=len(assignments), **results)


def verify_generated_witnesses():
    results = Counter()
    for shape in ["garden50", "flat64", "terrace116", "stack192", "stack256"]:
        positions = shape_positions(shape)
        geometry = Geometry(positions)
        for k in sorted({1, 3, 4, 6, 16, len(positions) // 2}):
            for method in ["immediate", "buffer"]:
                for seed in range(20):
                    deal = generate(geometry, k, seed, method)
                    counts = Counter(deal.faces)
                    assert len(counts) == k
                    assert all(count > 0 and count % 2 == 0 for count in counts.values())
                    assert max(counts.values()) - min(counts.values()) <= 2
                    assert sorted(deal.witness) == list(range(len(positions)))
                    solver = Solver(deal)
                    remaining = (1 << len(positions)) - 1
                    hand = set()
                    certified = {remaining}
                    for depth, index in enumerate(deal.witness):
                        assert directly_free(positions, remaining, index), (shape, k, method, seed, depth)
                        remaining &= ~(1 << index)
                        face = deal.faces[index]
                        if face in hand:
                            hand.remove(face)
                        else:
                            hand.add(face)
                        assert len(hand) <= 3
                        certified.add(remaining)
                        results["witness_steps"] += 1
                    assert not remaining and not hand
                    # Every seed entry is a directly verified witness suffix;
                    # no unrelated board subset is optimistically certified.
                    assert set(solver.cache) == certified
                    assert set(solver.cache.values()) == {WIN}
                    results["deals"] += 1
    return dict(results)


def main():
    start = time.perf_counter()
    assignments = sorted(set(itertools.permutations([0, 0, 1, 1, 2, 2, 3, 3])))
    assert len(assignments) == 2520
    shapes = {
        "row8": [(2 * x, 0, 0) for x in range(8)],
        "flat4x2": [(2 * x, 2 * y, 0) for y in range(2) for x in range(4)],
        "stack4x1x2": [(2 * x, 0, z) for z in range(2) for x in range(4)],
        "offset8": [(2 * x, 0, 0) for x in range(5)] + [(1, 1, 1), (5, 1, 1), (5, 1, 2)],
    }
    small = []
    for name, positions in shapes.items():
        result = verify_small_shape(name, positions, assignments)
        small.append(result)
        print(json.dumps(result), flush=True)
    generated = verify_generated_witnesses()
    output = {
        "status": "passed",
        "reference": "direct coordinate legality and separately memoized exhaustive recurrence",
        "arbitrary_deal_solver_cache": "reset to {0: WIN}; no planted solution assumed",
        "small_shapes": small,
        "total_compared_states": sum(row["states"] for row in small),
        "generated_witnesses": generated,
        "elapsed_seconds": round(time.perf_counter() - start, 3),
    }
    destination = Path(__file__).resolve().parents[1] / "docs/balance-experiments/verification.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output), flush=True)


if __name__ == "__main__":
    main()
