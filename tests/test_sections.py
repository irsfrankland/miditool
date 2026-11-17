"""Section suggestions must always return valid, in-range ranges, whatever the drums look like."""

from __future__ import annotations

import random
import unittest

from tests.support import BAR, HAT, KICK, SNARE

import suggest_sections

KEYS = ("intro", "pattern1", "pattern1Fill", "pattern2", "pattern2Fill", "ending")


def song(bars: int, density=lambda bar: 8, seed=0):
    rng = random.Random(seed)
    events = []
    for bar in range(bars):
        for step in range(density(bar)):
            tick = bar * BAR + step * BAR // max(1, density(bar))
            events.append((tick, rng.choice((KICK, SNARE, HAT)), 100))
    return sorted(events), [b * BAR for b in range(bars + 1)]


class Suggest(unittest.TestCase):
    def check(self, divisions, count, label=""):
        self.assertEqual(set(divisions), set(KEYS), label)
        for key, (start, end) in divisions.items():
            self.assertTrue(1 <= start <= end <= max(1, count), (label, key, start, end, count))

    def test_valid_for_every_length(self):
        for bars in range(0, 70):
            events, bounds = song(bars, lambda b: 8 if b % 8 < 6 else 3)
            result = suggest_sections.suggest(events, bounds)
            self.check(result.divisions, bars, f"bars={bars}")

    def test_silent_file(self):
        result = suggest_sections.suggest([], [b * BAR for b in range(9)])
        self.check(result.divisions, 8)

    def test_no_boundaries(self):
        result = suggest_sections.suggest([(0, KICK, 100)], [])
        self.check(result.divisions, 0)

    def test_random_songs(self):
        for seed in range(60):
            rng = random.Random(seed)
            bars = rng.randint(1, 120)
            events, bounds = song(bars, lambda b, r=rng: r.choice((0, 2, 4, 8, 16)), seed)
            result = suggest_sections.suggest(events, bounds)
            self.check(result.divisions, bars, f"seed={seed}")
            parts = suggest_sections.song_parts(events, bounds)
            for start, end, _name in parts:
                self.assertTrue(1 <= start <= end <= bars, (seed, start, end, bars))

    def test_song_parts_cover_in_order_without_overlap(self):
        events, bounds = song(64, lambda b: 4 if b < 8 else 12 if b < 40 else 6)
        parts = suggest_sections.song_parts(events, bounds)
        previous_end = 0
        for start, end, _name in parts:
            self.assertGreater(start, previous_end)
            previous_end = end

    def test_events_outside_bars_do_not_crash(self):
        events = [(-10, KICK, 100), (10**9, SNARE, 100)]
        suggest_sections.suggest(events, [0, BAR, 2 * BAR])

    def test_zero_length_bars(self):
        suggest_sections.suggest([(0, KICK, 100)], [0, 0, 0, BAR])


if __name__ == "__main__":
    unittest.main()
