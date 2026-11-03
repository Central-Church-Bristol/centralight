"""The sampler: patches, weights, edges, shared spots, keep maps and identify colours."""

from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np

from centralight.model import Fixture, MapSpot
from centralight.new_show import show_from_patch
from centralight.sampler import IDENTIFY_PALETTE, Sampler, identify_colours, sampler_for


def light(fid: str, x: float, y: float, r: float = 0.08) -> Fixture:
    return Fixture(fid, fid, "slimpar9", 5, map=MapSpot(x, y, r))


class SamplerTests(unittest.TestCase):
    def test_rows_sum_to_one_and_patches_never_overlap(self) -> None:
        sampler = Sampler(show_from_patch().fixtures)
        self.assertEqual(len(sampler.ids), 8)
        np.testing.assert_allclose(sampler.weights.sum(axis=1), 1.0, rtol=1e-5)
        owner = sampler.owner.ravel()
        for i in range(len(sampler.ids)):
            nonzero = sampler.weights[i] > 0
            self.assertTrue(nonzero.any())
            self.assertTrue(np.all(owner[nonzero] == i))  # a light only uses its own pixels

    def test_nearest_light_wins_where_circles_overlap(self) -> None:
        sampler = Sampler([light("a", 0.4, 0.5, 0.2), light("b", 0.6, 0.5, 0.2)])
        row = sampler.owner[45]
        self.assertEqual(row[70], 0)  # x ~ 0.44: nearer a
        self.assertEqual(row[90], 1)  # x ~ 0.56: nearer b
        self.assertEqual(row[0], -1)  # outside both circles

    def test_centre_counts_most(self) -> None:
        sampler = Sampler([light("a", 0.5, 0.5, 0.1)])
        weights = sampler.weights[0].reshape(90, 160)
        self.assertGreater(weights[44, 79], weights[44, 89])

    def test_light_on_the_edge_gets_half_a_patch(self) -> None:
        sampler = Sampler([light("edge", 0.0, 0.5, 0.1)])
        weights = sampler.weights[0].reshape(90, 160)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=5)
        self.assertTrue(np.all(weights[:, 20:] == 0))

    def test_off_canvas_or_tiny_lights_take_the_colour_under_them(self) -> None:
        for spot in (light("off", 1.3, 0.5, 0.05), light("tiny", 0.5, 0.5, 0.0001)):
            with self.subTest(spot=spot.id):
                sampler = Sampler([spot])
                row = sampler.weights[0]
                self.assertAlmostEqual(float(row.sum()), 1.0, places=5)
                self.assertLessEqual(int((row > 0).sum()), 4)

    def test_lights_on_the_same_spot_share_a_patch(self) -> None:
        sampler = Sampler([light("a", 0.5, 0.5), light("b", 0.5, 0.5), light("c", 0.8, 0.5)])
        np.testing.assert_array_equal(sampler.weights[0], sampler.weights[1])
        self.assertGreater(float(sampler.weights[1].sum()), 0.99)

    def test_left_red_right_blue(self) -> None:
        show = show_from_patch()
        sampler = Sampler(show.fixtures)
        canvas = np.zeros((4, 90, 160), dtype=np.float32)
        canvas[0, :, :80] = 1.0
        canvas[2, :, 80:] = 1.0
        colours = sampler.colours(canvas)
        self.assertEqual(colours["slimpar-105"], (1.0, 0.0, 0.0))  # left wall
        self.assertEqual(colours["slimpar-108"], (0.0, 0.0, 1.0))  # right wall
        self.assertEqual(colours["slimpar-101"], (1.0, 0.0, 0.0))

    def test_keep_map_follows_the_light(self) -> None:
        show = show_from_patch()
        sampler = Sampler(show.fixtures)
        keep = sampler.keep({"slimpar-106", "slimpar-107"})
        i106 = sampler.ids.index("slimpar-106")
        self.assertTrue(np.all(keep[sampler.owner == i106] == 0))
        self.assertTrue(np.all(keep[sampler.owner == -1] == 1))
        self.assertIs(sampler.keep({"slimpar-107", "slimpar-106"}), keep)  # cached
        moved = replace(show, fixtures=tuple(replace(fx, map=MapSpot(0.5, 0.5, 0.08)) if fx.id == "slimpar-106" else fx for fx in show.fixtures))
        sampler2 = sampler_for(moved.fixtures, sampler)
        self.assertIsNot(sampler2, sampler)
        self.assertEqual(float(sampler2.keep({"slimpar-106"})[45, 80]), 0.0)
        self.assertIs(sampler_for(moved.fixtures, sampler2), sampler2)  # unchanged: reused


class IdentifyTests(unittest.TestCase):
    def test_palette_is_twelve_different_colours(self) -> None:
        self.assertEqual(len({rgb for _name, rgb in IDENTIFY_PALETTE}), 12)

    def test_same_seed_same_colours(self) -> None:
        ids = [f"slimpar-10{i}" for i in range(1, 9)]
        first = identify_colours(ids, 7)
        self.assertEqual(first, identify_colours(ids, 7))
        self.assertEqual(len({rgb for _name, rgb in first.values()}), 8)  # 8 lights, 8 colours
        self.assertNotEqual(first, identify_colours(ids, 8))


if __name__ == "__main__":
    unittest.main()
