from enum import IntEnum


class NrpStatus(IntEnum):
    """
    NRP Sensor status error codes
    Source: NrpControl2.h (Declarations for the NRP Control Library)
    """

    SUCCESS             = 0x00
    CALDATA_FORMAT      = 0x01
    OVERRANGE           = 0x02, "A/D limit reached, result may be wrong"
    NOTINSERVICEMODE    = 0x03
    CALZERO             = 0x04
    TRIGGERQUEUEFULL    = 0x05
    EVENTQUEUEFULL      = 0x06
    SAMPLEERROR         = 0x07
    OVERLOAD            = 0x08, "reduce RF input power immediately"
    HARDWARE            = 0x09
    CHECKSUM            = 0x0a
    ILLEGALSERIAL       = 0x0b
    FILTERTRUNCATED     = 0x0c
    BURST_TOO_SHORT     = 0x0d
    COMMUNICATION_ERROR = 0x0e
    RESULT_QUESTIONABLE = 0x40, "disrupted USB transfer; re-measure"

    # fatal errors
    GENERIC             = 0x80
    OVERMAX             = 0x81
    UNDERMIN            = 0x82
    VOLTAGE             = 0x83, "+/-5 Volt not correct"
    SYNTAX              = 0x84
    MEMORY              = 0x85
    PARAMETER           = 0x86
    TIMING              = 0x87
    NOTIDLE             = 0x88
    UNKNOWNCOMMAND      = 0x89
    OUTBUFFERFULL       = 0x8a
    FLASHPROG           = 0x8b
    CALDATANOTPRESENT   = 0x8c

    def __new__(cls, value, description=None):
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj._description = description
        return obj

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, int):
            new_member = int.__new__(cls, value)
            new_member._name_ = "UNKNOWN"
            new_member._value_ = value
            new_member._description = None
            cls._value2member_map_[value] = new_member
            return new_member

    def __str__(self):
        description = f"; {self._description}" if self._description else ""
        return f"<{self.name} ({self.value:#x}){description}>"

    @property
    def is_error(self) -> bool:
        return self is not NrpStatus.SUCCESS

    @property
    def is_fatal(self) -> bool:
        return bool(NrpStatus.GENERIC.value & self.value)
