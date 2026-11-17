"""The guitar and bass tab under the drum roll."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import tests.support  # noqa: F401  (puts scripts/ on the path)

import songsterr
import tab_roll
from tests.test_gui import GuiCase


def notes_of(items, bars=4, tuning=(64, 59, 55, 50, 45, 40)):
    return SimpleNamespace(notes=items, bars=bars, tuning=list(tuning), strings=len(tuning), kind="guitar")


BARS = [0, 2000, 4000, 6000, 8000, 10000]


class Mapping(unittest.TestCase):
    def test_notes_land_on_the_bar_the_line_up_names(self):
        notes = notes_of([(0, 0.0, 0.5, 1, 3, ""), (1, 0.5, 0.25, 0, 5, "")])
        out = tab_roll.map_notes(notes, BARS, 0, False)
        self.assertEqual(out, [(0, 1000, 1, 3, ""), (3000, 500, 0, 5, "")])

    def test_a_shift_moves_every_note_a_bar(self):
        notes = notes_of([(0, 0.0, 0.5, 1, 3, "")])
        self.assertEqual(tab_roll.map_notes(notes, BARS, 2, False)[0][0], 4000)
        self.assertEqual(tab_roll.map_notes(notes, BARS, -1, False), [])

    def test_half_time_puts_two_tab_bars_in_one_midi_bar(self):
        notes = notes_of([(0, 0.0, 0.5, 1, 3, ""), (1, 0.0, 0.5, 1, 3, ""), (2, 0.0, 0.5, 1, 3, "")])
        out = tab_roll.map_notes(notes, BARS, 0, True)
        self.assertEqual([item[0] for item in out], [0, 1000, 2000])
        self.assertEqual(out[0][1], 500)

    def test_bars_that_run_past_the_song_are_left_out(self):
        notes = notes_of([(9, 0.0, 0.5, 1, 3, "")])
        self.assertEqual(tab_roll.map_notes(notes, BARS, 0, False), [])
        self.assertEqual(tab_roll.map_notes(notes, [0], 0, False), [])

    def test_the_result_is_in_time_order(self):
        notes = notes_of([(1, 0.0, 0.1, 0, 1, ""), (0, 0.5, 0.1, 1, 2, ""), (0, 0.5, 0.1, 0, 3, "")])
        times = [(item[0], item[2]) for item in tab_roll.map_notes(notes, BARS, 0, False)]
        self.assertEqual(times, sorted(times))


class Writing(unittest.TestCase):
    def test_fret_text(self):
        self.assertEqual(tab_roll.fret_text(5, ""), "5")
        self.assertEqual(tab_roll.fret_text(5, "d"), "x")
        self.assertEqual(tab_roll.fret_text(5, "g"), "(5)")
        self.assertEqual(tab_roll.fret_text(12, "s"), "12/")
        self.assertEqual(tab_roll.fret_text(7, "hb"), "7hb")
        self.assertEqual(tab_roll.fret_text(0, "p"), "0")

    def test_open_string_names(self):
        self.assertEqual([tab_roll.open_string_name(p) for p in (64, 59, 55, 50, 45, 40)], ["E", "B", "G", "D", "A", "E"])
        self.assertEqual(tab_roll.open_string_name(30), "F#")


class LaneCase(GuiCase):
    def show_tab(self, kind="guitar", *more):
        """Open the song and switch on the lane (and any more); returns the app."""
        self.open_song()
        app = self.app
        app.root.deiconify()
        app.root.geometry("1500x1300+0+0")
        self.pump(0.4)
        for each in (kind, *more):
            app.tab_roll.show_kind(each)
        return app

    def texts(self, kind="guitar"):
        canvas = self.app.tab_roll.lanes[kind].canvas
        return [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]


class OnScreen(LaneCase):
    def test_it_starts_hidden_and_reads_nothing_until_shown(self):
        self.open_song()
        app = self.app
        self.assertEqual(app.tab_roll.shown_kinds(), [])
        self.assertFalse(any(lane.winfo_ismapped() for lane in app.tab_roll.lanes.values()))
        self.pump(0.5)
        self.assertEqual(app.tab_parts, {})
        self.assertNoErrors()

    def test_it_sits_between_the_drum_roll_and_the_ranges(self):
        app = self.app
        self.assertEqual(int(app.tab_roll.grid_info()["row"]), int(app.roll.grid_info()["row"]) + 1)
        self.assertEqual(app.tab_roll.master, app.roll.master)

    def test_showing_guitar_reads_the_best_part_and_draws_the_notes(self):
        app = self.show_tab()
        lane = app.tab_roll.lanes["guitar"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)), "the guitar never reached its lane")
        self.assertEqual(len(lane.notes), len(app.tab.bars))
        self.assertEqual(lane.strings, 6)
        self.assertEqual(lane.tuning[0], 64)
        self.pump(0.3)
        texts = self.texts()
        self.assertIn("3", texts)  # the fake part plays fret 3 on the second string
        self.assertEqual(sorted(text for text in texts if text in {"E", "B", "G", "D", "A"}), ["A", "B", "D", "E", "E", "G"])
        self.assertEqual(lane.part_name.get(), "Lead Guitar")
        self.assertIn("Guitar", texts)
        self.assertNoErrors()

    def test_the_fake_part_lines_up_with_the_chart(self):
        app = self.show_tab()
        lane = app.tab_roll.lanes["guitar"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)))
        bar_ms = app.view["bar_ms"]
        shift = app.view["tab_shift"]
        self.assertEqual(lane.notes[0][0], bar_ms[shift])
        app.on_shift(1)
        self.assertEqual(lane.notes[0][0], bar_ms[shift + 1])
        app.on_undo()
        self.assertEqual(lane.notes[0][0], bar_ms[shift])
        self.assertNoErrors()

    def test_bass_reads_the_bass(self):
        app = self.show_tab("bass")
        lane = app.tab_roll.lanes["bass"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)))
        self.assertEqual(lane.strings, 4)
        self.assertEqual(lane.part_name.get(), "Bass")
        self.assertEqual(lane.notes[0][3], 0)
        self.assertEqual(int(lane.canvas["height"]), lane.full_height())
        self.assertNoErrors()

    def test_a_song_with_no_bass_part_says_so(self):
        self.tab.parts = [part for part in self.tab.parts if part.kind != "bass"]
        app = self.show_tab("bass")
        self.pump(0.3)
        self.assertIn("Songsterr has no bass part for this song.", self.texts("bass"))
        self.assertFalse(any(key[1] == 2 for key in app.tab_parts))
        self.assertNoErrors()

    def test_a_part_that_cannot_be_read_says_so_and_tries_again_when_toggled(self):
        self.part_error = "offline"
        app = self.show_tab()
        self.assertTrue(self.pump(5, lambda: any("could not be read" in text for text in self.texts())))
        self.part_error = ""
        app.tab_roll.show_kind("guitar", False)
        app.tab_roll.show_kind("guitar")
        self.assertTrue(self.pump(5, lambda: bool(app.tab_roll.lanes["guitar"].notes)))
        self.assertNoErrors()

    def test_the_window_is_shared_with_the_drum_roll(self):
        app = self.show_tab("guitar", "bass")
        self.pump(0.3)
        app.roll.zoom(1)
        for lane in app.tab_roll.lanes.values():
            self.assertEqual(lane.window_ms, app.roll.window_ms)
        app.tab_roll.lanes["bass"]._wheel(SimpleNamespace(delta=-120))
        for lane in app.tab_roll.lanes.values():
            self.assertEqual(lane.window_ms, app.roll.window_ms)
        self.assertGreater(app.roll.window_ms, 6000)
        self.assertNoErrors()

    def test_it_follows_the_playhead_and_slides_without_redrawing(self):
        app = self.show_tab()
        lane = app.tab_roll.lanes["guitar"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)))
        self.pump(0.3)
        lane.show(1000)
        before = lane.redraws
        for step in range(1, 40):
            lane.show(1000 + step * 15)
            app.root.update_idletasks()
        self.assertEqual(lane.redraws, before)
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        first = lane.position
        self.pump(0.6)
        self.assertGreater(lane.position, first + 200)
        app.on_stop()
        self.assertNoErrors()

    def test_hiding_a_lane_stops_its_work(self):
        app = self.show_tab()
        lane = app.tab_roll.lanes["guitar"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)))
        app.tab_roll.show_kind("guitar", False)
        self.pump(0.2)
        self.assertEqual(app.tab_roll.shown_kinds(), [])
        self.assertFalse(lane.winfo_ismapped())
        redraws = lane.redraws
        app.tab_roll.show(5000)
        self.assertEqual(lane.redraws, redraws)
        self.assertNoErrors()

    def test_closing_the_song_clears_it(self):
        app = self.show_tab()
        lane = app.tab_roll.lanes["guitar"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)))
        with mock.patch.object(songsterr, "fetch_part", side_effect=AssertionError("should not read again")):
            app.update_roll()
        app.view = None
        app.update_roll()
        self.assertEqual(lane.notes, [])
        self.assertEqual(lane.bar_ms, [])
        self.assertNoErrors()

    def test_two_parts_of_one_kind_are_listed_best_first(self):
        self.tab.parts.append(songsterr.Part(4, "guitar", "Rhythm Guitar", 50))
        app = self.show_tab()
        lane = app.tab_roll.lanes["guitar"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)))
        self.assertEqual([label for _i, label in lane.parts], ["Rhythm Guitar", "Lead Guitar"])
        self.assertEqual(lane.part_name.get(), "Rhythm Guitar")
        lane.part_name.set("Lead Guitar")
        lane._part_changed()
        self.assertTrue(self.pump(5, lambda: lane.part_index() == 1))
        self.assertNoErrors()


class Stacked(LaneCase):
    """Guitar, bass and vocal at once, one under the other."""

    def setUp(self):
        super().setUp()
        self.tab.parts.append(songsterr.Part(3, "vocals", "Lead Vocals", 2))

    def test_the_lanes_stack_in_order_under_the_drum_roll(self):
        app = self.show_tab("vocal", "guitar", "bass")
        panel = app.tab_roll
        self.assertTrue(self.pump(5, lambda: all(panel.lanes[kind].notes for kind in tab_roll.KINDS)))
        self.pump(0.3)
        tops = [panel.lanes[kind].winfo_y() for kind in tab_roll.KINDS]
        self.assertEqual(tops, sorted(tops))
        self.assertEqual(len(set(tops)), 3)
        for upper, lower in zip(tab_roll.KINDS, tab_roll.KINDS[1:]):
            self.assertLessEqual(panel.lanes[upper].winfo_y() + panel.lanes[upper].winfo_height(), panel.lanes[lower].winfo_y())
        self.assertEqual([panel.lanes[kind].mode for kind in tab_roll.KINDS], ["tab", "tab", "melody"])
        self.assertNoErrors()

    def test_each_lane_comes_and_goes_on_its_own(self):
        app = self.show_tab("guitar", "bass")
        panel = app.tab_roll
        self.assertTrue(self.pump(5, lambda: panel.lanes["guitar"].notes and panel.lanes["bass"].notes))
        panel.show_kind("guitar", False)
        self.pump(0.2)
        self.assertEqual(panel.shown_kinds(), ["bass"])
        self.assertTrue(panel.lanes["bass"].winfo_ismapped())
        self.assertFalse(panel.lanes["guitar"].winfo_ismapped())
        panel.show_kind("vocal")
        self.assertTrue(self.pump(5, lambda: bool(panel.lanes["vocal"].notes)))
        self.assertEqual(panel.shown_kinds(), ["bass", "vocal"])
        self.assertNoErrors()

    def test_every_lane_moves_in_the_same_frame_as_the_drum_roll(self):
        app = self.show_tab("guitar", "bass", "vocal")
        panel = app.tab_roll
        self.assertTrue(self.pump(5, lambda: all(panel.lanes[kind].notes for kind in tab_roll.KINDS)))
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        for _ in range(12):
            self.pump(0.05)
            positions = {app.roll.position, *(panel.lanes[kind].position for kind in tab_roll.KINDS)}
            self.assertEqual(len(positions), 1, positions)
        self.assertGreater(app.roll.position, 0)
        app.on_stop()
        self.assertNoErrors()

    def test_lanes_follow_while_the_drum_roll_is_hidden(self):
        app = self.show_tab("guitar")
        lane = app.tab_roll.lanes["guitar"]
        self.assertTrue(self.pump(5, lambda: bool(lane.notes)))
        app.roll.roll_check.invoke()
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        first = lane.position
        self.pump(0.6)
        self.assertGreater(lane.position, first + 200)
        app.on_stop()
        self.assertNoErrors()

    def test_the_chips_carry_the_lane_colours(self):
        app = self.app
        for kind in tab_roll.KINDS:
            style = str(app.tab_roll.chips[kind].cget("style"))
            self.assertEqual(style, f"{tab_roll.TITLES[kind]}.Chip.TCheckbutton")
        self.assertEqual(str(app.roll.roll_check.cget("style")), "Chip.TCheckbutton")


if __name__ == "__main__":
    unittest.main()
