"""Vocal parts: kept from a MIDI, taken from Songsterr, shown as a melody, heard, and added to a MIDI."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.support import BAR, PPQ, band_midi, drum_midi, fake_vocal_track

import build_section_chart as chart
import find_drum_midi as finder
import keep_tracks
import preview_ui
import songsterr
import string_synth
from tests.test_gui import MIDI_HITS, GuiCase, fake_tab
RATE = preview_ui.RATE


class Classify(unittest.TestCase):
    def test_names_and_programs(self):
        for name in ("Lead Vocal", "Backing Vocals", "Vox", "Voice", "Singer", "Melody", "Choir"):
            self.assertEqual(keep_tracks.classify(name, 0), "vocal", name)
        for program in (52, 53, 54, 85):
            self.assertEqual(keep_tracks.classify("Track 7", program), "vocal", program)

    def test_a_guitar_or_bass_name_still_wins(self):
        self.assertEqual(keep_tracks.classify("Melody Guitar", 54), "guitar")
        self.assertEqual(keep_tracks.classify("Bass Vocal Line", 0), "bass")

    def test_unrelated_tracks_are_not_kept(self):
        self.assertIsNone(keep_tracks.classify("Piano", 0))

    def test_summary_names_all_three(self):
        k = keep_tracks.Kept
        parts = [k("a", "guitar", 0, 27, ()), k("b", "bass", 1, 33, ()), k("c", "vocal", 2, 54, ())]
        self.assertEqual(keep_tracks.summary(parts), "1 guitar, 1 bass and 1 vocal")
        self.assertEqual(keep_tracks.summary(parts[::2]), "1 guitar and 1 vocal")

    def test_kind_of_songsterrs_word(self):
        self.assertEqual(keep_tracks.kind_of("vocals"), "vocal")
        self.assertEqual(keep_tracks.kind_of("bass"), "bass")
        self.assertEqual(keep_tracks.kind_of("drums"), "guitar")


class Placing(unittest.TestCase):
    BOUNDS = [0, 1920, 3840, 5760]

    def notes(self, items, bars=3):
        return SimpleNamespace(name="Vocals", kind="vocals", bars=bars, tuning=[69, 64, 60, 55], notes=items, program=-1)

    def test_a_vocal_gets_a_vocal_program(self):
        out = keep_tracks.from_tab(self.notes([(0, 0.0, 0.5, 0, 2, "")] + [(1, 0.0, 0.5, 0, 0, "")] + [(2, 0.0, 0.5, 0, 0, "")]), self.BOUNDS, PPQ, 2, -1, "vocal")
        self.assertEqual((out.kind, out.program, out.channel), ("vocal", 54, 2))
        self.assertEqual(out.notes[0][2], 71)

    def test_shift_moves_the_part_and_bars_outside_the_midi_are_dropped(self):
        tab = self.notes([(0, 0.0, 0.5, 0, 0, ""), (1, 0.0, 0.5, 0, 0, ""), (2, 0.0, 0.5, 0, 0, "")])
        out = keep_tracks.place_tab(tab, self.BOUNDS, PPQ, 2, None, "vocal", shift=1)
        self.assertEqual([note[0] for note in out.notes], [1920, 3840])
        back = keep_tracks.place_tab(tab, self.BOUNDS, PPQ, 2, None, "vocal", shift=-1)
        self.assertEqual([note[0] for note in back.notes], [0, 1920])

    def test_half_time_spreads_each_bar_over_two(self):
        tab = self.notes([(0, 0.0, 0.5, 0, 0, ""), (1, 0.0, 0.5, 0, 0, "")], bars=2)
        out = keep_tracks.place_tab(tab, self.BOUNDS, PPQ, 2, None, "vocal", half=True)
        self.assertEqual([note[0] for note in out.notes], [0, 960])
        self.assertLess(out.notes[0][1], 960)


class Sound(unittest.TestCase):
    def test_a_vocal_rings_for_the_whole_note_and_then_stops(self):
        mix = [0.0] * int(RATE * 2.5)
        string_synth.mix_notes(mix, [(0, 1500, 69, 100, "vocal", "")], 0, 2500, 1.0, RATE, 0.5)
        held = max(abs(x) for x in mix[int(RATE * 1.2) : int(RATE * 1.4)])
        after = max(abs(x) for x in mix[int(RATE * 2.2) : int(RATE * 2.5)])
        self.assertGreater(held, 0.03)
        self.assertLess(after, held / 4)

    def test_vocal_notes_from_a_tab_use_the_tuning(self):
        notes = string_synth.from_tab([(0, 500, 0, 3, "")], [69, 64, 60, 55], "vocal")
        self.assertEqual((notes[0][2], notes[0][4]), (72, "vocal"))


class InTheFinder(GuiCase):
    def setUp(self):
        super().setUp()
        self.midi_path.write_bytes(band_midi(MIDI_HITS, vocal=True))
        self.tab.parts.append(songsterr.Part(3, "vocals", "Lead Vocals", 2))

    def test_a_midi_vocal_is_kept_with_the_guitar_and_bass(self):
        self.open_song()
        kinds = sorted(track.kind for track in self.app.view["kept"])
        self.assertEqual(kinds, ["bass", "guitar", "vocal"])
        vocal = next(track for track in self.app.view["kept"] if track.kind == "vocal")
        self.assertEqual(vocal.program, 54)
        self.assertEqual(len(vocal.notes), len(MIDI_HITS))
        self.assertNoErrors()

    def test_saving_carries_the_vocal_and_deleting_bars_takes_it_along(self):
        self.open_song()
        app = self.app
        data, _d, _b = app.export_data("song")
        path = self.base / "song.mid"
        path.write_bytes(data)
        self.assertIn("vocal", [track.kind for track in chart.parse_midi(path)["kept"]])
        app.apply_selection(3, 4)
        app.on_delete_bars()
        vocal = next(track for track in app.view["kept"] if track.kind == "vocal")
        self.assertEqual(len(vocal.notes), len(MIDI_HITS) - 2)
        app.on_undo()
        vocal = next(track for track in app.view["kept"] if track.kind == "vocal")
        self.assertEqual(len(vocal.notes), len(MIDI_HITS))
        self.assertNoErrors()

    def test_the_roll_shows_the_midis_vocal_as_a_melody(self):
        self.open_song()
        app = self.app
        app.root.deiconify()
        app.root.geometry("1500x1300+0+0")
        self.pump(0.4)
        app.tab_roll.show_kind("vocal")
        roll = app.tab_roll.lanes["vocal"]
        self.assertTrue(self.pump(5, lambda: roll.mode == "melody" and bool(roll.notes)))
        self.assertTrue(roll.midi_numbers)
        self.assertEqual(len(roll.notes), len(MIDI_HITS))
        self.assertTrue(roll.part_name.get().startswith("MIDI"))
        self.assertNoErrors()

    def test_the_roll_can_switch_to_songsterrs_vocal(self):
        self.open_song()
        app = self.app
        app.root.deiconify()
        app.root.geometry("1500x1300+0+0")
        self.pump(0.4)
        app.tab_roll.show_kind("vocal")
        roll = app.tab_roll.lanes["vocal"]
        self.pump(0.3)
        roll.part_box.current(1)
        roll.part_box.event_generate("<<ComboboxSelected>>")
        self.assertTrue(self.pump(5, lambda: roll.mode == "melody" and not roll.midi_numbers and bool(roll.notes)))
        self.assertEqual(len(roll.notes), len(self.tab.bars))
        self.assertNoErrors()

    def test_a_song_with_no_vocal_anywhere_says_so(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.tab.parts = [part for part in self.tab.parts if part.kind != "vocals"]
        self.open_song()
        app = self.app
        app.root.deiconify()
        self.pump(0.3)
        app.tab_roll.show_kind("vocal")
        self.pump(0.3)
        canvas = app.tab_roll.lanes["vocal"].canvas
        texts = [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]
        self.assertIn("Songsterr has no vocal part for this song.", texts)
        self.assertNoErrors()

    def test_the_midi_vocal_is_heard_in_preference_to_songsterrs(self):
        self.open_song()
        app = self.app
        app.tab_roll.lanes["vocal"].hear.set(True)
        app.on_hear_changed()
        plans, missing = app.hear_plan()
        self.assertEqual([(plan["kind"], plan["source"]) for plan in plans], [("vocal", "MIDI")])
        self.assertEqual(missing, [])
        self.assertEqual(app.tab_roll.lanes["vocal"].hear_note["text"], "MIDI")

    def test_without_a_midi_vocal_songsterrs_is_heard(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        app.tab_roll.lanes["vocal"].hear.set(True)
        app.on_hear_changed()
        plans, _missing = app.hear_plan()
        self.assertEqual([(plan["kind"], plan["source"]) for plan in plans], [("vocal", "Songsterr")])
        notes = app.resolve_hear(plans[0])
        self.assertTrue(notes)
        self.assertEqual({note[4] for note in notes}, {"vocal"})
        self.assertNoErrors()


class Adding(GuiCase):
    def setUp(self):
        super().setUp()
        self.tab.parts.append(songsterr.Part(3, "vocals", "Lead Vocals", 2))
        self.midi_path.write_bytes(drum_midi(MIDI_HITS))

    def add(self):
        app = self.app
        app.on_add_tab_parts()
        self.assertTrue(self.pump(5, lambda: not app.busy))
        self.pump(0.2)

    def test_it_adds_every_kind_the_midi_lacks_on_the_midis_bars(self):
        self.open_song()
        app = self.app
        self.assertEqual(app.view["kept"], [])
        self.add()
        kept = app.view["kept"]
        self.assertEqual(sorted(track.kind for track in kept), ["bass", "guitar", "vocal"])
        self.assertEqual(len({track.channel for track in kept}), 3)
        self.assertNotIn(9, {track.channel for track in kept})
        first_bar = app.view["bounds"][app.view["tab_shift"]]
        self.assertTrue(all(first_bar <= track.notes[0][0] < first_bar + BAR for track in kept), [t.notes[0] for t in kept])
        self.assertNoErrors()

    def test_it_is_undoable_and_saves_into_the_file(self):
        self.open_song()
        app = self.app
        self.add()
        data, _d, _b = app.export_data("song")
        path = self.base / "added.mid"
        path.write_bytes(data)
        self.assertEqual(sorted(track.kind for track in chart.parse_midi(path)["kept"]), ["bass", "guitar", "vocal"])
        app.on_undo()
        self.assertEqual(app.view["kept"], [])
        app.on_redo()
        self.assertEqual(len(app.view["kept"]), 3)
        self.assertNoErrors()

    def test_it_only_adds_what_is_missing(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS))
        self.open_song()
        app = self.app
        before = list(app.view["kept"])
        self.add()
        kept = app.view["kept"]
        self.assertEqual(kept[: len(before)], before)
        self.assertEqual([track.kind for track in kept[len(before):]], ["vocal"])
        self.assertNotIn(kept[-1].channel, {track.channel for track in before})
        self.assertNoErrors()

    def test_with_everything_present_it_changes_nothing(self):
        self.midi_path.write_bytes(band_midi(MIDI_HITS, vocal=True))
        self.open_song()
        app = self.app
        before = list(app.view["kept"])
        self.add()
        self.assertEqual(app.view["kept"], before)
        self.assertNoErrors()

    def test_saying_no_changes_nothing(self):
        self.open_song()
        self.answer = False
        self.add()
        self.assertEqual(self.app.view["kept"], [])
        self.assertNoErrors()

    def test_parts_that_cannot_be_read_are_reported_and_nothing_breaks(self):
        self.open_song()
        app = self.app
        self.part_error = "offline"
        self.add()
        self.assertEqual(app.view["kept"], [])
        self.assertFalse(app.busy)
        self.assertNoErrors()

    def test_the_menu_entry_follows_what_can_be_added(self):
        self.open_song()
        app = self.app
        self.assertEqual(app.tab_parts_button["state"], "normal")
        self.tab.parts = self.tab.parts[:1]
        app.refresh_tab_panel()
        self.assertEqual(app.tab_parts_button["state"], "disabled")


class FromSongsterr(GuiCase):
    def test_a_download_includes_the_vocal(self):
        tab = fake_tab()
        tab.parts.append(songsterr.Part(3, "vocals", "Lead Vocals", 2))
        path = finder.songsterr_midi(tab)
        midi = chart.parse_midi(path)
        self.assertEqual(sorted(track.kind for track in midi["kept"]), ["bass", "guitar", "vocal"])
        vocal = next(track for track in midi["kept"] if track.kind == "vocal")
        self.assertEqual(vocal.channel, 2)

    def test_the_fake_vocal_has_pitch(self):
        notes = songsterr.part_to_notes(fake_vocal_track(5))
        self.assertEqual(notes.kind, "vocals")
        self.assertEqual(len({note[4] for note in notes.notes}), 5)


if __name__ == "__main__":
    unittest.main()
