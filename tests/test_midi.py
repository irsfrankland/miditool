"""MIDI reading, bar building, export and tempo maps."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support import BAR, KICK, PPQ, drum_midi, hit, meter_event, midi_file, name_event, track_chunk, vlq

import build_section_chart as chart
import midi_export


def parse_bytes(data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "t.mid"
        path.write_bytes(data)
        return chart.parse_midi(path)


class VariableLength(unittest.TestCase):
    def test_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 16383, 16384, 2_097_151, 0x0FFFFFFF):
            data = midi_export._vlq(value)
            self.assertEqual(chart.read_vlq(data, 0), (value, len(data)), value)

    def test_truncated(self):
        with self.assertRaises(ValueError):
            chart.read_vlq(b"\x81", 0)

    def test_negative_is_clamped(self):
        self.assertEqual(midi_export._vlq(-5), b"\x00")


class Parsing(unittest.TestCase):
    def test_counts_hits_per_bar(self):
        midi = parse_bytes(drum_midi([4, 8, 0, 2]))
        hits, meters, bounds = chart.build_measures(midi)
        self.assertEqual(hits, [4, 8, 0, 2])
        self.assertEqual(meters, [(1, 4, 4)])
        self.assertEqual(bounds, [0, BAR, 2 * BAR, 3 * BAR, 4 * BAR])

    def test_running_status(self):
        # note-on, then two more note-ons using running status
        track = bytearray()
        track += vlq(0) + b"\xff\x03\x05Drums"
        track += vlq(0) + bytes([0x99, 36, 100])
        track += vlq(10) + bytes([38, 100])
        track += vlq(10) + bytes([42, 100])
        track += vlq(0) + b"\xff\x2f\x00"
        data = midi_file([b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)])
        midi = parse_bytes(data)
        self.assertEqual([e[1] for e in midi["events"]], [36, 38, 42])

    def test_note_on_velocity_zero_is_not_a_hit(self):
        events = [name_event("Drums"), (0, bytes([0x99, 36, 0])), (5, bytes([0x99, 38, 90]))]
        midi = parse_bytes(midi_file([track_chunk(events)]))
        self.assertEqual([e[1] for e in midi["events"]], [38])

    def test_not_a_midi_file(self):
        with self.assertRaises(ValueError):
            parse_bytes(b"RIFF" + b"\x00" * 40)

    def test_smpte_timing_rejected(self):
        data = b"MThd" + (6).to_bytes(4, "big") + b"\x00\x01\x00\x01" + b"\xe7\x28" + track_chunk([name_event("Drums")])
        with self.assertRaises(ValueError):
            parse_bytes(data)

    def test_zero_ticks_per_beat_rejected(self):
        data = b"MThd" + (6).to_bytes(4, "big") + b"\x00\x01\x00\x01" + b"\x00\x00" + track_chunk([name_event("Drums")])
        with self.assertRaises(ValueError):
            parse_bytes(data)

    def test_missing_track_chunk(self):
        data = midi_file([track_chunk([name_event("Drums")])]) [:-10] + b"XXXX" * 4
        # the header promises one track; corrupt chunk id
        data = data.replace(b"MTrk", b"MTrX", 1)
        with self.assertRaises(ValueError):
            parse_bytes(data)

    def test_truncated_track_does_not_hang_or_crash_oddly(self):
        good = drum_midi([4, 4])
        for cut in (14, 20, 25, 30, len(good) - 3):
            try:
                parse_bytes(good[:cut])
            except (ValueError, IndexError):
                pass  # an error is fine; a hang or another exception type is not

    def test_truncated_file_raises_value_error(self):
        # A damaged download should give the user a readable ValueError, not an IndexError.
        good = drum_midi([4, 4])
        raised = set()
        for cut in range(14, len(good)):
            try:
                parse_bytes(good[:cut])
            except Exception as error:  # noqa: BLE001
                raised.add(type(error).__name__)
        self.assertLessEqual(raised, {"ValueError"}, f"unfriendly errors from truncated files: {raised}")

    def test_percussion_track_is_not_the_kit(self):
        kit = track_chunk([name_event("Drums")] + hit(0, KICK))
        perc = track_chunk([name_event("Percussion")] + hit(0, 60))
        midi = parse_bytes(midi_file([kit, perc]))
        self.assertEqual([e[1] for e in midi["events"]], [KICK])

    def test_channel_10_used_when_no_kit_named(self):
        track = track_chunk([name_event("Song")] + hit(0, KICK, channel=9) + hit(10, 60, channel=0))
        midi = parse_bytes(midi_file([track]))
        self.assertEqual([e[1] for e in midi["events"]], [KICK])

    def test_kit_name_matching(self):
        self.assertTrue(chart.is_kit_track("Drums"))
        self.assertTrue(chart.is_kit_track("  DRUM   kit "))
        self.assertFalse(chart.is_kit_track("Percussion drums"))
        self.assertFalse(chart.is_kit_track("Steel Drums"))
        self.assertFalse(chart.is_kit_track("Guitar"))

    def test_empty_file_has_no_bars(self):
        midi = parse_bytes(midi_file([track_chunk([name_event("Drums")])]))
        hits, _meters, bounds = chart.build_measures(midi)
        self.assertEqual(hits, [])
        self.assertEqual(bounds, [0, 0])


class Meters(unittest.TestCase):
    def test_meter_change_midway(self):
        events = [name_event("Drums"), meter_event(0, 4, 4), meter_event(2 * BAR, 3, 4)]
        for bar in range(4):
            events += hit(bar * BAR if bar < 2 else 2 * BAR + (bar - 2) * 3 * PPQ)
        midi = parse_bytes(midi_file([track_chunk(events)]))
        hits, meters, bounds = chart.build_measures(midi)
        self.assertEqual(meters, [(1, 4, 4), (3, 3, 4)])
        self.assertEqual(bounds[:5], [0, BAR, 2 * BAR, 2 * BAR + 3 * PPQ, 2 * BAR + 6 * PPQ])
        self.assertEqual(hits, [1, 1, 1, 1])

    def test_meter_text(self):
        self.assertEqual(chart.meter_text([(1, 4, 4), (9, 3, 4), (11, 3, 4)]), "4/4, 3/4 at bar 9")
        self.assertEqual(chart.meter_text([]), "4/4")

    def test_six_eight(self):
        events = [name_event("Drums"), meter_event(0, 6, 8)] + hit(0) + hit(PPQ * 3)
        midi = parse_bytes(midi_file([track_chunk(events)]))
        hits, _meters, bounds = chart.build_measures(midi)
        self.assertEqual(bounds[1], PPQ * 3)
        self.assertEqual(hits[:2], [1, 1])

    def test_meter_exactly_on_bar_does_not_split_bar(self):
        events = [name_event("Drums"), meter_event(BAR, 3, 4)] + hit(0) + hit(BAR) + hit(BAR + 3 * PPQ)
        midi = parse_bytes(midi_file([track_chunk(events)]))
        _hits, _meters, bounds = chart.build_measures(midi)
        self.assertEqual(bounds[:3], [0, BAR, BAR + 3 * PPQ])


class Tempo(unittest.TestCase):
    def test_constant_tempo(self):
        convert = chart.tick_to_ms([(0, 500_000)], PPQ)
        self.assertEqual(convert(0), 0)
        self.assertEqual(convert(PPQ), 500)
        self.assertEqual(convert(BAR), 2000)

    def test_default_tempo_when_missing(self):
        convert = chart.tick_to_ms([], PPQ)
        self.assertEqual(convert(PPQ), 500)

    def test_tempo_starting_after_zero(self):
        convert = chart.tick_to_ms([(PPQ, 250_000)], PPQ)
        # before the first tempo the first tempo already applies
        self.assertEqual(convert(PPQ), 250)

    def test_tempo_change(self):
        convert = chart.tick_to_ms([(0, 500_000), (BAR, 250_000)], PPQ)
        self.assertEqual(convert(BAR), 2000)
        self.assertEqual(convert(BAR + PPQ), 2250)

    def test_text(self):
        self.assertEqual(chart.tempo_text([]), "No tempo in the file")
        self.assertEqual(chart.tempo_text([(0, 500_000)]), "120 BPM")
        self.assertIn("2 tempo changes", chart.tempo_text([(0, 500_000), (10, 250_000)]))

    def test_clean_tempos(self):
        self.assertEqual(midi_export.clean_tempos([]), [(0, 500_000)])
        self.assertEqual(midi_export.clean_tempos([(480, 400_000)]), [(0, 400_000), (480, 400_000)][:1])
        self.assertEqual(midi_export.clean_tempos([(0, 500_000), (10, 500_000), (20, 250_000)]), [(0, 500_000), (20, 250_000)])
        self.assertEqual(midi_export.clean_tempos([(5, 300_000), (5, 400_000), (0, 0)]), [(0, 400_000)])

    def test_tempo_at(self):
        tempos = [(0, 500_000), (100, 250_000)]
        self.assertEqual(midi_export.tempo_at(tempos, 99), 500_000)
        self.assertEqual(midi_export.tempo_at(tempos, 100), 250_000)
        self.assertEqual(midi_export.tempo_at([], 5), 500_000)


class Export(unittest.TestCase):
    def roundtrip(self, bar_hits, bpm=120):
        midi = parse_bytes(drum_midi(bar_hits, bpm))
        hits, meters, bounds = chart.build_measures(midi)
        data = midi_export.build(
            midi["ppq"], midi["events"], midi["tempos"], bounds, [(n, d) for _b, n, d in meters] * len(hits), name="Test"
        )
        return midi, parse_bytes(data), hits

    def test_roundtrip_keeps_hits_and_ppq(self):
        original, again, hits = self.roundtrip([4, 8, 2, 6])
        self.assertEqual(again["ppq"], original["ppq"])
        self.assertEqual([e[1] for e in again["events"]], [e[1] for e in original["events"]])
        self.assertEqual(chart.build_measures(again)[0], hits)

    def test_export_is_type_0_single_track(self):
        _o, _a, _h = self.roundtrip([2, 2])
        data = midi_export.build(PPQ, [[0, 36, 100]], [(0, 500_000)], [0, BAR], [(4, 4)])
        self.assertEqual(data[8:10], (0).to_bytes(2, "big"))
        self.assertEqual(data[10:12], (1).to_bytes(2, "big"))

    def test_note_clamped(self):
        data = midi_export.build(PPQ, [[0, 300, 500], [10, -5, 0]], [(0, 500_000)], [0, BAR], [(4, 4)])
        events = parse_bytes(data)["events"]
        self.assertTrue(all(0 <= e[1] <= 127 and 1 <= e[2] <= 127 for e in events))

    def test_meter_for(self):
        self.assertEqual(midi_export.meter_for(BAR, PPQ, (4, 4)), (4, 4))
        self.assertEqual(midi_export.meter_for(3 * PPQ, PPQ, (4, 4)), (3, 4))
        self.assertEqual(midi_export.meter_for(PPQ * 3 // 2, PPQ, (4, 4)), (3, 8))

    def test_title_non_latin_does_not_crash(self):
        midi_export.build(PPQ, [], [(0, 500_000)], [0, BAR], [(4, 4)], name="日本語 — song")

    def test_no_boundaries(self):
        data = midi_export.build(PPQ, [], [], [], [])
        self.assertEqual(data[:4], b"MThd")

    def test_pedal_parts_back_to_back(self):
        events = [[b * BAR, KICK, 100] for b in range(8)]
        bounds = [b * BAR for b in range(9)]
        meters = [(4, 4)] * 8
        divisions = {
            "intro": [1, 1], "pattern1": [2, 3], "pattern1Fill": [3, 3],
            "pattern2": [5, 6], "pattern2Fill": [6, 6], "ending": [8, 8],
        }
        out_events, tempos, out_bounds, out_meters, placed = midi_export.pedal_parts(
            events, [(0, 500_000)], bounds, meters, divisions
        )
        self.assertEqual(placed["intro"], [1, 1])
        self.assertEqual(placed["pattern1"], [2, 3])
        self.assertEqual(placed["pattern1Fill"], [3, 3])  # inside pattern 1: not copied twice
        self.assertEqual(placed["pattern2"], [4, 5])
        self.assertEqual(placed["pattern2Fill"], [5, 5])
        self.assertEqual(placed["ending"], [6, 6])
        self.assertEqual(len(out_bounds) - 1, 6)
        self.assertEqual(len(out_meters), 6)
        self.assertEqual([e[0] for e in out_events], [i * BAR for i in range(6)])

    def test_pedal_parts_out_of_range_bar(self):
        divisions = {"intro": [1, 1], "pattern1": [2, 3], "pattern1Fill": [3, 3],
                     "pattern2": [5, 99], "pattern2Fill": [6, 6], "ending": [8, 8]}
        with self.assertRaises((IndexError, ValueError)):
            midi_export.pedal_parts([], [(0, 500_000)], [b * BAR for b in range(9)], [(4, 4)] * 8, divisions)


class Retime(unittest.TestCase):
    def test_tab_bar_lengths(self):
        bars = [{"meter": (4, 4)}, {"meter": (3, 4)}, {"meter": (4, 4)}]
        lengths = midi_export.tab_bar_lengths(bars, [(1, 120.0), (3, 60.0)])
        self.assertEqual([round(x) for x in lengths], [2000, 1500, 4000])

    def test_retime_matches_each_bar(self):
        bounds = [0, BAR, 2 * BAR, 3 * BAR]
        tempos, matched = midi_export.retime_to_tab(PPQ, bounds, [(0, 500_000)], [2000.0, 4000.0], 0, False)
        self.assertEqual(matched, 2)
        convert = chart.tick_to_ms(tempos, PPQ)
        self.assertEqual(convert(BAR), 2000)
        self.assertEqual(convert(2 * BAR), 6000)
        # unmatched third bar keeps the original tempo
        self.assertEqual(convert(3 * BAR) - convert(2 * BAR), 2000)

    def test_retime_with_shift(self):
        bounds = [0, BAR, 2 * BAR, 3 * BAR]
        tempos, matched = midi_export.retime_to_tab(PPQ, bounds, [(0, 500_000)], [1000.0, 3000.0], 1, False)
        # MIDI bar 2 = tab bar 1, MIDI bar 3 = tab bar 2
        self.assertEqual(matched, 2)
        convert = chart.tick_to_ms(tempos, PPQ)
        self.assertEqual(convert(2 * BAR) - convert(BAR), 1000)
        self.assertEqual(convert(3 * BAR) - convert(2 * BAR), 3000)

    def test_retime_half_time(self):
        bounds = [0, BAR, 2 * BAR]
        tempos, matched = midi_export.retime_to_tab(PPQ, bounds, [(0, 500_000)], [1000.0, 1000.0, 1500.0, 1500.0], 0, True)
        self.assertEqual(matched, 2)
        convert = chart.tick_to_ms(tempos, PPQ)
        self.assertEqual(convert(BAR), 2000)
        self.assertEqual(convert(2 * BAR) - convert(BAR), 3000)


class SongListPaint(unittest.TestCase):
    def test_paint_roles_and_spans(self):
        divisions = {"intro": [1, 2], "pattern1": [3, 6], "pattern1Fill": [6, 6], "pattern2": [7, 8],
                     "pattern2Fill": [8, 8], "ending": [9, 10]}
        roles = chart.paint_roles(10, divisions)
        self.assertEqual(roles[0], "intro")
        self.assertEqual(roles[9], "ending")
        spans = chart.role_spans(roles)
        self.assertEqual(spans[0], ("intro", 1, 2))
        self.assertEqual(sum(end - start + 1 for _r, start, end in spans), 10)

    def test_paint_roles_ignores_out_of_range(self):
        divisions = {k: [50, 60] for k in ("intro", "pattern1", "pattern1Fill", "pattern2", "pattern2Fill", "ending")}
        self.assertEqual(set(chart.paint_roles(5, divisions)), {"unstored"})


if __name__ == "__main__":
    unittest.main()
