import datetime
from collections import defaultdict
from utils.datetimes_utils import get_global_timezone

from enum import Enum
class ReservationStatus(Enum):
    CONFIRMED_STATUS = 'confirmed'
    PENDING_CONFIRMATION_STATUS = 'book_noconfirm'
    PENDING_CANCELATION_STATUS = 'delete_noconfirm'
    PENDING_UPDATE_STATUS = 'update_noconfirm'
    DELETED_STATUS = 'deleted'

    
class Reservation:
    def __init__(self, reservation_id: str, user: str, start_time: datetime.datetime, end_time: datetime.datetime, service_name: str, status: ReservationStatus = None, expires_at: datetime.datetime=None):
        object.__setattr__(self, 'timestamp', datetime.datetime.now(tz=get_global_timezone()) )
        object.__setattr__(self, 'reservation_id', reservation_id)
        object.__setattr__(self, 'user', user)
        object.__setattr__(self, 'start_time', start_time)
        object.__setattr__(self, 'end_time', end_time)
        object.__setattr__(self, 'service_name', service_name)
        self.status = status
        self._expires_at = expires_at
        self.is_confirmed = True if not expires_at else False

    def mark_as_pending_confirmation(self, expires_at: datetime.datetime = None):
        self.status = ReservationStatus.PENDING_CONFIRMATION_STATUS
        self.is_confirmed = False
        self._expires_at = expires_at
    
    def mark_as_confirmed(self):
        self.status = ReservationStatus.CONFIRMED_STATUS
        self.is_confirmed = True
        self._expires_at = None
        
    def mark_as_pending_update(self, updated_reservation: 'Reservation', expires_at: datetime.datetime = None, ):
        self.status = ReservationStatus.PENDING_UPDATE_STATUS
        self.__update_reservation__ = updated_reservation
        if isinstance(updated_reservation, Reservation):
            self.__update_reservation__.mark_as_pending_confirmation(expires_at)
        
    def mark_as_confirmed_update(self, updated_reservation: 'Reservation' = None):
        if updated_reservation is not None:
            self.__update_reservation__ = updated_reservation
        if not isinstance (self.__update_reservation__, Reservation):
            raise ValueError('Cannot confirm. No valid update reservation.')
        self.mark_as_deleted()
        self.__update_reservation__.mark_as_confirmed()
        
    def mark_as_pending_delete(self, expires_at: datetime.datetime = None):
        self.status = ReservationStatus.PENDING_CANCELATION_STATUS
        self._expires_at = expires_at
        
    def mark_as_deleted(self):
        self.status = ReservationStatus.DELETED_STATUS
        self._expires_at = None
        
    def is_confirmation_expired(self, now=None):
        expiry = self.get_pending_status_expiration()
        if expiry is None:
            return False
        
        if now is None:
            now = datetime.datetime.now(tz=expiry.tzinfo)
        return now > expiry

    def get_associated_update_reservation(self):
        return getattr(self, '__update_reservation__', None)
        
    def get_pending_status_expiration(self):
        return getattr(self, '_expires_at', None)
        
    def pop_associated_update_reservation(self):
        associated_res = self.get_associated_update_reservation()
        if hasattr(self, '__update_reservation__'):            
            delattr(self, '__update_reservation__')
        return associated_res

    def __setattr__(self, attribute, value):
        if attribute in ['reservation_id', 'user', 'start_time', 'end_time', 'service_name', 'timestamp']:
            raise ValueError(f'Cannot set attribute {attribute}. It is final')
        if attribute=='status':
            self.status_change_timestamp = datetime.datetime.now(tz=get_global_timezone())
        return object.__setattr__(self, attribute, value)

    def to_dict(self):
        obj_dict = self.__dict__.copy()
        for attr in obj_dict:
            if isinstance(obj_dict[attr], Reservation):
                obj_dict[attr] = getattr(self, attr).to_dict()
        return obj_dict
        
    def copy(self):
        import copy
        return copy.copy(self)

    def __repr__(self):
        self_dct = self.to_dict()
        rep_str = f"reservation_id = {self.reservation_id} - " if self.reservation_id else ""
        rep_str += ' - '.join(f'{k} = {v}' for k,v in self_dct.items() if k in ['service_name', 'user'])
        rep_str += f". From {self_dct['start_time']} to {self_dct['end_time']}" 
        if (expiry_t := getattr(self, '_expires_at', False)):
            rep_str += f" Time limit to confirm: {expiry_t}"
        return rep_str
        
    def __eq__(self, other):
        if not isinstance(other, Reservation):
            return False
        return all(getattr(self, attribute)==getattr(other, attribute) for attribute in ['reservation_id', 'user', 'service_name', 'start_time', 'end_time', 'status', 'timestamp'])

        
import asyncio
from collections import defaultdict

class ReservationManager:

    def __init__(self):
        self.reservations_id_mappings = {}
        self.reservations_by_user = defaultdict(set)
        self.reservations_by_date = defaultdict(lambda: defaultdict(set))

        self._date_locks = defaultdict(asyncio.Lock)
        self._user_locks = defaultdict(asyncio.Lock)
        

    async def insert_reservation(self, reservation):
        if reservation.reservation_id in self.reservations_id_mappings:
            raise KeyError('Reservation id already existing')
        
        res_date = reservation.start_time.date()

        date_lock = self._date_locks[res_date]
        user_lock = self._user_locks[reservation.user]
        async with date_lock:
            async with user_lock:
                self.reservations_id_mappings[reservation.reservation_id] = reservation
                self.reservations_by_user[reservation.user].add(reservation.reservation_id)
                self.reservations_by_date[res_date][reservation.start_time].add(reservation.reservation_id)

        return reservation

    async def remove_reservation(self, reservation_id: str):
        reservation = self.reservations_id_mappings.get(reservation_id)
        if reservation is None:
            raise KeyError(f'Non existing reservation id: {reservation_id}')

        res_date = reservation.start_time.date()

        date_lock = self._date_locks[res_date]
        user_lock = self._user_locks[reservation.user]
        async with date_lock:
            async with user_lock:
                reservation = self.reservations_id_mappings.pop(reservation_id, None)
                if reservation is None:
                    raise KeyError(f'Non existing reservation id: {reservation_id}')

                daily_reservations = self.reservations_by_date[res_date]
                user_reservations = self.reservations_by_user[reservation.user]
                
                daily_reservations[reservation.start_time].remove(reservation_id)
                user_reservations.remove(reservation_id)

                if not daily_reservations[reservation.start_time]:
                    del daily_reservations[reservation.start_time]
                if not daily_reservations:
                    del self.reservations_by_date[res_date]
                    del self._date_locks[res_date]
                if not user_reservations:
                    del self.reservations_by_user[reservation.user]
                    del self._user_locks[reservation.user]

        return reservation

    
    def get_reservations_by_user(self, user: str) -> list[Reservation]:
        reservation_ids = self.reservations_by_user.get(user, [])
        return [reservation for res_id,reservation in self.reservations_id_mappings.items() if res_id in reservation_ids]
    
    def get_reservations_by_date(self, date: datetime.date) -> list[Reservation]:
        reservation_ids = self.reservations_by_date.get(date, {}).values()
        return [reservation for res_id,reservation in self.reservations_id_mappings.items() if res_id in reservation_ids]

    def get_reservations_by_start_time(self, start_time: datetime.datetime) -> Reservation:
        date = start_time.date()
        daily_reservations = self.reservations_by_date.get(date, {})
        reservation_ids = daily_reservations.get(start_time, [])
        return [self.get_reservation(res_id) for res_id in reservation_ids]

    def get_reservation(self, reservation_id: str) -> Reservation:
        return self.reservations_id_mappings.get(reservation_id, None)

    def get_all_reservation_ids(self):
        return list(self.reservations_id_mappings.keys())

    def _find_reservations_by_inner_time(self, inner_time: datetime.datetime) -> Reservation:
        from bisect import bisect_right
        daily_reservations = sorted(self.reservations_by_date.get(inner_time.date(), {}).items(), key=lambda x: x[0])
        matching_index = bisect_right(daily_reservations, inner_time, key=lambda x: x[0]) - 1
        matched_reservations = []
        if matching_index>=0:
            potential_match_ids = daily_reservations[matching_index][1]
            potential_match_reservations = [self.get_reservation(res_id) for res_id in potential_match_ids]
            for reserv in potential_match_reservations:
                if reserv.start_time <= inner_time < reserv.end_time:
                    matched_reservations.append(reserv)
        return matched_reservations
        

def generate_new_reservation_id():
    import uuid
    return str(uuid.uuid4())      

      
def is_overlapping(res1: Reservation, res2: Reservation):
    return (
        res1.start_time < res2.end_time
        and res2.start_time < res1.end_time
    )