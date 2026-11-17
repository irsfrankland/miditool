"""Songsterr tab reading, repeat play-out, line-up search, drum conversion and section rules."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support  # noqa: F401  (puts scripts/ on the path)

import songsterr


def measure(hits=0, signature=None, **extra):
    beats = [{"duration": [1, 8], "notes": [{"fret": 38}]} for _ in range(hits)]
    item = {"voices": [{"beats": beats}]} if hits else {"rest": True, "voices": []}
    if signature:
        item["signature"] = signature
    item.update(extra)
    return item


class PlayOrder(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(songsterr.play_order([measure(1)] * 4), [0, 1, 2, 3])

    def test_empty(self):
        self.assertEqual(songsterr.play_order([]), [])

    def test_simple_repeat(self):
        ms = [measure(1, repeatStart=True), measure(1, repeat=2), measure(1)]
        self.assertEqual(songsterr.play_order(ms), [0, 1, 0, 1, 2])

    def test_repeat_three_times(self):
        ms = [measure(1, repeatStart=True), measure(1, repeat=3)]
        self.assertEqual(songsterr.play_order(ms), [0, 1, 0, 1, 0, 1])

    def test_alternate_endings(self):
        ms = [measure(1, repeatStart=True), measure(1, alternateEnding=[1], repeat=2), measure(1, alternateEnding=[2]), measure(1)]
        self.assertEqual(songsterr.play_order(ms), [0, 1, 0, 2, 3])

    def test_two_repeats_in_a_row(self):
        ms = [
            measure(1, repeatStart=True), measure(1, repeat=2),
            measure(1, repeatStart=True), measure(1, repeat=2),
        ]
        self.assertEqual(songsterr.play_order(ms), [0, 1, 0, 1, 2, 3, 2, 3])

    def test_repeat_without_start_goes_back_to_bar_one(self):
        ms = [measure(1), measure(1, repeat=2)]
        self.assertEqual(songsterr.play_order(ms), [0, 1, 0, 1])

    def test_never_loops_forever(self):
        ms = [measure(1, repeatStart=True), measure(1, repeat=10**9)]
        self.assertLessEqual(len(songsterr.play_order(ms)), 20000)

    def test_junk_endings_do_not_crash(self):
        ms = [measure(1, alternateEnding=["x"]), measure(1, alternateEnding=None, repeat="two")]
        self.assertEqual(songsterr.play_order(ms), [0, 1])


class ReadMeasures(unittest.TestCase):
    def test_hits_meters_markers(self):
        ms = [
            measure(4, [4, 4], marker={"text": "Intro"}),
            measure(8),
            measure(6, [3, 4], marker={"text": "[A] Verse 1"}),
        ]
        bars, tempos = songsterr.read_measures(ms, [], True)
        self.assertEqual([b["hits"] for b in bars], [4, 8, 6])
        self.assertEqual([b["meter"] for b in bars], [(4, 4), (4, 4), (3, 4)])
        self.assertEqual([b["marker"] for b in bars], ["Intro", "", "[A] Verse 1"])
        self.assertEqual(tempos, [])

    def test_non_drum_tracks_have_no_hits(self):
        bars, _ = songsterr.read_measures([measure(4)], [], False)
        self.assertEqual(bars[0]["hits"], 0)

    def test_ties_and_rests_not_counted(self):
        beat = {"duration": [1, 4], "notes": [{"fret": 38, "tie": True}, {"fret": 36, "rest": True}, {"fret": 42}]}
        bars, _ = songsterr.read_measures([{"voices": [{"beats": [beat, {"rest": True, "notes": [{"fret": 1}]}]}]}], [], True)
        self.assertEqual(bars[0]["hits"], 1)

    def test_tempo_marks_follow_played_bars(self):
        ms = [measure(1, repeatStart=True), measure(1, repeat=2), measure(1)]
        _bars, tempos = songsterr.read_measures(ms, [{"measure": 0, "bpm": 100}, {"measure": 2, "bpm": 140}], True)
        self.assertEqual(tempos, [(1, 100.0), (5, 140.0)])

    def test_tempo_first_mark_after_bar_one_is_backfilled(self):
        _bars, tempos = songsterr.read_measures([measure(1)] * 3, [{"measure": 2, "bpm": 90}], True)
        self.assertEqual(tempos, [(1, 90.0), (3, 90.0)])

    def test_junk_tempo_marks_ignored(self):
        _bars, tempos = songsterr.read_measures([measure(1)], [{"measure": 0, "bpm": None}, {"bpm": "x"}], True)
        self.assertEqual(tempos, [])

    def test_bad_signature_keeps_previous(self):
        ms = [measure(1, [3, 4]), {"signature": [4, 0]}, {"signature": "x"}]
        self.assertEqual(songsterr.measure_meters(ms), [(3, 4)] * 3)


class TabMath(unittest.TestCase):
    def tab(self, bars, tempos):
        return songsterr.Tab(1, "A", "T", "u", "l", bars=bars, tempos=tempos)

    def test_seconds_with_tempo_change(self):
        bars = [{"meter": (4, 4), "hits": 0, "marker": ""}] * 4
        tab = self.tab(bars, [(1, 120.0), (3, 60.0)])
        self.assertAlmostEqual(tab.seconds(), 2 + 2 + 4 + 4)

    def test_seconds_default_120(self):
        bars = [{"meter": (3, 4), "hits": 0, "marker": ""}]
        self.assertAlmostEqual(self.tab(bars, []).seconds(), 1.5)

    def test_meter_text(self):
        bars = [{"meter": (4, 4)}, {"meter": (4, 4)}, {"meter": (3, 4)}]
        self.assertEqual(self.tab(bars, []).meter_text(), "4/4, 3/4 at bar 3")
        self.assertEqual(self.tab([], []).meter_text(), "4/4")

    def test_best_part_by_views(self):
        tab = self.tab([], [])
        tab.parts = [songsterr.Part(0, "drums", "a", 5), songsterr.Part(1, "drums", "b", 50), songsterr.Part(2, "bass", "c", 1)]
        self.assertEqual(tab.best_part("drums").index, 1)
        self.assertIsNone(tab.best_part("guitar"))

    def test_tab_cache_roundtrip(self):
        tab = self.tab([{"meter": (4, 4), "hits": 3, "marker": "x", "source": 1}], [(1, 100.0)])
        tab.sections = [(1, 1, "Intro")]
        tab.parts = [songsterr.Part(0, "drums", "n", 4)]
        from dataclasses import asdict

        loaded = songsterr._tab_from(json.loads(json.dumps(asdict(tab))))
        self.assertEqual(loaded.bars[0]["meter"], (4, 4))
        self.assertEqual(loaded.sections, [(1, 1, "Intro")])
        self.assertEqual(loaded.tempos, [(1, 100.0)])
        self.assertEqual(loaded.parts[0].kind, "drums")


class Names(unittest.TestCase):
    def test_part_kind(self):
        self.assertEqual(songsterr.part_kind({"instrumentId": 1024}), "drums")
        self.assertEqual(songsterr.part_kind({"hash": "bass_1"}), "bass")
        self.assertEqual(songsterr.part_kind({"instrument": "Distortion Guitar"}), "guitar")
        self.assertEqual(songsterr.part_kind({}), "other")

    def test_part_name(self):
        self.assertEqual(songsterr.part_name("Angus Young | Gibson SG | Lead Guitar"), "Lead Guitar (Angus Young)")
        self.assertEqual(songsterr.part_name(""), "Unnamed part")
        self.assertEqual(songsterr.part_name("Drums"), "Drums")

    def test_same_title(self):
        self.assertTrue(songsterr.same_title("TNT", "T.N.T."))
        self.assertFalse(songsterr.same_title("", ""))
        self.assertFalse(songsterr.same_title("A", "B"))

    def test_slug(self):
        self.assertEqual(songsterr.slug("AC/DC  Back In Black!"), "ac-dc-back-in-black")
        self.assertEqual(songsterr.slug("!!!"), "song")

    def test_clean_name_and_kind(self):
        self.assertEqual(songsterr.clean_name("[A] Verse   1"), "Verse 1")
        self.assertEqual(songsterr.clean_name("[Chorus]"), "Chorus")
        self.assertEqual(songsterr.clean_name("   "), "Part")
        cases = {
            "Pre-Chorus": "prechorus", "Prechorus 2": "prechorus", "Chorus 2": "chorus",
            "Outro": "outro", "Ending chorus": "outro", "Bridge": "bridge", "Middle 8": "bridge",
            "Intro riff": "intro", "Verse": "verse", "Guitar Solo": "solo", "Interlude": "other",
            "Outro solo": "outro",
        }
        for name, expected in cases.items():
            self.assertEqual(songsterr.kind(name), expected, name)

    def test_sections_from(self):
        bars = [{"marker": m} for m in ["", "", "Verse", "", "Chorus", ""]]
        self.assertEqual(songsterr.sections_from(bars), [(1, 2, "Intro"), (3, 4, "Verse"), (5, 6, "Chorus")])
        self.assertEqual(songsterr.sections_from([{"marker": ""}] * 3), [])
        self.assertEqual(songsterr.sections_from([]), [])


class LineUp(unittest.TestCase):
    PATTERN = [0, 3, 8, 8, 6, 12, 12, 4, 9, 9, 2, 14, 14, 7, 5, 11, 11, 3, 8, 8, 1, 13, 13, 6]

    def test_finds_exact_shift(self):
        for shift in (-5, -1, 0, 1, 3, 7):
            midi = [0] * 40
            for i, h in enumerate(self.PATTERN):
                if 0 <= i + shift < 40:
                    midi[i + shift] = h
            got, score, sure = songsterr.align(self.PATTERN, midi)
            self.assertEqual(got, shift, (shift, score))
            self.assertTrue(sure)

    def test_silent_inputs(self):
        self.assertEqual(songsterr.align([], [1, 2]), (0, 0.0, False))
        self.assertEqual(songsterr.align([0, 0], [0, 0]), (0, 0.0, False))

    def test_unrelated_is_not_sure(self):
        import random

        rng = random.Random(7)
        a = [rng.randint(0, 20) for _ in range(30)]
        b = [rng.randint(0, 20) for _ in range(30)]
        _shift, _score, sure = songsterr.align(a, b)
        self.assertFalse(sure)

    def test_short_tab_does_not_crash(self):
        self.assertEqual(songsterr.shift_score([1, 2], [1, 2, 3], 0), None)

    def test_half_time_fold(self):
        tab = self.PATTERN * 2  # 48 tab bars
        midi = [tab[i] + tab[i + 1] for i in range(0, len(tab), 2)]  # 24 midi bars, half time
        shift, _score, sure, half = songsterr.fit(tab, midi)
        self.assertTrue(half)
        self.assertTrue(sure)
        self.assertEqual(shift, 0)

    def test_fit_straight(self):
        shift, _s, sure, half = songsterr.fit(self.PATTERN, self.PATTERN)
        self.assertEqual((shift, sure, half), (0, True, False))

    def test_place(self):
        sections = [(1, 4, "Intro"), (5, 12, "Verse"), (13, 20, "Chorus")]
        self.assertEqual(songsterr.place(sections, 2, 22), [(3, 6, "Intro"), (7, 14, "Verse"), (15, 22, "Chorus")])
        self.assertEqual(songsterr.place(sections, -4, 30), [(1, 8, "Verse"), (9, 16, "Chorus")])
        self.assertEqual(songsterr.place(sections, 0, 10), [(1, 4, "Intro"), (5, 10, "Verse")])
        self.assertEqual(songsterr.place(sections, 50, 10), [])
        self.assertEqual(songsterr.place(sections, -50, 10), [])

    def test_place_half_time(self):
        self.assertEqual(songsterr.place([(1, 4, "A"), (5, 8, "B")], 0, 4, half=True), [(1, 2, "A"), (3, 4, "B")])


class Divisions(unittest.TestCase):
    SECTIONS = [(1, 4, "Intro"), (5, 12, "Verse 1"), (13, 20, "Chorus"), (21, 24, "Bridge"), (25, 32, "Chorus 2"), (33, 36, "Outro")]

    def test_typical(self):
        d, notes = songsterr.divisions_from_sections(self.SECTIONS, 36)
        self.assertEqual(d["intro"], [1, 12])
        self.assertEqual(d["pattern1"], [13, 20])
        self.assertEqual(d["pattern1Fill"], [12, 12])
        self.assertEqual(d["pattern2"], [21, 24])
        self.assertEqual(d["pattern2Fill"], [24, 24])
        self.assertEqual(d["ending"], [33, 36])
        self.assertTrue(notes[0].startswith("Pattern 1 is Chorus"))

    def test_everything_in_range(self):
        for sections, count in ((self.SECTIONS, 36), (self.SECTIONS, 20), ([(1, 8, "Verse")], 8), ([(1, 1, "Chorus")], 1)):
            d, _ = songsterr.divisions_from_sections(sections, count)
            for key, (start, end) in d.items():
                self.assertTrue(1 <= start <= end <= count, (key, d, count))

    def test_no_sections(self):
        with self.assertRaises(ValueError):
            songsterr.divisions_from_sections([], 10)
        with self.assertRaises(ValueError):
            songsterr.divisions_from_sections(self.SECTIONS, 0)

    def test_ending_chorus_after_chorus_is_not_pattern(self):
        sections = [(1, 8, "Chorus"), (9, 16, "Ending Chorus")]
        d, _ = songsterr.divisions_from_sections(sections, 16)
        self.assertEqual(d["ending"][1], 16)


class DrumsToMidi(unittest.TestCase):
    def track(self, measures, tempo=None, **extra):
        return {"name": "Drums | Kit", "instrumentId": 1024, "measures": measures,
                "automations": {"tempo": tempo or [{"measure": 0, "bpm": 120, "type": 4}]}, **extra}

    def bar(self, notes, signature=None, **extra):
        beats = []
        for duration, fret, *flags in notes:
            beat = {"duration": duration, "notes": [{"fret": fret}] if fret is not None else []}
            if fret is None:
                beat["rest"] = True
            if flags:
                beat.update(flags[0])
            beats.append(beat)
        item = {"voices": [{"beats": beats}]}
        if signature:
            item["signature"] = signature
        item.update(extra)
        return item

    def test_basic_positions(self):
        quarter = [[1, 4], 36], [[1, 4], 38], [[1, 4], 36], [[1, 4], 38]
        out = songsterr.drums_to_midi(self.track([self.bar([(d, f) for d, f in quarter], [4, 4])]), 480)
        self.assertEqual([t[0] for t in out.ticks], [0, 480, 960, 1440])
        self.assertEqual([t[1] for t in out.ticks], [36, 38, 36, 38])
        self.assertEqual(out.bounds, [0, 1920])
        self.assertEqual(out.tempos, [(0, 500_000)])

    def test_empty_part(self):
        with self.assertRaises(ValueError):
            songsterr.drums_to_midi(self.track([]), 480)

    def test_unknown_drums_dropped_and_gp_extras_moved(self):
        out = songsterr.drums_to_midi(self.track([self.bar([([1, 4], 5), ([1, 4], 91), ([1, 4], 38)], [4, 4])]), 480)
        self.assertEqual([t[1] for t in out.ticks], [38, 38])
        self.assertEqual(out.dropped, {5: 1})
        self.assertEqual(out.moved, {91: 1})

    def test_ghost_accent_velocity(self):
        notes = [{"fret": 38, "ghost": True}, {"fret": 38, "accentuated": 2}, {"fret": 38}]
        beats = [{"duration": [1, 4], "notes": [n]} for n in notes]
        out = songsterr.drums_to_midi(self.track([{"signature": [4, 4], "voices": [{"beats": beats}]}]), 480)
        ghost, accent, plain = (t[2] for t in out.ticks)
        self.assertLess(ghost, plain)
        self.assertGreater(accent, plain)
        self.assertTrue(all(1 <= v <= 127 for v in (ghost, accent, plain)))

    def test_repeat_is_played_out(self):
        ms = [self.bar([([1, 1], 36)], [4, 4], repeatStart=True), self.bar([([1, 1], 38)], repeat=2)]
        out = songsterr.drums_to_midi(self.track(ms), 480)
        self.assertEqual(len(out.bounds) - 1, 4)
        self.assertEqual([t[1] for t in out.ticks], [36, 38, 36, 38])

    def test_tempo_change(self):
        ms = [self.bar([([1, 1], 36)], [4, 4]), self.bar([([1, 1], 36)])]
        out = songsterr.drums_to_midi(self.track(ms, [{"measure": 0, "bpm": 120}, {"measure": 1, "bpm": 60}]), 480)
        self.assertEqual(out.tempos[0], (0, 500_000))
        self.assertEqual(out.tempos[-1], (1920, 1_000_000))

    def test_tempo_note_unit(self):
        out = songsterr.drums_to_midi(self.track([self.bar([([1, 1], 36)], [4, 4])], [{"measure": 0, "bpm": 60, "type": 8}]), 480)
        self.assertEqual(out.tempos[0][1], 2_000_000)  # 60 eighth notes a minute = 30 quarter notes a minute

    def test_grace_note_before_beat_never_negative(self):
        beats = [{"duration": [1, 32], "graceNote": "beforeBeat", "notes": [{"fret": 38}]}, {"duration": [1, 4], "notes": [{"fret": 36}]}]
        out = songsterr.drums_to_midi(self.track([{"signature": [4, 4], "voices": [{"beats": beats}]}]), 480)
        self.assertTrue(all(t[0] >= 0 for t in out.ticks))

    def test_anacrusis_shortens_first_bar(self):
        ms = [self.bar([([1, 4], 36)], [4, 4]), self.bar([([1, 1], 36)])]
        out = songsterr.drums_to_midi(self.track(ms, anacrusis=True), 480)
        self.assertEqual(out.bounds[1], 480)

    def test_notes_beyond_bar_are_ignored(self):
        out = songsterr.drums_to_midi(self.track([self.bar([([1, 1], 36), ([1, 4], 38)], [4, 4])]), 480)
        self.assertEqual([t[1] for t in out.ticks], [36])

    def test_missing_duration_does_not_crash(self):
        beats = [{"notes": [{"fret": 38}]}, {"duration": [1, 4], "notes": [{"fret": 36}]}]
        songsterr.drums_to_midi(self.track([{"signature": [4, 4], "voices": [{"beats": beats}]}]), 480)

    def test_zero_denominator_duration(self):
        beats = [{"duration": [1, 0], "notes": [{"fret": 38}]}]
        try:
            songsterr.drums_to_midi(self.track([{"signature": [4, 4], "voices": [{"beats": beats}]}]), 480)
        except ZeroDivisionError:
            self.fail("a bad duration from Songsterr should not raise ZeroDivisionError")


class Autocomplete(unittest.TestCase):
    NAMES = ["AC/DC", "Metallica", "Mötley Crüe", "The Beatles", "Black Sabbath", "Beastie Boys", "Bee Gees"]

    def test_artist_prefix_and_limit(self):
        self.assertEqual(songsterr.artist_matches(self.NAMES, "met")[0], "Metallica")
        self.assertLessEqual(len(songsterr.artist_matches(self.NAMES, "b", limit=2)), 2)

    def test_artist_blank_query(self):
        result = songsterr.artist_matches(self.NAMES, "")
        self.assertEqual(result, [])

    def test_artist_accents(self):
        self.assertIn("Mötley Crüe", songsterr.artist_matches(self.NAMES, "motley"))


class Cache(unittest.TestCase):
    def test_find_cached_reads_a_fresh_file_without_the_network(self):
        from dataclasses import asdict
        import time

        tab = songsterr.Tab(7, "Band", "Song", "u", "l", bars=[{"meter": (4, 4), "hits": 1, "marker": "", "source": 1}])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{songsterr.slug('Band')}--{songsterr.slug('Song')}.json"
            path.write_text(json.dumps({"saved": time.time(), "tab": asdict(tab)}), encoding="utf-8")
            loaded = songsterr.find_cached("Band", "Song", Path(folder))
            self.assertEqual(loaded.song_id, 7)
            self.assertEqual(loaded.bars[0]["meter"], (4, 4))

    def test_popular_cache_fresh_and_corrupt(self):
        import time

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "popular.json"
            path.write_text(json.dumps({"saved": time.time(), "artists": ["A"], "songs": [["A", "S"], ["bad"]]}), encoding="utf-8")
            artists, songs = songsterr.popular_catalog_cached(Path(folder))
            self.assertEqual((artists, songs), (["A"], [("A", "S")]))


if __name__ == "__main__":
    unittest.main()