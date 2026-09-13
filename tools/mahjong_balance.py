"""Reproducible, standard-library-only experiments for the four-slot game.

The fourth unmatched tile is a terminal loss. A resolved playable hand therefore
has at most three distinct faces. Geometry exactly follows GameState.kt, including
half-tile overlap and side blocking. Solver answers are WIN, LOSS, or UNKNOWN;
node-budget exhaustion is never counted as a loss.

Examples:
  python tools/mahjong_balance.py selftest
  python tools/mahjong_balance.py run --output docs/balance-experiments/pilot --trials 4
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

WIN, LOSS, UNKNOWN = 1, -1, 0
VERSION = 1


def bits(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def shape_positions(name):
    if name == "garden50":
        p = [(6 - length + 2 * x, 2 * y, 0)
             for y, length in enumerate([3, 4, 5, 6, 5, 4, 3]) for x in range(length)]
        p += [(6 - length + 2 * x, 1 + 2 * y, 1)
              for y, length in enumerate([2, 3, 4, 4, 3, 2]) for x in range(length)]
        return p + [(5, 5, 2), (5, 7, 2)]
    sizes = {"flat64": [8], "terrace116": [8, 6, 4],
             "stack192": [8, 8, 8], "stack256": [8, 8, 8, 8]}[name]
    return [(8 - size + 2 * x, 8 - size + 2 * y, z)
            for z, size in enumerate(sizes) for y in range(size) for x in range(size)]


class Geometry:
    def __init__(self, positions):
        self.positions = positions
        self.n = len(positions)
        self.full = (1 << self.n) - 1
        self.top, self.left, self.right, self.affects = [[0] * self.n for _ in range(4)]
        for i, (x, y, z) in enumerate(positions):
            for j, (xx, yy, zz) in enumerate(positions):
                dx, dy = xx - x, yy - y
                if zz > z and abs(dx) < 2 and abs(dy) < 2:
                    self.top[i] |= 1 << j
                if zz == z and abs(dy) < 2:
                    if dx == -2:
                        self.left[i] |= 1 << j
                    if dx == 2:
                        self.right[i] |= 1 << j
            for j in bits(self.top[i] | self.left[i] | self.right[i]):
                self.affects[j] |= 1 << i
        self.initial_free = self.free(self.full)

    def is_free(self, i, mask):
        return not (self.top[i] & mask) and (not (self.left[i] & mask) or not (self.right[i] & mask))

    def free(self, mask):
        return sum(1 << i for i in bits(mask) if self.is_free(i, mask))

    def remove(self, mask, free, i):
        child = mask ^ (1 << i)
        frontier = free ^ (1 << i)
        for j in bits(self.affects[i] & child & ~frontier):
            if self.is_free(j, child):
                frontier |= 1 << j
        return child, frontier

    def random_order(self, rng):
        mask, free, order = self.full, self.initial_free, []
        while mask:
            i = rng.choice(list(bits(free)))
            order.append(i)
            mask, free = self.remove(mask, free, i)
        return order


@dataclass
class Deal:
    geometry: Geometry
    faces: list[int]
    witness: list[int]
    k: int


def generate(geometry, k, seed, generator):
    rng = random.Random(seed)
    order = geometry.random_order(rng)
    base, remainder = divmod(geometry.n // 2, k)
    pair_counts = [base] * k
    for face in rng.sample(range(k), remainder):
        pair_counts[face] += 1
    if generator == "immediate":
        pairs = [f for f, count in enumerate(pair_counts) for _ in range(count)]
        rng.shuffle(pairs)
        sequence = [f for f in pairs for _ in range(2)]
    elif generator == "buffer":
        counts, hand, sequence = [2 * p for p in pair_counts], 0, []
        while any(counts):
            options = [f for f, count in enumerate(counts)
                       if count and (hand.bit_count() < 3 or hand & (1 << f))]
            face = rng.choice(options)
            sequence.append(face)
            counts[face] -= 1
            hand ^= 1 << face
        assert hand == 0
    else:
        raise ValueError(generator)
    faces = [-1] * geometry.n
    for i, face in zip(order, sequence):
        faces[i] = face
    return Deal(geometry, faces, order, k)


class Solver:
    """Exact DFS with transpositions, bounded per top-level query.

    Hand parity is uniquely determined by remaining-board mask for a fixed deal;
    free frontier likewise. Only mask is required in the transposition key.
    Witness states initialize certified WIN entries. Winning DFS paths add further
    entries; LOSS is stored only after every child is rigorously proved losing.
    """

    def __init__(self, deal, budget=2000):
        self.deal, self.g, self.budget = deal, deal.geometry, budget
        if len(deal.faces) != self.g.n or any(f < 0 or f >= deal.k for f in deal.faces):
            raise ValueError("Every position needs a face in [0, k).")
        if any(count % 2 for count in Counter(deal.faces).values()):
            raise ValueError("Every face needs an even total tile count.")
        self.facebits = [1 << f for f in deal.faces]
        self.by_face = [sum(1 << i for i, f in enumerate(deal.faces) if f == face)
                        for face in range(deal.k)]
        self.cache = {0: WIN}
        # Arbitrary deals may omit a witness. Never seed their initial state WIN.
        if deal.witness:
            if len(deal.witness) != self.g.n or len(set(deal.witness)) != self.g.n:
                raise ValueError("A witness must remove every tile exactly once.")
            mask, free, hand, states = self.g.full, self.g.initial_free, 0, [self.g.full]
            for i in deal.witness:
                if i < 0 or i >= self.g.n or not free & (1 << i):
                    raise ValueError("A witness contains a geometrically illegal pick.")
                mask, free = self.g.remove(mask, free, i)
                hand ^= self.facebits[i]
                if hand.bit_count() >= 4:
                    raise ValueError("A witness fills all four unmatched slots.")
                states.append(mask)
            if mask or hand:
                raise ValueError("A witness must clear the board and hand.")
            self.cache.update((state, WIN) for state in states)
        self.nodes = 0
        self.total_nodes = 0

    def query(self, mask, hand, free):
        self.nodes = 0
        result = self._dfs(mask, hand, free)
        self.total_nodes += self.nodes
        return result

    def _dfs(self, mask, hand, free):
        if hand.bit_count() >= 4:
            return LOSS
        known = self.cache.get(mask)
        if known is not None:
            return known
        if self.nodes >= self.budget:
            return UNKNOWN
        self.nodes += 1
        if not mask:
            return WIN if not hand else LOSS
        matching = 0
        for f in bits(hand):
            matching |= self.by_face[f] & free
        candidates = matching if hand.bit_count() == 3 else free
        # Matches first; opening a blocked neighbor is the second preference.
        ordered = sorted(bits(candidates),
                         key=lambda i: (not bool(self.facebits[i] & hand),
                                        -(self.g.affects[i] & mask).bit_count(), i))
        unknown = False
        for i in ordered:
            child, frontier = self.g.remove(mask, free, i)
            result = self._dfs(child, hand ^ self.facebits[i], frontier)
            if result == WIN:
                self.cache[mask] = WIN
                return WIN
            unknown |= result == UNKNOWN
            if self.nodes >= self.budget and unknown:
                return UNKNOWN
        result = UNKNOWN if unknown else LOSS
        if result != UNKNOWN:
            self.cache[mask] = result
        return result


def state_row(solver, mask, hand, free, meta, depth):
    wins, losses, unknowns, matching, win_faces = [], [], [], [], set()
    start_nodes = solver.total_nodes
    for i in bits(free):
        child, frontier = solver.g.remove(mask, free, i)
        result = solver.query(child, hand ^ solver.facebits[i], frontier)
        (wins if result == WIN else losses if result == LOSS else unknowns).append(i)
        if result == WIN:
            win_faces.add(solver.deal.faces[i])
        if hand & solver.facebits[i]:
            matching.append(i)
    row = dict(meta, depth=depth, remaining_board=mask.bit_count(), hand=hand.bit_count(),
               free=free.bit_count(), safe=len(wins), losing=len(losses), unknown=len(unknowns),
               safe_distinct_faces=len(win_faces), immediate_matches=len(matching),
               safe_immediate_matches=len(set(wins) & set(matching)),
               solver_nodes=solver.total_nodes - start_nodes,
               complete_classification=not unknowns)
    return row, wins


def sample_safe_walk(deal, seed, budget, samples):
    """Sample a safe trajectory selected by randomized certified-winning actions.

    At non-sampled turns, shuffle moves and take the first proved winning action.
    At sampled turns, classify every next physical pick and choose a proved win.
    Unknown alternatives are excluded from path selection; this is selection bias,
    deliberately reported in the analysis rather than called a random-player path.
    """
    rng, solver = random.Random(seed ^ 0x8A31), Solver(deal, budget)
    mask, hand, free, rows = solver.g.full, 0, solver.g.initial_free, []
    max_depth = deal.geometry.n - 12  # exclude states with <12 board tiles
    sample_depths = set(round(i * max_depth / max(1, samples - 1)) for i in range(samples))
    for depth in range(max_depth + 1):
        if depth in sample_depths:
            row, choices = state_row(solver, mask, hand, free, {}, depth)
            rows.append(row)
            if not choices:
                return rows, "no_certified_move"
            i = rng.choice(choices)
        else:
            choices = list(bits(free))
            rng.shuffle(choices)
            for i in choices:
                child, frontier = solver.g.remove(mask, free, i)
                if solver.query(child, hand ^ solver.facebits[i], frontier) == WIN:
                    break
            else:
                return rows, "no_certified_move"
        mask, free = solver.g.remove(mask, free, i)
        hand ^= solver.facebits[i]
    return rows, "complete"


def heuristic_win(deal, seed):
    rng, g = random.Random(seed ^ 0x3319), deal.geometry
    mask, hand, free = g.full, 0, g.initial_free
    while mask:
        options = list(bits(free))
        matching = [i for i in options if hand & (1 << deal.faces[i])]
        if matching:
            options = matching
        else:
            scored = [(g.remove(mask, free, i)[1].bit_count(), i) for i in options]
            best = max(s for s, _ in scored)
            options = [i for s, i in scored if s == best]
        i = rng.choice(options)
        mask, free = g.remove(mask, free, i)
        hand ^= 1 << deal.faces[i]
        if hand.bit_count() >= 4:
            return False
    return not hand


def verify_witness(deal):
    g = deal.geometry
    mask, hand, free = g.full, 0, g.initial_free
    assert len(set(deal.witness)) == g.n
    assert all(v % 2 == 0 for v in Counter(deal.faces).values())
    for i in deal.witness:
        assert free & (1 << i)
        mask, free = g.remove(mask, free, i)
        hand ^= 1 << deal.faces[i]
        assert hand.bit_count() <= 3
        assert free == g.free(mask)
    assert mask == hand == 0


def brute(deal, mask, hand):
    if hand.bit_count() >= 4:
        return LOSS
    if not mask:
        return WIN if not hand else LOSS
    return WIN if any(brute(deal, mask ^ (1 << i), hand ^ (1 << deal.faces[i])) == WIN
                      for i in bits(deal.geometry.free(mask))) else LOSS


def selftest():
    g = Geometry([(0, 0, 0), (2, 0, 0), (4, 0, 0), (1, 1, 1)])
    assert g.initial_free == (1 << 2) | (1 << 3)
    assert g.free(g.full ^ (1 << 3)) == (1 << 0) | (1 << 2)
    # Two higher layers can cover lower tiles even when there is no middle tile.
    assert Geometry([(0, 0, 0), (1, 1, 2)]).initial_free == 2
    assert Geometry([(0, 0, 0), (2, 0, 2)]).initial_free == 3
    # A side tile offset by half a tile vertically still blocks that side.
    assert Geometry([(0, 0, 0), (2, 1, 0), (4, 0, 0)]).initial_free == 5
    for name in ["garden50", "flat64", "terrace116", "stack192", "stack256"]:
        geometry = Geometry(shape_positions(name))
        for generator in ["immediate", "buffer"]:
            for k in [2, min(16, geometry.n // 2), geometry.n // 2]:
                verify_witness(generate(geometry, k, 11, generator))
    # Compare ALL subsets against independent full enumeration on tiny deals.
    geometry = Geometry([(2*x, 0, z) for z in range(2) for x in range(3)])
    for faces in set(itertools.permutations([0, 0, 1, 1, 2, 2])):
        deal = Deal(geometry, list(faces), [], 3)
        solver = Solver(deal, 100000)
        solver.cache = {0: WIN}  # arbitrary assignments have no planted witness
        for mask in range(1 << 6):
            hand = 0
            for i in bits(geometry.full ^ mask):
                hand ^= 1 << faces[i]
            assert solver.query(mask, hand, geometry.free(mask)) == brute(deal, mask, hand)
    # A zero budget must say unknown, and never poison the losing-state cache.
    deal = Deal(Geometry([(2*i, 0, 0) for i in range(8)]), list(range(4))*2, [], 4)
    solver = Solver(deal, 0)
    solver.cache = {0: WIN}
    assert solver.query(solver.g.full, 0, solver.g.initial_free) == UNKNOWN
    assert solver.g.full not in solver.cache
    assert solver.query(0b11110000, 0b1111, 0b11110000) == LOSS
    # A matching pick is not necessarily globally safe. The isolated A at index9
    # consumes the buffered A but leaves ABCDABCD, which cannot clear in 4 slots.
    # Taking the row's left A opens a winning B C D D C B A A continuation.
    geometry = Geometry([(2*i, 2, 0) for i in range(8)] + [(7, 0, 0), (7, 4, 0)])
    deal = Deal(geometry, [0, 1, 2, 3, 0, 1, 2, 3, 0, 0], [], 4)
    solver = Solver(deal, 100000)
    solver.cache = {0: WIN}
    mask, free = geometry.remove(geometry.full, geometry.initial_free, 8)
    bad_mask, bad_free = geometry.remove(mask, free, 9)
    good_mask, good_free = geometry.remove(mask, free, 0)
    assert solver.query(bad_mask, 0, bad_free) == LOSS
    assert solver.query(good_mask, 0, good_free) == WIN
    raw = Deal(Geometry([(2*i, 0, 0) for i in range(8)]), list(range(4))*2, [], 4)
    solver = Solver(raw, 100000)
    assert solver.query(solver.g.full, 0, solver.g.initial_free) == LOSS
    try:
        Solver(Deal(raw.geometry, raw.faces, list(range(8)), 4))
    except ValueError:
        pass
    else:
        raise AssertionError("A losing witness must be rejected.")
    print("Selftests passed: geometry, all generator witnesses, 5,760 exact states, timeout semantics.")


def run(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    rows, board_rows = [], []
    config = dict(version=VERSION, **vars(args), python_random="MT19937", endgame_min_board=12,
                  sampling="uniformly spaced depths on randomized solver-certified safe walk",
                  status_semantics="unknown is neither win nor loss")
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    for shape_index, name in enumerate(args.shapes.split(",")):
        geometry = Geometry(shape_positions(name))
        for generator_index, generator in enumerate(args.generators.split(",")):
            for k in map(int, args.k.split(",")):
                if k > geometry.n // 2:
                    continue
                group_start = time.time()
                for trial in range(args.trials):
                    seed = args.seed + shape_index * 1_000_000 + generator_index * 100_000 + k * 1000 + trial
                    deal = generate(geometry, k, seed, generator)
                    state_rows, walk_status = sample_safe_walk(deal, seed, args.budget, args.samples)
                    meta = dict(shape=name, n=geometry.n, k=k, generator=generator, trial=trial, seed=seed)
                    rows.extend(dict(meta, **row) for row in state_rows)
                    board_rows.append(dict(meta, initial_free=geometry.initial_free.bit_count(),
                                           walk_status=walk_status,
                                           heuristic_win=heuristic_win(deal, seed)))
                group_rows = [r for r in rows if r["shape"] == name and r["generator"] == generator and r["k"] == k]
                complete = [r for r in group_rows if r["complete_classification"]]
                print(f"{name:10s} {generator:9s} K={k:2d} states={len(group_rows)} exact={len(complete)} "
                      f"mean_safe={sum(r['safe'] for r in complete)/max(1,len(complete)):.2f} "
                      f"seconds={time.time()-group_start:.1f}", flush=True)
                write_csv(out / "states.csv", rows)
                write_csv(out / "boards.csv", board_rows)
    # Separate larger cheap-player sample, distinct seeds; reports policy, not solvability.
    heuristic_rows = []
    for shape_index, name in enumerate(args.shapes.split(",")):
        geometry = Geometry(shape_positions(name))
        for generator_index, generator in enumerate(args.generators.split(",")):
            for k in map(int, args.k.split(",")):
                if k > geometry.n // 2:
                    continue
                wins = 0
                for trial in range(args.heuristic_trials):
                    seed = args.seed + 50_000_000 + shape_index*1_000_000 + generator_index*100_000 + k*1000 + trial
                    wins += heuristic_win(generate(geometry, k, seed, generator), seed)
                heuristic_rows.append(dict(shape=name, n=geometry.n, generator=generator, k=k,
                                           trials=args.heuristic_trials, wins=wins))
    write_csv(out / "heuristic.csv", heuristic_rows)
    config["elapsed_seconds"] = time.time() - started
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Saved {len(rows)} state rows; elapsed {time.time()-started:.1f}s", flush=True)


def write_csv(path, rows):
    if rows:
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def summarize(directory):
    directory = Path(directory)
    with (directory / "states.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    groups = {}
    for row in rows:
        for hand in ["all", row["hand"]]:
            key = (row["shape"], row["generator"], int(row["k"]), hand)
            groups.setdefault(key, []).append(row)
    summary = []
    for (shape, generator, k, hand), state_rows in groups.items():
        exact = [r for r in state_rows if int(r["unknown"]) == 0]
        n, ne = len(state_rows), len(exact)
        mean = lambda field, rs=state_rows: sum(int(r[field]) for r in rs) / len(rs) if rs else None
        percent = lambda condition: 100 * sum(condition(r) for r in exact) / ne if ne else None
        summary.append(dict(shape=shape, generator=generator, k=k, hand=hand,
                            states=n, exact_states=ne, mean_free=mean("free"),
                            mean_safe_lower=mean("safe"),
                            mean_safe_upper=mean("safe") + mean("unknown"),
                            mean_safe_exact=mean("safe", exact),
                            mean_safe_faces_exact=mean("safe_distinct_faces", exact),
                            mean_matching=mean("immediate_matches"),
                            forced_percent_exact=percent(lambda r: int(r["safe"]) == 1),
                            two_three_percent_exact=percent(lambda r: 2 <= int(r["safe"]) <= 3),
                            above_three_percent_exact=percent(lambda r: int(r["safe"]) > 3),
                            unknown_branches=sum(int(r["unknown"]) for r in state_rows),
                            total_branches=sum(int(r["free"]) for r in state_rows)))
    write_csv(directory / "summary.csv", summary)
    totals = dict(states=len(rows),
                  boards=len({(r["shape"], r["generator"], r["k"], r["seed"]) for r in rows}),
                  exact_states=sum(int(r["unknown"]) == 0 for r in rows),
                  unknown_branches=sum(int(r["unknown"]) for r in rows),
                  total_branches=sum(int(r["free"]) for r in rows),
                  safe_branches=sum(int(r["safe"]) for r in rows),
                  losing_branches=sum(int(r["losing"]) for r in rows),
                  hand3_states=sum(r["hand"] == "3" for r in rows),
                  exact_hand3_states=sum(r["hand"] == "3" and int(r["unknown"]) == 0 for r in rows),
                  immediate_matches_not_proven_safe=sum(int(r["immediate_matches"]) - int(r["safe_immediate_matches"])
                                                       for r in rows))
    if (directory / "heuristic.csv").exists():
        with (directory / "heuristic.csv").open(newline="", encoding="utf-8") as f:
            heuristic = list(csv.DictReader(f))
        for row in heuristic:
            n, wins = int(row["trials"]), int(row["wins"])
            if not n:
                continue
            p, z = wins/n, 1.959963984540054
            denominator = 1 + z*z/n
            center = (p + z*z/(2*n))/denominator
            margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))/denominator
            row.update(win_rate=p, wilson95_lower=center-margin, wilson95_upper=center+margin)
        write_csv(directory / "heuristic_summary.csv", heuristic)
        totals["heuristic_trials"] = sum(int(r["trials"]) for r in heuristic)
    (directory / "totals.json").write_text(json.dumps(totals, indent=2), encoding="utf-8")
    print(f"Saved summaries for {len(rows)} sampled states.")


def combine(output, directories):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    configs = []
    for filename in ["states.csv", "boards.csv", "heuristic.csv"]:
        rows = []
        for directory in directories:
            with (Path(directory) / filename).open(newline="", encoding="utf-8") as f:
                rows.extend(csv.DictReader(f))
        write_csv(output / filename, rows)
    for directory in directories:
        configs.append(json.loads((Path(directory) / "config.json").read_text(encoding="utf-8")))
    (output / "config.json").write_text(json.dumps(dict(source_runs=configs), indent=2), encoding="utf-8")
    summarize(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("selftest")
    s = subs.add_parser("summarize")
    s.add_argument("directory")
    c = subs.add_parser("combine")
    c.add_argument("output")
    c.add_argument("directories", nargs="+")
    p = subs.add_parser("run")
    p.add_argument("--output", required=True)
    p.add_argument("--shapes", default="garden50,flat64,terrace116,stack192")
    p.add_argument("--generators", default="immediate,buffer")
    p.add_argument("--k", default="6,8,12,16,24,32")
    p.add_argument("--trials", type=int, default=4)
    p.add_argument("--samples", type=int, default=6)
    p.add_argument("--budget", type=int, default=1000)
    p.add_argument("--heuristic-trials", type=int, default=200)
    p.add_argument("--seed", type=int, default=73191)
    args = parser.parse_args()
    if args.command == "selftest":
        selftest()
    elif args.command == "summarize":
        summarize(args.directory)
    elif args.command == "combine":
        combine(args.output, args.directories)
    else:
        run(args)


if __name__ == "__main__":
    main()
