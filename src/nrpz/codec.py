import struct

from nrpz.enums import (
    NrpRtype,
    NrpStatus,
    NrpTriggerState,
)
from nrpz.record import (
    NrpCommand,
    NrpRecord,
    NrpState,
    NrpStillAlive,
    NrpText,
    NrpValues,
)

RECORD_SIZE = 16


def decode_record(record: bytes) -> NrpRecord:
    "Decode one native 16-byte NRP-Z sensor record."

    if not isinstance(record, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"record must be a bytes-like object, got "
            f"{type(record).__name__}"
        )

    if len(record) != RECORD_SIZE:
        raise ValueError(
            f"NRP record must be exactly {RECORD_SIZE} bytes, "
            f"got {len(record)}"
        )

    record = bytes(record)
    rtype = NrpRtype(record[0])
    status = (
        None
        if rtype == NrpRtype.STILL_ALIVE
        else NrpStatus(record[1])
    )
    group = (
        None
        if rtype in (NrpRtype.STILL_ALIVE, NrpRtype.STATE_CHANGED)
        else record[2]
    )
    parameter = (
        None
        if rtype in (NrpRtype.STILL_ALIVE, NrpRtype.STATE_CHANGED)
        else record[3]
    )


    payload=None

    if rtype in (NrpRtype.FLOAT_PARAM,NrpRtype.FLOAT_RESULT):
        payload = NrpValues(
            values=struct.unpack_from("<fff", record, 4)
        )
    elif rtype == NrpRtype.BITFIELD_PARAM:
        # The R&S API exposes these as long *, but semantically they are
        # bitfields.
        payload=NrpValues(
            values=struct.unpack_from("<III", record, 4)
        )
    elif rtype == NrpRtype.LONG_PARAM:
        # Windows NrpControl2 uses 32-bit signed long values.
        payload=NrpValues(
            values=struct.unpack_from("<iii", record, 4)
        )
    elif rtype == NrpRtype.TEXT:
        payload = NrpText(
            text_offset=struct.unpack_from("<H", record, 4)[0],
            text_fragment=record[6:16]
        )
    elif rtype == NrpRtype.COMMAND_ACCEPTED:
        payload = NrpCommand(
            group_param=struct.unpack_from("<H", record, 2)[0]
        )
    elif rtype == NrpRtype.STATE_CHANGED:
        payload = NrpState(
            state=NrpTriggerState(record[2]),
        )
    elif rtype == NrpRtype.STILL_ALIVE:
        payload = NrpStillAlive(
            marker=record[1],
            state=NrpTriggerState(record[2])
        )
    else:
        # TODO: A/B/F/P/U/V/W recs
        pass


    return NrpRecord(
        type=rtype,
        status=status,
        group=group,
        parameter=parameter,
        payload=payload,
        raw=record
    )


def decode_message(records: list[bytes]):
    """Parse a list of 16-byte response records into (status, text, floats).

    Pure function (no I/O) so the framing logic is unit-testable without
    hardware. 'T' records are reassembled by offset into text; 'L'/'E' records
    yield float32 values; the terminating 'R' record supplies the status byte.
    """
    status_out = NrpStatus.SUCCESS
    text_out = bytearray()
    floats_out = []

    for record in records:

        record = decode_record(record)

        if record.type == NrpRtype.END:
            status_out = record.status
            break
        elif record.type == NrpRtype.TEXT:
            off = record.payload.text_offset
            if len(text_out) < off:
                text_out.extend(b"\x00" * (off - len(text_out)))
            text_out[off : off + 10] = record.payload.text_fragment

        elif record.type in (NrpRtype.PARAM, NrpRtype.RESULT, NrpRtype.INT):
            floats_out.append(record.payload.values[0])
        else:
            pass

    text_out = text_out.split(b"\x00", 1)[0].decode("latin1").strip()
    return status_out, text_out, floats_out
