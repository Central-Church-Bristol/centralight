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

    def test_a_picture_keeps_only_the_newest_videos(self) -> None:
        """Each new version of a linked video is a new file: a drawing (the Dashboard's live picture, the Editor's) keeps
        the newest picture of only the last few videos it drew, so a week of updates can't pile them up (the 4 Nov
        soak found 0.4 MB kept for every video ever shown)."""
        import shutil

        from centralight.elements import VIDEOS_KEPT, render_video

        media = Path(self.show.folder) / "media"
        ctx = RenderContext(16, 9, video_threaded=False)
        ctx.folder = self.show.folder
        ctx.time = 0.5

        def draw(name: str) -> str:
            layer = render_video({"id": "E1", "kind": "video", "file": name}, ctx)
            self.assertIsNotNone(layer, name)
            r, g, b = (float(layer[i].mean()) for i in range(3))
            return max((("red", r), ("green", g), ("blue", b)), key=lambda item: item[1])[0]

        def kept() -> list[str]:
            return [Path(key[1]).name for key in ctx.cache if isinstance(key, tuple) and key[:1] == ("video",)]

        names = [f"rgb {i}.avi" for i in range(VIDEOS_KEPT + 3)]
        for name in names:
            shutil.copyfile(media / "rgb.avi", media / name)
            self.assertEqual(draw(f"media/{name}"), "red")
            self.assertEqual(draw(f"media/{name}"), "red")  # (the same picture again: still one each)
        self.assertEqual(kept(), names[-VIDEOS_KEPT:])
        self.assertEqual(draw(f"media/{names[0]}"), "red")  # (an older one comes back when it's drawn again)
        self.assertEqual(kept(), names[-VIDEOS_KEPT + 1:] + names[:1])


class CopiesTests(unittest.TestCase):
    """Two running copies of one video input (on the Overlay, say) each have a reader of their own (M14 review: they
    shared one, which seeked back and forth every tick while the engine waited for it)."""

    def test_each_copy_has_its_own_reader(self) -> None:
        from centralight.elements import video_player

        library = VideoLibrary()
        self.addCleanup(library.close)
        a, b = {}, {}
        with tempfile.TemporaryDirectory() as folder:
            path = make_video(Path(folder) / "clip.avi")
            first = library.clip(path, threaded=True, player=id(a))
            self.assertIs(library.clip(path, threaded=True, player=id(a)), first)
            self.assertIsNot(library.clip(path, threaded=True, player=id(b)), first)
            self.assertIs(library.clip(path, threaded=True), library.clip(path, threaded=True))  # (no player: shared)
            library.close()
        engine_ctx = RenderContext(0, 0, per_light=True)
        engine_ctx.memory = a
        self.assertEqual(video_player(engine_ctx), id(a))
        editor_ctx = RenderContext(0, 0, video_threaded=False)
        editor_ctx.memory = a
        self.assertIsNone(video_player(editor_ctx))  # (exact frames: one shared reader, as before)

    def test_a_new_copy_takes_over_a_reader_another_has_finished_with(self) -> None:
        """(Review: sending a video to the Overlay again and again opened a reader each time: hundreds of threads and
        gigabytes, and a hitch on the lights at each send.)"""
        from unittest import mock

        from centralight import video_source

        library = VideoLibrary()
        self.addCleanup(library.close)
        with tempfile.TemporaryDirectory() as folder:
            path = make_video(Path(folder) / "clip.avi")
            first = library.clip(path, threaded=True, player=1)
            self.assertIsNotNone(first.frame_at(1.5))  # (its first picture straight away: no blank moment)
            self.assertIsNot(library.clip(path, threaded=True, player=2), first)  # (still in use: a reader of its own)
            real = time.monotonic
            with mock.patch.object(video_source.time, "monotonic", lambda: real() + 1.0):
                again = library.clip(path, threaded=True, player=3)  # (both have been idle a while now)
            self.assertIn(again, (first, library.clip(path, threaded=True, player=2)))
            self.assertEqual(main_colour(again.frame_at(0.2)), "red")  # (from its own moment, not the other copy's)
            library.close()

    def test_closing_readers_doesnt_hold_up_the_engine(self) -> None:
        from unittest import mock

        library = VideoLibrary()
        with tempfile.TemporaryDirectory() as folder:
            path = make_video(Path(folder) / "clip.avi")
            clip = library.clip(path, threaded=True, player=1)
            clip.frame_at(0.5)
            slow = clip.close

            def close_slowly() -> None:
                time.sleep(0.3)
                slow()

            clip.close = close_slowly
            start = time.monotonic()
            library.tidy(idle=-1.0)
            self.assertLess(time.monotonic() - start, 0.2)  # (closed on a thread of its own)
            deadline = time.monotonic() + 3.0
            while clip._cap is not None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIsNone(clip._cap)

    def test_the_engine_asks_for_each_copys_own_reader(self) -> None:
        from unittest import mock

        from centralight import video_source
        from centralight.new_show import show_from_patch

        show = show_from_patch()
        inp = Input(id="v", name="v", tracks=freeze([[{"id": "E1", "kind": "video", "file": "clip.avi"}], [], [], []]))
        players = []
        real = video_source.videos.clip

        def spy(path, threaded=True, player=None):
            players.append(player)
            return real(path, threaded=threaded, player=player)

        first, second = Instance(inp, 0.0), Instance(inp, 1.0)  # (sent a second apart)
        with tempfile.TemporaryDirectory() as folder:
            make_video(Path(folder) / "clip.avi")
            for per_light in (True, False):  # (the lights, and the Dashboard's picture)
                ctx = RenderContext(0 if per_light else 64, 0 if per_light else 36, folder=folder,
                                    lights=lights_for(show.fixtures, None), per_light=per_light)
                with mock.patch.object(video_source.videos, "clip", spy):
                    render_input(first, 1.5, ctx)
                    render_input(second, 1.5, ctx)
            video_source.videos.close()
        self.assertEqual(len(players), 4)
        self.assertNotIn(None, players)
        self.assertEqual(len(set(players)), 2)  # (one reader for each copy, the same for the lights and the picture)


if __name__ == "__main__":
    unittest.main()
