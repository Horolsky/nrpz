"""Hardware-free tests for the framing parser and helpers.

The record vectors below are real bytes captured from NRP-Z11 sensors answering
SCPI queries and producing pushed measurement results, so these are genuine
regression tests of the observed wire protocol.
Run with `pytest`, or directly: `python tests/test_nrpz.py`.
"""

from nrpz import NrpZ, dbm, decode_message, main

# Real *IDN? response: four 'T' text records (offset in bytes[4:6]) + 'R' end.
IDN_RECORDS = [
    bytes.fromhex("54000b030000524f4844452653434857"),  # off 0  "ROHDE&SCHW"
    bytes.fromhex("54000b030a0041525a2c4e52502d5a31"),  # off 10 "ARZ,NRP-Z1"
    bytes.fromhex("54000b031400312c3130313639392c30"),  # off 20 "1,101699,0"
    bytes.fromhex("54000b031e00342e3136000000000000"),  # off 30 "4.16"
    bytes.fromhex("52000b03000000000000000000000000"),  # 'R' end, status 0
]

# Real SENS:FREQ? response: one 'L' float record (1e9) + 'R' end.
FREQ_RECORDS = [
    bytes.fromhex("4c000309286b6e4e0000000000000000"),  # float32 LE 0x4e6e6b28 = 1e9
    bytes.fromhex("52000309000000000000000000000000"),
]

# Real records from NRP-Z11 / firmware 04.16.
#
# Important observation: byte[1] == 0x09 occurs on valid data/state records
# ('T', 'L', 'N', 'Z', 'E'). It must therefore not be interpreted generically
# as a standalone error/status byte for those record types.
FREQ_RECORDS_BYTE1_09 = [
    bytes.fromhex("4c09030920bcbe4c0000000000000000"),  # 100e6 float32
    bytes.fromhex("52000309000000000000000000000000"),
]

# Real SENS:RANG? response. 'N' carries a little-endian int32 at bytes[4:8].
RANGE_RECORDS_N = [
    bytes.fromhex("4e09030c020000000000000000000000"),  # integer 2
    bytes.fromhex("5200030c000000000000000000000000"),
]

# Abbreviated real *TST? response: keepalives, integer result, final ack.
SELFTEST_RECORDS_N = [
    bytes.fromhex("7a000000000000000000000000000000"),
    bytes.fromhex("7a000000000000000000000000000000"),
    bytes.fromhex("4e090b04010000000000000000000000"),  # integer 1
    bytes.fromhex("52000b04000000000000000000000000"),
]

# Real pushed average-power transaction at 100 MHz, range 0, averaging 16,
# with TinySA Ultra+ enabled at nominal -30 dBm.
# Sequence: Z(wait) -> R(ack) -> Z(measuring) -> z/z/z -> E(result) -> Z(idle).
# E payload = 7.776138204462768e-07 W (~ -31.092 dBm).
MEASUREMENT_RECORDS_MINUS_30_DBM = [
    bytes.fromhex("5a090200000000000000000000000000"),
    bytes.fromhex("52000903000000000000000000000000"),
    bytes.fromhex("5a090300000000000000000000000000"),
    bytes.fromhex("7a3f0300000000000000000000000000"),
    bytes.fromhex("7a8f0300000000000000000000000000"),
    bytes.fromhex("7adf0300000000000000000000000000"),
    bytes.fromhex("4509030037bd50350000000000000000"),
    bytes.fromhex("5a090000000000000000000000000000"),
]


def test_text_reassembly():
    status, text, floats = decode_message(IDN_RECORDS)
    assert status == 0
    assert text == "ROHDE&SCHWARZ,NRP-Z11,101699,04.16"
    assert floats == []


def test_numeric_query():
    status, text, floats = decode_message(FREQ_RECORDS)
    assert status == 0
    assert text == ""
    assert len(floats) == 1
    assert abs(floats[0] - 1e9) < 1.0  # exact float32 for 1e9




def test_data_record_byte1_09_is_not_query_error():
    status, text, floats = decode_message(FREQ_RECORDS_BYTE1_09)
    assert status == 0
    assert text == ""
    assert len(floats) == 1
    assert abs(floats[0] - 100e6) < 1.0


def test_integer_n_record():
    status, text, values = decode_message(RANGE_RECORDS_N)
    assert status == 0
    assert text == ""
    assert values == [2]


def test_integer_n_record_after_keepalives():
    status, text, values = decode_message(SELFTEST_RECORDS_N)
    assert status == 0
    assert text == ""
    assert values == [1]


def test_error_status_byte():
    # 'R' with a non-zero status (0x89 = unknown command) must surface.
    recs = [bytes.fromhex("52890000000000000000000000000000")]
    status, _, _ = decode_message(recs)
    assert status == 0x89




class _FakeDev:
    def __init__(self):
        self.writes = []

    def write(self, endpoint, data, timeout=None):
        self.writes.append((endpoint, bytes(data), timeout))
        return len(data)


def _sensor_for_pushed_records(records):
    sensor = NrpZ.__new__(NrpZ)
    sensor.dev = _FakeDev()
    queue = list(records)
    sensor.write = lambda cmd: None
    sensor._drain = lambda: None
    sensor._rec = lambda timeout_ms: queue.pop(0)
    return sensor


def test_measure_power_accepts_captured_e_record_byte1_09():
    sensor = _sensor_for_pushed_records(MEASUREMENT_RECORDS_MINUS_30_DBM)
    w = sensor.measure_power(
        100e6,
        count=16,
        timeout=2000,
        check_freq=False,
    )
    assert abs(w - 7.776138204462768e-7) < 1e-15


def test_measurement_fixture_contains_normal_init_ack():
    init_ack = MEASUREMENT_RECORDS_MINUS_30_DBM[1]
    result = MEASUREMENT_RECORDS_MINUS_30_DBM[6]
    assert init_ack[0] == ord("R")
    assert init_ack[1] == 0x00
    assert result[0] == ord("E")
    assert result[1] == 0x09


def test_help_no_device():
    # -h / --help must work without a sensor connected (no device access).
    assert main(["-h"]) == 0
    assert main(["--help"]) == 0
    assert main(["help"]) == 0


def test_dbm():
    assert abs(dbm(1e-3) - 0.0) < 1e-9  # 1 mW == 0 dBm
    assert abs(dbm(1.0) - 30.0) < 1e-9  # 1 W  == +30 dBm
    assert dbm(0.0) == float("-inf")
    assert dbm(-1e-9) == float("-inf")  # negative (below zero) -> -inf


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
