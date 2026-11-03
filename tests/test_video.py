"""Video elements: a clip decoded small and paced, looping, its speed, the reader thread, files replaced or
missing, and a video on the lights. The test video is made here: one second red, one green, one blue."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from centralight.bus import Instance, render_input
from centralight.elements import RenderContext
from centralight.model import Input
from centralight.new_show import create_show
from centralight.lights import lights_for
from centralight.show_io import freeze
from centralight.video_source import MAX_FPS, VideoClip, VideoLibrary

RED, GREEN, BLUE = (0, 0, 255), (0, 255, 0), (255, 0, 0)  # BGR, as OpenCV writes them


def make_video(path: Path, colours=(RED, GREEN, BLUE), fps: int = 10, size=(64, 36)) -> Path:
    import cv2

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    for colour in colours:
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[:] = colour
        for _ in range(fps):
            writer.write(frame)
    writer.release()
    return path


def main_colour(picture) -> str:
    r, g, b = (float(picture[..., i].mean()) for i in range(3))
    return max((("red", r), ("green", g), ("blue", b)), key=lambda item: item[1])[0]


class VideoClipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.video = make_video(self.folder / "rgb.avi")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_exact_frames_and_looping(self) -> None:
        clip = VideoClip(self.video, threaded=False)
        self.assertEqual(clip.error, "")
        self.assertAlmostEqual(clip.duration, 3.0, delta=0.05)
        for t, colour in ((0.5, "red"), (1.5, "green"), (2.5, "blue"), (3.5, "red"), (7.4, "green"), (0.2, "red")):
            self.assertEqual(main_colour(clip.frame_at(t)), colour, f"at {t} s")
        self.assertEqual(clip.frame_at(1.0).shape, (36, 64, 3))
        clip.close()

    def test_fast_videos_are_kept_to_25_pictures_a_second(self) -> None:
        fast = make_video(self.folder / "fast.avi", colours=(RED, GREEN), fps=50)
        clip = VideoClip(fast, threaded=False)
        clip.frame_at(0.0)
        clip._decode_towards(0.0, ahead=1.0)
        kept = [t for t in clip._times if t <= 1.0]
        self.assertLessEqual(len(kept), MAX_FPS + 2)
        self.assertGreaterEqual(len(kept), MAX_FPS - 2)
        clip.close()

    def test_big_pictures_are_made_small(self) -> None:
        big = make_video(self.folder / "big.avi", colours=(BLUE,), size=(1280, 720))
        clip = VideoClip(big, threaded=False)
        self.assertEqual(clip.frame_at(0.5).shape, (180, 320, 3))
        clip.close()

    def test_the_reader_thread(self) -> None:
        clip = VideoClip(self.video, threaded=True)
        deadline = time.monotonic() + 5
        picture = clip.frame_at(1.5)
        while (picture is None or main_colour(picture) != "green") and time.monotonic() < deadline:
            time.sleep(0.02)
            picture = clip.frame_at(1.5)
        self.assertEqual(main_colour(picture), "green")
        picture = clip.frame_at(0.4)  # back to the start: it seeks
        while main_colour(picture) != "red" and time.monotonic() < deadline:
            time.sleep(0.02)
            picture = clip.frame_at(0.4)
        self.assertEqual(main_colour(picture), "red")
        clip.close()
        self.assertFalse(clip._thread.is_alive())

    def test_broken_and_replaced_files(self) -> None:
        broken = self.folder / "broken.avi"
        broken.write_bytes(b"this isn't a video")
        clip = VideoClip(broken, threaded=False)
        self.assertTrue(clip.error)
        self.assertIsNone(clip.frame_at(1.0))
        library = VideoLibrary()
        first = library.clip(self.video, threaded=False)
        self.assertIs(library.clip(self.video, threaded=False), first)
        make_video(self.video, colours=(BLUE, BLUE))
        stamp = time.time() + 5
        os.utime(self.video, (stamp, stamp))
        second = library.clip(self.video, threaded=False)
        self.assertIsNot(second, first)  # replaced: opened again
        self.assertEqual(main_colour(second.frame_at(0.5)), "blue")
        second.last_used -= 60
        library.tidy()
        self.assertIsNot(library.clip(self.video, threaded=False), second)  # closed when idle, so a new one
        library.close()


class VideoElementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.show = create_show(Path(self._tmp.name) / "Show")
        (Path(self.show.folder) / "media").mkdir(exist_ok=True)
        make_video(Path(self.show.folder) / "media" / "rgb.avi")

    def tearDown(self) -> None:
        from centralight.video_source import videos

        videos.close()
        self._tmp.cleanup()

    def colour_at(self, element: dict, t: float) -> str:
        inp = Input(id="clip", name="Clip", tracks=freeze([[element], [], [], []]))
        ctx = RenderContext(0, 0, video_threaded=False, lights=lights_for(self.show.fixtures), per_light=True)
        ctx.folder = self.show.folder
        layer = render_input(Instance(inp, 0.0), t, ctx)
        r, g, b = (float(v) for v in layer[:3, 0])
        return max((("red", r), ("green", g), ("blue", b)), key=lambda item: item[1])[0] if max(r, g, b) > 0.2 else "dark"

    def test_on_the_lights(self) -> None:
        element = {"id": "E1", "kind": "video", "file": "media/rgb.avi"}
        self.assertEqual(self.colour_at(element, 0.5), "red")
        self.assertEqual(self.colour_at(element, 2.5), "blue")
        self.assertEqual(self.colour_at(element, 4.5), "green")  # it loops
        faster = dict(element, speed=2.0)
        self.assertEqual(self.colour_at(faster, 0.75), "green")  # 1.5 s into the clip
        self.assertEqual(self.colour_at(dict(element, file="media/nowhere.avi"), 1.0), "dark")  # missing: nothing


if __name__ == "__main__":
    unittest.main()
