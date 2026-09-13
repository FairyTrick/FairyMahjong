"""Shared data and independent rule checks for the current native UI suites.

For release verification use release_compatibility_test.py and run_native_checks.py.
This module provides fixtures only; importing it never starts ADB or a test run.
"""

from functools import lru_cache
import re

from save_migration_smoke_test import catalog_faces, free_tiles, replay, require


TILE = re.compile(r".+ tile, (\d+) of (\d+), (available|blocked|game over|covered|side blocked|hand full)")


def catalog_names():
    names = catalog_faces()
    require(len(set(names.values())) == len(names), "Catalog names must distinguish every identity")
    return names


def fixture(name, positions, faces, picks=()):
    """Build a legacy v4 fixture; current UI callers explicitly add v5 orientation.

    Migration checks deliberately inject v4 and expect the app to persist v5.
    UI tests that need an unchanged save convert to v5 before injection.
    """
    state = {"version": 4, "layout": name, "positions": positions,
             "faces": faces, "picks": list(picks), "haptics": False}
    replay(state)
    return state


def winnable(state):
    """Exact small-fixture oracle independent of Android's hint implementation."""
    remaining, held = replay(state)
    faces = state["faces"]

    @lru_cache(None)
    def search(board, hand):
        if len(hand) >= 4:
            return False
        if not board:
            return not hand
        for index in free_tiles(state["positions"], set(board)):
            face = faces[index]
            child_hand = set(hand)
            if face in child_hand:
                child_hand.remove(face)
            else:
                child_hand.add(face)
            if search(tuple(tile for tile in board if tile != index), tuple(sorted(child_hand))):
                return True
        return False

    return search(tuple(sorted(remaining)), tuple(sorted(faces[index] for index in held)))
