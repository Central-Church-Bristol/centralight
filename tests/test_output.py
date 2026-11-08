"""DMX encoding and the output thread (send on change, keepalive, trim, reconnect)."""

from __future__ import annotations

import json
import random
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from centralight.dmx_output import build_packet
from centralight.fixtures import PATCH, set_fixture

from centralight.new_show import fixture_id, show_from_patch
from centralight.output import (
    BUSY,
    CLOSED,
    CONNECTED,
    ERROR,
    MISSING,
    USB_MAX_RATE,
    DeviceSpec,
    DmxOutputThread,
    SimulatedDevice,
    device_spec,
    encode_dmx,
    to_byte,
    universe_size,
)
from centralight.settings import OUTPUT_ARTNET, OUTPUT_SIMULATED, OUTPUT_USB, Settings
from tests.fakes import FakePort


def wait_for(condition, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class EncodeTests(unittest.TestCase):
    def test_matches_set_fixture_for_random_looks(self) -> None:
        """Parity: encode_dmx gives the same bytes as fixtures.set_fixture (how the first console scripts did it)."""
        show = show_from_patch()
        rng = random.Random(4)
        for _ in range(200):
            expected = bytearray(512)
            colours = {}
            levels = {}
            for item in PATCH:
                fid = fixture_id(item["name"])
                rgb = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
                if item["profile"] == "slimpar9":
                    colours[fid] = tuple(v / 255 for v in rgb)
                    set_fixture(expected, item, rgb)
                else:
                    level = rng.randrange(256)
                    levels[fid] = level / 255
                    set_fixture(expected, item, (level, level, level))
            self.assertEqual(encode_dmx(show.fixtures, colours, levels), bytes(expected))

    def test_black_still_has_slimpar_dimmers_at_full(self) -> None:
        show = show_from_patch()
        expected = bytearray(512)
        for item in PATCH:
            set_fixture(expected, item, (0, 0, 0))
        self.assertEqual(encode_dmx(show.fixtures, {}, {}), bytes(expected))
        self.assertEqual(expected[5 - 1 + 7], 255)  # SlimPAR 101's dimmer (8th channel)

    def test_to_byte_round_trips_every_value(self) -> None:
        for value in range(256):
            self.assertEqual(to_byte(value / 255), value)
        self.assertEqual(to_byte(-1), 0)
        self.assertEqual(to_byte(2), 255)

    def test_church_universe_is_76_channels(self) -> None:
        self.assertEqual(universe_size(show_from_patch().fixtures), 76)
        self.assertEqual(universe_size([]), 24)


class SpecTests(unittest.TestCase):
    def test_default_settings_are_simulated(self) -> None:
        spec = device_spec(Settings(), 76)
        self.assertEqual(spec.kind, OUTPUT_SIMULATED)
        self.assertIsInstance(spec.factory(), SimulatedDevice)

    def test_usb_trims_unless_full_universe(self) -> None:
        self.assertEqual(device_spec(Settings(output=OUTPUT_USB, full_universe=False), 76).trim, 76)
        self.assertIsNone(device_spec(Settings(output=OUTPUT_USB, full_universe=True), 76).trim)
        self.assertIsNone(device_spec(Settings(output=OUTPUT_ARTNET), 76).trim)

    def test_new_settings_send_all_512_and_saved_ones_keep_theirs(self) -> None:
        """8 Nov: like MagicQ and the script that worked. A settings file that says false (as the church PC's does)
        stays false: "Send all 512 channels" ticks it."""
        self.assertTrue(Settings().full_universe)
        self.assertIsNone(device_spec(Settings(output=OUTPUT_USB), 76).trim)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"output": "usb", "full_universe": False}), encoding="utf-8")
            self.assertFalse(Settings.load(path).full_universe)
            path.write_text(json.dumps({"output": "usb"}), encoding="utf-8")
            self.assertTrue(Settings.load(path).full_universe)

    def test_only_the_eurolite_is_held_to_30_a_second(self) -> None:
        self.assertAlmostEqual(device_spec(Settings(output=OUTPUT_USB), 76).min_gap, 1 / 30)
        self.assertEqual(device_spec(Settings(output=OUTPUT_ARTNET), 76).min_gap, 0.0)
        self.assertEqual(device_spec(Settings(), 76).min_gap, 0.0)  # (Simulated)

    def test_specs_never_open_anything_until_asked(self) -> None:
        # Building specs is safe; only factory() would open the port (and the test guard refuses that)
        spec = device_spec(Settings(output=OUTPUT_USB, usb_port="COM3"), 76)
        with self.assertRaises(RuntimeError):
            spec.factory()


class OutputThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port = FakePort()
        self.thread = DmxOutputThread(
            DeviceSpec(OUTPUT_USB, "COM9", self.port.factory, trim=76),
            keepalive=0.2,
            retry=0.1,
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.thread.close(blackout=True, timeout=2.0)

    def frame(self, value: int) -> bytes:
        data = bytearray(512)
        data[4] = value
        return bytes(data)

    def test_trims_to_76_channels(self) -> None:
        self.thread.submit(self.frame(10))
        self.assertTrue(wait_for(lambda: len(self.port.all_sent()) >= 1))
        self.assertEqual(len(self.port.all_sent()[0]), 76)
        self.assertEqual(self.thread.status().state, CONNECTED)
        self.assertEqual(self.thread.status().label, "COM9")

    def test_sends_on_change_plus_keepalive(self) -> None:
        for _ in range(20):
            self.thread.submit(self.frame(1))  # the same frame over and over
        time.sleep(0.5)
        count = len(self.port.all_sent())
        self.assertGreaterEqual(count, 2)  # the first send plus keepalives every 0.2 s
        self.assertLessEqual(count, 5)
        self.thread.submit(self.frame(2))
        self.assertTrue(wait_for(lambda: self.port.all_sent()[-1][4] == 2))

    def test_unplug_and_replug_recovers(self) -> None:
        self.thread.submit(self.frame(1))
        self.assertTrue(wait_for(lambda: len(self.port.all_sent()) >= 1))
        self.port.unplug()
        self.thread.submit(self.frame(2))
        self.assertTrue(wait_for(lambda: self.thread.status().state in (ERROR, MISSING)))
        self.thread.submit(self.frame(3))  # changes while unplugged...
        time.sleep(0.2)
        self.port.replug()
        # ...are sent as soon as the dongle is back
        self.assertTrue(wait_for(lambda: self.thread.status().state == CONNECTED))
        self.assertTrue(wait_for(lambda: self.port.devices[-1].sent and self.port.devices[-1].sent[-1][4] == 3))
        self.assertGreaterEqual(len(self.port.devices), 2)

    def test_busy_port_is_reported_then_connects(self) -> None:
        self.thread.close()
        self.port = FakePort()
        self.port.busy = True
        self.thread = DmxOutputThread(DeviceSpec(OUTPUT_USB, "COM9", self.port.factory, trim=76), retry=0.1)
        self.thread.start()
        self.thread.submit(self.frame(7))
        self.assertTrue(wait_for(lambda: self.thread.status().state == BUSY))
        self.assertIn("another program", self.thread.status().message)
        self.port.busy = False
        self.assertTrue(wait_for(lambda: self.thread.status().state == CONNECTED))
        self.assertTrue(wait_for(lambda: self.port.all_sent() and self.port.all_sent()[-1][4] == 7))

    def test_close_with_blackout(self) -> None:
        self.thread.submit(self.frame(9))
        self.assertTrue(wait_for(lambda: len(self.port.all_sent()) >= 1))
        self.thread.close(blackout=True)
        device = self.port.devices[-1]
        self.assertTrue(device.closed)
        self.assertTrue(device.closed_with_blackout)
        self.assertEqual(device.sent[-1], bytes(512))
        self.assertEqual(self.thread.status().state, CLOSED)

    def test_close_leaving_lights_sends_newest_frame_first(self) -> None:
        self.thread.submit(self.frame(9))
        self.assertTrue(wait_for(lambda: len(self.port.all_sent()) >= 1))
        self.thread.submit(self.frame(42))
        self.thread.close(blackout=False)
        device = self.port.devices[-1]
        self.assertTrue(device.closed)
        self.assertFalse(device.closed_with_blackout)
        self.assertEqual(device.sent[-1][4], 42)

    def test_switching_output_blacks_out_the_old_one(self) -> None:
        self.thread.submit(self.frame(5))
        self.assertTrue(wait_for(lambda: len(self.port.all_sent()) >= 1))
        old = self.port.devices[-1]
        simulated = SimulatedDevice()
        self.thread.set_spec(DeviceSpec(OUTPUT_SIMULATED, "Simulated", lambda: simulated, trim=76))
        self.assertTrue(wait_for(lambda: simulated.last is not None))
        self.assertTrue(old.closed)
        self.assertTrue(old.closed_with_blackout)
        self.assertEqual(simulated.last[4], 5)
        self.assertEqual(self.thread.status().kind, OUTPUT_SIMULATED)


class UsbRateTests(unittest.TestCase):
    """8 Nov: the festoons flickered while an NDI input played, the engine making a new frame 40 times a second. To the
    Eurolite, CentraLight now sends 30 packets a second at most, always ending on the newest frame."""

    def start(self, full_universe: bool = False) -> DmxOutputThread:
        self.port = FakePort()
        spec = device_spec(Settings(output=OUTPUT_USB, usb_port="COM9", full_universe=full_universe), 76)
        self.thread = DmxOutputThread(replace(spec, factory=self.port.factory), retry=0.1)
        self.thread.start()
        self.addCleanup(self.thread.close, True, 2.0)
        return self.thread

    @staticmethod
    def frame(value: int) -> bytes:
        data = bytearray(512)
        data[4] = value
        return bytes(data)

    def test_a_fast_stream_goes_out_30_a_second_at_most_ending_on_the_newest_frame(self) -> None:
        thread = self.start()
        end = time.monotonic() + 2.0
        value = 0
        while time.monotonic() < end:  # (a new frame every 12.5 ms: faster than the engine's 40 a second)
            value = value % 250 + 1
            thread.submit(self.frame(value))
            time.sleep(0.0125)
        self.assertTrue(wait_for(lambda: self.port.all_sent() and self.port.all_sent()[-1][4] == value, 0.5))  # (the last)
        times = list(thread._sent_times)
        busiest = max(sum(1 for t in times if start <= t < start + 1.0) for start in times)
        self.assertLessEqual(busiest, USB_MAX_RATE)
        self.assertGreaterEqual(busiest, 15)  # (and it keeps up)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 1 / USB_MAX_RATE - 0.001)

    def test_the_keepalive_is_still_once_a_second(self) -> None:
        thread = self.start(full_universe=True)
        thread.submit(self.frame(7))
        time.sleep(2.5)
        times = list(thread._sent_times)
        self.assertEqual(len(times), 3)  # (the frame, then the same again once a second)
        for a, b in zip(times, times[1:]):
            self.assertAlmostEqual(b - a, 1.0, delta=0.1)
        self.assertEqual(len(self.port.all_sent()[-1]), 512)

    def test_512_channels_30_times_a_second_fit_the_link(self) -> None:
        seconds = len(build_packet(bytes(512))) * 11 / 250000  # (250 kbaud, 8N2: 11 bits a byte)
        self.assertAlmostEqual(seconds, 0.0228, delta=0.0002)
        self.assertLess(seconds, 1 / USB_MAX_RATE)  # (each is sent before the next is due)
        self.assertLess(seconds * USB_MAX_RATE, 0.7)  # (the link busy 68 % of the time, against 91 % at 40 a second)


if __name__ == "__main__":
    unittest.main()
