"""The RC-10R footswitch model: rhythm and loop switch behavior."""

from __future__ import annotations

import unittest

from tests import support  # noqa: F401

from pedal_sim import PedalMachine

DIVISIONS = {
    "intro": [1, 2], "pattern1": [3, 6], "pattern1Fill": [6, 6],
    "pattern2": [7, 10], "pattern2Fill": [10, 10], "ending": [11, 12],
}


def machine(**overrides) -> PedalMachine:
    divisions = dict(DIVISIONS)
    divisions.update(overrides)
    return PedalMachine(divisions)


def play_range(m: PedalMachine, key: str):
    """Run one range's bars to the end the way the player does; returns the state at the end."""
    start, end = DIVISIONS[key]
    for _bar in range(start, end):
        m.bar_done()
    return m.range_done()


class Rhythm(unittest.TestCase):
    def test_start_plays_intro_then_pattern1(self):
        m = machine()
        self.assertEqual(m.rhythm_tap(), "start")
        self.assertEqual(m.start(), "intro")
        self.assertEqual(play_range(m, "intro"), "pattern1")

    def test_no_intro_when_intro_is_pattern1_first_bar(self):
        m = machine(intro=[3, 3])
        self.assertEqual(m.start(), "pattern1")

    def test_pattern_loops_until_pressed(self):
        m = machine()
        m.start()
        play_range(m, "intro")
        self.assertEqual(play_range(m, "pattern1"), "pattern1")

    def test_tap_asks_for_fill_at_next_bar_then_returns_to_same_pattern(self):
        m = machine()
        m.start(); play_range(m, "intro")
        self.assertEqual(m.rhythm_tap(), "pending")
        self.assertEqual(m.bar_done(), "pattern1Fill")
        self.assertEqual(m.range_done(), "pattern1")

    def test_second_tap_while_waiting_does_nothing(self):
        m = machine()
        m.start(); play_range(m, "intro")
        m.rhythm_tap()
        self.assertIsNone(m.rhythm_tap())

    def test_hold_switches_pattern(self):
        m = machine()
        m.start(); play_range(m, "intro")
        self.assertEqual(m.rhythm_hold(), "pending")
        self.assertEqual(m.bar_done(), "pattern1Fill")
        self.assertEqual(m.range_done(), "pattern2")
        m.rhythm_hold(); m.bar_done()
        self.assertEqual(m.range_done(), "pattern1")

    def test_double_press_ends_through_fill_then_ending_then_stops(self):
        m = machine()
        m.start(); play_range(m, "intro")
        self.assertEqual(m.rhythm_double(), "pending")
        self.assertEqual(m.bar_done(), "pattern1Fill")
        self.assertEqual(m.range_done(), "ending")
        self.assertEqual(m.range_done(), "idle")

    def test_double_during_intro_goes_to_ending(self):
        m = machine()
        m.start()
        m.rhythm_double()
        self.assertEqual(m.range_done(), "ending")

    def test_double_during_fill(self):
        m = machine()
        m.start(); play_range(m, "intro")
        m.rhythm_tap(); m.bar_done()
        m.rhythm_double()
        self.assertEqual(m.range_done(), "ending")

    def test_double_when_idle_or_ending_is_ignored(self):
        m = machine()
        self.assertIsNone(m.rhythm_double())

    def test_hold_while_ending_press_pending_is_ignored(self):
        m = machine()
        m.start(); play_range(m, "intro")
        m.rhythm_double()
        self.assertIsNone(m.rhythm_hold())

    def test_stop_returns_to_idle(self):
        m = machine()
        m.start()
        m.stop_all()
        self.assertEqual(m.state, "idle")
        self.assertIsNone(m.pending)

    def test_missing_divisions_do_not_crash_idle_text(self):
        m = PedalMachine({})
        self.assertIn("Press the rhythm switch", m.waiting_text())
        self.assertEqual(m.lcd_lines(None, None, None)[0][:3], "01:")

    def test_start_without_divisions_is_an_error_or_handled(self):
        # The pedal window should never be opened without ranges; make sure the failure is a KeyError, not a hang.
        with self.assertRaises(KeyError):
            PedalMachine({}).start()


class Sync(unittest.TestCase):
    def test_hold_while_stopped_toggles_sync(self):
        m = machine()
        self.assertEqual(m.rhythm_hold(), "sync")
        self.assertTrue(m.sync)
        m.rhythm_hold()
        self.assertFalse(m.sync)

    def test_cannot_toggle_sync_while_playing(self):
        m = machine()
        m.start()
        self.assertFalse(m.set_sync(True))

    def test_synced_tracks_follow_patterns(self):
        m = machine()
        m.set_sync(True)
        m.set_clock(0.0, 2000)
        m.start()
        play_range(m, "intro")
        self.assertEqual(m.track, 1)
        self.assertEqual(m.loop, "rec")
        m.set_clock(4.0)
        m.rhythm_hold(); m.bar_done()
        m.range_done()  # fill ends, pattern 2 starts
        self.assertEqual(m.track, 2)
        self.assertTrue(m.tracks[1])
        self.assertEqual(m.length[1], 4)


class Looper(unittest.TestCase):
    def test_loop_without_drums_asks_to_start_rhythm(self):
        m = machine()
        self.assertEqual(m.loop_tap(), "loop")
        self.assertEqual(m.note, "START RHYTHM")
        self.assertEqual(m.loop, "off")

    def test_record_play_overdub_cycle(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start()
        m.loop_tap()
        self.assertEqual(m.loop, "rec")
        m.set_clock(4.02)
        m.loop_tap()
        self.assertEqual(m.loop, "play")
        self.assertEqual(m.length[1], 4)
        m.loop_tap()
        self.assertEqual(m.loop, "dub")
        m.loop_tap()
        self.assertEqual(m.loop, "play")

    def test_recording_closes_on_whole_bars_with_timing_report(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start()
        m.loop_tap()
        m.set_clock(3.9)
        m.loop_tap()
        self.assertEqual(m.length[1], 4)
        self.assertIn("early", m.result)

    def test_undo_redo(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start()
        m.loop_tap(); m.set_clock(4.0); m.loop_tap()
        m.loop_tap()           # overdub
        m.loop_hold()
        self.assertEqual(m.note, "UNDO")
        m.loop_hold()
        self.assertEqual(m.note, "REDO")

    def test_hold_on_first_take_erases_it(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start()
        m.loop_tap(); m.set_clock(4.0); m.loop_tap()
        m.loop_hold()
        self.assertFalse(m.tracks[1])
        self.assertEqual(m.loop, "off")

    def test_double_stops_loop(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start()
        m.loop_tap(); m.set_clock(4.0); m.loop_tap()
        self.assertEqual(m.loop_double(), "loop")
        self.assertEqual(m.loop, "off")

    def test_hold_while_stopped_clears_phrase(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start()
        m.loop_tap(); m.set_clock(4.0); m.loop_tap()
        m.loop_double()
        self.assertEqual(m.loop_hold(), "loop")
        self.assertEqual(m.note, "PHRASE CLEARED")
        self.assertFalse(any(m.tracks.values()))

    def test_fit_messages(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start(); play_range(m, "intro")
        for bars, text in ((4, "matches"), (2, "plays 2 times"), (8, "covers"), (3, "drift")):
            self.assertIn(text, m._fit(1, bars), bars)

    def test_loop_progress_bounds(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start(); play_range(m, "intro")
        m.loop_tap()
        for now in (0.0, 1.5, 3.99, 10.0):
            m.set_clock(now)
            self.assertTrue(0.0 <= m.loop_progress() <= 1.0)
            bar = m.loop_bar()
            self.assertTrue(bar is None or bar[0] >= 1)

    def test_lcd_lines_are_16_wide(self):
        m = machine()
        m.set_clock(0.0, 2000)
        m.start()
        for args in ((None, None, None), (3, 2, 120), (999, 4, 300)):
            first, second = m.lcd_lines(*args, meter="12/8")
            self.assertEqual((len(first), len(second)), (16, 16))

    def test_guide_text_never_empty_in_main_states(self):
        m = machine()
        self.assertTrue(m.loop_guide())
        m.set_clock(0.0, 2000)
        m.start()
        self.assertTrue(m.loop_guide())
        m.loop_tap()
        self.assertTrue(m.loop_guide())


if __name__ == "__main__":
    unittest.main()
