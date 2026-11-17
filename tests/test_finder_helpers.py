"""Pure helpers in the finder: file names, song list writing, tempo and bar editing."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import drum_midi

import find_drum_midi as finder

DIVISIONS = {
    "intro": [1, 1], "pattern1": [2, 3], "pattern1Fill": [3, 3],
    "pattern2": [4, 4], "pattern2Fill": [4, 4], "ending": [4, 4],
}


class Names(unittest.TestCase):
    def test_clean_name(self):
        cases = {
            "Back_In_Black": "Back In Black",
            "Song [midifind.com]": "Song",
            "Song (2)": "Song",
            "Song (RC-10R v2)": "Song",
            "Song Drums": "Song",
            "Beatles (The) - Help": "The Beatles - Help",
            "  - Weird -  ": "Weird",
        }
        for raw, expected in cases.items():
            self.assertEqual(finder.clean_name(raw), expected, raw)

    def test_guess_artist_title(self):
        self.assertEqual(finder.guess_artist_title(Path("AC-DC - Back In Black.mid")), ("AC-DC", "Back In Black"))
        self.assertEqual(finder.guess_artist_title(Path("Artists/Metallica/One.mid")), ("Metallica", "One"))
        self.assertEqual(finder.guess_artist_title(Path("lonely.mid")), ("", "lonely"))
        self.assertEqual(finder.guess_artist_title(Path("A — B.mid")), ("A", "B"))

    def test_song_id(self):
        self.assertEqual(finder.song_id("Ain't Talkin' 'Bout Love"), "aint-talkin-bout-love")
        self.assertEqual(finder.song_id("!!!"), "song")
        self.assertTrue(finder.song_id("日本語"))

    def test_safe_name_and_artist_dir(self):
        self.assertEqual(finder.safe_name('A:B*C?', "x"), "A_B_C_")
        self.assertEqual(finder.safe_name("...", "fallback"), "fallback")
        self.assertEqual(finder.safe_name("name. .", "x"), "name")
        self.assertEqual(finder.safe_name("a\x00b\x1f.mid", "x"), "a_b_.mid")
        for device in ("CON", "nul.mid", "Com1.mid", "LPT9", "aux.txt.mid"):
            self.assertTrue(finder.safe_name(device, "x").startswith("_"), device)
        self.assertEqual(finder.safe_name("console.mid", "x"), "console.mid")
        self.assertEqual(finder.safe_name("../../evil.mid", "x"), ".._.._evil.mid")
        with self.assertRaises(ValueError):
            finder.artist_dir(Path("."), "???"[:0])

    def test_artist_dir_case_insensitive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Metallica").mkdir()
            self.assertEqual(finder.artist_dir(root, "metallica"), root / "Metallica")
            self.assertEqual(finder.artist_dir(root, "New Band"), root / "New Band")

    def test_artist_key(self):
        self.assertEqual(finder.artist_key("Mötley Crüe"), finder.artist_key("motley crue"))
        self.assertEqual(finder.artist_key("AC/DC"), finder.artist_key("ac dc"))
        self.assertEqual(finder.artist_key("日本"), "日本")

    def test_rank(self):
        good = finder.rank("Back In Black drums", "AC DC", "Back In Black")
        bad = finder.rank("Something else entirely", "AC DC", "Back In Black")
        self.assertGreater(good, bad)

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "a.mid"
            self.assertEqual(finder.unique_path(base), base)
            base.write_bytes(b"1")
            self.assertEqual(finder.unique_path(base).name, "a (2).mid")
            (Path(folder) / "a (2).mid").write_bytes(b"1")
            self.assertEqual(finder.unique_path(base).name, "a (3).mid")

    def test_song_length_and_brief_meters(self):
        self.assertEqual(finder.song_length(0), "0:00")
        self.assertEqual(finder.song_length(61_400), "1:01")
        self.assertEqual(finder.song_length(-5), "0:00")
        self.assertEqual(finder.brief_meters("4/4, 3/4 at bar 2"), "4/4, 3/4 at bar 2")
        self.assertIn("2 more meter changes", finder.brief_meters("a, b, c, d, e"))

    def test_short_path(self):
        self.assertEqual(finder.short_path(Path("C:/a")), str(Path("C:/a")))
        self.assertTrue(finder.short_path(Path("C:/a/b/c/d.mid")).startswith("…"))


class Lengths(unittest.TestCase):
    def test_parse_length(self):
        self.assertEqual(finder.parse_length(" 3 ", "4", "x"), (3, 4))
        for bad in (("0", "1"), ("1", "0"), ("a", "1"), ("", "1"), ("1.5", "2"), ("-1", "2")):
            with self.assertRaises(ValueError, msg=bad):
                finder.parse_length(*bad, "x")

    def test_divisions_and_typing_lines(self):
        raw = {key: (str(start), str(end - start + 1)) for key, (start, end) in DIVISIONS.items()}
        self.assertEqual(finder.divisions_from(raw), DIVISIONS)
        self.assertEqual(finder.typing_lines(DIVISIONS)[1], "Pattern 1 2 × 2")


class TempoAndBars(unittest.TestCase):
    def test_cut_tempos(self):
        tempos = [(0, 500_000), (1000, 400_000), (3000, 300_000)]
        self.assertEqual(finder.cut_tempos(tempos, 500, 1500), [(0, 500_000), (500, 400_000), (2000, 300_000)])
        self.assertEqual(finder.cut_tempos([(0, 500_000)], 100, 200), [(0, 500_000)])

    def test_insert_tempos(self):
        tempos = [(0, 500_000), (1000, 400_000)]
        self.assertEqual(finder.insert_tempos(tempos, 500, 100), [(0, 500_000), (1100, 400_000)])

    def test_bar_meters(self):
        self.assertEqual(finder.bar_meters([(1, 4, 4), (3, 3, 4)], 4), [(4, 4), (4, 4), (3, 4), (3, 4)])
        self.assertEqual(finder.bar_meters([], 2), [(4, 4)] * 2)

    def test_hits_per_bar(self):
        bounds = [0, 100, 200, 300]
        events = [(0, 36, 1), (99, 36, 1), (100, 36, 1), (299, 36, 1), (300, 36, 1), (999, 36, 1)]
        self.assertEqual(finder.hits_per_bar(events, bounds), [2, 1, 1])
        self.assertEqual(finder.hits_per_bar([], [0]), [])
        self.assertEqual(finder.hits_per_bar([(5, 1, 1)], []), [])

    def test_shift_span(self):
        cases = {
            (1, 3, 5, 6): (1, 3),
            (8, 10, 5, 6): (6, 8),
            (5, 6, 5, 6): None,
            (3, 8, 5, 6): (3, 6),
            (3, 5, 5, 6): (3, 4),
            (6, 9, 5, 6): (5, 7),
        }
        for args, expected in cases.items():
            self.assertEqual(finder.shift_span(*args), expected, args)

    def test_place_span_always_valid(self):
        for start in range(1, 12):
            for end in range(start, 12):
                for cut_start in range(1, 12):
                    for cut_end in range(cut_start, 12):
                        new_length = 11 - (cut_end - cut_start + 1)
                        left, right = finder.place_span(start, end, cut_start, cut_end, new_length)
                        if new_length >= 1:
                            self.assertTrue(1 <= left <= right <= new_length, (start, end, cut_start, cut_end, left, right))

    def test_shift_parts_for_cut_and_insert(self):
        parts = [(1, 4, "A"), (5, 8, "B"), (9, 12, "C")]
        self.assertEqual(finder.shift_parts_for_cut(parts, 5, 8, 8), [(1, 4, "A"), (5, 8, "C")])
        self.assertEqual(finder.shift_parts_for_insert(parts, 5, 2, 14), [(1, 4, "A"), (7, 10, "B"), (11, 14, "C")])
        self.assertEqual(finder.shift_parts_for_insert(parts, 6, 2, 14)[1], (5, 10, "B"))
        for left, right, _name in finder.shift_parts_for_insert(parts, 1, 50, 14):
            self.assertTrue(1 <= left <= right <= 14)


class SongList(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.catalog_path = self.root / "song-list.json"
        self.drums = self.root / "Drum Tracks"
        self.catalog_path.write_text(json.dumps({"drumRoot": str(self.drums), "songs": [], "excluded": []}), encoding="utf-8")
        self.source = self.root / "src.mid"
        self.source.write_bytes(drum_midi([4, 8, 8, 2]))

    def tearDown(self):
        self.folder.cleanup()

    def add(self, artist="Band", title="Song", **kw):
        return finder.add_to_list(self.source, artist, title, DIVISIONS, catalog_path=self.catalog_path, drum_root=self.drums, **kw)

    def test_add_then_replace(self):
        report, slug = self.add()
        self.assertEqual(slug, "song")
        self.assertIn("Added Band", report)
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["songs"]), 1)
        self.assertTrue((self.drums / catalog["songs"][0]["file"]).is_file())
        report, slug = self.add()
        self.assertIn("Replaced", report)
        self.assertEqual(len(json.loads(self.catalog_path.read_text(encoding="utf-8"))["songs"]), 1)

    def test_same_title_other_artist_gets_its_own_id(self):
        _r, first = self.add("One", "Intro")
        _r, second = self.add("Two", "Intro")
        self.assertNotEqual(first, second)

    def test_requires_artist_and_title(self):
        for artist, title in (("", "x"), ("x", " "), ("  ", "  ")):
            with self.assertRaises(ValueError):
                self.add(artist, title)

    def test_not_a_midi_leaves_list_untouched(self):
        self.source.write_bytes(b"junk junk junk junk")
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(json.loads(self.catalog_path.read_text(encoding="utf-8"))["songs"], [])
        self.assertFalse(self.drums.exists() and any(self.drums.rglob("*.mid")))

    def test_file_already_in_drum_folder_is_not_copied(self):
        inside = self.drums / "Band" / "Song.mid"
        inside.parent.mkdir(parents=True)
        inside.write_bytes(self.source.read_bytes())
        finder.add_to_list(inside, "Band", "Song", DIVISIONS, catalog_path=self.catalog_path, drum_root=self.drums)
        self.assertEqual(len(list(self.drums.rglob("*.mid"))), 1)

    def test_notes_flag_problems(self):
        notes = finder.file_notes(self.source)
        self.assertEqual(notes, [])
        silent = self.root / "silent.mid"
        silent.write_bytes(drum_midi([0, 0]))
        self.assertTrue(any("no drum-kit notes" in note for note in finder.file_notes(silent)))

    def test_format_catalog_inlines_ranges_and_keeps_crlf(self):
        text = finder.format_catalog({"songs": [{"divisions": {"intro": [1, 2]}}]}, "\r\n")
        self.assertIn("[1, 2]", text)
        self.assertTrue(text.endswith("\r\n"))
        self.assertNotIn("\n\n", text.replace("\r\n", ""))

    def test_write_atomic_replaces(self):
        target = self.root / "x.json"
        finder.write_atomic(target, b"one")
        finder.write_atomic(target, b"two")
        self.assertEqual(target.read_bytes(), b"two")
        self.assertFalse((self.root / "x.json.tmp").exists())

    def test_locked_originals_are_not_replaced(self):
        # an original song already in the list keeps its fixed ranges
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["songs"].append({"id": "thunderstruck", "title": "Thunderstruck", "artist": "AC/DC", "file": "a.mid", "divisions": DIVISIONS, "notes": []})
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaises(ValueError):
            finder.add_to_list(self.source, "AC/DC", "Thunderstruck", DIVISIONS, catalog_path=self.catalog_path, drum_root=self.drums)

    def test_list_id_exhaustion(self):
        songs = [{"id": "s", "title": "S", "artist": "a0"}] + [{"id": f"s-b-{n}", "title": "S", "artist": f"a{n}"} for n in range(2, 100)]
        songs.append({"id": "s-b", "title": "S", "artist": "other"})
        with self.assertRaises(ValueError):
            finder.list_id({"songs": songs}, "b", "S")


class Settings(unittest.TestCase):
    def test_load_settings_tolerates_garbage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.json"
            old = finder.SETTINGS_PATH
            finder.SETTINGS_PATH = path
            try:
                self.assertEqual(finder.load_settings(), {})
                path.write_text("{not json", encoding="utf-8")
                self.assertEqual(finder.load_settings(), {})
                path.write_text("[1,2]", encoding="utf-8")
                self.assertEqual(finder.load_settings(), {})
                finder.save_settings({"a": 1})
                self.assertEqual(finder.load_settings(), {"a": 1})
            finally:
                finder.SETTINGS_PATH = old

    def test_start_geometry(self):
        geometry = finder.start_geometry("")
        self.assertRegex(geometry, r"^\d+x\d+\+-?\d+\+-?\d+$")
        self.assertEqual(finder.start_geometry("99999x99999+0+0"), geometry)
        self.assertEqual(finder.start_geometry("garbage"), geometry)


if __name__ == "__main__":
    unittest.main()
