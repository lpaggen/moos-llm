from pathlib import Path
import unittest

from tools.label_collision_polygons import ChartTransform, convex_hull, moos_polygon_spec, read_chart_info


class CollisionPolygonLabelerTests(unittest.TestCase):
    def test_reads_commented_moos_chart_info(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            info = Path(directory) / "chart.info"
            info.write_text("""// chart\ndatum_lat=53\ndatum_lon = 4.8\nlat_north=54\nlat_south=52\nlon_west=4\nlon_east=6 // edge\n""")
            self.assertEqual(read_chart_info(info)["lon_east"], 6)

    def test_pixel_latlon_round_trip_and_local_datum(self):
        transform = ChartTransform(1000, 500, {
            "datum_lat": 53.0, "datum_lon": 5.0,
            "lat_north": 54.0, "lat_south": 52.0,
            "lon_west": 4.0, "lon_east": 6.0,
        })
        lat, lon = transform.pixel_to_latlon(250, 125)
        self.assertAlmostEqual(lat, 53.5)
        self.assertAlmostEqual(lon, 4.5)
        pixel_x, pixel_y = transform.latlon_to_pixel(lat, lon)
        self.assertAlmostEqual(pixel_x, 250)
        self.assertAlmostEqual(pixel_y, 125)
        local_x, local_y = transform.latlon_to_xy(53, 5)
        self.assertAlmostEqual(local_x, 0)
        self.assertAlmostEqual(local_y, 0)

    def test_chart_xy_uses_same_affine_mapping_as_marine_viewer(self):
        transform = ChartTransform(1000, 500, {
            "datum_lat": 53.0, "datum_lon": 5.0,
            "lat_north": 54.0, "lat_south": 52.0,
            "lon_west": 4.0, "lon_east": 6.0,
        })
        west_x, north_y = transform.latlon_to_xy(54, 4)
        east_x, south_y = transform.latlon_to_xy(52, 6)
        self.assertLess(west_x, 0)
        self.assertGreater(east_x, 0)
        self.assertLess(south_y, 0)
        self.assertGreater(north_y, 0)
        local_x, local_y = transform.latlon_to_xy(53, 5)
        self.assertAlmostEqual(local_x, 0)
        self.assertAlmostEqual(local_y, 0)

    def test_convex_hull_and_moos_spec(self):
        hull = convex_hull([(0, 0), (2, 0), (1, 0.5), (2, 2), (0, 2)])
        self.assertEqual(set(hull), {(0, 0), (2, 0), (2, 2), (0, 2)})
        self.assertTrue(moos_polygon_spec("rock 1", hull).startswith("pts={"))
        self.assertTrue(moos_polygon_spec("rock 1", hull).endswith("label=rock_1"))


if __name__ == "__main__":
    unittest.main()
