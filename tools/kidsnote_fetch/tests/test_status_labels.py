"""Every life-record code kidsnote's own UI knows must render as Korean, never as a raw code.

tests/data/kidsnote_status_labels_ko.json is kidsnote's wording, copied from
the i18n store of its web report page. A raw `watery` leaked into published
pages before this test existed.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    import notion_mirror as nm
except ImportError:  # pragma: no cover - requests not installed
    nm = None

OFFICIAL = json.loads((HERE / "data" / "kidsnote_status_labels_ko.json").read_text(encoding="utf-8"))
STATUS_FIELDS = ("meal_status", "bowel_status", "temperature_status", "mood_status", "health_status",
                 "outdoor_activity_status", "bath_status", "nail_status")
# Codes added on 2026-09-14: their wording must be kidsnote's, word for word.
ADDED_STATUS = {"watery", "diarrhea", "bw_no", "bw_one", "bw_two", "ml_free", "ml_one", "ml_two",
                "slight", "bath", "shower", "cut", "ok", "true", "false"}
ADDED_SLEEP = {"sleep_hardly", "sleep_late", "sleep_well", "slp_good", "slp_normal", "slp_bad"}
ADDED_WEATHER = {"hail", "shower", "shower_rain"}
RAW_CODE = re.compile(r"[a-z]+_[a-z_0-9]+|\b(?:watery|diarrhea|slight|shower|none|true|false|undefined)\b")


@unittest.skipIf(nm is None, "notion_mirror dependencies (requests) not installed")
class OfficialLabelCoverageTest(unittest.TestCase):
    def test_fixture_is_complete(self):
        for field in STATUS_FIELDS + ("sleep_hour", "weather", "activity_rate"):
            with self.subTest(field=field):
                self.assertTrue(OFFICIAL.get(field), f"fixture has no codes for {field}")

    def test_every_status_code_has_a_korean_label(self):
        for field in STATUS_FIELDS:
            for code in OFFICIAL[field]:
                with self.subTest(field=field, code=code):
                    self.assertIn(code, nm.STATUS_KO)

    def test_every_sleep_code_has_a_korean_label(self):
        for code in OFFICIAL["sleep_hour"]:
            with self.subTest(code=code):
                self.assertIn(code, nm.SLEEP_HOUR_KO)

    def test_every_weather_code_is_labelled_or_hidden(self):
        for code in OFFICIAL["weather"]:
            with self.subTest(code=code):
                self.assertTrue(code in nm.WEATHER_KO or code in nm.WEATHER_HIDDEN)
        self.assertEqual({c for c, label in OFFICIAL["weather"].items() if label == "표시안함"},
                         set(nm.WEATHER_HIDDEN))

    def test_added_codes_use_kidsnote_wording(self):
        official_status = {}
        for field in STATUS_FIELDS:
            official_status.update(OFFICIAL[field])
        for code in ADDED_STATUS:
            with self.subTest(code=code):
                self.assertEqual(nm.STATUS_KO[code], official_status[code])
        for code in ADDED_SLEEP:
            with self.subTest(code=code):
                self.assertEqual(nm.SLEEP_HOUR_KO[code], OFFICIAL["sleep_hour"][code])
        for code in ADDED_WEATHER:
            with self.subTest(code=code):
                self.assertEqual(nm.WEATHER_KO[code].split(" ", 1)[-1], OFFICIAL["weather"][code])
        self.assertEqual(nm.ACTIVITY_RATE_KO, OFFICIAL["activity_rate"])

    def test_shared_status_table_has_no_conflicting_new_words(self):
        # STATUS_KO is shared by all *_status fields; a new code must mean the same thing everywhere.
        for code in ADDED_STATUS:
            labels = {OFFICIAL[f][code] for f in STATUS_FIELDS if code in OFFICIAL[f]}
            with self.subTest(code=code):
                self.assertEqual(len(labels), 1, labels)


@unittest.skipIf(nm is None, "notion_mirror dependencies (requests) not installed")
class LifeRecordChipsTest(unittest.TestCase):
    def bits(self, **report):
        return nm.NotionMirror._life_record_bits(report)

    def assertNoRawCodes(self, bits):
        for chip in bits:
            with self.subTest(chip=chip):
                self.assertIsNone(RAW_CODE.search(chip), chip)

    def test_codes_that_leaked_before_are_korean_now(self):
        bits = self.bits(meal_status="fixed", sleep_hour="sleep_hardly", bowel_status="watery",
                         temperature_status="normal")
        self.assertIn("💩 배변 묽음", bits)
        self.assertIn("💤 수면 잠을 설쳤어요", bits)
        self.assertNoRawCodes(bits)
        self.assertIn("💩 배변 설사", self.bits(bowel_status="diarrhea"))

    def test_every_official_code_renders_without_raw_code(self):
        for field in STATUS_FIELDS:
            for code in OFFICIAL[field]:
                value = {"true": True, "false": False}.get(code, code) if field == "outdoor_activity_status" else code
                with self.subTest(field=field, code=code):
                    bits = self.bits(**{field: value})
                    self.assertEqual(len(bits), 1, bits)
                    self.assertNoRawCodes(bits)
        for code in OFFICIAL["sleep_hour"]:
            with self.subTest(sleep_hour=code):
                self.assertNoRawCodes(self.bits(sleep_hour=code))

    def test_outdoor_activity_booleans_and_strings(self):
        self.assertIn("🏃 야외활동 O", self.bits(outdoor_activity_status=True))
        self.assertIn("🏃 야외활동 X", self.bits(outdoor_activity_status=False))
        self.assertIn("🏃 야외활동 O", self.bits(outdoor_activity_status="true"))
        self.assertEqual(self.bits(outdoor_activity_status=None), [])

    def test_activity_rate_numbers_and_strings(self):
        self.assertIn("⭐ 활동 적극적", self.bits(activity_rate=10))
        self.assertIn("⭐ 활동 보통", self.bits(activity_rate="20"))
        self.assertIn("⭐ 활동 소극적", self.bits(activity_rate=30))
        self.assertEqual(self.bits(activity_rate=None), [])

    def test_unknown_future_code_still_shows_instead_of_disappearing(self):
        self.assertIn("💩 배변 brand_new_code", self.bits(bowel_status="brand_new_code"))

    def test_empty_report(self):
        self.assertEqual(self.bits(), [])


if __name__ == "__main__":
    unittest.main()
