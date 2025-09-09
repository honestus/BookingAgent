import datetime
from collections import defaultdict

CONFIRMED_STATUS = 'confirmed'
PENDING_CONFIRMATION_STATUS = 'book_noconfirm'
PENDING_CANCELATION_STATUS = 'delete_noconfirm'
PENDING_UPDATE_STATUS = 'update_noconfirm'
DELETED_STATUS = 'deleted'
class Reservation:
    def __init__(self, reservation_id: str, user: str, start_time: datetime.datetime, end_time: datetime.datetime, service_name: str, status: str = None, is_confirmed: bool = True):
        object.__setattr__(self, 'reservation_id', reservation_id)
        object.__setattr__(self, 'user', user)
        object.__setattr__(self, 'start_time', start_time)
        object.__setattr__(self, 'end_time', end_time)
        object.__setattr__(self, 'service_name', service_name)
        object.__setattr__(self, 'status', status)
        object.__setattr__(self, 'is_confirmed', is_confirmed)
        object.__setattr__(self, 'timestamp', datetime.datetime.now())
        object.__setattr__(self, 'status_change_timestamp', self.timestamp)



    def __setattr__(self, attribute, value):
        if attribute in ['reservation_id', 'user', 'start_time', 'end_time', 'service_name', 'timestamp']:
            raise ValueError(f'Cannot set attribute {attribute}. It is final')
        if attribute=='status':
            object.__setattr__(self, 'status_change_timestamp', datetime.datetime.now())
        return object.__setattr__(self, attribute, value)

    def to_dict(self):
        return self.__dict__

    def __repr__(self):
        self_dct = self.to_dict()
        rep_str = f"reservation_id = {self.reservation_id} - " if self.reservation_id else ""
        rep_str += ' - '.join(f'{k} = {v}' for k,v in self_dct.items() if k in ['service_name', 'user'])
        rep_str += f". From {self_dct['start_time']} to {self_dct['end_time']}" 
        return rep_str
        
    def __eq__(self, other):
        if not isinstance(other, Reservation):
            return False
        return all(getattr(self, attribute)==getattr(other, attribute) for attribute in ['reservation_id', 'user', 'service_name', 'start_time', 'end_time', 'timestamp'])


class ReservationManager:

    def __init__(self, reservations: list[Reservation] = []):
        self.reservations_id_mappings = {}
        self.reservations_by_user = defaultdict(list)
        self.reservations_by_date = defaultdict(dict)
        for reservation in reservations:
            self.__insert_reservation_mapping__(reservation)


    def __insert_reservation_mapping__(self, reservation: Reservation):
        self.reservations_id_mappings[reservation.reservation_id] = reservation
        self.reservations_by_user[reservation.user].append(reservation.reservation_id)
        self.reservations_by_date[reservation.start_time.date()][reservation.start_time] = reservation.reservation_id
        return True

    def __remove_reservation_mapping__(self, reservation: Reservation):
        user = reservation.user
        res_id = reservation.reservation_id
        date = reservation.start_time.date()
        self.reservations_by_date[date].pop(reservation.start_time)
        self.reservations_by_user[user].remove(res_id)
        self.reservations_id_mappings.pop(res_id)
        return True

    def get_reservations_by_user(self, user: str) -> list[Reservation]:
        reservation_ids = self.reservations_by_user.get(user, [])
        return [reservation for res_id,reservation in self.reservations_id_mappings.items() if res_id in reservation_ids]
    def get_reservations_by_date(self, date: datetime.date) -> list[Reservation]:
        reservation_ids = self.reservations_by_date.get(date, {}).values()
        return [reservation for res_id,reservation in self.reservations_id_mappings.items() if res_id in reservation_ids]

    def get_reservation_by_start_time(self, start_time: datetime.datetime) -> Reservation:
        date = start_time.date()
        daily_reservations = self.reservations_by_date.get(date, {})
        reservation_id = daily_reservations.get(start_time, None)
        return self.get_reservation(reservation_id)

    def get_reservation(self, reservation_id: str) -> Reservation:
        return self.reservations_id_mappings.get(reservation_id, None)

    def get_all_reservation_ids(self):
        return list(self.reservations_id_mappings.keys())

    

    def _find_reservation_by_inner_time(self, inner_time: datetime.datetime) -> Reservation:
        from bisect import bisect_right
        daily_reservations = sorted(self.reservations_by_date.get(inner_time.date(), {}).items(), key=lambda x: x[0])
        matching_index = bisect_right(daily_reservations, inner_time, key=lambda x: x[0]) - 1
        if matching_index>=0:
            potential_match_id = daily_reservations[matching_index][1]
            potential_match_reservation = self.reservations_id_mappings[potential_match_id]
            if potential_match_reservation.start_time <= inner_time < potential_match_reservation.end_time:
                return potential_match_reservation
        return None