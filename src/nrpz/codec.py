import struct

from nrpz.enums import NrpRtype, NrpStatus


def decode_message(records: list[bytes]):
    """Parse a list of 16-byte response records into (status, text, floats).

    Pure function (no I/O) so the framing logic is unit-testable without
    hardware. 'T' records are reassembled by offset into text; 'L'/'E' records
    yield float32 values; the terminating 'R' record supplies the status byte.
    """
    parts, floats, status = {}, [], 0
    for rec in records:
        if len(rec) < 6:
            continue
        t = NrpRtype(rec[0])
        if t == NrpRtype.END:
            status = rec[1]
            break
        elif t == NrpRtype.TEXT:
            offset = rec[4] | (rec[5] << 8)
            parts[offset] = rec[6:16]
        elif t in (NrpRtype.PARAM, NrpRtype.RESULT):
            floats.append(struct.unpack("<f", rec[4:8])[0])
        elif t == NrpRtype.INT:
            floats.append(struct.unpack("<i", rec[4:8])[0])
        else:
            pass
    out = bytearray()
    for off in sorted(parts):
        if len(out) < off:
            out.extend(b"\x00" * (off - len(out)))
        out[off : off + 10] = parts[off]
    return NrpStatus(status), out.split(b"\x00", 1)[0].decode("latin1").strip(), floats
