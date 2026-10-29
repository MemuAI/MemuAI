"""Invalid generated countdowns and refreshed source provenance."""
import unittest
from app import panels
from tests.panels_test import PanelsBase


class CountdownInput(unittest.TestCase):
    def test_invalid_clock_values_are_not_wrapped_to_another_time(self):
        for text in ["01.01.2027 25:90", "01.01.2027 24:00", "01.01.2027 15:75",
                     "01.01.2027 00:30 AM", "01.01.2027 13:30 PM"]:
            with self.subTest(text=text):
                self.assertEqual(panels._lenient_target(text), "")

    def test_invalid_timezone_offsets_are_rejected_without_an_exception(self):
        for text in ["01.01.2027 10:30 UTC+25:00", "01.01.2027 10:30 UTC-24:00",
                     "01.01.2027 10:30 UTC+03:90"]:
            with self.subTest(text=text):
                self.assertEqual(panels._lenient_target(text), "")

    def test_valid_twelve_hour_clock_and_explicit_timezone_are_preserved(self):
        self.assertEqual(panels._lenient_target("01.01.2027 12:30 AM UTC+03:00"), "2027-01-01T00:30+03:00")
        self.assertEqual(panels._lenient_target("01.01.2027 12:30 PM UTC-05:30"), "2027-01-01T12:30-05:30")


class RefreshSources(PanelsBase):
    def test_an_explicitly_empty_source_list_clears_obsolete_links(self):
        panel = panels.create_panel(template="clock", start=False)
        panels.update_data(panel["id"], {"clock_now": "10:00"}, [{"url": "https://old.example", "title": "old"}])
        result = panels._finish(panel["id"], values={"clock_now": "11:00"}, sources=[])
        self.assertEqual(result["sources"], [])

    def test_a_local_refresh_can_preserve_sources_when_no_source_list_was_supplied(self):
        panel = panels.create_panel(template="clock", start=False)
        panels.update_data(panel["id"], {"clock_now": "10:00"}, [{"url": "https://old.example", "title": "old"}])
        result = panels._finish(panel["id"], values={"clock_now": "11:00"})
        self.assertEqual(result["sources"][0]["url"], "https://old.example")


if __name__ == "__main__":
    unittest.main()
