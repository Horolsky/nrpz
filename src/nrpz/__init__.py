"""Native Linux driver for Rohde & Schwarz NRP-Z USB power sensors.

No VISA, no vendor DLLs -- talks the sensor's USB protocol directly via libusb.
The protocol was reverse-engineered from R&S nrp.dll and verified against an
NRP-Z11 (readings checked against a reference source).

The sensor is a vendor-specific USB device (0aad:000c) with two 16-byte bulk
endpoints and speaks SCPI. Old sensors like the NRP-Z11 are "legacy-native":
just claim the interface and talk -- no vendor control transfer (newer sensors
booting in binary mode would need VRT_SET_LEGACY_OPEN, req 0x09, which the
NRP-Z11 STALLs).

Wire format
-----------
Write : SCPI text + '\\n' to EP 0x01 (bulk OUT).
Read  : stream of 16-byte records from EP 0x82 (bulk IN). byte[0] = record type:
          'T' 0x54  text     : byte[4:6]=uint16 LE offset, byte[6:16]=10 chars
          'L' 0x4c  parameter: float32 LE at byte[4:8]      (e.g. FREQ readback)
          'E' 0x45  result   : float32 LE at byte[4:8]; byte[1]=result status
          'N' 0x44  result   : integer LE at byte[4:8]; byte[1]=result status
          'Z' 0x5a  state    : byte[2]=trigger state (0 idle,2 wait,3 measuring)
          'R' 0x52  end/ack  : byte[1]=status/error code (0=OK, e.g. 0x89=unknown)
        The sensor answers EVERY command with one message ending in an 'R'; a
        query puts its data records before that 'R'. Read one full message per
        command written or the TX FIFO desyncs. Measurement results arrive as a
        separate pushed message ('Z' MEASURING -> 'E' result -> 'Z' IDLE) after
        the INIT command's own 'R' ack.

Scope: legacy text/scalar mode -- *IDN?, config, zeroing, average power. Trace/
CCDF waveform block reads (the native binary typed-queue protocol) are not
decoded.
"""

import argparse
import math
import sys
import time

import usb.core
import usb.util

from nrpz.codec import decode_message, decode_record
from nrpz.enums import NrpRtype

__version__ = "0.1.2"
__all__ = ["NrpZ", "NrpError", "decode_message", "dbm", "main", "VID", "PID"]

VID, PID = 0x0AAD, 0x000C
EP_OUT, EP_IN = 0x01, 0x82

class NrpError(Exception):
    pass



def dbm(w):
    """Convert power in watts to dBm. Returns -inf for non-positive power."""
    return 10 * math.log10(w / 1e-3) if w and w > 0 else float("-inf")


def humanize(x: float):
    """Human-readable representation of numerical value"""
    magnitude = abs(x)
    if magnitude < 1e-9:
        return x * 1e12, 'p'
    if magnitude < 1e-6:
        return x * 1e9, 'n'
    if magnitude < 1e-3:
        return x * 1e6, 'µ'
    if magnitude < 1e0:
        return x * 1e3, 'm'
    if magnitude < 1e3:
        return x, ''
    if magnitude < 1e6:
        return x * 1e-3, 'K'
    if magnitude < 1e9:
        return x * 1e-6, 'M'
    if magnitude < 1e12:
        return x * 1e-9, 'G'
    if magnitude < 1e15:
        return x * 1e-12, 'T'

    return x * 1e-15, 'E'


class NrpZ:
    def __init__(self, serial=None):
        match = (lambda d: usb.util.get_string(d, d.iSerialNumber) == serial) if serial else None
        self.dev = usb.core.find(idVendor=VID, idProduct=PID, custom_match=match)
        if self.dev is None:
            raise NrpError("NRP-Z sensor not found (0aad:000c). Plugged in? In 'dialout' group?")

    # ---- lifecycle -----------------------------------------------------
    def open(self):
        d = self.dev
        try:
            if d.is_kernel_driver_active(0):
                d.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass
        # NB: do NOT set_configuration() -- SET_CONFIGURATION resets the sensor's
        # SCPI session and the first responses come back empty.
        usb.util.claim_interface(d, 0)
        self._drain()
        return self

    def close(self):
        usb.util.dispose_resources(self.dev)

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    # ---- raw record I/O ------------------------------------------------
    def _drain(self):
        while True:
            try:
                self.dev.read(EP_IN, 16, timeout=60)
            except usb.core.USBError:
                return

    def _rec(self, timeout_ms):
        return bytes(self.dev.read(EP_IN, 16, timeout=timeout_ms))

    def _read_records(self, idle_ms, max_ms) -> list[bytes]:
        """Read raw 16-byte records up to and including the 'R' terminator.
        Inactivity timeout: keeps waiting while records arrive (including 'z'
        keepalives during slow ops), stops on 'R' or `idle_ms` of silence."""
        start = time.monotonic()
        recs = []
        while (time.monotonic() - start) * 1000 < max_ms:
            try:
                rec = self._rec(idle_ms)
            except usb.core.USBError as e:
                if "timed out" in str(e).lower():
                    break
                raise
            recs.append(rec)
            if len(rec) >= 1 and rec[0] == NrpRtype.END:
                break
        return recs

    def _read_message(self, idle_ms=1500, max_ms=15000):
        """Read one response message and decode it -> (status, text, floats)."""
        return decode_message(self._read_records(idle_ms, max_ms))

    # ---- command layer -------------------------------------------------
    def write(self, cmd):
        """Send a command and consume its ack. Raises on device error status."""
        self.dev.write(EP_OUT, (cmd.rstrip("\n") + "\n").encode(), timeout=2000)
        status, _, _ = self._read_message(idle_ms=2000)
        if status.is_error:
            raise NrpError(f"{cmd!r} -> {status}")

    def ask(self, cmd, idle_ms=1500, max_ms=15000):
        """Query returning text (str) or, for numeric queries, a float."""
        self.dev.write(EP_OUT, (cmd.rstrip("\n") + "\n").encode(), timeout=2000)
        _, text, floats = self._read_message(idle_ms=idle_ms, max_ms=max_ms)
        if floats and not text:
            return floats[0]
        return text

    # ---- high level ----------------------------------------------------
    def idn(self):
        return self.ask("*IDN?")

    def info(self):
        """Parse SYSTEM:INFO? into a dict (cal dates, power/freq range, etc.)."""
        d = {}
        for line in self.ask("SYSTEM:INFO?").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                d[k.strip()] = v.strip()
        return d

    _flim = None

    def freq_limits(self):
        """(min_hz, max_hz) of the sensor's calibrated frequency range."""
        if self._flim is None:
            i = self.info()
            self._flim = (float(i["MinFreq"]), float(i["MaxFreq"]))
        return self._flim

    def selftest(self):
        return self.ask("TEST:SENS?")

    def zero(self):
        """Zero the sensor. The RF input must be quiet (below the auto-zero
        threshold, ~ -30 dBm). Blocks until the ~4s calibration completes and
        raises if the sensor rejects it (status 0x04 = CALZERO, i.e. too much
        power at the input to zero)."""
        self.dev.write(EP_OUT, b"CALibration:ZERO:AUTO ONCE\n", timeout=2000)
        status, _, _ = self._read_message(idle_ms=2500, max_ms=20000)
        self._drain()
        if status.is_error:
            raise NrpError(
                f"zero rejected ({status}); disconnect or "
                "terminate the RF input (needs < ~-30 dBm) and retry"
            )

    def measure_power(self, freq_hz=1e9, count=4, timeout=8000, offset_dB=0, check_freq=True):
        """One-shot average-power measurement. Returns calibrated power in watts
        (signed float32 -- near the noise floor with no signal it can read
        slightly negative, which is normal). `freq_hz` sets the calibration
        frequency: the sensor applies its stored cal-factor at this frequency,
        so pass the actual carrier frequency for a calibrated reading. `count`
        is the averaging factor; integration time = 2 * aperture * count."""
        if check_freq:
            lo, hi = self.freq_limits()
            if not (lo <= freq_hz <= hi):
                raise NrpError(
                    f"freq {freq_hz:g} Hz outside calibrated range "
                    f"{lo:g}-{hi:g} Hz; reading would be uncalibrated"
                )
        for c in (
            "*RST",
            'SENSe:FUNCtion "POWer:AVG"',
            f"SENSe:FREQuency {freq_hz:g}",
            "SENSe:AVERage:COUNt:AUTO OFF",
            f"SENSe:AVERage:COUNt {count}",
            "SENSe:AVERage:TCONtrol REPeat",  # single-shot: clear+refill the
            "SENSe:AVERage:STATe ON",  # filter, so integration = count windows
            f"SENSe:CORRection:OFFSet {offset_dB:g}",
            f"SENSe:CORRection:OFFSet:STATe ON",
            "INITiate:CONTinuous OFF",
            "TRIGger:SOURce IMMediate",
        ):
            self.write(c)
        # INIT: ack is 'Z'+'R'; the result is pushed afterwards as an 'E' record.
        self.dev.write(EP_OUT, b"INITiate:IMMediate\n", timeout=2000)

        deadline = time.monotonic() + timeout / 1000.0
        last_error = None # avoid repeating warnings
        while time.monotonic() < deadline:
            try:
                rec = self._rec(1000)
            except usb.core.USBError:
                continue
            if len(rec) < 8:
                continue

            result = decode_record(rec)
            if result.type == NrpRtype.STILL_ALIVE:
                continue
            if result.status.is_fatal:
                raise NrpError(f"fatal error: {result.status}")
            elif result.status.is_error and result.status != last_error:
                last_error = result.status
                print(f"WARNING: non-fatal error {result.status}", file=sys.stderr)

            if result.type == NrpRtype.RESULT and result.payload.values:
                self._drain()
                return result.payload.values[0]
        raise NrpError("measurement timed out (no result pushed)")


HELP = """\
nrpz - native Linux driver for R&S NRP-Z USB power sensors

usage:
  nrpz [idn]                  print sensor identity (*IDN?)  [default]
  nrpz power <freq_hz> [--zero]
                              average-power measurement at <freq_hz> Hz;
                              --zero runs a zero calibration first
  nrpz test                   run the sensor self-test
  nrpz '<SCPI>'               send raw SCPI; queries (with ?) print the reply
  nrpz -h | --help            show this help

examples:
  nrpz
  nrpz power 2.4e9 --zero
  nrpz 'SENS:FREQ?'
"""

def _argument_parser():
    parser = argparse.ArgumentParser(
        prog="nrpz",
        description="Native Linux driver for R&S NRP-Z USB power sensors",
        epilog="examples:\n  nrpz\n  nrpz power 2.4e9 --zero\n  nrpz 'SENS:FREQ?'",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("idn", aliases=["id"], help="print sensor identity (*IDN?)")
    commands.add_parser("test", aliases=["selftest"], help="run the sensor self-test")

    power = commands.add_parser(
        "power", aliases=["meas", "measure"], help="measure average power"
    )
    power.add_argument("freq_hz", type=float, nargs="?", default=1e9, help="frequency in Hz")
    power.add_argument("--offset", "-o", type=float, default=0, help="external offset in dB")
    power.add_argument("--zero", action="store_true", help="run zero calibration first")

    raw = commands.add_parser("raw", help=argparse.SUPPRESS)
    raw.add_argument("scpi", nargs="+", metavar="SCPI")
    return parser


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "help":
        args[0] = "--help"

    commands = {"id", "idn", "test", "selftest", "power", "meas", "measure", "raw"}
    if args and args[0] not in commands and args[0] not in ("-h", "--help"):
        args.insert(0, "raw")

    try:
        parsed = _argument_parser().parse_args(args)
    except SystemExit as exc:
        return exc.code

    try:
        with NrpZ() as s:
            if parsed.command is None or parsed.command in ("id", "idn"):
                print(s.idn())
            elif parsed.command in ("test", "selftest"):
                print(s.selftest())
            elif parsed.command in ("power", "meas", "measure"):
                if parsed.zero:
                    try:
                        s.zero()
                    except NrpError as e:
                        print(f"warning: {e}", file=sys.stderr)
                w = s.measure_power(parsed.freq_hz, offset_dB=parsed.offset)
                hw, uw = humanize(w)
                print(f"{hw:.3f} {uw}W    {dbm(w):.2f} dBm")
            else:  # raw SCPI passthrough
                for c in parsed.scpi:
                    if "?" in c:
                        print(s.ask(c))
                    else:
                        s.write(c)
                        print(f"{c} (sent)")

    except NrpError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
