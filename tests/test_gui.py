"""Drive the real finder window with no sound, no network and a throwaway song list.

Every test builds its own window, so one test cannot leave another in a strange state.
Any exception raised inside a Tk callback is collected and fails the test.
"""

from __future__ import annotations

import gc
import json
import socket
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from tests.support import drum_midi, fake_bass_track, fake_drum_track, fake_guitar_track, fake_vocal_track

import build_section_chart as chart
import chrome_bridge
import find_drum_midi as finder
import songsterr

PATTERN = [3, 8, 8, 6, 12, 12, 4, 9, 9, 2, 14, 14, 7, 5, 11, 11, 3, 8, 8, 1, 13, 13, 6, 10]
MIDI_HITS = [2, 4] + PATTERN  # 26 bars; the tab starts two bars in


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fake_tab(title="Test Song", artist="Test Band", hits=PATTERN) -> songsterr.Tab:
    bars = [{"meter": (4, 4), "hits": h, "marker": "", "source": i + 1} for i, h in enumerate(hits)]
    bars[0]["marker"], bars[8]["marker"], bars[16]["marker"] = "Verse", "Chorus", "Outro"
    tab = songsterr.Tab(1, artist, title, "https://example.invalid/tab", "label", bars=bars, tempos=[(1, 120.0)])
    tab.sections = songsterr.sections_from(bars)
    tab.parts = [
        songsterr.Part(0, "drums", "Drums", 10),
        songsterr.Part(1, "guitar", "Lead Guitar", 5),
        songsterr.Part(2, "bass", "Bass", 3),
    ]
    return tab


class GuiCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.base = base
        self.source_dir = base / "source"
        self.source_dir.mkdir()
        self.midi_path = self.source_dir / "Test Band - Test Song.mid"
        self.midi_path.write_bytes(drum_midi(MIDI_HITS))
        (base / "settings.json").write_text(json.dumps({"sourceFolder": str(self.source_dir)}), encoding="utf-8")
        song_list = base / "song-list.json"
        song_list.write_text(json.dumps({"drumRoot": str(base / "Drum Tracks"), "songs": [], "excluded": []}), encoding="utf-8")
        self.tab = fake_tab()
        self.drum_part_error = ""
        self.part_error = ""
        self.status_log: list[str] = []
        self.dialogs: list[tuple[str, str]] = []

        port = free_port()
        patches = [
            mock.patch.object(chart, "MAP_PATH", song_list),
            mock.patch.object(finder, "SETTINGS_PATH", base / "settings.json"),
            mock.patch.object(finder, "LOCAL_DATA", base / "local"),
            mock.patch.object(finder, "SONGSTERR_CACHE", base / "local" / "songsterr"),
            mock.patch.object(finder, "PREVIEWS", base / "local" / "previews"),
            mock.patch.object(finder, "ERROR_LOG", base / "local" / "errors.log"),
            mock.patch.object(finder, "ARTWORK", base / "art"),
            mock.patch.object(finder, "INCOMING", base / "incoming"),
            mock.patch.object(finder, "EXPORTS", base / "exports"),
            mock.patch.object(finder, "ChromeBridge", lambda: chrome_bridge.ChromeBridge(port)),
            mock.patch.object(finder, "audio_command", lambda *a, **k: None),
            mock.patch.object(finder, "lookup_songsterr", lambda artist, title: (self.tab, "")),
            mock.patch.object(songsterr, "popular_catalog_cached", lambda *a, **k: ([], [])),
            mock.patch.object(songsterr, "drum_part", lambda tab: self._drum_part(tab)),
            mock.patch.object(songsterr, "fetch_part", lambda tab, index: self._fetch_part(tab, index)),
            mock.patch.object(finder.album_art, "cover_for", lambda *a, **k: None),
            mock.patch.object(finder.messagebox, "askyesno", lambda *a, **k: self._ask(*a)),
            mock.patch.object(finder.messagebox, "showinfo", lambda *a, **k: self.dialogs.append(("info", str(a)))),
            mock.patch.object(finder.messagebox, "showerror", lambda *a, **k: self.dialogs.append(("error", str(a)))),
            mock.patch.object(finder.messagebox, "showwarning", lambda *a, **k: self.dialogs.append(("warning", str(a)))),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.answer = True
        self.app = finder.Finder()
        self.errors: list[str] = []
        self.app.root.report_callback_exception = lambda t, e, tb: self.errors.append(f"{t.__name__}: {e}")
        self.app.root.withdraw()
        self.addCleanup(self.close)
        self.pump(0.3)

    def _drum_part(self, tab):
        """Songsterr's drum part, without the network. Tests can set drum_part_error to make the read fail."""
        if self.drum_part_error:
            raise OSError(self.drum_part_error)
        return {}, fake_drum_track(len(tab.bars))

    def _fetch_part(self, tab, index):
        """A guitar or bass part, without the network."""
        if self.part_error:
            raise OSError(self.part_error)
        make = {1: fake_guitar_track, 3: fake_vocal_track}.get(index, fake_bass_track)
        return make(len(tab.bars))

    def _ask(self, *args):
        self.dialogs.append(("ask", str(args)))
        return self.answer

    def close(self):
        app = self.app
        root = app.root
        for step in (lambda: app.stop(drop_audio=True), app.bridge.close):
            try:
                step()
            except (tk.TclError, OSError):
                pass
        try:
            for job in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(job)
        except tk.TclError:
            pass
        try:
            root.destroy()
        except tk.TclError:
            pass
        # unittest keeps every test case alive until the run ends. Letting go of the window here lets Tk free its
        # menus and windows, or a long run reaches Windows' limit of 10,000 user objects per process.
        self.app = None
        self.temp.cleanup()
        gc.collect()

    def pump(self, seconds=0.2, until=None):
        end = time.time() + seconds
        while time.time() < end:
            self.app.root.update()
            if until and until():
                return True
            time.sleep(0.01)
        return bool(until and until())

    def open_song(self):
        self.app.open_source(self.midi_path)
        self.assertTrue(self.pump(5, lambda: self.app.tab is not None), "the fake Songsterr tab never arrived")

    def assertNoErrors(self):
        self.pump(0.2)
        self.assertEqual(self.errors, [])


class Startup(GuiCase):
    def test_window_builds_with_nothing_open(self):
        app = self.app
        self.assertIsNone(app.view)
        self.assertEqual(str(app.chart_shift_down["state"]), "disabled")
        self.assertEqual(str(app.chart_shift_up["state"]), "disabled")
        self.assertEqual(app.chart_shift_label["text"], "Songsterr line-up")
        self.assertNoErrors()

    def test_loop_controls_sit_with_the_line_up_arrows_not_the_toolbar(self):
        app = self.app
        row = app.chart_shift_down.nametowidget(app.chart_shift_down.winfo_parent())
        self.assertIs(app.chart_shift_up.nametowidget(app.chart_shift_up.winfo_parent()), row)
        self.assertIs(app.loop_start_entry.nametowidget(app.loop_start_entry.winfo_parent()).nametowidget(
            app.loop_start_entry.nametowidget(app.loop_start_entry.winfo_parent()).winfo_parent()), row)
        # and the row is directly above the chart's grid cell
        self.assertLess(row.master.grid_info()["row"], app.chart_holder.grid_info()["row"])
        self.assertEqual(int(app.chart_holder.grid_info()["row"]) - int(row.master.grid_info()["row"]), 1)

    def test_no_song_actions_do_not_crash(self):
        app = self.app
        for call in (app.on_undo, app.on_redo, app.on_delete_bars, app.on_blank_bars, app.open_export, app.open_pedal,
                     app.on_add, app.on_stop, app.on_shift_auto, lambda: app.on_shift(1), lambda: app.on_shift(-1),
                     lambda: app.play("all"), lambda: app.play("pattern1"), app.on_use_tab, app.copy_ranges):
            call()
        self.assertNoErrors()


class Opening(GuiCase):
    def test_open_midi_loads_chart_and_ranges(self):
        self.open_song()
        app = self.app
        self.assertEqual(len(app.view["hits"]), len(MIDI_HITS))
        self.assertEqual(app.view["hits"], MIDI_HITS)
        for key, _label, _fill in finder.DIVISIONS:
            start, end = app.view["divisions"][key]
            self.assertTrue(1 <= start <= end <= len(MIDI_HITS), key)
            self.assertEqual(app.starts[key].get(), str(start))
        self.assertIn("Test Band", app.heading["text"])
        self.assertNoErrors()

    def test_bad_file_shows_an_error_and_keeps_the_open_song(self):
        self.open_song()
        bad = self.source_dir / "bad.mid"
        bad.write_bytes(b"MThd" + b"\x00" * 30)
        before = self.app.view
        self.app.open_source(bad)
        self.assertIs(self.app.view, before)
        self.assertTrue(any(kind == "error" for kind, _ in self.dialogs))
        self.assertNoErrors()

    def test_file_with_no_tracks_or_no_drums_opens_without_errors(self):
        empty = self.source_dir / "Empty - Nothing.mid"
        empty.write_bytes(b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + (480).to_bytes(2, "big"))
        self.app.open_source(empty)
        self.pump(0.3)
        silent = self.source_dir / "Silent - Song.mid"
        silent.write_bytes(drum_midi([0, 0, 0]))
        self.app.open_source(silent)
        self.pump(0.3)
        self.app.play("all")
        self.app.on_delete_bars()
        self.app.open_export()
        self.assertNoErrors()

    def test_truncated_file_gives_a_readable_error(self):
        bad = self.source_dir / "short.mid"
        bad.write_bytes(self.midi_path.read_bytes()[:40])
        self.app.open_source(bad)
        text = " ".join(message for kind, message in self.dialogs if kind == "error")
        self.assertIn("could not be read", text)
        self.assertNotIn("index out of range", text)
        self.assertNoErrors()

    def test_reopening_returns_to_the_same_state(self):
        self.open_song()
        app = self.app
        app.apply_selection(3, 5)
        other = self.source_dir / "Other - Song.mid"
        other.write_bytes(drum_midi([4, 4, 4, 4, 8, 8, 8, 8]))
        app.open_source(other)
        self.pump(0.3)
        app.open_source(self.midi_path)
        self.assertEqual(app.selection, (3, 5))
        self.assertNoErrors()


class LineUpArrows(GuiCase):
    def test_arrows_enable_when_tab_is_matched_and_shift_the_line_up(self):
        self.open_song()
        app = self.app
        self.assertEqual(str(app.chart_shift_down["state"]), "normal")
        self.assertEqual(str(app.chart_shift_up["state"]), "normal")
        self.assertEqual(app.view["tab_shift"], 2, "the tab starts two bars into the MIDI")
        self.assertEqual(app.chart_shift_label["text"], "Songsterr bar 1 = MIDI bar 3")
        app.chart_shift_up.invoke()
        self.assertEqual(app.view["tab_shift"], 3)
        self.assertEqual(app.chart_shift_label["text"], "Songsterr bar 1 = MIDI bar 4")
        app.chart_shift_down.invoke()
        app.chart_shift_down.invoke()
        self.assertEqual(app.view["tab_shift"], 1)
        self.assertEqual(app.chart_shift_label["text"], "Songsterr bar 1 = MIDI bar 2")
        # sections move with it
        first = app.view["tab_parts"][0]
        self.assertEqual(first[0], 1 + 1)
        self.assertNoErrors()

    def test_info_line_does_not_repeat_the_line_up_or_claim_it_lines_up_well(self):
        self.open_song()
        app = self.app
        for _ in range(3):
            shown = app.tab_info["text"] + " " + app.tab_tip["text"]
            self.assertNotIn("lines up well", shown)
            self.assertNotIn("line up well", shown)
            self.assertNotIn("= MIDI bar", app.tab_info["text"])
            self.assertNotIn("MIDI bar 1 = Songsterr", app.tab_info["text"])
            app.chart_shift_down.invoke()
        # the line-up is still shown once, beside the arrows
        self.assertIn("MIDI bar", app.chart_shift_label["text"])
        self.assertIn("Songsterr: 24 bars", app.tab_info["text"])

    def test_arrows_go_negative_and_say_so(self):
        self.open_song()
        app = self.app
        for _ in range(4):
            app.chart_shift_down.invoke()
        self.assertEqual(app.view["tab_shift"], -2)
        self.assertEqual(app.chart_shift_label["text"], "MIDI bar 1 = Songsterr bar 3")
        self.assertNoErrors()

    def test_line_up_moves_are_undoable_and_do_not_mark_the_song_unsaved(self):
        self.open_song()
        app = self.app
        app.chart_shift_up.invoke()
        self.assertFalse(app.dirty)
        app.on_undo()
        self.assertEqual(app.view["tab_shift"], 2)
        app.on_redo()
        self.assertEqual(app.view["tab_shift"], 3)
        self.assertNoErrors()

    def test_menu_and_arrows_share_state(self):
        self.open_song()
        app = self.app
        app.shift_up.configure(state="normal")
        app.on_shift(1)
        self.assertEqual(app.chart_shift_label["text"], app.shift_label["text"])

    def test_many_presses_never_crash(self):
        self.open_song()
        app = self.app
        for _ in range(120):
            app.chart_shift_up.invoke()
        for _ in range(240):
            app.chart_shift_down.invoke()
        self.assertEqual(app.view["tab_shift"], 2 + 120 - 240)
        self.assertEqual(app.view["tab_parts"], [])  # shifted entirely off the file
        self.assertNoErrors()

    def test_auto_line_up_restores_the_best_fit(self):
        self.open_song()
        app = self.app
        for _ in range(5):
            app.chart_shift_up.invoke()
        app.on_shift_auto()
        self.assertEqual(app.view["tab_shift"], 2)
        self.assertNoErrors()

    def test_arrows_disable_when_a_different_song_has_no_tab(self):
        self.open_song()
        app = self.app
        self.tab = None
        other = self.source_dir / "Nobody - Nothing.mid"
        other.write_bytes(drum_midi([3, 3, 3, 3, 3, 3]))
        app.open_source(other)
        self.pump(1.0, lambda: str(app.chart_shift_up["state"]) == "disabled")
        self.assertEqual(str(app.chart_shift_up["state"]), "disabled")
        self.assertEqual(str(app.chart_shift_down["state"]), "disabled")
        self.assertEqual(app.chart_shift_label["text"], "Songsterr line-up")
        self.assertNoErrors()


class LoopControls(GuiCase):
    def test_loop_checkbox_needs_a_selection(self):
        self.open_song()
        app = self.app
        app.loop_on.set(True)
        app.on_loop_toggle()
        self.assertFalse(app.loop_on.get())
        self.assertIn("Drag across", app.status["text"])

    def test_selection_fills_fields_and_loop_starts_playback(self):
        self.open_song()
        app = self.app
        app.apply_selection(4, 7)
        self.assertEqual((app.loop_start.get(), app.loop_bars.get()), ("4", "4"))
        app.loop_on.set(True)
        app.on_loop_toggle()
        self.assertTrue(self.pump(5, lambda: app.playing is not None), "playback never started")
        self.assertEqual(app.playing["loop"] is not None, True)
        app.on_stop()
        self.assertIsNone(app.playing)
        self.assertNoErrors()

    def test_typing_loop_fields(self):
        self.open_song()
        app = self.app
        app.loop_start.set("5")
        app.loop_bars.set("3")
        app.on_loop_fields()
        self.assertEqual(app.selection, (5, 7))
        for start, bars in (("0", "2"), ("x", "2"), ("5", "-1"), ("999", "5"), ("", "")):
            app.loop_start.set(start)
            app.loop_bars.set(bars)
            app.on_loop_fields()
        self.assertNoErrors()

    def test_arrow_keys_nudge_the_loop(self):
        self.open_song()
        app = self.app
        app.apply_selection(2, 3)
        app.nudge_selection(1, False)
        self.assertEqual(app.selection, (3, 4))
        app.nudge_selection(1, True)
        self.assertEqual(app.selection, (3, 5))
        app.nudge_selection(-1, True)
        app.nudge_selection(-1, False)
        self.assertEqual(app.selection, (2, 3))
        for _ in range(60):
            app.nudge_selection(1, False)
        self.assertEqual(app.selection[1], len(MIDI_HITS))
        app.on_stop()
        self.assertNoErrors()


class Editing(GuiCase):
    def test_delete_blank_insert_undo_redo_keep_everything_consistent(self):
        self.open_song()
        app = self.app
        total = len(MIDI_HITS)

        def consistent():
            view = app.view
            self.assertEqual(len(view["bounds"]), len(view["hits"]) + 1)
            self.assertEqual(len(view["bar_ms"]), len(view["hits"]) + 1)
            self.assertEqual(len(view["meters"]), len(view["hits"]))
            for key, (start, end) in view["divisions"].items():
                self.assertTrue(1 <= start <= end <= len(view["hits"]), (key, start, end, len(view["hits"])))
            for start, end, _name in view["parts"] + view["tab_parts"]:
                self.assertTrue(1 <= start <= end <= len(view["hits"]))
            self.assertEqual(sum(view["hits"]), len([e for e in view["ticks"] if view["bounds"][0] <= e[0] < view["bounds"][-1]]))

        app.apply_selection(5, 8)
        app.on_delete_bars()
        self.assertEqual(len(app.view["hits"]), total - 4)
        consistent()
        app.apply_selection(2, 3)
        app.on_blank_bars()
        self.assertEqual(app.view["hits"][1:3], [0, 0])
        consistent()
        app.on_bars_inserted(4, 2)
        self.assertEqual(len(app.view["hits"]), total - 4 + 2)
        consistent()
        for _ in range(3):
            app.on_undo()
            consistent()
        self.assertEqual(app.view["hits"], MIDI_HITS)
        for _ in range(3):
            app.on_redo()
            consistent()
        self.assertTrue(app.dirty)
        self.assertNoErrors()

    def test_cannot_delete_every_bar(self):
        self.open_song()
        app = self.app
        app.apply_selection(1, len(MIDI_HITS))
        app.on_delete_bars()
        self.assertEqual(len(app.view["hits"]), len(MIDI_HITS))
        self.assertIn("at least one bar", app.status["text"])

    def test_declined_delete_changes_nothing(self):
        self.open_song()
        app = self.app
        self.answer = False
        app.apply_selection(2, 3)
        app.on_delete_bars()
        self.assertEqual(app.view["hits"], MIDI_HITS)
        self.assertFalse(app.dirty)

    def test_editing_range_entries(self):
        self.open_song()
        app = self.app
        app.starts["pattern1"].set("5")
        app.lengths["pattern1"].set("4")
        app.on_ranges()
        self.assertEqual(app.view["divisions"]["pattern1"], [5, 8])
        app.starts["pattern1"].set("abc")
        app.on_ranges()
        self.assertEqual(app.view["divisions"]["pattern1"], [5, 8])
        self.assertNoErrors()

    def test_insert_bars_at_the_end_and_far_past_it(self):
        self.open_song()
        app = self.app
        app.on_bars_inserted(len(MIDI_HITS) + 1, 3)
        app.on_bars_inserted(10_000, 1)
        app.on_bars_inserted(1, 2)
        self.assertEqual(len(app.view["hits"]), len(MIDI_HITS) + 6)
        self.assertNoErrors()

    def test_delete_with_tab_parts_shown(self):
        self.open_song()
        app = self.app
        self.assertTrue(app.view["tab_parts"])
        app.apply_selection(1, 12)
        app.on_delete_bars()
        for start, end, _ in app.view["tab_parts"]:
            self.assertTrue(1 <= start <= end <= len(app.view["hits"]))
        self.assertNoErrors()


class Exporting(GuiCase):
    def test_export_data_modes_are_valid_midi(self):
        self.open_song()
        app = self.app
        for mode in ("song", "parts"):
            data, divisions, bars = app.export_data(mode)
            path = self.base / f"{mode}.mid"
            path.write_bytes(data)
            midi = chart.parse_midi(path)
            hits, _m, bounds = chart.build_measures(midi)
            self.assertGreaterEqual(len(hits), 1)
            self.assertLessEqual(abs(len(hits) - bars), 1)
            for key, (start, end) in divisions.items():
                self.assertTrue(1 <= start <= end <= bars, (mode, key))

    def test_export_after_edits(self):
        self.open_song()
        app = self.app
        app.apply_selection(2, 4)
        app.on_delete_bars()
        app.on_bars_inserted(2, 2)
        data, _d, _b = app.export_data("song")
        (self.base / "edited.mid").write_bytes(data)
        chart.parse_midi(self.base / "edited.mid")

    def test_use_tab_drums_button_without_network_does_not_crash(self):
        self.open_song()
        with mock.patch.object(songsterr, "drum_part", side_effect=OSError("offline")):
            self.app.on_use_tab_drums()
            self.pump(0.5)
        self.assertNoErrors()

    def test_unexpected_error_reading_tab_drums_does_not_leave_the_app_busy(self):
        # Any failure in the background read must release the buttons again.
        for error in (RuntimeError("boom"), AttributeError("x"), IndexError("y"), ZeroDivisionError("z")):
            self.open_song()
            app = self.app
            with mock.patch.object(songsterr, "drum_part", side_effect=error):
                app.on_use_tab_drums()
                self.pump(1.0, lambda: not app.busy)
            self.assertFalse(app.busy, f"{type(error).__name__} left the finder busy")
            self.assertNoErrors()

    def test_match_tempo_changes_tempo_map_and_can_be_undone(self):
        self.open_song()
        app = self.app
        before = list(app.view["tempos"])
        app.on_match_tempo()
        self.pump(0.2)
        app.on_undo()
        self.assertEqual(app.view["tempos"], before)
        self.assertNoErrors()


class Transport(GuiCase):
    def test_play_pause_resume_stop(self):
        self.open_song()
        app = self.app
        app.play("all")
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        app.on_pause_button()
        self.pump(0.1)
        app.on_pause_button()
        self.pump(0.1)
        app.on_stop()
        self.assertIsNone(app.playing)
        self.assertNoErrors()

    def test_play_each_section(self):
        self.open_song()
        app = self.app
        for key, _label, _fill in finder.DIVISIONS:
            app.play(key)
            self.pump(1.5, lambda: app.playing is not None)
            app.on_stop()
        self.assertNoErrors()

    def test_space_toggles_and_zoom_and_text_scale(self):
        self.open_song()
        app = self.app
        app.on_space(mock.Mock())
        self.pump(2, lambda: app.playing is not None)
        app.on_space(mock.Mock())
        for direction in (1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1, -1, -1):
            app.on_zoom(direction)
        for step in (10, 10, -10, 0, -10, -10, 10):
            app.step_text_scale(step)
            self.pump(0.1)
        app.on_stop()
        self.assertNoErrors()


class ChartHeight(GuiCase):
    def chart_height_at(self, height):
        self.app.root.geometry(f"1700x{height}+0+0")
        self.pump(0.8)
        return self.app.chart_holder.winfo_height()

    def test_chart_stops_growing_at_the_maximum_and_shrinks_when_the_window_is_short(self):
        self.open_song()
        self.app.root.deiconify()
        tall = self.chart_height_at(1400)
        self.assertEqual(tall, finder.CHART_MAX)
        self.assertGreaterEqual(self.chart_height_at(1100), finder.CHART_MIN)
        middle = self.chart_height_at(700)
        self.assertLess(middle, finder.CHART_MAX)
        self.assertGreaterEqual(middle, finder.CHART_MIN)
        self.assertLessEqual(self.chart_height_at(620), middle)
        self.assertEqual(self.chart_height_at(540), finder.CHART_MIN)
        # and it grows back
        self.assertEqual(self.chart_height_at(1400), finder.CHART_MAX)
        self.assertNoErrors()

    def test_the_chart_fills_its_holder_and_the_ranges_sit_right_under_it(self):
        self.open_song()
        self.app.root.deiconify()
        self.chart_height_at(1400)
        holder = self.app.chart_holder
        self.assertEqual(self.app.chart.winfo_height(), holder.winfo_height())
        self.assertEqual(self.app.chart.winfo_width(), holder.winfo_width())
        self.assertEqual(int(holder.grid_info()["row"]) + 1, int(self.app.roll.grid_info()["row"]))
        self.assertEqual(int(holder.grid_info()["row"]) + 3, int(self.app.range_entries[0].master.master.grid_info()["row"]))
        self.assertNoErrors()


class DrumRollView(GuiCase):
    def tab_times(self):
        return self.app.roll.sources["tab"][0]

    def show_songsterr(self):
        self.app.roll.tab_radio.invoke()
        self.assertEqual(self.app.roll_source.get(), "tab")

    def open_with_tab_drums(self):
        self.open_song()
        app = self.app
        self.show_songsterr()
        self.assertTrue(self.pump(5, lambda: bool(self.tab_times())), "Songsterr's drums never reached the roll")
        return app

    def texts(self):
        canvas = self.app.roll.canvas
        return [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]

    def test_roll_sits_between_the_chart_and_the_ranges(self):
        app = self.app
        rows = [int(widget.grid_info()["row"]) for widget in (app.chart_holder, app.roll)]
        self.assertEqual(rows[1], rows[0] + 1)
        self.assertEqual(int(app.roll.master.grid_slaves(row=rows[1] + 1)[0].grid_info()["row"]), rows[1] + 1)
        self.assertEqual(app.roll.sources["midi"], ([], []))
        self.assertNoErrors()

    def test_midi_and_songsterr_drums_reach_the_roll(self):
        app = self.open_with_tab_drums()
        times, events = app.roll.sources["midi"]
        self.assertEqual(len(events), len(app.view["events"]))
        self.assertEqual(times, sorted(times))
        self.assertEqual(app.roll.tab_name, "Kit (Drums)")
        # the fake tab has 24 bars of 4 quarter beats; each beat is a kick or snare, a hat, and a crash on the bar's first beat
        notes = [event[1] for event in app.roll.sources["tab"][1]]
        self.assertEqual(len(notes), 24 * (4 * 2 + 1))
        self.assertEqual({finder.drum_roll.row_for(n) for n in notes}, {0, 3, 4, 8})
        self.assertNoErrors()

    def test_only_one_roll_is_shown_at_a_time(self):
        app = self.open_with_tab_drums()
        app.root.deiconify()
        app.root.geometry("1500x1200+0+0")
        self.pump(0.6)
        roll = app.roll
        self.assertEqual(int(roll.canvas["height"]), roll.full_height())
        self.assertEqual(roll.full_height(), finder.drum_roll.HEADER_H + len(finder.drum_roll.ROWS) * finder.drum_roll.ROW_H + 4)
        titles = [text for text in self.texts() if text.startswith(("MIDI", "Songsterr"))]
        self.assertEqual(len(titles), 1)
        self.assertTrue(titles[0].startswith("Songsterr"))
        self.assertEqual(self.texts().count("Kick"), 1)
        roll.midi_radio.invoke()
        self.pump(0.3)
        titles = [text for text in self.texts() if text.startswith(("MIDI", "Songsterr"))]
        self.assertEqual(titles, ["MIDI"])
        self.assertEqual(self.texts().count("Kick"), 1)
        self.assertNoErrors()

    def test_songsterr_drums_follow_the_line_up_arrows(self):
        app = self.open_with_tab_drums()
        bar_ms = app.view["bar_ms"]
        shift = app.view["tab_shift"]
        self.assertEqual(self.tab_times()[0], bar_ms[shift])
        app.on_shift(1)
        self.assertEqual(self.tab_times()[0], bar_ms[shift + 1])
        app.on_shift(-1)
        app.on_shift(-1)
        self.assertEqual(self.tab_times()[0], bar_ms[shift - 1])
        app.on_undo()
        app.on_undo()
        app.on_undo()
        self.assertEqual(self.tab_times()[0], bar_ms[shift])
        self.assertNoErrors()

    def test_roll_stopped_sits_at_the_start_or_the_selected_bar(self):
        app = self.open_with_tab_drums()
        bar_ms = app.view["bar_ms"]
        self.assertEqual(app.roll.position, bar_ms[0])
        app.apply_selection(5, 6)
        self.assertEqual(app.roll.position, bar_ms[4])
        app.clear_selection()
        self.assertEqual(app.roll.position, bar_ms[0])

    def test_roll_follows_the_playhead_while_playing_and_when_paused(self):
        app = self.open_with_tab_drums()
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        first = app.roll.position
        self.assertGreaterEqual(first, app.view["bar_ms"][2])
        self.pump(0.6)
        self.assertGreater(app.roll.position, first + 200)
        app.pause_playback()
        held = app.roll.position
        self.pump(0.4)
        self.assertEqual(app.roll.position, held)
        self.assertAlmostEqual(held, app.playhead_ms(), delta=60)
        app.chart_play.invoke()
        self.pump(0.4)
        self.assertGreater(app.roll.position, held + 150)
        app.on_stop()
        self.assertNoErrors()

    def test_roll_moves_at_the_chosen_speed(self):
        app = self.open_with_tab_drums()
        app.bpm_text.set("60")
        app.on_bpm_entered()
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        start = app.roll.position
        self.pump(1.0)
        moved = app.roll.position - start
        self.assertTrue(250 < moved < 800, moved)  # about half of the second that went by
        app.on_stop()
        self.assertNoErrors()

    def test_roll_updates_many_times_a_second_not_in_steps_of_the_old_timer(self):
        app = self.open_with_tab_drums()
        shown = []
        original = app.roll.show
        app.roll.show = lambda position: (shown.append((time.perf_counter(), position)), original(position))[1]
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        shown.clear()
        self.pump(1.0)
        app.roll.show = original  # stop recording before the stop itself moves the roll back
        app.on_stop()
        self.assertGreater(len(shown), 40, "the roll should be moved far more often than the 25 times a second of the old timer")
        positions = [position for _when, position in shown]
        self.assertEqual(positions, sorted(positions))
        gaps = [b - a for (a, _), (b, _) in zip(shown, shown[1:])]
        self.assertLess(sorted(gaps)[int(len(gaps) * 0.9)], 0.04)
        self.assertNoErrors()

    def test_playing_slides_the_drawn_hits_instead_of_drawing_them_again(self):
        app = self.open_with_tab_drums()
        app.root.deiconify()
        app.root.geometry("1500x1200+0+0")
        self.pump(0.6)
        roll = app.roll
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        self.pump(0.2)
        draws = roll.redraws
        canvas = roll.canvas
        # Measure from the song time the roll has already reached, not from wall
        # clock. A busy machine makes pump(1.2) cover more than 1200 ms of playhead.
        started = roll.position
        before = canvas.bbox("scroll")[0]
        self.pump(1.2)
        # A margin refresh is fine once the playhead nears the edge of what was
        # pre-drawn; the point is that every frame does not redraw the hits.
        self.assertLessEqual(roll.redraws, draws + 1, "the hits were drawn again while they only needed to slide")
        moved_ms = roll.position - started
        self.assertGreater(moved_ms, 800)
        if roll.redraws == draws:
            plot = canvas.winfo_width() - finder.drum_roll.GUTTER - 8
            slid = before - canvas.bbox("scroll")[0]
            self.assertAlmostEqual(slid, plot * moved_ms / roll.window_ms, delta=plot * 0.08)
        app.on_stop()
        self.assertNoErrors()

    def test_a_long_run_draws_again_only_now_and_then(self):
        app = self.open_with_tab_drums()
        app.root.deiconify()
        app.root.geometry("1500x1200+0+0")
        self.pump(0.6)
        roll = app.roll
        roll.zoom(-1)
        roll.zoom(-1)  # a 3 second window, so the edge of what was drawn comes quickly
        draws = roll.redraws
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        self.pump(4.0)
        self.assertGreaterEqual(roll.redraws, draws + 1)
        self.assertLessEqual(roll.redraws, draws + 6)
        # every hit that should be on screen now is drawn: the red line has hits close to it
        app.on_stop()
        self.assertNoErrors()

    def test_roll_draws_shapes_for_hits_in_the_window(self):
        app = self.open_with_tab_drums()
        app.root.deiconify()
        app.root.geometry("1700x1200+0+0")
        self.pump(0.6)
        app.apply_selection(5, 5)
        self.pump(0.3)
        canvas = app.roll.canvas
        self.assertGreater(canvas.winfo_width(), 800)
        self.assertGreater(len(canvas.find_all()), 60)
        reds = [item for item in canvas.find_all() if canvas.type(item) == "line" and canvas.itemcget(item, "fill") == finder.drum_roll.NOW_LINE]
        self.assertEqual(len(reds), 1)
        app.roll.zoom(1)
        app.roll.zoom(-1)
        app.roll.zoom(-1)
        self.assertNoErrors()

    def test_window_zoom_has_limits(self):
        app = self.app
        roll = app.roll
        steps = finder.drum_roll.ZOOM_STEPS_MS
        for _ in range(len(steps) + 3):
            roll.zoom(-1)
        self.assertEqual(roll.window_ms, steps[0])
        self.assertEqual(str(roll.zoom_out_button["state"]), "disabled")
        for _ in range(len(steps) + 3):
            roll.zoom(1)
        self.assertEqual(roll.window_ms, steps[-1])
        self.assertEqual(str(roll.zoom_in_button["state"]), "disabled")
        self.assertEqual(roll.zoom_label["text"], "18 s")
        self.assertNoErrors()

    def test_hiding_the_roll(self):
        app = self.open_with_tab_drums()
        roll = app.roll
        roll.roll_check.invoke()
        self.assertFalse(app.show_roll.get())
        self.assertEqual(roll.canvas.grid_info(), {})
        self.assertEqual(str(roll.midi_radio["state"]), "disabled")
        self.assertEqual(str(roll.tab_radio["state"]), "disabled")
        redraws = roll.redraws
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        self.pump(0.3)
        self.assertEqual(roll.redraws, redraws)
        app.on_stop()
        roll.roll_check.invoke()
        self.assertTrue(app.show_roll.get())
        self.assertEqual(int(roll.canvas["height"]), roll.full_height())
        self.assertNoErrors()

    def test_a_failed_read_says_so_and_a_second_try_works(self):
        self.drum_part_error = "no connection"
        self.open_song()
        app = self.app
        self.show_songsterr()
        self.assertTrue(self.pump(5, lambda: "could not be read" in app.roll.tab_note))
        self.assertIn("no connection", app.roll.tab_note)
        self.assertEqual(self.tab_times(), [])
        self.drum_part_error = ""
        app.roll.midi_radio.invoke()
        app.roll.tab_radio.invoke()
        self.assertTrue(self.pump(5, lambda: bool(self.tab_times())))
        self.assertEqual(app.roll.tab_note, "")
        self.assertNoErrors()

    def test_no_tab_means_a_note_and_no_fetching(self):
        calls = []
        self.tab = fake_tab()
        self.tab.parts = []
        with mock.patch.object(songsterr, "drum_part", lambda tab: calls.append(tab) or ({}, fake_drum_track(3))):
            self.open_song()
            self.show_songsterr()
            self.pump(0.5)
        self.assertEqual(calls, [])
        self.assertIn("no drum part", self.app.roll.tab_note)
        self.assertNoErrors()

    def test_songsterr_is_not_read_until_it_is_chosen(self):
        calls = []
        with mock.patch.object(songsterr, "drum_part", lambda tab: calls.append(tab) or ({}, fake_drum_track(3))):
            self.open_song()
            self.pump(0.5)
            self.assertEqual(calls, [])
            self.assertEqual(self.app.roll_source.get(), "midi")
            self.show_songsterr()
            self.assertTrue(self.pump(5, lambda: bool(calls)))
        self.assertNoErrors()

    def test_nothing_is_read_when_the_roll_is_hidden(self):
        calls = []
        self.app.roll.tab_radio.invoke()
        self.app.roll.roll_check.invoke()
        with mock.patch.object(songsterr, "drum_part", lambda tab: calls.append(tab) or ({}, fake_drum_track(3))):
            self.open_song()
            self.pump(0.5)
        self.assertEqual(calls, [])
        self.assertNoErrors()

    def test_switching_songs_resets_the_roll(self):
        app = self.open_with_tab_drums()
        other = self.source_dir / "Two - Song.mid"
        other.write_bytes(drum_midi([4, 6, 8, 4]))
        app.open_source(other)
        self.pump(0.6)
        self.assertEqual(len(app.roll.sources["midi"][1]), len(app.view["events"]))
        self.assertEqual(app.roll.bar_ms, app.view["bar_ms"])
        self.assertNoErrors()

    def test_edits_and_undo_update_the_roll(self):
        app = self.open_with_tab_drums()
        before = len(app.roll.sources["midi"][1])
        app.apply_selection(3, 4)
        app.on_delete_bars()
        self.assertLess(len(app.roll.sources["midi"][1]), before)
        self.assertEqual(app.roll.bar_ms, app.view["bar_ms"])
        app.on_undo()
        self.assertEqual(len(app.roll.sources["midi"][1]), before)
        self.assertNoErrors()

    def test_using_the_tab_drums_replaces_the_midi_rows(self):
        app = self.open_with_tab_drums()
        app.on_use_tab_drums()
        self.assertTrue(self.pump(5, lambda: not app.busy))
        self.pump(0.3)
        self.assertEqual(len(app.roll.sources["midi"][1]), 24 * 9)
        self.assertEqual(app.roll.sources["midi"][0], self.tab_times())
        self.assertNoErrors()

    def test_session_switch_back_keeps_the_roll_working(self):
        app = self.open_with_tab_drums()
        app.stop()
        other = self.source_dir / "Two - Song.mid"
        other.write_bytes(drum_midi([4, 6, 8, 4]))
        app.open_source(other)
        self.pump(0.5)
        app.open_source(self.midi_path)
        self.pump(1.0)
        self.assertEqual(len(app.roll.sources["midi"][1]), len(app.view["events"]))
        self.assertNoErrors()


class PlayButtonAndTempo(GuiCase):
    def commands(self):
        """Replace the audio calls with a recorder."""
        sent: list[str] = []
        patch = mock.patch.object(finder, "audio_command", lambda command, optional=False: sent.append(command))
        patch.start()
        self.addCleanup(patch.stop)
        return sent

    def type_bpm(self, text):
        self.app.bpm_text.set(text)
        self.app.on_bpm_entered()

    def test_play_button_sits_between_the_arrows(self):
        app = self.app
        row = app.chart_shift_down.nametowidget(app.chart_shift_down.winfo_parent())
        order = [widget for widget, _space in row.items]
        self.assertEqual(order.index(app.chart_play), order.index(app.chart_shift_down) + 1)
        self.assertEqual(order.index(app.chart_shift_up), order.index(app.chart_play) + 1)
        self.assertEqual(str(app.chart_play["state"]), "disabled")
        self.assertEqual(app.bpm_text.get(), "")
        for widget in (app.bpm_entry, app.bpm_down, app.bpm_up, app.bpm_reset):
            self.assertEqual(str(widget["state"]), "disabled")
        app.toggle_play()
        self.assertNoErrors()

    def test_play_button_plays_pauses_and_resumes(self):
        self.open_song()
        app = self.app
        self.assertEqual(str(app.chart_play["state"]), "normal")
        self.assertEqual(app.chart_play["text"], "Play")
        app.chart_play.invoke()
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        self.assertEqual(app.chart_play["text"], "Pause")
        app.chart_play.invoke()
        self.assertTrue(app.playing["paused"])
        self.assertEqual(app.chart_play["text"], "Play")
        app.chart_play.invoke()
        self.assertFalse(app.playing["paused"])
        self.assertEqual(app.chart_play["text"], "Pause")
        app.on_stop()
        self.assertEqual(app.chart_play["text"], "Play")
        self.assertNoErrors()

    def test_space_and_the_button_share_one_state(self):
        self.open_song()
        app = self.app
        app.on_space(mock.Mock())
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        app.chart_play.invoke()
        self.assertTrue(app.playing["paused"])
        app.on_space(mock.Mock())
        self.assertFalse(app.playing["paused"])
        app.on_stop()
        self.assertNoErrors()

    def test_tempo_box_shows_the_songs_tempo(self):
        self.open_song()
        app = self.app
        self.assertEqual(app.base_bpm(), 120)
        self.assertEqual(app.bpm_text.get(), "120")
        self.assertEqual(app.bpm_note["text"], "")
        self.assertEqual(str(app.bpm_reset["state"]), "disabled")
        for widget in (app.bpm_entry, app.bpm_down, app.bpm_up):
            self.assertEqual(str(widget["state"]), "normal")

    def test_typing_a_tempo_sets_the_speed(self):
        self.open_song()
        app = self.app
        self.type_bpm("90")
        self.assertAlmostEqual(app.speed(), 0.75)
        self.assertEqual(app.bpm_text.get(), "90")
        self.assertEqual(app.bpm_note["text"], "75%")
        self.assertEqual(str(app.bpm_reset["state"]), "normal")
        self.type_bpm(" 150 bpm ")
        self.assertAlmostEqual(app.speed(), 1.25)
        app.reset_bpm()
        self.assertEqual(app.speed(), 1.0)
        self.assertEqual(app.bpm_text.get(), "120")
        self.assertNoErrors()

    def test_tempo_is_limited_and_nonsense_is_ignored(self):
        self.open_song()
        app = self.app
        self.type_bpm("5")
        self.assertEqual(app.speed(), finder.MIN_SPEED)
        self.assertEqual(app.bpm_text.get(), "30")
        self.type_bpm("9999")
        self.assertEqual(app.speed(), finder.MAX_SPEED)
        self.assertEqual(app.bpm_text.get(), "240")
        for bad in ("", "fast", "-20", "0", "nan", "inf", "1e999"):
            self.type_bpm(bad)
            self.assertEqual(app.speed(), finder.MAX_SPEED, bad)
            self.assertEqual(app.bpm_text.get(), "240", bad)
        self.assertNoErrors()

    def test_plus_and_minus_step_one_bpm(self):
        self.open_song()
        app = self.app
        app.bpm_up.invoke()
        self.assertEqual(app.bpm_text.get(), "121")
        app.bpm_down.invoke()
        app.bpm_down.invoke()
        self.assertEqual(app.bpm_text.get(), "119")
        for _ in range(3):
            app.bpm_down.invoke()
        self.assertEqual(app.bpm_text.get(), "116")
        self.assertNoErrors()

    def test_the_playhead_moves_at_the_chosen_speed(self):
        self.open_song()
        app = self.app
        app.playing = {"anchor_ms": 1000.0, "origin": time.perf_counter() - 2.0, "paused": False, "loop": None,
                       "label": "x", "file_start": 0, "speed": 0.5}
        self.assertAlmostEqual(app.playhead_ms(), 2000, delta=100)
        app.playing["speed"] = 2.0
        self.assertAlmostEqual(app.playhead_ms(), 5000, delta=100)
        app.playing["paused"] = True
        self.assertEqual(app.playhead_ms(), 1000.0)
        app.playing = None

    def test_playing_starts_in_the_stretched_audio(self):
        sent = self.commands()
        self.open_song()
        app = self.app
        self.type_bpm("60")
        start = app.view["bar_ms"][2] - app.view["bar_ms"][0]
        app.start_playback(3, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        self.assertEqual(app.playing["speed"], 0.5)
        self.assertIn(f"seek drum to {round(start / 0.5)}", sent)
        app.on_stop()
        self.assertNoErrors()

    def test_changing_the_tempo_while_playing_carries_on_from_the_same_place(self):
        sent = self.commands()
        self.open_song()
        app = self.app
        app.start_playback(5, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        before = app.playhead_ms()
        sent.clear()
        self.type_bpm("60")
        self.assertTrue(self.pump(10, lambda: app.playing is not None and app.playing["speed"] == 0.5))
        self.assertGreaterEqual(app.playhead_ms(), before - 1)
        self.assertLess(app.playhead_ms(), before + 3000)
        self.assertFalse(app.playing["paused"])
        seeks = [command for command in sent if command.startswith("seek drum to")]
        self.assertEqual(len(seeks), 1)
        file_start = app.view["bar_ms"][0]
        self.assertGreaterEqual(int(seeks[0].split()[-1]), (before - file_start) / 0.5 - 1)
        app.on_stop()
        self.assertNoErrors()

    def test_changing_the_tempo_while_paused_stays_paused(self):
        self.open_song()
        app = self.app
        app.start_playback(5, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        app.pause_playback()
        held = app.playhead_ms()
        self.type_bpm("100")
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        self.assertTrue(app.playing["paused"])
        self.assertEqual(app.chart_play["text"], "Play")
        self.assertAlmostEqual(app.playhead_ms(), held, delta=1)
        app.chart_play.invoke()
        self.assertFalse(app.playing["paused"])
        app.on_stop()
        self.assertNoErrors()

    def test_changing_the_tempo_while_preparing_still_starts(self):
        self.open_song()
        app = self.app
        app.start_playback(1, "test", None)
        self.assertTrue(app.preparing)
        self.type_bpm("80")
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        self.assertAlmostEqual(app.playing["speed"], 80 / 120)
        app.on_stop()
        self.assertNoErrors()

    def test_a_loop_keeps_looping_after_a_tempo_change(self):
        self.open_song()
        app = self.app
        app.apply_selection(3, 4)
        app.loop_on.set(True)
        app.play("all")
        self.assertTrue(self.pump(5, lambda: app.playing is not None))
        self.type_bpm("140")
        self.assertTrue(self.pump(10, lambda: app.playing is not None and app.playing["speed"] > 1.1))
        self.assertIsNotNone(app.playing["loop"])
        self.pump(1.5)
        bar_ms = app.view["bar_ms"]
        self.assertTrue(bar_ms[2] <= app.playhead_ms() <= bar_ms[4] + 50)
        app.on_stop()
        self.assertNoErrors()

    def test_the_tempo_stays_through_edits_and_undo_and_is_per_song(self):
        self.open_song()
        app = self.app
        self.type_bpm("100")
        app.apply_selection(3, 3)
        app.on_delete_bars()
        self.assertAlmostEqual(app.speed(), 100 / 120)
        app.on_undo()
        self.assertAlmostEqual(app.speed(), 100 / 120)
        self.assertEqual(app.bpm_text.get(), "100")
        other = self.source_dir / "Two - Song.mid"
        other.write_bytes(drum_midi([4, 6, 8, 4, 6, 8, 4, 4], bpm=90))
        app.open_source(other)
        self.pump(0.5)
        self.assertEqual(app.speed(), 1.0)
        self.assertEqual(app.bpm_text.get(), "90")
        self.assertNoErrors()

    def test_an_edit_after_a_tempo_change_renders_the_new_bars(self):
        self.open_song()
        app = self.app
        self.type_bpm("60")
        app.apply_selection(3, 3)
        app.on_delete_bars()
        app.play("all")
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        self.assertEqual(app.playing["speed"], 0.5)
        app.on_stop()
        self.assertNoErrors()

    def test_following_chrome_needs_the_original_tempo(self):
        self.open_song()
        app = self.app
        def post():
            now = int(time.time() * 1000)
            app.bridge.receive({"songId": self.tab.song_id, "title": "Test Song", "playing": True, "measure": 1,
                                "markAt": now, "markFraction": 0.0, "speed": 100, "sentAt": now})
            self.pump(0.15)

        post()
        self.assertTrue(app.chrome_live())
        self.type_bpm("100")
        # Chrome keeps playing, but the preview must not start by itself at the changed tempo.
        for _ in range(6):
            post()
        self.assertFalse(app.chrome_live())
        self.assertIn("not following", app.chrome_text)
        self.assertIsNone(app.playing)
        # Play now plays here instead of asking Chrome
        app.toggle_play()
        self.assertTrue(self.pump(10, lambda: app.playing is not None))
        self.assertFalse(app.playing.get("follow"))
        app.on_stop()
        app.reset_bpm()
        post()
        self.assertTrue(app.chrome_live())
        self.assertNoErrors()

    def test_pedal_readout_and_stopping_with_a_tempo_change(self):
        self.open_song()
        app = self.app
        self.type_bpm("60")
        self.assertEqual(app.bar_bpm(0), 60)
        app.open_pedal()
        app.pedal_press("rhythm_tap")
        self.pump(0.5)
        app.pedal_press("rhythm_tap")
        self.pump(0.8)
        self.type_bpm("100")
        self.pump(0.3)
        app.pedal_stop()
        self.assertIsNone(app.playing)
        self.assertNoErrors()

    def test_audio_failure_during_a_tempo_change_is_reported_not_raised(self):
        self.open_song()
        app = self.app
        app.start_playback(2, "test", None)
        self.assertTrue(self.pump(5, lambda: app.playing is not None))

        def broken(command, optional=False):
            if not optional and command.startswith(("seek", "open")):
                raise RuntimeError("Playback failed (257)")

        with mock.patch.object(finder, "audio_command", broken):
            self.type_bpm("100")
            self.pump(3)
        self.assertIsNone(app.playing)
        self.assertEqual(self.errors, [])
        app.on_stop()


class Pedal(GuiCase):
    def test_pedal_window_opens_runs_and_closes(self):
        self.open_song()
        app = self.app
        app.open_pedal()
        self.assertIsNotNone(app.pedal_window)
        for name in ("rhythm_tap", "rhythm_hold", "rhythm_double", "loop_tap", "loop_hold", "loop_double"):
            app.pedal_press(name)
            self.pump(0.2)
        app.pedal_stop()
        app.pedal_clear()
        app.pedal_window.close()
        self.pump(0.2)
        self.assertNoErrors()

    def test_switching_songs_with_pedal_open(self):
        self.open_song()
        app = self.app
        app.open_pedal()
        other = self.source_dir / "Two - Song.mid"
        other.write_bytes(drum_midi([4, 6, 8, 4, 6, 8, 4, 4]))
        app.open_source(other)
        self.pump(0.5)
        app.edit_ranges = None
        self.assertNoErrors()


class Chrome(GuiCase):
    def test_chrome_follow_with_no_extension_reports_waiting(self):
        self.open_song()
        self.pump(0.5)
        self.assertNotIn("Traceback", self.app.chrome_label["text"])
        self.assertNoErrors()

    def test_posted_state_is_followed_without_errors(self):
        self.open_song()
        app = self.app
        now = int(time.time() * 1000)
        for measure in (0, 1, 2, 3):
            app.bridge.receive({
                "songId": self.tab.song_id, "title": "Test Song", "playing": True, "measure": measure,
                "markAt": now + measure * 2000, "markFraction": 0.1, "speed": 100, "sentAt": now,
            })
            self.pump(0.15)
        app.bridge.receive({"playing": False, "measure": None})
        self.pump(0.2)
        app.bridge.receive({"garbage": object.__class__.__name__})
        self.pump(0.2)
        self.assertNoErrors()

    def state(self, measure, **extra):
        base = {"songId": self.tab.song_id, "title": "Test Song", "playing": True, "measure": measure,
                "markAt": time.time() * 1000, "markFraction": 0.0, "speed": 100}
        base.update(extra)
        return base

    def test_chrome_target_maps_tab_bars_onto_midi_bars_through_the_line_up(self):
        self.open_song()
        app = self.app
        bar_ms = app.view["bar_ms"]
        shift = app.view["tab_shift"]
        self.assertEqual(shift, 2)
        target, tab_bar, midi_bar = app.chrome_target(self.state(0, markFraction=0.25))
        self.assertEqual((tab_bar, midi_bar), (1, 1 + shift))
        length = bar_ms[midi_bar] - bar_ms[midi_bar - 1]
        self.assertAlmostEqual(target, bar_ms[midi_bar - 1] + 0.25 * length, delta=60)
        # moving the line-up one bar later moves the target one bar later
        app.on_shift(1)
        _t, _tb, moved = app.chrome_target(self.state(0))
        self.assertEqual(moved, midi_bar + 1)

    def test_chrome_target_elapsed_time_is_capped_and_paused_means_no_elapsed(self):
        self.open_song()
        app = self.app
        late = self.state(3, markAt=time.time() * 1000 - 60_000)
        _t, _tb, midi_bar = app.chrome_target(late)
        length = app.view["bar_ms"][midi_bar] - app.view["bar_ms"][midi_bar - 1]
        target = app.chrome_target(late)[0]
        self.assertLessEqual(target, app.view["bar_ms"][midi_bar - 1] + length * 1.5 + 1)
        paused = app.chrome_target(self.state(3, playing=False, markAt=time.time() * 1000 - 60_000))[0]
        self.assertAlmostEqual(paused, app.view["bar_ms"][midi_bar - 1], delta=1)

    def test_chrome_target_outside_the_song_is_none(self):
        self.open_song()
        app = self.app
        self.assertIsNone(app.chrome_target(self.state(500)))
        for _ in range(60):
            app.on_shift(1)
        self.assertIsNone(app.chrome_target(self.state(0)))
        self.assertNoErrors()

    def test_chrome_state_reasons(self):
        self.open_song()
        app = self.app
        self.assertIn("not connected", app.chrome_state()[1])
        app.bridge.receive(self.state(1, songId=999999))
        self.assertIn("Chrome: showing", app.chrome_state()[1])
        app.bridge.receive(self.state(None))
        self.assertIn("cursor not visible", app.chrome_state()[1])
        app.bridge.receive(self.state("junk"))
        self.assertIn("cursor not visible", app.chrome_state()[1])
        app.bridge.receive(self.state(2))
        state, why = app.chrome_state()
        self.assertEqual((state["measure"], why), (2, ""))

    def test_follow_does_not_start_audio_for_a_slowed_tab(self):
        self.open_song()
        app = self.app
        for _ in range(10):
            app.bridge.receive(self.state(2, speed=50))
            if self.pump(0.3, lambda: "50%" in app.chrome_label["text"]):
                break
        self.assertIsNone(app.playing)
        self.assertIn("50%", app.chrome_label["text"])

    def test_frozen_chrome_setup_copies_the_helper_to_a_stable_folder(self):
        from tests.support import ROOT as repo

        with mock.patch.object(finder, "FROZEN", True), mock.patch.object(finder, "BUNDLE", repo), \
                mock.patch.object(finder.subprocess, "Popen") as popen:
            self.app.chrome_setup()
            self.app.chrome_setup()  # twice: the copy must overwrite, not fail
        copied = self.base / "local" / "chrome-extension"
        for name in ("manifest.json", "content.js"):
            self.assertTrue((copied / name).is_file(), name)
        popen.assert_called()
        shown = " ".join(message.replace("\\\\", "\\") for kind, message in self.dialogs if kind == "ask")
        self.assertIn(str(copied), shown)

    def test_chrome_setup_dialog_does_not_crash(self):
        with mock.patch.object(finder.subprocess, "Popen") as popen:
            self.app.chrome_setup()
        self.assertTrue(any(kind == "ask" for kind, _ in self.dialogs))
        popen.assert_called()


class Quit(GuiCase):
    def test_quit_with_unsaved_edits_asks_and_can_be_declined(self):
        self.open_song()
        app = self.app
        app.apply_selection(2, 3)
        app.on_blank_bars()
        self.answer = False
        app.on_quit()
        self.assertTrue(app.root.winfo_exists())
        self.answer = True
        app.on_quit()
        with self.assertRaises(tk.TclError):
            app.root.winfo_exists()
            app.root.update()


if __name__ == "__main__":
    unittest.main()
