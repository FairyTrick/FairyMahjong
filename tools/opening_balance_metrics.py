"""Compare exported, paired Kotlin deals without running Android or the game code.

Usage:
  python tools/opening_balance_metrics.py --input artifacts/opening-balance/deals.jsonl --output artifacts/opening-balance/metrics.json

Each JSONL row contains shape, seed, faceCount, positions ([x2, y2, layer]),
solution (tile indices), baseline (faces), and tuned (faces). Coordinates are
half-tile units. This independently computes blockers and validates both winning
witnesses. Pair peeling is an opening-only proxy, not a solver or win-rate model.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import itertools
import json
from pathlib import Path
import random


def indices(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


class Geometry:
    def __init__(self, positions):
        self.positions = positions
        self.full = (1 << len(positions)) - 1
        self.blockers = []
        for x, y, layer in positions:
            above = left = right = 0
            for index, (other_x, other_y, other_layer) in enumerate(positions):
                dx, dy = other_x - x, other_y - y
                if other_layer > layer and abs(dx) < 2 and abs(dy) < 2:
                    above |= 1 << index
                if other_layer == layer and abs(dy) < 2:
                    if dx == -2:
                        left |= 1 << index
                    elif dx == 2:
                        right |= 1 << index
            self.blockers.append((above, left, right))
        self.initial_free = self.free(self.full)

    def is_free(self, index, remaining):
        if not remaining & (1 << index):
            return False
        above, left, right = self.blockers[index]
        return not (above & remaining) and (
            not (left & remaining) or not (right & remaining)
        )

    def free(self, remaining):
        return tuple(index for index in indices(remaining) if self.is_free(index, remaining))


@lru_cache(maxsize=64)
def geometry_for(positions):
    return Geometry(positions)


def initially_free_pairs(geometry, faces):
    counts = Counter(faces[index] for index in geometry.initial_free)
    return sum(count // 2 for count in counts.values())


def peel_pairs(geometry, faces, shared_seed, cap=8):
    """Uniformly choose among unordered, currently free, identical tile pairs.

    Both variants start with the same RNG seed. They can diverge as their legal
    choices change. At most eight pairs are removed, always with an empty hand
    after each pair. A stop means a buffered pick is needed by this greedy route;
    it does not imply that the position is losing or that every route needs one.
    """
    rng = random.Random(shared_seed)
    remaining = geometry.full
    for count in range(cap):
        by_face = defaultdict(list)
        for index in geometry.free(remaining):
            by_face[faces[index]].append(index)
        pairs = sorted(
            pair
            for matching in by_face.values()
            for pair in itertools.combinations(matching, 2)
        )
        if not pairs:
            return count
        first, second = rng.choice(pairs)
        remaining &= ~(1 << first | 1 << second)
    return cap


def witness_metrics(geometry, faces, solution, opening_picks):
    if sorted(solution) != list(range(len(faces))):
        raise ValueError("The witness must remove every tile exactly once")
    if any(count % 2 for count in Counter(faces).values()):
        raise ValueError("Each face must have an even total count")
    remaining = geometry.full
    hand = set()
    area = peak = 0
    hand_at_boundary = None
    for offset, index in enumerate(solution):
        if not geometry.is_free(index, remaining):
            raise ValueError(f"Witness pick {offset + 1} is blocked")
        remaining &= ~(1 << index)
        face = faces[index]
        if face in hand:
            hand.remove(face)
        else:
            hand.add(face)
        if len(hand) >= 4:
            raise ValueError(f"Witness loses at pick {offset + 1}")
        if offset < opening_picks:
            area += len(hand)
            peak = max(peak, len(hand))
        if offset + 1 == opening_picks:
            hand_at_boundary = frozenset(hand)
    if remaining or hand:
        raise ValueError("Witness does not clear board and hand")
    return {"buffer_area": area, "buffer_peak": peak, "hand_at_boundary": hand_at_boundary}


class Aggregate:
    def __init__(self):
        self.boards = 0
        self.changed_boards = 0
        self.changed_tiles = 0
        self.max_changed_tiles = 0
        self.latest_changed_witness_pick = 0
        self.suffix_identical = 0
        self.boundary_hand_identical = 0
        self.face_counts_identical = 0
        self.free_tiles = 0
        self.variants = {
            name: {"initial_pairs": Counter(), "peel_pairs": Counter(),
                   "buffer_area": 0, "buffer_peak": Counter()}
            for name in ("baseline", "tuned")
        }

    def add(self, row, geometry, opening_picks, measurements):
        self.boards += 1
        self.free_tiles += len(geometry.initial_free)
        changed = [i for i, (old, new) in enumerate(zip(row["baseline"], row["tuned"])) if old != new]
        self.changed_boards += bool(changed)
        self.changed_tiles += len(changed)
        self.max_changed_tiles = max(self.max_changed_tiles, len(changed))
        changed_picks = [offset + 1 for offset, index in enumerate(row["solution"]) if index in changed]
        self.latest_changed_witness_pick = max(self.latest_changed_witness_pick, max(changed_picks, default=0))
        self.suffix_identical += all(pick <= opening_picks for pick in changed_picks)
        self.boundary_hand_identical += (
            measurements["baseline"]["hand_at_boundary"] == measurements["tuned"]["hand_at_boundary"]
        )
        self.face_counts_identical += Counter(row["baseline"]) == Counter(row["tuned"])
        for name, metrics in measurements.items():
            variant = self.variants[name]
            variant["initial_pairs"][metrics["initial_pairs"]] += 1
            variant["peel_pairs"][metrics["peel_pairs"]] += 1
            variant["buffer_area"] += metrics["buffer_area"]
            variant["buffer_peak"][metrics["buffer_peak"]] += 1

    def report(self):
        def mean_hist(hist):
            return round(sum(value * count for value, count in hist.items()) / self.boards, 4)

        def hist_json(hist, limit=None):
            last = max(hist, default=0) if limit is None else limit
            return {str(value): hist[value] for value in range(last + 1)}

        variants = {}
        for name, metrics in self.variants.items():
            peel = metrics["peel_pairs"]
            variants[name] = {
                "mean_initially_free_disjoint_pairs": mean_hist(metrics["initial_pairs"]),
                "initially_free_disjoint_pairs_histogram": hist_json(metrics["initial_pairs"]),
                "mean_pairs_peeled_capped_at_8": mean_hist(peel),
                "pairs_peeled_capped_at_8_histogram": hist_json(peel, 8),
                "needs_buffer_before_completing_4_pairs_pct": round(100 * sum(peel[i] for i in range(4)) / self.boards, 4),
                "needs_buffer_before_completing_8_pairs_pct": round(100 * sum(peel[i] for i in range(8)) / self.boards, 4),
                "mean_opening_witness_buffer_area": round(metrics["buffer_area"] / self.boards, 4),
                "opening_witness_buffer_peak_histogram": hist_json(metrics["buffer_peak"], 3),
            }
        return {
            "boards": self.boards,
            "changed_boards": self.changed_boards,
            "changed_boards_pct": round(100 * self.changed_boards / self.boards, 4),
            "mean_changed_tiles": round(self.changed_tiles / self.boards, 4),
            "mean_changed_tiles_among_changed_boards": round(self.changed_tiles / self.changed_boards, 4) if self.changed_boards else 0,
            "max_changed_tiles": self.max_changed_tiles,
            "latest_changed_witness_pick_1_based": self.latest_changed_witness_pick,
            "identical_suffix_boards": self.suffix_identical,
            "identical_boundary_hand_boards": self.boundary_hand_identical,
            "identical_face_count_boards": self.face_counts_identical,
            "mean_initially_free_tiles": round(self.free_tiles / self.boards, 4),
            **variants,
        }


def analyze(source, opening_picks=16):
    groups = defaultdict(Aggregate)
    seen = set()
    face_counts = Counter()
    for line_number, line in enumerate(source, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            positions = tuple(tuple(position) for position in row["positions"])
            if not positions or any(len(position) != 3 for position in positions):
                raise ValueError("Positions must be nonempty triples")
            if len(positions) != len(set(positions)):
                raise ValueError("Duplicate positions")
            if any(not isinstance(value, int) for position in positions for value in position):
                raise ValueError("Position coordinates must be integers")
            geometry = geometry_for(positions)
            identity = (row["shape"], row["seed"], row["faceCount"])
            if identity in seen:
                raise ValueError(f"Duplicate paired deal: {identity}")
            seen.add(identity)
            face_counts[row["faceCount"]] += 1
            shared_seed = int.from_bytes(hashlib.sha256(
                f"opening-pair-peel-v1:{row['shape']}:{row['seed']}:{row['faceCount']}".encode("utf-8")
            ).digest()[:8], "big")
            measurements = {}
            for name in ("baseline", "tuned"):
                faces = row[name]
                if len(faces) != len(positions) or any(not isinstance(face, int) or face < 0 for face in faces):
                    raise ValueError(f"Invalid {name} faces")
                metrics = witness_metrics(geometry, faces, row["solution"], opening_picks)
                metrics["initial_pairs"] = initially_free_pairs(geometry, faces)
                metrics["peel_pairs"] = peel_pairs(geometry, faces, shared_seed)
                measurements[name] = metrics
            orientation = "landscape" if "landscape" in row["shape"].lower() else "portrait"
            for group in ("overall", f"orientation:{orientation}", f"shape:{row['shape']}"):
                groups[group].add(row, geometry, opening_picks, measurements)
        except (ValueError, KeyError, TypeError, IndexError) as error:
            raise ValueError(f"Input line {line_number}: {error}") from error
    if not seen:
        raise ValueError("No deal rows were provided")
    return {
        "method": {
            "version": 1,
            "opening_witness_picks": opening_picks,
            "pair_peeling_cap": 8,
            "pair_choice": "Uniform among unordered identical pairs whose two tiles are currently free; same seeded RNG per paired deal.",
            "buffer_metric": "The selected greedy route cannot continue taking immediate pairs. This is not a win-rate estimate or a proof that buffering is unavoidable on every route.",
            "witness_buffer_area": "Sum of occupied hand slots after each of the opening witness picks, with matching resolved first.",
            "suffix_boundary": f"Exact faces at witness picks {opening_picks + 1} onward, plus separately checked hand contents after pick {opening_picks}.",
            "orientation": "Shape IDs containing landscape are landscape; all others are portrait.",
        },
        "face_count_histogram": dict(sorted(face_counts.items())),
        "validated_winning_witnesses": 2 * len(seen),
        "overall": groups["overall"].report(),
        "by_orientation": {key.removeprefix("orientation:"): value.report() for key, value in sorted(groups.items()) if key.startswith("orientation:")},
        "by_shape": {key.removeprefix("shape:"): value.report() for key, value in sorted(groups.items()) if key.startswith("shape:")},
    }


def self_test():
    positions = ((0, 0, 0), (2, 0, 0), (4, 0, 0), (2, 0, 1))
    geometry = Geometry(positions)
    # Exhaustively compare blocker masks with direct coordinate comparisons.
    for remaining in range(1 << len(positions)):
        expected = []
        for index in indices(remaining):
            x, y, layer = positions[index]
            others = [positions[j] for j in indices(remaining) if j != index]
            covered = any(z > layer and abs(xx - x) < 2 and abs(yy - y) < 2 for xx, yy, z in others)
            left = any(z == layer and xx == x - 2 and abs(yy - y) < 2 for xx, yy, z in others)
            right = any(z == layer and xx == x + 2 and abs(yy - y) < 2 for xx, yy, z in others)
            if not covered and not (left and right):
                expected.append(index)
        assert geometry.free(remaining) == tuple(expected)
    assert geometry.initial_free == (0, 2, 3)
    assert initially_free_pairs(geometry, (0, 1, 0, 1)) == 1
    assert peel_pairs(geometry, (0, 1, 0, 1), 7) == 1
    witness = witness_metrics(geometry, (0, 1, 0, 1), (0, 2, 3, 1), 4)
    assert witness["buffer_area"] == 2 and witness["buffer_peak"] == 1
    flat = Geometry(tuple((2 * index, 0, 0) for index in range(4)))
    assert peel_pairs(flat, (0, 1, 1, 0), 7) == 2
    try:
        witness_metrics(flat, (0, 1, 2, 3), (0, 1, 2, 3), 4)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid witness should be rejected")
    print("PASS: independent blockers, pair peeling, witness metrics, invalid input rejection")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--opening-picks", type=int, default=16)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.input is None:
        parser.error("--input is required unless --self-test is used")
    if args.opening_picks < 1:
        parser.error("--opening-picks must be positive")
    with args.input.open(encoding="utf-8-sig") as source:
        report = analyze(source, args.opening_picks)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # Keep terminal output compact; detailed per-shape histograms live in the file.
    print(json.dumps({"validated_winning_witnesses": report["validated_winning_witnesses"],
                      "overall": report["overall"], "by_orientation": report["by_orientation"]}, separators=(",", ":")))


if __name__ == "__main__":
    main()
