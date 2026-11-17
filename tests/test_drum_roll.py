"""The drum roll's row mapping and its Songsterr-to-MIDI bar mapping."""

from __future__ import annotations

import unittest

import tests.support  # noqa: F401  (puts scripts/ on the path)
import drum_roll

BAR_MS = [0, 2000, 4000, 6000, 8000, 10000]  # five 2-second bars
BOUNDS = [0, 1920, 3840, 5760]  # a three-bar tab, 480 ticks per quarter


def hit(tick, note=38, velocity=100):
    return [tick, note, velocity]


class Rows(unittest.TestCase):
    def test_every_standard_drum_has_a_row(self):
        names = {
            36: "kick", 35: "kick", 38: "snare", 40: "snare", 37: "snare", 39: "snare",
            42: "hat", 44: "hat", 46: "hat_open", 51: "ride", 53: "ride", 59: "ride",
            49: "crash", 57: "crash", 55: "crash", 52: "crash",
            48: "tom_high", 50: "tom_high", 45: "tom_mid", 47: "tom_mid", 41: "tom_low", 43: "tom_low",
        }
        for note, key in names.items():
            self.assertEqual(drum_roll.ROWS[drum_roll.row_for(note)][0], key, note)

    def test_no_drum_is_on_two_rows_and_unknown_sounds_have_none(self):
        seen = set()
        for _key, _label, _color, _shape, notes in drum_roll.ROWS:
            self.assertFalse(seen & notes)
            seen |= notes
        for note in (0, 27, 54, 56, 70, 127, -1):
            self.assertIsNone(drum_roll.row_for(note), note)

    def test_rows_are_told_apart_by_colour_or_shape(self):
        looks = [(color, shape) for _key, _label, color, shape, _notes in drum_roll.ROWS]
        self.assertEqual(len(set(looks)), len(looks))
        self.assertEqual(len({color for color, _shape in looks}), len(looks))

    def test_mix(self):
        self.assertEqual(drum_roll.mix("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(drum_roll.mix("#000000", "#ffffff", 1), "#ffffff")
        self.assertEqual(drum_roll.mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(drum_roll.mix("#000000", "#ffffff", 7), "#ffffff")
        self.assertEqual(drum_roll.mix("#000000", "#ffffff", -3), "#000000")


class BarNumbers(unittest.TestCase):
    def test_songsterr_rows_show_the_tabs_own_bar_numbers(self):
        class Fake:
            tab_shift, tab_half = 2, False
            bar_number = drum_roll.DrumRoll.bar_number

        roll = Fake()
        self.assertEqual(roll.bar_number("midi", 5), 5)
        self.assertEqual(roll.bar_number("tab", 3), 1)
        self.assertEqual(roll.bar_number("tab", 5), 3)
        self.assertLessEqual(roll.bar_number("tab", 2), 0)  # before the tab starts: not shown
        roll.tab_half = True
        self.assertEqual(roll.bar_number("tab", 3), 1)
        self.assertEqual(roll.bar_number("tab", 4), 3)


class TabEvents(unittest.TestCase):
    def test_hits_land_inside_the_bar_they_belong_to(self):
        out = drum_roll.tab_events([hit(0), hit(960), hit(1920), hit(2400)], BOUNDS, BAR_MS, 0, False)
        self.assertEqual([item[0] for item in out], [0, 1000, 2000, 2500])

    def test_line_up_shift_moves_the_tab_later_or_earlier(self):
        later = drum_roll.tab_events([hit(0)], BOUNDS, BAR_MS, 2, False)
        self.assertEqual(later[0][0], 4000)
        earlier = drum_roll.tab_events([hit(1920), hit(3840)], BOUNDS, BAR_MS, -1, False)
        self.assertEqual([item[0] for item in earlier], [0, 2000])

    def test_bars_with_no_midi_bar_are_dropped(self):
        out = drum_roll.tab_events([hit(0), hit(1920), hit(3840)], BOUNDS, BAR_MS, 3, False)
        self.assertEqual([item[0] for item in out], [6000, 8000, 10000][:2])
        self.assertEqual(drum_roll.tab_events([hit(0)], BOUNDS, BAR_MS, 5, False), [])
        self.assertEqual(drum_roll.tab_events([hit(0)], BOUNDS, BAR_MS, -1, False), [])

    def test_half_time_puts_two_tab_bars_in_one_midi_bar(self):
        out = drum_roll.tab_events([hit(0), hit(960), hit(1920), hit(2880)], BOUNDS, BAR_MS, 0, True)
        self.assertEqual([item[0] for item in out], [0, 500, 1000, 1500])

    def test_hits_keep_their_note_and_velocity_and_are_sorted(self):
        out = drum_roll.tab_events([hit(1920, 36, 90), hit(0, 49, 120)], BOUNDS, BAR_MS, 0, False)
        self.assertEqual(out, [[0, 49, 120], [2000, 36, 90]])

    def test_the_midi_bar_length_sets_the_spacing(self):
        slow = [0, 4000, 8000]
        out = drum_roll.tab_events([hit(960)], BOUNDS, slow, 0, False)
        self.assertEqual(out[0][0], 2000)

    def test_odd_input_does_not_crash(self):
        self.assertEqual(drum_roll.tab_events([], BOUNDS, BAR_MS, 0, False), [])
        self.assertEqual(drum_roll.tab_events([hit(0)], [0], BAR_MS, 0, False), [])
        self.assertEqual(drum_roll.tab_events([hit(0)], BOUNDS, [0], 0, False), [])
        self.assertEqual(drum_roll.tab_events([hit(0)], [], [], 0, False), [])
        # a bar with no length, ticks past the end and before the start
        out = drum_roll.tab_events([hit(5), hit(99999), hit(-50)], [0, 0, 100], BAR_MS, 0, False)
        self.assertIsInstance(out, list)
        out = drum_roll.tab_events([hit(99999), hit(-50)], BOUNDS, BAR_MS, 0, False)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
