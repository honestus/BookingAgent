from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


# =========================================================
# SNAPSHOTS
# =========================================================


# =========================================================
# CALENDAR OBJECTS (Slot, Segment, BusinessCalendar)
# =========================================================

@dataclass(frozen=True, slots=True)
class SlotSnapshot:
    """
    Snapshot semanticamente stabile dello slot.

    NOTA:
    - is_booked viene salvato come valore già valutato
      (quindi NON dipende più da _clear()).
    """

    start_time: datetime

    is_booked: bool
    booking_expires_at: datetime | None

    def __repr__(self):
        status = "Booked" if self.is_booked else "Free"
        return f"<SlotSnapshot {self.start_time} - {status}>"


@dataclass(frozen=True, slots=True)
class SegmentSnapshot:
    start_time: datetime
    end_time: datetime
    slots: tuple[SlotSnapshot, ...]

    def __repr__(self):
        return (
            f"<Segment - from {self.start_time} to {self.end_time}. Contains {len(self.slots)} slots>"
        )
        
    def to_str(self, deep: bool=False):
        if deep:
            return self.__repr__()
        return f"Segment from {self.start_time} to {self.end_time}."


@dataclass(frozen=True, slots=True)
class BusinessCalendarSnapshot:
    slot_minutes_duration: int
    segments: tuple[SegmentSnapshot, ...]

    def __repr__(self):
        inner_segments_repr = f"{'\n'.join([s.to_str(deep=False) for s in self.segments])}" if bool(self.segments) else "[]"
        return (
            f"BusinessCalendar - slot_minutes_duration={self.slot_minutes_duration}.\nContains the following shifts (i.e. opening hours): \n{inner_segments_repr}"
        )

# =========================================================
# SERVICE
# =========================================================

@dataclass(frozen=True, slots=True)
class ServiceSnapshot:
    service_name: str
    price: float
    minutes_duration: int
    description: str | None = None

    def __repr__(self):
        return (f"Service: {self.service_name}. Price: {self.price}. Duration: {self.minutes_duration} mins."+f" Description: {self.description}." if self.description else "")
        
    def to_dict(self):
        return self.__dict__


# =========================================================
# RESERVATION
# =========================================================

@dataclass(frozen=True, slots=True)
class ReservationSnapshot:
    timestamp: datetime
    reservation_id: str
    user: str
    service_name: str
    start_time: datetime
    end_time: datetime
    status: ReservationStatus
    is_confirmed: bool
    expires_at: datetime | None
    inner_update_reservation: Reservation | None

    def is_confirmation_expired(self, now: datetime = None) -> bool:
        if self.expires_at is None:
            return False
        if now is None:
            now = datetime.now(tz=self.expires_at.tzinfo)
        return now > self.expires_at
        
        
    def get_associated_update_reservation(self):
        return getattr(self, 'inner_update_reservation', None)

    def to_dict(self):
        return self.__dict__

    def __repr__(self):
        rep_str = (f"reservation_id = {self.reservation_id} - " if self.reservation_id else "")
        rep_str += f"service_name = {self.service_name} - user = {self.user}"
        rep_str += f". From {self.start_time} to {self.end_time}"
        if (expiry_t := getattr(self, 'expires_at', False)):
            rep_str += f" -- Time limit to confirm: {expiry_t}"
        return rep_str
        return rep_str

# =========================================================
# MAPPERS
# =========================================================

def _service_to_snapshot(service) -> ServiceSnapshot:
    return ServiceSnapshot(
        service_name=service.service_name,
        minutes_duration=service.minutes_duration,
        price=service.price,
        description=service.description,
    )


def _slot_to_snapshot(slot) -> SlotSnapshot:
    return SlotSnapshot(
        start_time=slot.start_time,
        is_booked=slot.is_booked(),
        booking_expires_at=slot._booking_expires_at
    )


def _segment_to_snapshot(segment) -> SegmentSnapshot:
    return SegmentSnapshot(
        start_time=segment.start_time,
        end_time=segment.end_time,
        slots=tuple(
            _slot_to_snapshot(slot)
            for slot in segment.slots
        ),
    )


def _calendar_to_snapshot(calendar) -> BusinessCalendarSnapshot:
    return BusinessCalendarSnapshot(
        slot_minutes_duration=calendar.slot_minutes_duration,
        segments=tuple(
            _segment_to_snapshot(segment)
            for segment in calendar.segments
        ),
    )


def _reservation_to_snapshot(reservation) -> ReservationSnapshot:
    inner_upd = reservation.get_associated_update_reservation()
    inner_upd_snapshot = _reservation_to_snapshot(inner_upd) if isinstance(inner_upd, Reservation) else map_object_to_snapshot(inner_upd)
    return ReservationSnapshot(
        timestamp=reservation.timestamp,
        reservation_id=reservation.reservation_id,
        user=reservation.user,
        start_time=reservation.start_time,
        end_time=reservation.end_time,
        service_name=reservation.service_name,
        status=reservation.status,
        is_confirmed=reservation.is_confirmed,
        expires_at=reservation._expires_at,
        inner_update_reservation=inner_upd_snapshot,
    )


# =========================================================
# REGISTRY
# =========================================================

from backend.reservations import Reservation
from backend.policy import Service
from backend.business_calendar import BusinessCalendar, Segment, Slot

_SNAPSHOT_MAPPERS = {
    Reservation: _reservation_to_snapshot,
    Service: _service_to_snapshot,
    BusinessCalendar: _calendar_to_snapshot,
    Segment: _segment_to_snapshot,
    Slot: _slot_to_snapshot,
}


# =========================================================
# GENERIC SNAPSHOT CONVERTER
# =========================================================

def map_object_to_snapshot(obj: Any) -> Any:
    from copy import deepcopy
    """
    Converts domain objects into immutable snapshots.

    Rules:
    - known domain objects -> dedicated snapshot
    - list/tuple/set -> recursive conversion
    - dict -> recursive conversion
    - primitive immutable types -> returned as-is
    - fallback -> deepcopy
    """

    if obj is None:
        return None
        
    if type(obj) in _SNAPSHOT_MAPPERS:
        ##custom objects mapping
        mapper_fn = _SNAPSHOT_MAPPERS[type(obj)]
        return mapper_fn(obj)

    # Immutable primitive types
    if isinstance(obj, (str, int, float, bool, bytes)):
        return obj

    # Datetime already immutable enough
    if isinstance(obj, datetime):
        return obj

    # Collections
    if isinstance(obj, list):
        return [map_object_to_snapshot(x) for x in obj]

    if isinstance(obj, tuple):
        return tuple(map_object_to_snapshot(x) for x in obj)

    if isinstance(obj, set):
        return frozenset(map_object_to_snapshot(x) for x in obj)

    if isinstance(obj, dict):
        return {
            map_object_to_snapshot(k): map_object_to_snapshot(v)
            for k, v in obj.items()
        }

        return mapper(obj)

    # Last resort fallback
    return deepcopy(obj)