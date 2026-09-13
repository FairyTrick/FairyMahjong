"""Check shared fixture compatibility without starting ADB."""

import unittest

from meadow_ui_smoke_test import meadow_fixture
from smoke_fixtures import TILE, catalog_names, fixture, replay, winnable


class SmokeFixturesTest(unittest.TestCase):
    def test_legacy_and_migrated_saves_replay_the_same_physical_tiles(self):
        legacy = fixture("fixture-migration", [[0, 0, 0], [2, 0, 0], [0, 2, 0], [2, 2, 0]],
                         [6, 61, 6, 61], [0])
        self.assertEqual(legacy["version"], 4)
        for orientation in ("portrait", "landscape"):
            migrated = dict(legacy, version=5, orientation=orientation)
            self.assertEqual(replay(migrated), ({1, 2, 3}, [0]))
            self.assertEqual(replay(migrated), replay(legacy))
            self.assertTrue(winnable(migrated))

    def test_current_meadow_consumer_injects_v5_and_retains_a_winning_path(self):
        state = meadow_fixture()
        self.assertEqual((state["version"], state["orientation"]), (5, "portrait"))
        self.assertEqual(replay(state), (set(range(8)), []))
        self.assertTrue(winnable(state))

    def test_v5_without_orientation_is_not_silently_treated_as_v4(self):
        state = meadow_fixture()
        del state["orientation"]
        with self.assertRaisesRegex(AssertionError, "orientation"):
            replay(state)

    def test_full_hand_and_invalid_picks_are_not_winning_states(self):
        losing = fixture("fixture-loss", [[i * 2, 0, 0] for i in range(8)],
                         [0, 1, 2, 3, 0, 1, 2, 3], [0, 7, 1, 6])
        self.assertEqual(len(replay(losing)[1]), 4)
        self.assertFalse(winnable(losing))
        with self.assertRaisesRegex(AssertionError, "pick history"):
            replay(dict(losing, picks=[1]))

    def test_native_tile_parser_keeps_physical_identity_groups(self):
        names = catalog_names()
        for identity, name in names.items():
            description = f"{name} tile, {identity + 1} of {len(names)}, available. Hint: pick next"
            match = TILE.match(description)
            self.assertIsNotNone(match)
            self.assertEqual(match.groups(), (str(identity + 1), str(len(names)), "available"))


if __name__ == "__main__":
    unittest.main()
