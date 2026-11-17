"""The drum preview audio and the bar chart, at their edges."""

from __future__ import annotations

import io
import time
import tkinter as tk
import unittest
import wave

from tests import support  # noqa: F401

import preview_ui

DIVISIONS = {
    "intro": [1, 1], "pattern1": [1, 1], "pattern1Fill": [1, 1],
    "pattern2": [1, 1], "pattern2Fill": [1, 1], "ending": [1, 1],
}


def spread(count):
    keys = ("intro", "pattern1", "pattern1Fill", "pattern2", "pattern2Fill", "ending")
    return {key: [min(count, 1 + index), min(count, 1 + index)] for index, key in enumerate(keys)}


class Render(unittest.TestCase):
    def wav(self, events, start, end):
        data = preview_ui.render_section(events, start, end)
        with wave.open(io.BytesIO(data)) as handle:
            return handle.getnframes(), handle.getframerate(), handle.getnchannels(), handle.getsampwidth()

    def test_valid_wav_for_normal_input(self):
        frames, rate, channels, width = self.wav([[0, 36, 100], [500, 38, 100], [1000, 42, 60]], 0, 2000)
        self.assertEqual((channels, width), (1, 2))
        self.assertAlmostEqual(frames / rate, 2.05, delta=0.1)

    def test_empty_and_degenerate_ranges(self):
        for events, start, end in (([], 0, 1000), ([[5, 36, 100]], 0, 0), ([[5, 36, 100]], 100, 50), ([[5, 36, 100]], 5, 5)):
            frames, _rate, _c, _w = self.wav(events, start, end)
            self.assertGreater(frames, 0)

    def test_every_gm_drum_and_odd_velocity_renders(self):
        events = [[index * 20, note, velocity] for index, (note, velocity) in enumerate((n, v) for n in range(0, 128) for v in (0, 1, 127, 200))]
        self.wav(events, 0, events[-1][0] + 500)

    def test_speed_stretches_the_audio_without_changing_the_hits(self):
        events = [[0, 36, 100], [500, 38, 100], [1000, 42, 60]]
        normal = self.wav(events, 0, 2000)[0]
        slow = self.wav_speed(events, 2000, 0.5)
        fast = self.wav_speed(events, 2000, 2.0)
        self.assertAlmostEqual(slow / 16000, 4.05, delta=0.1)
        self.assertAlmostEqual(fast / 16000, 1.05, delta=0.1)
        self.assertGreater(slow, normal)
        self.assertEqual(preview_ui.render_section(events, 0, 2000, 1.0), preview_ui.render_section(events, 0, 2000))

    def test_silly_speeds_do_not_crash(self):
        for speed in (0, -1, 0.0001, 1e9):
            preview_ui.render_section([[0, 36, 100]], 0, 200, speed)

    def wav_speed(self, events, end, speed):
        with wave.open(io.BytesIO(preview_ui.render_section(events, 0, end, speed))) as handle:
            return handle.getnframes()

    def test_events_outside_the_window_are_ignored(self):
        inside = preview_ui.render_section([[500, 38, 100]], 0, 1000)
        outside = preview_ui.render_section([[5000, 38, 100]], 0, 1000)
        self.assertNotEqual(inside, outside)
        self.assertEqual(outside, preview_ui.render_section([], 0, 1000))

    def test_a_long_song_renders_in_a_reasonable_time(self):
        events = [[ms, 38 if (ms // 250) % 2 else 36, 100] for ms in range(0, 300_000, 250)]
        started = time.time()
        self.wav(events, 0, 300_000)
        self.assertLess(time.time() - started, 30, "rendering five minutes of drums took too long")

    def test_bar_lanes(self):
        bounds = [0, 100, 200]
        events = [(10, 36, 100), (20, 38, 100), (150, 42, 100), (160, 49, 100), (999, 36, 100)]
        lanes, crashes = preview_ui.bar_lanes(events, bounds)
        self.assertEqual(len(lanes), 2)
        self.assertIsInstance(crashes, set)
        self.assertEqual(preview_ui.bar_lanes([], [0]), ([], set()))


class Chart(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.chart = preview_ui.SectionChart(self.root)
        self.chart.pack(fill="both", expand=True)
        self.root.update()
        self.addCleanup(self.root.destroy)

    def draw(self, count, **extra):
        hits = [(i * 7) % 13 for i in range(count)]
        meters = [(4, 4)] * count
        times = [i * 2000 for i in range(count + 1)]
        self.chart.draw(hits, spread(max(count, 1)), extra.get("parts", []), [tuple(index % 3 for index in range(len(preview_ui.LANES)))] * count, set(), meters, times)
        self.root.update()

    def test_draw_various_sizes(self):
        for count in (0, 1, 2, 3, 17, 300, 3000):
            self.draw(count)

    def test_zoom_levels_and_scale(self):
        self.draw(40)
        for scale in (0.8, 1.0, 1.5):
            self.chart.set_scale(scale)
            self.draw(40)
        self.root.update()

    def test_selection_and_reveal(self):
        self.draw(60)
        self.chart.show_selection(5, 9)
        self.chart.show_selection(None, None)
        self.chart.show_selection(59, 60)
        self.chart.reveal(1)
        self.chart.reveal(60)
        self.chart.reveal(10_000)
        self.root.update()

    def test_parts_outside_the_song_are_skipped(self):
        self.draw(10, parts=[(0, 3, "Bad"), (5, 2, "Backwards"), (8, 50, "Past the end"), (2, 4, "Fine")])

    def test_clicks_on_empty_chart_do_nothing(self):
        self.draw(0)
        for sequence in ("<ButtonPress-1>", "<ButtonRelease-1>", "<ButtonPress-1>", "<ButtonRelease-1>", "<ButtonPress-3>", "<ButtonRelease-3>", "<Motion>", "<MouseWheel>"):
            self.chart.canvas.event_generate(sequence, x=40, y=40)
        self.root.update()


if __name__ == "__main__":
    unittest.main()
