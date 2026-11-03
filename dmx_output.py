"""
DMX outputs for CentraLight: the Eurolite USB dongle, or Art-Net.

Both take a 512-byte bytearray (index 0 = DMX channel 1) and send it as one
universe. close() sends a blackout first so the lights don't stay on.
"""

from __future__ import annotations

import serial
from serial.tools import list_ports
from stupidArtnet import StupidArtnet

FTDI_VID = 0x0403
FTDI_PID = 0x6001  # FT232R, used by the Eurolite PRO Cable / MK2


def find_port() -> str | None:
    """Print every COM port and return the first FTDI one (the Eurolite)."""
    print("COM ports:")
    found = None
    for p in list_ports.comports():
        ids = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "----:----"
        print(f"  {p.device:6} {ids}  sn={p.serial_number}  {p.description}")
        if found is None and p.vid == FTDI_VID and p.pid == FTDI_PID:
            found = p.device
    return found


def build_packet(channels: bytes) -> bytes:
    """Wrap DMX channel data in an Enttec-Pro label 6 (Output Only Send DMX) packet."""
    payload = b"\x00" + bytes(channels)  # DMX start code 0, then channels 1..n
    n = len(payload)
    return bytes([0x7E, 0x06, n & 0xFF, n >> 8]) + payload + b"\xE7"


class UsbDmxOutput:
    """Eurolite USB-DMX512 PRO Cable Interface (FTDI COM port).

    Takes Enttec-Pro style label 6 packets at 250000 baud 8N2 and keeps
    re-sending the last frame on its own.
    """

    def __init__(self, port: str | None = None) -> None:
        detected = find_port()
        self.port = port or detected
        if self.port is None:
            raise ConnectionError(
                "No FTDI (0403:6001) COM port found. Check the USB cable and "
                "Device Manager > Ports (COM & LPT), or set the port by hand."
            )
        try:
            self.ser = serial.Serial(
                self.port,
                baudrate=250000,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_TWO,
                write_timeout=1,
            )
        except serial.SerialException as exc:
            raise ConnectionError(
                f"Could not open {self.port}: {exc}. Close MagicQ, QLC+ or any other "
                "script using the dongle, then try again."
            ) from exc

    def send(self, dmx: bytes) -> None:
        self.ser.write(build_packet(dmx))

    def close(self, blackout: bool = True) -> None:
        """Close the port. blackout=False leaves the lights as they are (the dongle keeps repeating)."""
        if blackout:
            try:
                self.send(bytes(512))
                self.ser.flush()
            except serial.SerialException as exc:
                print(f"WARNING: blackout not sent — {exc}")
        self.ser.close()


class ArtNetOutput:
    """Art-Net over UDP (e.g. to MagicQ or an Art-Net node): the original CentraLight route."""

    def __init__(self, ip: str, universe: int = 0) -> None:
        self.artnet = StupidArtnet(ip, universe, 512)

    def send(self, dmx: bytes) -> None:
        self.artnet.set(bytearray(dmx))
        self.artnet.show()

    def close(self, blackout: bool = True) -> None:
        if blackout:
            self.artnet.blackout()
        self.artnet.close()
