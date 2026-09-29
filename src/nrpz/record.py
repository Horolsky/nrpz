from dataclasses import dataclass
from typing import Optional, Tuple, Union

from nrpz.enums import NrpDataType, NrpRtype, NrpStatus, NrpTriggerState


Number = Union[int, float]


@dataclass(frozen=True)
class NrpValues:
    values: Tuple[Number, Number, Number]


@dataclass(frozen=True)
class NrpText:
    text_offset: int
    text_fragment: bytes


@dataclass(frozen=True)
class NrpCommand:
    group_param: int


@dataclass(frozen=True)
class NrpState:
    state: NrpTriggerState


@dataclass(frozen=True)
class NrpStillAlive:
    marker: int
    state: NrpTriggerState


NrpPayload = Union[
    NrpValues,
    NrpText,
    NrpCommand,
    NrpState,
    NrpStillAlive,
]


@dataclass(frozen=True)
class NrpRecord:
    type: NrpRtype
    status: Optional[NrpStatus]
    group: Optional[int]
    parameter: Optional[int]
    payload: NrpPayload
    raw: bytes
