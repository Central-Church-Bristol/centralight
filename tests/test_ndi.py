"""NDI in, with a fake NDI library: shrinking, no signal, reconnecting, sharing receivers."""

from __future__ import annotations

import time
import unittest

import numpy as np

from centralight.ndi_source import CONNECTED, NO_SIGNAL, NdiDiscovery, NdiReceiver, matches, shrink
from centralight.sources import SourceManager
from tests.fakes import FakeClock, FakeNdi, FakeSender

MBP = "AV-PC (Media Background Pane)"


def wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


class ShrinkTests(unittest.TestCase):
    def test_1080p_becomes_320x180_rgb(self) -> None:
        bgrx = np.zeros((1080, 1920, 4), dtype=np.uint8)
        bgrx[:, :, 2] = 255  # red in BGRX
        image = shrink(bgrx, 1920, 1080)
        self.assertEqual(image.shape, (180, 320, 3))
        self.assertEqual(tuple(image[90, 160]), (255, 0, 0))

    def test_portrait_and_small_frames_keep_their_shape(self) -> None:
        self.assertEqual(shrink(np.zeros((1920, 1080, 4), np.uint8), 1080, 1920).shape, (180, 101, 3))
        self.assertEqual(shrink(np.zeros((90, 160, 4), np.uint8), 160, 90).shape, (90, 160, 3))

    def test_strided_rows(self) -> None:
        data = np.zeros((540, 1000, 4), dtype=np.uint8)  # each row is padded past 960 pixels
        data[:, :960, 1] = 200
        self.assertEqual(tuple(shrink(data, 960, 540)[0, 0]), (0, 200, 0))

    def test_names_match_ignoring_case(self) -> None:
        self.assertTrue(matches("media background pane", MBP))
        self.assertFalse(matches("vMix", MBP))


class ReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sender = FakeSender(MBP, colour=(0, 0, 255))
        self.ndi = FakeNdi(self.sender, FakeSender("AV-PC (vMix - Output 1)"))
        self.discovery = NdiDiscovery(self.ndi, interval=0.05)
        self.discovery.start()
        self.receiver = NdiReceiver("Media Background Pane", self.discovery, self.ndi, no_signal_after=0.5, reconnect_after=1.0)
        self.receiver.start()

    def tearDown(self) -> None:
        self.receiver.stop()
        self.discovery.stop()

    def test_frames_arrive_small_and_get_freed(self) -> None:
        self.assertTrue(wait_for(lambda: self.receiver.status().state == CONNECTED))
        frame = self.receiver.latest()
        self.assertEqual(frame.image.shape, (180, 320, 3))
        self.assertEqual(tuple(frame.image[0, 0]), (0, 0, 255))
        self.assertEqual(frame.source_size, (960, 540))
        status = self.receiver.status()
        self.assertEqual(status.found_name, MBP)
        self.assertEqual(self.ndi.settings[0].ndi_recv_name, "CentraLight")
        self.assertEqual(self.ndi.settings[0].bandwidth, self.ndi.RECV_BANDWIDTH_HIGHEST)
        time.sleep(0.6)
        self.assertTrue(15 <= self.receiver.status().fps <= 30, self.receiver.status().fps)
        self.receiver.stop()
        self.assertEqual(self.ndi.captured, self.ndi.freed)  # every frame was given back

    def test_no_signal_holds_the_last_frame(self) -> None:
        self.assertTrue(wait_for(lambda: self.receiver.status().state == CONNECTED))
        self.sender.paused = True
        self.assertTrue(wait_for(lambda: self.receiver.status().state == NO_SIGNAL, 2.0))
        self.assertIsNotNone(self.receiver.latest())  # the lights keep their colour
        self.sender.paused = False
        self.assertTrue(wait_for(lambda: self.receiver.status().state == CONNECTED, 3.0))

    def test_a_quiet_source_is_not_pestered_with_reconnects(self) -> None:
        # Media Background Pane sometimes sends one picture and then nothing for a while
        self.assertTrue(wait_for(lambda: self.receiver.status().state == CONNECTED))
        self.sender.paused = True
        time.sleep(2.5)
        self.assertEqual(self.receiver.status().state, NO_SIGNAL)
        self.assertEqual(self.receiver.connects, 1)

    def test_reconnects_after_the_source_restarts(self) -> None:
        self.assertTrue(wait_for(lambda: self.receiver.status().state == CONNECTED))
        seq = self.receiver.latest().seq
        self.sender.running = False  # Media Background Pane closes...
        self.assertTrue(wait_for(lambda: self.receiver.status().state == NO_SIGNAL, 2.0))
        time.sleep(1.0)
        self.sender.colour = (0, 255, 0)
        started = time.monotonic()
        self.sender.running = True  # ...and opens again
        self.assertTrue(wait_for(lambda: self.receiver.status().state == CONNECTED, 6.0))
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertGreater(self.receiver.latest().seq, seq)
        self.assertEqual(tuple(self.receiver.latest().image[0, 0]), (0, 255, 0))
        self.assertTrue(wait_for(lambda: self.receiver.connects >= 2, 2.0))  # a fresh connection
        self.assertTrue(wait_for(lambda: self.receiver.status().state == CONNECTED, 2.0))


class SourceManagerTests(unittest.TestCase):
    def test_kept_feeds_stay_others_stop_after_linger(self) -> None:
        ndi = FakeNdi(FakeSender(MBP), FakeSender("AV-PC (Camera)"))
        clock = FakeClock()
        manager = SourceManager(ndi, linger=10.0, clock=clock)
        try:
            manager.set_keep({"Media Background Pane"})
            manager.acquire("camera", "editor")
            self.assertTrue(wait_for(lambda: manager.frame("media background pane") is not None))
            self.assertTrue(wait_for(lambda: manager.frame("camera") is not None))
            manager.release("camera", "editor")
            manager.tidy()
            clock.advance(9.0)
            manager.tidy()
            self.assertIn("camera", manager.statuses())
            clock.advance(2.0)
            manager.tidy()
            self.assertNotIn("camera", manager.statuses())
            self.assertIn("media background pane", manager.statuses())  # used by an input: kept
            self.assertIn(MBP, manager.available())
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main()
