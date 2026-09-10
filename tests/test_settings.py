import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.settings import (
    FOCUS_CLOSE,
    FOCUS_FIRST,
    FOCUS_LAST,
    FOCUS_STAY,
    PLACEMENT_OVERLAY,
    PLACEMENT_POPUP,
    SETTINGS_FILE,
    Settings,
    load_settings,
    parse_settings,
    tab_label,
)


class ParseSettingsTest(unittest.TestCase):
    def test_an_empty_file_yields_the_defaults(self):
        settings, warnings = parse_settings("", SETTINGS_FILE)
        self.assertEqual(settings, Settings())
        self.assertEqual(warnings, [])

    def test_reads_prefix_suffix_and_focus_mode(self):
        settings, warnings = parse_settings(
            '[tabs]\nlabel_prefix = "run:"\nlabel_suffix = " *"\n'
            '[dashboard]\nfocus_mode = "first"\n',
            SETTINGS_FILE,
        )
        self.assertEqual(settings.label_prefix, "run:")
        self.assertEqual(settings.label_suffix, " *")
        self.assertEqual(settings.focus_mode, FOCUS_FIRST)
        self.assertEqual(warnings, [])

    def test_broken_toml_yields_the_defaults_and_one_warning(self):
        settings, warnings = parse_settings("[tabs\n", SETTINGS_FILE)
        self.assertEqual(settings, Settings())
        self.assertEqual(len(warnings), 1)
        self.assertIn("invalid TOML", warnings[0])

    def test_an_unknown_focus_mode_names_the_value_it_rejected(self):
        """A silent fallback would read as the mode having been accepted."""
        settings, warnings = parse_settings(
            '[dashboard]\nfocus_mode = "middle"\n', SETTINGS_FILE
        )
        self.assertEqual(settings.focus_mode, FOCUS_STAY)
        self.assertEqual(len(warnings), 1)
        self.assertIn("middle", warnings[0])

    def test_a_non_string_label_is_reported_and_ignored(self):
        settings, warnings = parse_settings("[tabs]\nlabel_prefix = 3\n", SETTINGS_FILE)
        self.assertEqual(settings.label_prefix, "")
        self.assertEqual(len(warnings), 1)
        self.assertIn("label_prefix", warnings[0])

    def test_unknown_keys_pass_silently(self):
        """A file written for a newer version must stay usable."""
        settings, warnings = parse_settings(
            '[tabs]\nlabel_prefix = "run:"\ncolour = "blue"\n[future]\nx = 1\n',
            SETTINGS_FILE,
        )
        self.assertEqual(settings.label_prefix, "run:")
        self.assertEqual(warnings, [])

    def test_focus_mode_last_is_accepted(self):
        settings, _ = parse_settings('[dashboard]\nfocus_mode = "last"\n', SETTINGS_FILE)
        self.assertEqual(settings.focus_mode, FOCUS_LAST)

    def test_focus_mode_close_is_accepted(self):
        settings, warnings = parse_settings(
            '[dashboard]\nfocus_mode = "close"\n', SETTINGS_FILE
        )
        self.assertEqual(settings.focus_mode, FOCUS_CLOSE)
        self.assertEqual(warnings, [])


class PlacementTest(unittest.TestCase):
    def test_the_default_is_an_overlay(self):
        settings, _ = parse_settings("", SETTINGS_FILE)
        self.assertEqual(settings.placement, PLACEMENT_OVERLAY)
        self.assertIsNone(settings.popup_width)
        self.assertIsNone(settings.popup_height)

    def test_a_popup_can_be_sized_in_cells_or_percent(self):
        settings, warnings = parse_settings(
            '[dashboard]\nplacement = "popup"\npopup_width = "60%"\npopup_height = 20\n',
            SETTINGS_FILE,
        )
        self.assertEqual(settings.placement, PLACEMENT_POPUP)
        self.assertEqual(settings.popup_width, "60%")
        self.assertEqual(settings.popup_height, "20")
        self.assertEqual(warnings, [])

    def test_an_unknown_placement_names_the_value_it_rejected(self):
        settings, warnings = parse_settings(
            '[dashboard]\nplacement = "floating"\n', SETTINGS_FILE
        )
        self.assertEqual(settings.placement, PLACEMENT_OVERLAY)
        self.assertEqual(len(warnings), 1)
        self.assertIn("floating", warnings[0])

    def test_a_malformed_dimension_is_dropped_rather_than_passed_on(self):
        """Herdr would refuse the open; the default popup size is a better
        answer than no dashboard."""
        settings, warnings = parse_settings(
            '[dashboard]\npopup_width = "x%y"\npopup_height = "50 %"\n', SETTINGS_FILE
        )
        self.assertIsNone(settings.popup_width)
        self.assertIsNone(settings.popup_height)
        self.assertEqual(len(warnings), 2)

    def test_a_boolean_is_not_a_dimension(self):
        settings, warnings = parse_settings(
            "[dashboard]\npopup_width = true\n", SETTINGS_FILE
        )
        self.assertIsNone(settings.popup_width)
        self.assertEqual(len(warnings), 1)


class TabLabelTest(unittest.TestCase):
    def test_defaults_leave_the_name_untouched(self):
        self.assertEqual(tab_label("api", Settings()), "api")

    def test_wraps_the_name_in_prefix_and_suffix(self):
        settings = Settings(label_prefix="run:", label_suffix="!")
        self.assertEqual(tab_label("api", settings), "run:api!")


class LoadSettingsTest(unittest.TestCase):
    def test_a_missing_file_is_the_normal_case(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": directory}, clear=False):
            settings, warnings = load_settings()
        self.assertEqual(settings, Settings())
        self.assertEqual(warnings, [])

    def test_reads_the_file_from_the_plugin_config_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, SETTINGS_FILE), "w", encoding="utf-8") as handle:
                handle.write('[tabs]\nlabel_prefix = "svc/"\n')
            with patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": directory}, clear=False):
                settings, warnings = load_settings()
        self.assertEqual(settings.label_prefix, "svc/")
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
