"""Guitar and bass tracks are kept when a MIDI is saved, and follow the edits the drums follow."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.support import BAR, PPQ, band_midi, drum_midi, midi_file, name_event, track_chunk

import build_section_chart as chart
import find_drum_midi as finder
import keep_tracks
import midi_export
import songsterr
from tests.test_gui import MIDI_HITS, GuiCase, fake_tab

K = keep_tracks.Kept


def kept(*notes, kind="guitar", channel=0):
    return K("Part", kind, channel, 27, tuple(notes))


def parse(path, data):
    path.write_bytes(data)
    return chart.parse_midi(path)


class Reading(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "song.mid"

    def test_guitar_and_bass_are_kept_and_the_piano_is_not(self):
        midi = parse(self.path, band_midi([2, 2, 2]))
        names = {track.name: track for track in midi["kept"]}
        self.assertEqual(set(names), {"Lead Guitar", "Bass"})
        self.assertEqual(names["Lead Guitar"].kind, "guitar")
        self.assertEqual(names["Bass"].kind, "bass")
        self.assertEqual(names["Lead Guitar"].program, 29)
        self.assertEqual(names["Bass"].channel, 1)

    def test_notes_keep_their_lengths(self):
        midi = parse(self.path, band_midi([1, 1]))
        guitar = next(track for track in midi["kept"] if track.kind == "guitar")
        self.assertEqual([note[:2] for note in guitar.notes], [(0, BAR // 2), (BAR, BAR // 2)])
        bass = next(track for track in midi["kept"] if track.kind == "bass")
        self.assertEqual(bass.notes[0][1], BAR - 1)

    def test_drums_are_unchanged_by_keeping_the_rest(self):
        with_band = parse(self.path, band_midi([3, 1, 2]))
        plain = parse(self.path, drum_midi([3, 1, 2]))
        self.assertEqual(with_band["events"], plain["events"])
        self.assertEqual(plain["kept"], [])

    def test_channel_ten_is_never_kept(self):
        midi = parse(self.path, drum_midi([2, 2]))
        self.assertEqual(midi["kept"], [])

    def test_one_track_with_several_channels_is_split_by_program(self):
        events = [
            name_event("Everything"),
            (0, bytes([0xC0, 30])),
            (0, bytes([0xC1, 34])),
            (0, bytes([0xC2, 0])),
            (0, bytes([0x90, 60, 90])), (100, bytes([0x80, 60, 0])),
            (0, bytes([0x91, 40, 90])), (200, bytes([0x81, 40, 0])),
            (0, bytes([0x92, 72, 90])), (200, bytes([0x82, 72, 0])),
        ]
        midi = parse(self.path, midi_file([track_chunk(events)], fmt=0))
        self.assertEqual(sorted((track.kind, track.channel) for track in midi["kept"]), [("bass", 1), ("guitar", 0)])
        self.assertTrue(all("channel" in track.name for track in midi["kept"]))

    def test_a_note_that_never_ends_still_gets_a_length(self):
        events = [name_event("Bass"), (0, bytes([0xC0, 33])), (0, bytes([0x90, 40, 90]))]
        midi = parse(self.path, midi_file([track_chunk(events)]))
        self.assertGreater(midi["kept"][0].notes[0][1], 0)

    def test_overlapping_repeats_of_a_note_pair_up_in_order(self):
        notes = keep_tracks._lengths([(0, 40, 90), (50, 40, 90)], [(100, 40), (200, 40)], 10)
        self.assertEqual([(note[0], note[1]) for note in notes], [(0, 100), (50, 150)])

    def test_classify(self):
        self.assertEqual(keep_tracks.classify("Bass guitar", 0), "bass")
        self.assertEqual(keep_tracks.classify("Rhythm Guitar", 0), "guitar")
        self.assertEqual(keep_tracks.classify("Gtr 2", None), "guitar")
        self.assertEqual(keep_tracks.classify("Track 4", 34), "bass")
        self.assertEqual(keep_tracks.classify("Track 4", 27), "guitar")
        self.assertIsNone(keep_tracks.classify("Strings", 48))
        self.assertIsNone(keep_tracks.classify("Bass drum", 0))
        self.assertIsNone(keep_tracks.classify("Bassoon", 70))


class Editing(unittest.TestCase):
    def test_cut_removes_and_closes_the_gap(self):
        track = kept((0, 10, 60, 90), (100, 10, 61, 90), (250, 10, 62, 90))
        out = keep_tracks.cut([track], 100, 200)[0]
        self.assertEqual(out.notes, ((0, 10, 60, 90), (150, 10, 62, 90)))

    def test_cut_stops_a_note_that_rings_into_it(self):
        out = keep_tracks.cut([kept((50, 500, 60, 90))], 100, 200)[0]
        self.assertEqual(out.notes, ((50, 50, 60, 90),))

    def test_blank_keeps_the_timing(self):
        track = kept((0, 500, 60, 90), (150, 10, 61, 90), (300, 10, 62, 90))
        out = keep_tracks.blank([track], 100, 200)[0]
        self.assertEqual(out.notes, ((0, 100, 60, 90), (300, 10, 62, 90)))

    def test_insert_moves_later_notes_and_stops_ringing_ones(self):
        track = kept((0, 500, 60, 90), (300, 10, 61, 90))
        out = keep_tracks.insert([track], 200, 1000)[0]
        self.assertEqual(out.notes, ((0, 200, 60, 90), (1300, 10, 61, 90)))

    def test_copy_ranges_moves_each_range(self):
        track = kept((0, 10, 60, 90), (120, 10, 61, 90), (500, 300, 62, 90))
        out = keep_tracks.copy_ranges([track], [(100, 200, 0), (400, 600, -300)])[0]
        self.assertEqual(out.notes, ((120, 10, 61, 90), (200, 100, 62, 90)))

    def test_summary(self):
        self.assertEqual(keep_tracks.summary([kept(), kept(kind="bass")]), "1 guitar and 1 bass")
        self.assertEqual(keep_tracks.summary([kept(kind="bass")]), "1 bass")


class Writing(unittest.TestCase):
    ARGS = (PPQ, [[0, 38, 100]], [(0, 500_000)], [0, BAR, 2 * BAR], [(4, 4), (4, 4)])

    def test_without_other_parts_it_is_still_type_0_with_one_track(self):
        data = midi_export.build(*self.ARGS)
        self.assertEqual(int.from_bytes(data[8:10], "big"), 0)
        self.assertEqual(int.from_bytes(data[10:12], "big"), 1)

    def test_with_other_parts_it_is_type_1_with_a_track_each(self):
        extra = [kept((0, 100, 64, 90)), kept((0, 100, 40, 90), kind="bass", channel=1)]
        data = midi_export.build(*self.ARGS, extra=extra)
        self.assertEqual(int.from_bytes(data[8:10], "big"), 1)
        self.assertEqual(int.from_bytes(data[10:12], "big"), 3)
        self.assertEqual(data.count(b"MTrk"), 3)

    def test_the_drums_stay_first_on_channel_ten(self):
        import tempfile
        from pathlib import Path

        data = midi_export.build(*self.ARGS, extra=[kept((0, 100, 64, 90))])
        with tempfile.TemporaryDirectory() as folder:
            midi = parse(Path(folder) / "out.mid", data)
        self.assertEqual(midi["events"], [(0, 38, 100)])
        self.assertEqual(len(midi["kept"]), 1)

    def test_round_trip_keeps_notes_channel_and_program(self):
        import tempfile
        from pathlib import Path

        original = K("Lead Guitar", "guitar", 3, 29, ((0, 240, 64, 90), (480, 120, 67, 100)))
        data = midi_export.build(*self.ARGS, extra=[original])
        with tempfile.TemporaryDirectory() as folder:
            midi = parse(Path(folder) / "out.mid", data)
        back = midi["kept"][0]
        self.assertEqual((back.name, back.kind, back.channel, back.program), ("Lead Guitar", "guitar", 3, 29))
        self.assertEqual(back.notes, original.notes)

    def test_a_kept_part_on_channel_ten_is_moved_off_the_drums(self):
        chunk = keep_tracks.chunk(K("Odd", "guitar", 9, 27, ((0, 10, 60, 90),)))
        self.assertNotIn(bytes([0x99, 60, 90]), chunk)

    def test_pedal_parts_reports_where_each_range_came_from(self):
        divisions = {
            "intro": [1, 1], "pattern1": [2, 3], "pattern1Fill": [3, 3],
            "pattern2": [4, 4], "pattern2Fill": [4, 4], "ending": [5, 5],
        }
        bounds = [bar * BAR for bar in range(6)]
        ranges: list = []
        midi_export.pedal_parts([], [(0, 500_000)], bounds, [(4, 4)] * 5, divisions, ranges)
        self.assertEqual([item[:2] for item in ranges], [(0, BAR), (BAR, 3 * BAR), (3 * BAR, 4 * BAR), (4 * BAR, 5 * BAR)])
        self.assertEqual([item[2] for item in ranges], [0, 0, 0, 0])


class FromTab(unittest.TestCase):
    def notes(self, items, bars=2, tuning=(64, 59, 55, 50, 45, 40), kind="guitar"):
        return SimpleNamespace(name="Part", kind=kind, bars=bars, tuning=list(tuning), notes=items, program=29)

    BOUNDS = [0, 1920, 3840]

    def test_pitch_is_open_string_plus_fret_and_timing_follows_the_bars(self):
        tab = self.notes([(0, 0.0, 0.5, 1, 3, ""), (1, 0.5, 0.25, 0, 0, "")])
        out = keep_tracks.from_tab(tab, self.BOUNDS, PPQ, 0, 29)
        self.assertEqual([(note[0], note[2]) for note in out.notes], [(0, 62), (1920 + 960, 64)])
        self.assertEqual(out.program, 29)
        self.assertEqual(out.kind, "guitar")

    def test_a_different_number_of_bars_is_left_out(self):
        tab = self.notes([(0, 0.0, 0.5, 1, 3, "")], bars=3)
        self.assertIsNone(keep_tracks.from_tab(tab, self.BOUNDS, PPQ, 0))

    def test_an_empty_part_is_left_out(self):
        self.assertIsNone(keep_tracks.from_tab(self.notes([]), self.BOUNDS, PPQ, 0))

    def test_a_tied_note_rings_on_instead_of_restriking(self):
        tab = self.notes([(0, 0.5, 0.5, 1, 3, ""), (1, 0.0, 0.5, 1, 3, "t")])
        out = keep_tracks.from_tab(tab, self.BOUNDS, PPQ, 0)
        self.assertEqual(len(out.notes), 1)
        self.assertGreater(out.notes[0][1], 1800)

    def test_dead_and_ghost_notes_are_quieter_and_dead_notes_short(self):
        tab = self.notes([(0, 0.0, 0.5, 1, 3, "d"), (0, 0.5, 0.25, 1, 3, "g"), (1, 0.0, 0.25, 1, 3, "a")])
        out = keep_tracks.from_tab(tab, self.BOUNDS, PPQ, 0)
        dead, ghost, accent = out.notes
        self.assertLessEqual(dead[1], PPQ // 8)
        self.assertLess(ghost[3], 60)
        self.assertGreater(accent[3], 100)

    def test_pitch_is_kept_inside_midi_range(self):
        tab = self.notes([(0, 0.0, 0.5, 0, 99, "")], tuning=(60,) * 6)
        self.assertEqual(keep_tracks.from_tab(tab, self.BOUNDS, PPQ, 0).notes[0][2], 127)

    def test_a_bass_part_gets_a_bass_program_by_default(self):
        tab = self.notes([(0, 0.0, 0.5, 0, 0, "")], kind="bass")
        tab.program = -1
        out = keep_tracks.from_tab(tab, self.BOUNDS, PPQ, 1, tab.program)
        self.assertEqual((out.kind, out.program), ("bass", 33))


class PartProgram(unittest.TestCase):
    def test_part_to_notes_carries_the_instrument(self):
        from tests.support import fake_guitar_track

        notes = songsterr.part_to_notes(fake_guitar_track(3))
        self.assertEqual(notes.program, 29)
        self.assertEqual(notes.bars, 3)
        self.assertEqual(len(notes.notes), 3)
        self.assertEqual(notes.tuning[1], 59)

    def test_part_to_notes_reads_repeats_ties_rests_and_bad_data(self):
        track = {
            "strings": 4,
            "tuning": [43, 38, 33, 28],
            "instrumentId": "x",
            "measures": [
                {"signature": [4, 4], "repeatStart": True, "voices": [{"beats": [
                    {"duration": [1, 2], "notes": [{"string": 2, "fret": 5}]},
                    {"duration": [1, 2], "rest": True, "notes": []},
                ]}]},
                {"signature": [4, 4], "repeat": 2, "voices": [{"beats": [
                    {"duration": [1, 1], "notes": [{"string": 9, "fret": 1}, {"string": 0, "fret": "bad"}, {"string": 1, "fret": 2, "tie": True}]},
                ]}]},
            ],
        }
        notes = songsterr.part_to_notes(track)
        self.assertEqual(notes.program, -1)
        self.assertEqual(notes.bars, 4)
        flags = [item[5] for item in notes.notes]
        self.assertIn("t", flags)
        self.assertTrue(all(0 <= item[3] < 4 for item in notes.notes))


class InTheFinder(GuiCase):
    def setUp(self):
        super().setUp()
        self.midi_path.write_bytes(band_midi(MIDI_HITS))

    def kept_ticks(self):
        return {track.kind: [note[0] for note in track.notes] for track in self.app.view["kept"]}

    def test_opening_a_song_with_a_band_keeps_the_guitar_and_bass(self):
        self.open_song()
        view = self.app.view
        self.assertEqual(sorted(track.kind for track in view["kept"]), ["bass", "guitar"])
        self.assertTrue(all(len(track.notes) == len(MIDI_HITS) for track in view["kept"]))
        self.assertNoErrors()

    def test_saving_the_whole_song_carries_them_and_the_drums_do_not_change(self):
        self.open_song()
        data, _divisions, bars = self.app.export_data("song")
        midi = parse(self.base / "song.mid", data)
        self.assertEqual(int.from_bytes(data[8:10], "big"), 1)
        self.assertEqual(sorted(track.kind for track in midi["kept"]), ["bass", "guitar"])
        plain, _d, _b = self.app.export_data("song", with_parts=False)
        self.assertEqual(int.from_bytes(plain[8:10], "big"), 0)
        self.assertEqual(parse(self.base / "plain.mid", plain)["events"], midi["events"])
        self.assertEqual(bars, len(MIDI_HITS))

    def test_a_file_with_no_band_still_saves_as_type_0(self):
        self.midi_path.write_bytes(drum_midi(MIDI_HITS))
        self.open_song()
        self.assertEqual(self.app.view["kept"], [])
        data, _d, _b = self.app.export_data("song")
        self.assertEqual(int.from_bytes(data[8:10], "big"), 0)

    def test_deleting_bars_takes_the_guitar_and_bass_with_them_and_undo_brings_them_back(self):
        self.open_song()
        app = self.app
        before = list(app.view["kept"])
        app.apply_selection(3, 4)
        app.on_delete_bars()
        ticks = self.kept_ticks()
        bounds = app.view["bounds"]
        self.assertEqual(ticks["guitar"], [bound for bound in bounds[:-1]][: len(ticks["guitar"])])
        self.assertEqual(len(ticks["guitar"]), len(MIDI_HITS) - 2)
        app.on_undo()
        self.assertEqual(app.view["kept"], before)
        self.assertNoErrors()

    def test_blanking_bars_silences_them_and_inserting_bars_moves_later_notes(self):
        self.open_song()
        app = self.app
        app.apply_selection(2, 3)
        app.on_blank_bars()
        self.assertEqual(len(app.view["kept"][0].notes), len(MIDI_HITS) - 2)
        app.on_bars_inserted(1, 2)
        first = app.view["kept"][0].notes[0][0]
        self.assertEqual(first, app.view["bounds"][2])
        self.assertNoErrors()

    def test_pedal_parts_carry_only_those_bars(self):
        self.open_song()
        data, _d, bars = self.app.export_data("parts")
        midi = parse(self.base / "parts.mid", data)
        counts = {track.kind: len(track.notes) for track in midi["kept"]}
        self.assertTrue(counts)
        self.assertTrue(all(0 < count <= bars for count in counts.values()), counts)
        self.assertLessEqual(bars, len(MIDI_HITS))

    def test_using_songsterrs_drums_swaps_the_guitar_and_bass_for_songsterrs(self):
        self.open_song()
        app = self.app
        app.on_use_tab_drums()
        self.assertTrue(self.pump(5, lambda: len(app.view["bounds"]) - 1 == len(self.tab.bars)))
        self.assertEqual(sorted(track.kind for track in app.view["kept"]), ["bass", "guitar"])
        self.assertTrue(all(len(track.notes) == len(self.tab.bars) for track in app.view["kept"]))
        app.on_undo()
        self.assertEqual(len(app.view["kept"][0].notes), len(MIDI_HITS))
        self.assertNoErrors()

    def test_using_songsterrs_drums_when_the_guitar_cannot_be_read_still_works(self):
        self.open_song()
        app = self.app
        self.part_error = "offline"
        app.on_use_tab_drums()
        self.assertTrue(self.pump(5, lambda: len(app.view["bounds"]) - 1 == len(self.tab.bars)))
        self.assertEqual(app.view["kept"], [])
        self.assertNoErrors()

    def test_the_export_dialog_opens_with_the_option(self):
        import tkinter as tk
        from unittest import mock

        self.open_song()
        with mock.patch.object(tk.Toplevel, "grab_set", lambda dialog: None):
            self.app.open_export()
        self.pump(0.2)
        _mode, with_parts, _keep = self.app._export_vars
        self.assertTrue(with_parts.get())
        dialogs = [child for child in self.app.root.winfo_children() if isinstance(child, tk.Toplevel)]
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                try:
                    texts.append(str(child.cget("text")))
                except tk.TclError:
                    pass
                walk(child)

        walk(dialogs[-1])
        self.assertTrue(any("Keep the 1 guitar and 1 bass tracks for later" in text for text in texts), texts)
        dialogs[-1].destroy()
        self.assertNoErrors()


class FromSongsterr(GuiCase):
    def test_downloading_a_tab_as_midi_keeps_its_guitar_and_bass(self):
        tab = fake_tab()
        path = finder.songsterr_midi(tab)
        midi = chart.parse_midi(path)
        self.assertEqual(sorted(track.kind for track in midi["kept"]), ["bass", "guitar"])
        self.assertEqual(int.from_bytes(path.read_bytes()[8:10], "big"), 1)
        self.assertGreater(len(midi["events"]), 0)

    def test_a_part_that_cannot_be_read_is_skipped_and_the_drums_still_download(self):
        self.part_error = "offline"
        path = finder.songsterr_midi(fake_tab())
        self.assertEqual(chart.parse_midi(path)["kept"], [])
        self.assertEqual(int.from_bytes(path.read_bytes()[8:10], "big"), 0)

    def test_a_song_with_no_guitar_or_bass_part_downloads_as_before(self):
        tab = fake_tab()
        tab.parts = tab.parts[:1]
        path = finder.songsterr_midi(tab)
        self.assertEqual(chart.parse_midi(path)["kept"], [])


if __name__ == "__main__":
    unittest.main()
