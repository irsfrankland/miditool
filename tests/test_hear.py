"""Hearing the guitar and bass with the drums."""

from __future__ import annotations

import unittest
from unittest import mock

from tests.support import band_midi, drum_midi

import find_drum_midi as finder
import keep_tracks
import preview_ui
import songsterr
import string_synth
from tests.test_gui import MIDI_HITS, GuiCase

RATE = preview_ui.RATE


def crossings(samples, start, count):
    window = samples[start : start + count]
    return sum(1 for a, b in zip(window, window[1:]) if (a < 0) != (b < 0))


class Notes(unittest.TestCase):
    def test_tab_notes_become_pitches_and_ties_ring_on(self):
        mapped = [(0, 500, 1, 3, ""), (500, 500, 1, 3, "t"), (1000, 200, 0, 0, "g"), (1200, 200, 0, 0, "a")]
        notes = string_synth.from_tab(mapped, [64, 59, 55, 50, 45, 40], "guitar")
        self.assertEqual([(note[0], note[1], note[2]) for note in notes], [(0, 1000, 62), (1000, 200, 64), (1200, 200, 64)])
        self.assertLess(notes[1][3], 60)
        self.assertGreater(notes[2][3], 100)

    def test_a_string_the_tuning_does_not_have_is_skipped(self):
        self.assertEqual(string_synth.from_tab([(0, 100, 7, 0, "")], [64, 59], "guitar"), [])

    def test_kept_tracks_are_timed_by_the_tempo(self):
        track = keep_tracks.Kept("Bass", "bass", 1, 33, ((480, 240, 40, 100),))
        notes = string_synth.from_kept([track], lambda tick: tick * 2)
        self.assertEqual(notes, [(960, 480, 40, 100, "bass", "")])


class Sound(unittest.TestCase):
    def mix(self, notes, start=0, end=2000, speed=1.0, gain=0.5, seconds=2.2):
        mix = [0.0] * int(RATE * seconds)
        added = string_synth.mix_notes(mix, notes, start, end, speed, RATE, gain)
        return mix, added

    def test_a_note_sounds_at_its_pitch(self):
        for pitch, hz in ((57, 220.0), (40, 82.4), (69, 440.0)):
            for kind in ("guitar", "bass"):
                mix, _ = self.mix([(0, 800, pitch, 100, kind, "")])
                count = crossings(mix, int(RATE * 0.05), int(RATE * 0.25))
                self.assertAlmostEqual(count / 2 / 0.25, hz, delta=hz * 0.12, msg=(kind, pitch))

    def test_a_note_starts_where_it_is_placed_and_not_before(self):
        mix, _ = self.mix([(500, 400, 52, 100, "guitar", "")])
        self.assertEqual(max(abs(x) for x in mix[: int(RATE * 0.49)]), 0.0)
        self.assertGreater(max(abs(x) for x in mix[int(RATE * 0.5) : int(RATE * 0.7)]), 0.05)

    def test_notes_outside_the_section_are_left_out(self):
        _mix, added = self.mix([(100, 100, 52, 100, "guitar", ""), (5000, 100, 52, 100, "guitar", "")], start=500, end=3000)
        self.assertEqual(added, 0)

    def test_slower_playback_spaces_notes_further_apart(self):
        notes = [(0, 100, 52, 100, "guitar", ""), (1000, 100, 52, 100, "guitar", "")]
        mix, _ = self.mix(notes, speed=0.5, seconds=2.5)
        quiet = max(abs(x) for x in mix[int(RATE * 1.4) : int(RATE * 1.9)])
        loud = max(abs(x) for x in mix[int(RATE * 2.0) : int(RATE * 2.2)])
        self.assertGreater(loud, 0.05)
        self.assertLess(quiet, loud)

    def test_a_short_note_stops_and_a_long_one_rings(self):
        short, _ = self.mix([(0, 100, 52, 100, "guitar", "")])
        long, _ = self.mix([(0, 1500, 52, 100, "guitar", "")])
        late = slice(int(RATE * 0.5), int(RATE * 0.6))
        self.assertEqual(max(abs(x) for x in short[late]), 0.0)
        self.assertGreater(max(abs(x) for x in long[late]), 0.01)

    def test_muted_notes_are_shorter_and_dead_notes_are_a_click(self):
        plain, _ = self.mix([(0, 1500, 52, 100, "guitar", "")])
        muted, _ = self.mix([(0, 1500, 52, 100, "guitar", "p")])
        dead, _ = self.mix([(0, 1500, 52, 100, "guitar", "d")])
        late = slice(int(RATE * 0.5), int(RATE * 0.6))
        self.assertEqual(max(abs(x) for x in muted[late]), 0.0)
        self.assertEqual(max(abs(x) for x in dead[int(RATE * 0.1) :]), 0.0)
        self.assertGreater(max(abs(x) for x in plain[late]), 0.01)
        self.assertGreater(max(abs(x) for x in dead[: int(RATE * 0.05)]), 0.01)

    def test_louder_velocity_and_gain_are_louder(self):
        soft, _ = self.mix([(0, 300, 52, 40, "guitar", "")])
        hard, _ = self.mix([(0, 300, 52, 120, "guitar", "")])
        quiet, _ = self.mix([(0, 300, 52, 120, "guitar", "")], gain=0.1)
        self.assertGreater(max(hard), max(soft))
        self.assertGreater(max(hard), max(quiet))

    def test_every_pitch_and_a_note_that_runs_past_the_end_are_safe(self):
        notes = [(index * 10, 5000, pitch, 100, "bass" if pitch < 50 else "guitar", "") for index, pitch in enumerate(range(0, 128, 3))]
        mix, added = self.mix(notes, seconds=1.0)
        self.assertEqual(added, len(notes))
        self.assertEqual(len(mix), int(RATE * 1.0))

    def test_drums_alone_render_exactly_as_before(self):
        events = [[0, 36, 100], [500, 38, 100], [1000, 42, 90]]
        alone = preview_ui.render_section(events, 0, 1500)
        self.assertEqual(preview_ui.render_section(events, 0, 1500, 1.0, None), alone)
        self.assertEqual(preview_ui.render_section(events, 0, 1500, 1.0, []), alone)

    def test_strings_change_the_audio_and_the_length_stays(self):
        events = [[0, 36, 100], [500, 38, 100]]
        alone = preview_ui.render_section(events, 0, 1500)
        with_guitar = preview_ui.render_section(events, 0, 1500, 1.0, [(0, 800, 52, 100, "guitar", "")], 0.5)
        self.assertNotEqual(alone, with_guitar)
        self.assertEqual(len(alone), len(with_guitar))


class InTheFinder(GuiCase):
    def setUp(self):
        super().setUp()
        self.renders: list[tuple] = []
        real = finder.render_section

        def spy(events, start, end, speed=1.0, strings=None, gain=0.4):
            self.renders.append((len(strings or []), gain, speed))
            return real(events, start, end, speed, strings, gain)

        patch = mock.patch.object(finder, "render_section", spy)
        patch.start()
        self.addCleanup(patch.stop)

    def play_and_wait(self, bar=3):
        app = self.app
        app.start_playback(bar, "test", None)
        self.assertTrue(self.pump(15, lambda: app.playing is not None), "playback never started")

    def test_it_is_off_by_default_and_the_drums_render_alone(self):
        self.open_song()
        app = self.app
        self.assertIsNone(app.hear_key())
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], 0)
        self.assertEqual(app.playing["with"], "")
        self.assertNoErrors()

    def test_the_midis_own_guitar_is_used_when_it_has_one(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.on_hear_changed()
        plans, missing = app.hear_plan()
        self.assertEqual([(plan["kind"], plan["source"]) for plan in plans], [("guitar", "MIDI")])
        self.assertEqual(missing, [])
        self.assertEqual(app.tab_roll.lanes["guitar"].hear_note["text"], "MIDI")
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], len(MIDI_HITS))
        self.assertIn("with guitar (MIDI)", app.playing["with"])
        self.assertNoErrors()

    def test_songsterrs_part_is_the_fallback_and_follows_the_line_up(self):
        self.open_song()
        app = self.app
        app.tab_roll.lanes["bass"].hear.set(True)
        app.on_hear_changed()
        plans, _missing = app.hear_plan()
        self.assertEqual([(plan["kind"], plan["source"]) for plan in plans], [("bass", "Songsterr")])
        self.assertEqual(app.tab_roll.lanes["bass"].hear_note["text"], "Songsterr")
        first = app.hear_key()
        app.on_shift(1)
        self.assertNotEqual(app.hear_key(), first)
        self.play_and_wait()
        self.assertGreater(self.renders[-1][0], 0)
        self.assertIn("with bass (Songsterr)", app.playing["with"])
        self.assertNoErrors()

    def test_the_vocal_plays_with_the_drums(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS, vocal=True))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["vocal"].hear.set(True)
        app.on_hear_changed()
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], len(MIDI_HITS))
        self.assertIn("vocal (MIDI)", app.playing["with"])
        app.on_stop()
        self.assertNoErrors()

    def test_both_together(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.tab_roll.lanes["bass"].hear.set(True)
        app.on_hear_changed()
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], 2 * len(MIDI_HITS))
        self.assertIn("guitar (MIDI) and bass (MIDI)", app.playing["with"])

    def test_nothing_to_play_says_so_and_the_drums_still_play(self):
        self.tab.parts = self.tab.parts[:1]
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.on_hear_changed()
        self.assertEqual(app.tab_roll.lanes["guitar"].hear_note["text"], "none")
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], 0)
        self.assertIn("no guitar in the MIDI or on Songsterr", app.playing["with"])
        self.assertNoErrors()

    def test_a_part_that_cannot_be_read_does_not_stop_the_drums(self):
        self.part_error = "offline"
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.on_hear_changed()
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], 0)
        self.assertIn("guitar could not be read", app.playing["with"])
        self.assertNoErrors()

    def test_zero_volume_is_the_same_as_off(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.tab_roll.hear_volume.set(0)
        self.assertIsNone(app.hear_key())
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], 0)

    def test_switching_it_on_while_playing_carries_on_from_the_same_place(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        self.play_and_wait(bar=4)
        self.pump(0.5)
        before = app.playhead_ms()
        count = len(self.renders)
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.on_hear_changed()
        self.assertTrue(self.pump(15, lambda: app.playing is not None and len(self.renders) > count))
        self.assertGreater(self.renders[-1][0], 0)
        self.assertGreaterEqual(app.playhead_ms(), before - 50)
        self.assertLess(app.playhead_ms(), before + 3000)
        self.assertIn("guitar", app.playing["with"])
        app.on_stop()
        self.assertNoErrors()

    def test_switching_it_on_while_paused_stays_paused(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        self.play_and_wait(bar=4)
        app.pause_playback()
        held = app.playhead_ms()
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.on_hear_changed()
        self.assertTrue(self.pump(15, lambda: app.playing is not None))
        self.assertTrue(app.playing["paused"])
        self.assertAlmostEqual(app.playhead_ms(), held, delta=5)
        app.on_stop()
        self.assertNoErrors()

    def test_moving_the_volume_to_where_it_already_is_does_not_restart(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        self.play_and_wait()
        renders = len(self.renders)
        app.on_hear_changed()
        app.on_hear_changed()
        self.pump(0.3)
        self.assertEqual(len(self.renders), renders)
        self.assertIsNotNone(app.playing)
        app.on_stop()

    def test_changing_the_volume_builds_the_audio_again(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        self.play_and_wait()
        app.tab_roll.hear_volume.set(100)
        app.on_hear_changed()
        self.assertTrue(self.pump(15, lambda: app.playing is not None and self.renders[-1][1] > 0.5))
        app.on_stop()
        self.assertNoErrors()

    def test_audio_built_with_the_guitar_is_not_reused_without_it(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        self.play_and_wait()
        app.on_stop()
        count = len(self.renders)
        app.tab_roll.lanes["guitar"].hear.set(False)
        self.play_and_wait()
        self.assertGreater(len(self.renders), count)
        self.assertEqual(self.renders[-1][0], 0)
        self.assertEqual(app.playing["with"], "")
        app.on_stop()

    def test_the_same_settings_reuse_the_audio(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        self.play_and_wait()
        app.on_stop()
        count = len(self.renders)
        self.play_and_wait()
        self.assertEqual(len(self.renders), count)
        self.assertIn("guitar (MIDI)", app.playing["with"])
        app.on_stop()

    def test_a_bar_edit_moves_the_strings_with_the_drums_in_the_audio(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["guitar"].hear.set(True)
        app.apply_selection(3, 4)
        app.on_delete_bars()
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], len(MIDI_HITS) - 2)
        app.on_stop()

    def test_a_song_without_a_band_still_plays_normally_after_hearing_is_on(self):
        self.midi_path.write_bytes(drum_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        self.tab.parts = self.tab.parts[:1]
        app.tab_roll.lanes["bass"].hear.set(True)
        app.on_hear_changed()
        self.play_and_wait()
        self.assertEqual(self.renders[-1][0], 0)
        app.on_stop()
        self.assertNoErrors()

    def test_each_lane_has_its_own_hear_switch_and_the_panel_has_one_volume(self):
        panel = self.app.tab_roll
        self.assertEqual(panel.hear_volume.get(), 60)
        for kind, lane in panel.lanes.items():
            self.assertFalse(lane.hear.get(), kind)
            row = lane.hear_check.nametowidget(lane.hear_check.winfo_parent())
            self.assertIs(lane.hear_note.nametowidget(lane.hear_note.winfo_parent()), row)
            self.assertIs(row.nametowidget(row.winfo_parent()), lane)
        self.assertIs(panel.volume.nametowidget(panel.volume.winfo_parent()).master, panel)
        self.assertEqual(panel.hearing(), [])
        panel.lanes["bass"].hear.set(True)
        self.assertEqual(panel.hearing(), ["bass"])

    def test_picking_a_part_on_a_lane_decides_what_is_heard(self):
        self.tab.parts.append(songsterr.Part(3, "guitar", "Rhythm Guitar", 50))
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        panel = app.tab_roll
        panel.lanes["guitar"].hear.set(True)
        panel.show_kind("guitar")
        self.assertTrue(self.pump(5, lambda: bool(panel.lanes["guitar"].parts)))
        self.assertEqual(app.hear_choice("guitar")["source"], "MIDI")
        lane = panel.lanes["guitar"]
        lane.part_name.set("Rhythm Guitar")
        lane._part_changed()
        self.assertEqual(app.hear_choice("guitar")["source"], "Songsterr")
        self.assertEqual(app.hear_choice("guitar")["part"].index, 3)
        key = app.hear_key()
        panel.show_kind("guitar", False)
        self.assertNotEqual(app.hear_key(), key)
        self.assertEqual(app.hear_choice("guitar")["source"], "MIDI")
        self.assertNoErrors()

    def test_switching_parts_while_playing_rebuilds_and_carries_on(self):
        self.tab.parts.append(songsterr.Part(3, "guitar", "Rhythm Guitar", 50))
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        panel = app.tab_roll
        panel.lanes["guitar"].hear.set(True)
        panel.show_kind("guitar")
        self.assertTrue(self.pump(5, lambda: bool(panel.lanes["guitar"].parts)))
        self.play_and_wait(bar=4)
        self.pump(0.3)
        before = app.playhead_ms()
        count = len(self.renders)
        lane = panel.lanes["guitar"]
        lane.part_name.set("Rhythm Guitar")
        lane._part_changed()
        self.assertTrue(self.pump(15, lambda: app.playing is not None and len(self.renders) > count))
        self.assertEqual(app.hear_choice("guitar")["source"], "Songsterr")
        self.assertIn("guitar (Songsterr)", app.playing["with"])
        self.assertGreaterEqual(app.playhead_ms(), before - 50)
        self.assertLess(app.playhead_ms(), before + 3000)
        app.on_stop()
        self.assertNoErrors()


if __name__ == "__main__":
    unittest.main()
