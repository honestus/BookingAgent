from backend import domain_logic
from backend.reservations import ReservationStatus
from application.snapshots import ReservationSnapshot, ServiceSnapshot, BusinessCalendarSnapshot
from utils.rw_lock import RWLock, AsyncLockWrapper, NoLock, LockMode
import asyncio, datetime
from dataclasses import dataclass

@dataclass
class UserDataState:
    has_any_data: bool
    has_confirmed_data: bool
    has_pending_adds: bool
    has_pending_cancelations: bool
    has_pending_updates: bool
    has_unconfirmed_expired_data: bool

class UserCache:
    def __init__(self, reservations: list[ReservationSnapshot], use_lock: bool = True):
        self.reservations = {}
        for r in reservations:
            self.reservations[r.reservation_id] = r
        self._lock = AsyncLockWrapper() if use_lock else NoLock()
        
    async def upsert_reservation(self, reservation: ReservationSnapshot):
        async with self._lock.get_lock():
            self.reservations[reservation.reservation_id] = reservation
        
    async def remove_reservation(self, reservation_id: str):
        async with self._lock.get_lock():
            if reservation_id in self.reservations:
                self.reservations.pop(reservation_id)
                return True
            return False

    async def get_confirmed_reservations(self) -> list[ReservationSnapshot]:
        async with self._lock.get_lock():
            return [r for r in self.reservations.values() if domain_logic.is_reservation_confirmed_nopending(r)]
            
            
    async def get_pending_reservations(self) -> list[ReservationSnapshot]:
        async with self._lock.get_lock():
            return [r for r in self.reservations.values() if domain_logic.is_reservation_pending_confirmation(r)]

            
    async def get_pending_cancellations(self) -> list[ReservationSnapshot]:
        async with self._lock.get_lock():
            return [r for r in self.reservations.values() if domain_logic.is_reservation_pending_cancelation(r)]
    
    async def get_pending_updates(self) -> list[tuple[ReservationSnapshot, ReservationSnapshot]]:
        async with self._lock.get_lock():
            return [r for r in self.reservations.values() if domain_logic.is_reservation_pending_update(r)]
    
    async def get_all_active_reservations(self, future_only: bool = True) -> list[ReservationSnapshot]:
        async with self._lock.get_lock():
            active_reservations = [r for r in self.reservations.values() if domain_logic.is_reservation_active(r) and (not future_only or not domain_logic.is_past_reservation(r))]
            return active_reservations
            
    async def get_all_expired_unconfirmed_reservations(self, future_only: bool = True) -> list[ReservationSnapshot]:
        async with self._lock.get_lock():
            expired_reservations = [r for r in self.reservations.values() if domain_logic.is_reservation_confirmation_expired(r) and (not future_only or not domain_logic.is_past_reservation(r))]
        return expired_reservations
            
    async def get_reservations_state(self) -> UserDataState:
        
        any_active: bool = False
        any_confirmed: bool = False
        any_pending_confirmation: bool = False
        any_pending_cancelation: bool = False
        any_pending_update: bool = False
        any_expired: bool = False
        async with self._lock.get_lock():
            empty: bool = not bool(self.reservations)
            
            for r in self.reservations.values():
                if domain_logic.is_reservation_confirmation_expired(r):
                    any_expired = True
                    continue
                if not domain_logic.is_reservation_active(r):
                    continue
                any_active=True
                if domain_logic.is_reservation_confirmed_nopending(r):
                    any_confirmed=True
                    continue
                if domain_logic.is_reservation_pending_confirmation(r):
                    any_pending_confirmation=True
                    continue
                if domain_logic.is_reservation_pending_cancelation(r):
                    any_pending_cancelation=True
                    continue
                if domain_logic.is_reservation_pending_update(r):
                    any_pending_update=True
                    continue
        return UserDataState(has_any_data=any_active, has_confirmed_data=any_confirmed, has_pending_adds=any_pending_confirmation, has_pending_cancelations=any_pending_cancelation, has_pending_updates=any_pending_update, has_unconfirmed_expired_data=any_expired)

    async def set_reservations(self, reservations: list[ReservationSnapshot]):
        async with self._lock.get_lock():
            self.reservations = {}
            for r in reservations:
                self.reservations[r.reservation_id] = r

          
        
class SystemCache:
    def __init__(self, services: list[ServiceSnapshot], opening_hours: list[tuple[datetime.datetime, datetime.datetime]], user_caches: dict[str, UserCache] = None, use_lock: bool = True):
        self.services = dict()
        for s in services:
            self.services[s.service_name] = s
        self.opening_hours = list(opening_hours)
        self._user_caches = dict() if user_caches is None else dict(user_caches) 
        
        self._lock = RWLock() if use_lock else NoLock()
        
        
    def get_user_cache(self, user_id: str) -> UserCache:
        if user_id not in self._user_caches:
            return None
        return self._user_caches[user_id]
        
    def set_user_cache(self, user_id: str, cache: UserCache):
        if user_id in self._user_caches:
            raise ValueError('User_id already exists.')
        self._user_caches[user_id] = cache
        
    async def get_services(self) -> list[ServiceSnapshot]:
        async with self._lock.get_lock(LockMode.READ):
            return list(self.services.values())
        
        
    async def get_opening_hours(self) -> list[tuple[datetime.datetime, datetime.datetime]]:
        async with self._lock.get_lock(LockMode.READ):
            return list(self.opening_hours)
        
    async def set_services(self, services: list[ServiceSnapshot]):
        async with self._lock.get_lock(LockMode.WRITE):
            self.services = {}
            for s in services:
                self.services[s.service_name] = s
        
    async def set_opening_hours(self, opening_hours: list[tuple[datetime.datetime, datetime.datetime]]):
        async with self._lock.get_lock(LockMode.WRITE):
            self.opening_hours = opening_hours
            
    async def upsert_service(self, service: ServiceSnapshot):
        async with self._lock.get_lock(LockMode.WRITE):
            self.services[service.service_name] = service
        
    async def remove_service(self, service_name: str):
        async with self._lock.get_lock(LockMode.WRITE):
            if service_name in self.services:
                self.services.pop(service_name)
                return True
            return False
        
    async def get_prompt_context(self, user_id: str) -> dict[str, "Any"]:
        uc = self.get_user_cache(user_id)
        if uc is None:
            raise KeyError(f'No data for the user {user_id}')
        user_reservations = {'Confirmed': await uc.get_confirmed_reservations(),
                             'Unconfirmed - pending_for_confirmation': await uc.get_pending_reservations(),
                             'Pending_for_cancelation': await uc.get_pending_cancellations(),
                             'Pending_for_update': await uc.get_pending_updates(),
                             'Unconfirmed - expired': await uc.get_all_expired_unconfirmed_reservations(),
                             }
        return {
            "reservations": {k:v for k,v in user_reservations.items() if bool(v)},
            "services": await self.get_services(),
            "opening_hours": await self.get_opening_hours()
        }