import math
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


LIB_DIR = Path(__file__).parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from coordinates import DisplayGeometry, map_widget_point  # noqa: E402


class CoordinateMappingTests(unittest.TestCase):
    def test_fit_scaling_removes_vertical_letterbox(self):
        geometry = DisplayGeometry(
            widget_width=1600,
            widget_height=1000,
            scale_factor=1,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=True,
        )

        mapped = map_widget_point(geometry, 800, 500)

        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.x, 639)
        self.assertEqual(mapped.y, 359)
        self.assertAlmostEqual(mapped.render_scale, 1.25)

    def test_hidpi_logical_coordinates_are_mapped_via_device_pixels(self):
        geometry = DisplayGeometry(
            widget_width=800,
            widget_height=500,
            scale_factor=2,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=True,
        )

        mapped = map_widget_point(geometry, 400, 250)

        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.x, 639)
        self.assertEqual(mapped.y, 359)

    def test_only_downscale_centers_native_size_in_larger_widget(self):
        geometry = DisplayGeometry(
            widget_width=1600,
            widget_height=1000,
            scale_factor=1,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=True,
            only_downscale=True,
        )

        mapped = map_widget_point(geometry, 800, 500)

        self.assertIsNotNone(mapped)
        self.assertAlmostEqual(mapped.x, 640)
        self.assertAlmostEqual(mapped.y, 360)
        self.assertEqual(mapped.render_scale, 1)

    def test_letterbox_point_is_not_a_guest_coordinate(self):
        geometry = DisplayGeometry(
            widget_width=1600,
            widget_height=1000,
            scale_factor=1,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=True,
        )

        self.assertIsNone(map_widget_point(geometry, 800, 20))

    def test_unscaled_oversized_surface_accounts_for_scrolling(self):
        geometry = DisplayGeometry(
            widget_width=800,
            widget_height=600,
            scale_factor=1,
            framebuffer_width=1200,
            framebuffer_height=900,
            scaling=False,
            scroll_x=200,
            scroll_y=150,
        )

        mapped = map_widget_point(geometry, 0, 0)

        self.assertIsNotNone(mapped)
        self.assertAlmostEqual(mapped.x, 200)
        self.assertAlmostEqual(mapped.y, 150)

    def test_display_and_monitor_origin_are_retained_without_changing_local_point(self):
        geometry = DisplayGeometry(
            widget_width=1280,
            widget_height=720,
            scale_factor=1,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=False,
            display=1,
            monitor_origin_x=1920,
            monitor_origin_y=100,
        )

        mapped = map_widget_point(geometry, 100, 75)

        self.assertEqual(mapped.display, 1)
        self.assertEqual(mapped.x, 100)
        self.assertEqual(mapped.y, 75)
        self.assertEqual(mapped.surface_x, 2020)
        self.assertEqual(mapped.surface_y, 175)
        self.assertEqual(mapped.framebuffer_width, 1280)
        self.assertEqual(mapped.framebuffer_height, 720)

    def test_right_and_bottom_edges_are_exclusive(self):
        geometry = DisplayGeometry(
            widget_width=1280,
            widget_height=720,
            scale_factor=1,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=False,
        )

        self.assertIsNone(map_widget_point(geometry, 1280, 719))
        self.assertIsNone(map_widget_point(geometry, 1279, 720))

    def test_scaled_last_rendered_pixel_reaches_last_guest_pixel(self):
        geometry = DisplayGeometry(
            widget_width=1000,
            widget_height=800,
            scale_factor=1,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=True,
        )

        mapped = map_widget_point(geometry, 999, 399)

        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.x, 1279)

    def test_rounding_uses_spice_uniform_input_scale(self):
        geometry = DisplayGeometry(
            widget_width=500,
            widget_height=400,
            scale_factor=1,
            framebuffer_width=1280,
            framebuffer_height=720,
            scaling=True,
        )

        mapped = map_widget_point(geometry, 250, 60)

        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.x, 640)
        self.assertEqual(mapped.y, 2)

    def test_geometry_rejects_invalid_values_and_scroll_range(self):
        with self.assertRaises(ValueError):
            DisplayGeometry(0, 600, 1, 1280, 720)
        with self.assertRaises(ValueError):
            DisplayGeometry(800, 600, 0, 1280, 720)
        with self.assertRaises(ValueError):
            DisplayGeometry(800, 600, 1, 1200, 900, scaling=False, scroll_x=401)

        geometry = DisplayGeometry(800, 600, 1, 1280, 720)
        with self.assertRaises(ValueError):
            map_widget_point(geometry, math.nan, 1)

    def test_geometry_is_immutable(self):
        geometry = DisplayGeometry(800, 600, 1, 1280, 720)
        with self.assertRaises(FrozenInstanceError):
            geometry.widget_width = 10


if __name__ == "__main__":
    unittest.main()
