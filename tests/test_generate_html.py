import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import generate_html
import storage

REPO_HISTORY = Path(__file__).resolve().parents[1] / "data" / "history.csv"


def decode_payload(payload_b64):
    raw = base64.b64decode(payload_b64).decode("utf-8")
    return raw, json.loads(
        raw,
        parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
    )


class NormalizeJsonValueTests(unittest.TestCase):
    def test_recursively_normalizes_non_finite_and_numpy_pandas_scalars(self):
        value = {
            "nested": [
                float("nan"),
                (float("inf"), float("-inf")),
                np.array([np.float32(1.25), np.float64(np.nan)]),
            ],
            "integer": np.int64(7),
            "boolean": np.bool_(True),
            "missing": pd.NA,
            "not_a_time": pd.NaT,
        }

        normalized = generate_html.normalize_json_value(value)

        self.assertEqual(
            normalized,
            {
                "nested": [None, [None, None], [1.25, None]],
                "integer": 7,
                "boolean": True,
                "missing": None,
                "not_a_time": None,
            },
        )
        self.assertEqual(
            json.loads(json.dumps(normalized, allow_nan=False)),
            normalized,
        )

    def test_unsupported_values_are_not_silently_stringified(self):
        normalized = generate_html.normalize_json_value({"value": object()})
        with self.assertRaises(TypeError):
            json.dumps(normalized, allow_nan=False)


class HistoryValidationTests(unittest.TestCase):
    def test_duplicate_date_ticker_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            source = pd.read_csv(REPO_HISTORY).iloc[[0, 0]]
            source.to_csv(path, index=False)

            with mock.patch.object(storage, "CSV_MIRROR", path):
                with self.assertRaisesRegex(ValueError, "duplicate Date/Ticker"):
                    generate_html.load_history()

    def test_invalid_dates_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            source = pd.read_csv(REPO_HISTORY).iloc[[0]].copy()
            source["Date"] = "not-a-date"
            source.to_csv(path, index=False)

            with mock.patch.object(storage, "CSV_MIRROR", path):
                with self.assertRaisesRegex(ValueError, "invalid Date"):
                    generate_html.load_history()


class StorageRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.storage_patch = mock.patch.multiple(
            storage,
            DATA_DIR=self.data_dir,
            PARQUET_DIR=self.data_dir / "history_parquet",
            CSV_MIRROR=self.data_dir / "history.csv",
        )
        self.storage_patch.start()
        source = pd.read_csv(REPO_HISTORY).iloc[[0]].copy()
        source["Date"] = "2026-01-02"
        self.first_day = source
        self.second_day = source.copy()
        self.second_day["Date"] = "2026-01-05"

    def tearDown(self):
        self.storage_patch.stop()
        self.temp_dir.cleanup()

    def test_same_date_cannot_be_appended_twice(self):
        storage.append_daily_run(self.first_day)

        with self.assertRaisesRegex(ValueError, "data already exists"):
            storage.append_daily_run(self.first_day)

        history = storage.read_history()
        self.assertEqual(len(history), 1)
        self.assertTrue(pd.api.types.is_datetime64_ns_dtype(history["Date"]))

    def test_date_filters_work_for_partition_strings(self):
        storage.append_daily_run(self.first_day)
        storage.append_daily_run(self.second_day)

        filtered = storage.read_history(
            columns=["Ticker", "Price"],
            start="2026-01-05",
            end="2026-01-05",
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(list(filtered.columns), ["Ticker", "Price"])


class RepositoryHistoryIntegrityTests(unittest.TestCase):
    def test_parquet_source_matches_csv_mirror(self):
        repo_data = REPO_HISTORY.parent
        with mock.patch.multiple(
            storage,
            DATA_DIR=repo_data,
            PARQUET_DIR=repo_data / "history_parquet",
            CSV_MIRROR=REPO_HISTORY,
        ):
            parquet = storage.read_history()

        csv = pd.read_csv(REPO_HISTORY)
        csv["Date"] = pd.to_datetime(
            csv["Date"],
            format="mixed",
        ).dt.normalize()
        csv = csv[storage.SCHEMA_COLUMNS]
        csv = csv.sort_values(["Date", "Ticker"]).reset_index(drop=True)

        self.assertFalse(parquet.duplicated(["Date", "Ticker"]).any())
        pd.testing.assert_frame_equal(
            parquet,
            csv,
            check_dtype=False,
            check_categorical=False,
            rtol=1e-12,
            atol=1e-12,
        )


class PayloadRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(storage, "CSV_MIRROR", REPO_HISTORY):
            cls.raw, cls.payload = decode_payload(generate_html.build_payload())

    def test_payload_is_strict_json_with_missing_values_as_null(self):
        self.assertNotIn("NaN", self.raw)
        self.assertNotIn("Infinity", self.raw)
        self.assertTrue(
            any(
                value is None
                for day in self.payload["data"].values()
                for rows in day["drill"].values()
                for row in rows
                for value in row
            )
        )

    def test_every_date_has_consistent_complete_views(self):
        payload = self.payload
        self.assertEqual(payload["latest"], payload["dates"][0])
        self.assertEqual(payload["prior"], payload["dates"][1])
        self.assertEqual(set(payload["dates"]), set(payload["data"]))

        for index, date_str in enumerate(payload["dates"]):
            with self.subTest(date=date_str):
                day = payload["data"][date_str]
                global_count = len(day["global_rows"])
                drill_count = sum(len(rows) for rows in day["drill"].values())
                self.assertGreater(global_count, 0)
                self.assertEqual(global_count, drill_count)
                self.assertEqual(len(day["sector_rows"]), len(day["drill"]))
                self.assertTrue(
                    all(
                        len(row) == len(day["global_cols"])
                        for row in day["global_rows"]
                    )
                )
                self.assertTrue(
                    all(
                        len(row) == len(day["sector_cols"])
                        for row in day["sector_rows"]
                    )
                )
                self.assertTrue(
                    all(
                        len(row) == len(day["drill_cols"])
                        for rows in day["drill"].values()
                        for row in rows
                    )
                )
                has_change = "ScoreChange" in day["sector_cols"]
                self.assertEqual(has_change, index < len(payload["dates"]) - 1)

    def test_generated_html_embeds_the_exact_strict_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "index.html"
            with mock.patch.object(storage, "CSV_MIRROR", REPO_HISTORY):
                payload_b64 = generate_html.build_payload()
            generate_html.write_html(output, payload_b64)
            html = output.read_text(encoding="utf-8")

        self.assertIn(
            "const DB   = JSON.parse(atob('" + payload_b64 + "'));",
            html,
        )
        self.assertIn("function parseCSV(text)", html)
        self.assertIn("function escHtml(val)", html)


if __name__ == "__main__":
    unittest.main()
