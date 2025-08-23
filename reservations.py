import datetime, uuid, warnings
from datetime import timedelta
from collections import defaultdict
from datetimes_utils import validate_time
from policy import PolicyManager
from business_calendar import BusinessCalendar
from booking_errors import *

class Reservation:
    def __init__(self, reservation_id, user, start_time,end_time, service_name):
        object.__setattr__(self, 'reservation_id', reservation_id)
        object.__setattr__(self, 'user', user)
        object.__setattr__(self, 'start_time', start_time)
        object.__setattr__(self, 'end_time', end_time)
        object.__setattr__(self, 'service_name', service_name)

    def __setattr__(self, attribute, value):
        raise ValueError(f'Cannot set attribute {attribute}. It is final')

    def to_dict(self):
        return self.__dict__

    def __repr__(self):
        rep_str = ' - '.join(f'{k}:{v}' for k,v in self.to_dict().items())
        return rep_str





class ReservationManager:

    def __init__(self, calendar: BusinessCalendar, policy_manager: PolicyManager):
        
        self.calendar = calendar
        self.policy_manager = policy_manager
        self.reservations_id_mappings = {}
        self.reservations_by_user = defaultdict(list)
        self.reservations_by_date = defaultdict(dict)


    def __insert_reservation_mapping__(self, reservation):
        self.reservations_id_mappings[reservation.reservation_id] = reservation
        self.reservations_by_user[reservation.user].append(reservation.reservation_id)
        self.reservations_by_date[reservation.start_time.date()][reservation.start_time] = reservation.reservation_id
        return True

    def __remove_reservation_mapping__(self, reservation):
        user = reservation.user
        res_id = reservation.reservation_id
        date = reservation.start_time.date()
        self.reservations_by_date[date].pop(reservation.start_time)
        self.reservations_by_user[user].remove(res_id)
        self.reservations_id_mappings.pop(res_id)
        return True

    def generate_new_reservation_id(self):
        return str(uuid.uuid4())
        
    def make_reservation(self, service_name, start_time, user, force_past_slots=False, force_advance_reservation=False, force_default_grid=True, minutes_duration=None):
        if force_past_slots:
            force_advance_reservation=True
        start_time = validate_time(start_time)
        if not start_time:
            raise TypeError('start_time must be a datetime object containing date, hours and minutes of the requested reservation')
        if service_name not in self.policy_manager.services:
            raise PolicyError('Unknown service')
        if start_time.minute % self.calendar.slot_minutes_duration:
            raise ValueError('Invalid start time — please select a 5-minute aligned slot (e.g. 11:00, 12:25, 16:30...)')
        if user=='admin':
           raise PermissionError('Cannot use admin here')
        curr_time = validate_time(datetime.datetime.now())
        if not force_past_slots and start_time<curr_time:
            raise PastTimeError('Cannot reserve on a past timeframe!')
        if not force_advance_reservation and start_time<curr_time + timedelta(minutes=self.policy_manager.min_advance_booking_minutes):
            raise PolicyError(f'Too late to book on the requested time. Reservation must be requested at least {self.policy_manager.min_advance_booking_minutes} minutes before.')
        
        if minutes_duration is None:
            minutes_duration = self.policy_manager.services[service_name].minutes_duration
        end_time = start_time+timedelta(minutes=minutes_duration)
        slots_to_book = self.calendar.get_slots(start_time=start_time, end_time=end_time, same_segment_only=True)
        if not slots_to_book:
            raise ClosingTimeError('Out of working hours')
        try:
            self.calendar._lock_slots(slots_to_book)
            self.calendar.reserve_slots(slots=slots_to_book)
        except Exception as e:
            self.calendar._unlock_slots(slots_to_book)
            raise e

        reservation_id = self.generate_new_reservation_id()
        self.__insert_reservation_mapping__(Reservation(reservation_id=reservation_id, user=user, start_time=start_time, end_time=end_time, service_name=service_name))
        self.calendar._unlock_slots(slots_to_book)

        return (True, reservation_id, service_name, start_time, minutes_duration)


    def cancel_reservation(self, reservation_id, user, force_past_slots=False, force_advance_cancelation=False):
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
        try:
            prev_reservation = self.reservations_id_mappings[reservation_id]
            booked_user, booked_service, booked_start_time, booked_end_time = prev_reservation.user, prev_reservation.service_name, prev_reservation.start_time, prev_reservation.end_time
        except KeyError:
            raise NotPreviouslyBookedError(f'You dont have any reservation with booking id: {reservation_id}')
        if booked_user!=user:
            raise NotPreviouslyBookedError(f'You dont have any reservation with booking id: {reservation_id}')
        if not force_advance_cancelation and booked_start_time - timedelta(minutes = self.policy_manager.min_advance_cancelation_minutes) <= datetime.datetime.now():
            raise PolicyError(f'Too late to cancel. Cancelations must be requested at least {self.policy_manager.min_advance_cancelation_minutes} minutes before.')

        slots_to_unbook = self.calendar.get_slots(start_time=booked_start_time, end_time=booked_end_time, same_segment_only=True)
        try:
            self.calendar._lock_slots(slots_to_unbook)
            self.calendar.free_slots(slots_to_unbook)
        except Exception as e: 
            print(e) ##should never happen as far as a whole segment is deleted or a cancelation is done not-properly
            self.calendar._unlock_slots(slots_to_unbook)
            return (False, reservation_id, None, None) 
        
        self.__remove_reservation_mapping__(prev_reservation)
        self.calendar._unlock_slots(slots_to_unbook)
        return (True, reservation_id, booked_service, booked_start_time, booked_end_time)
    
    def cancel_reservation_by_time(self, start_time, user, service_name=None, force_past_slots=False, force_advance_cancelation=False):
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
        start_time = validate_time(start_time)
        if not start_time:
            raise TypeError('start_time must be a datetime object containing date, hours and minutes of the reservation to cancel')
        if service_name is not None and service_name not in self.policy_manager.services:
            raise PolicyError('Unknown service')
        if start_time.minute%self.policy_manager.default_slot_duration:
            raise ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')
        
        curr_time = validate_time(datetime.datetime.now())
        if not force_past_slots and start_time<curr_time:
            raise PastTimeError('Cannot cancel on a past timeframe!')
        if not force_advance_cancelation and start_time<curr_time + timedelta(minutes=self.policy_manager.min_advance_cancelation_minutes):
            raise PolicyError(f'Too late to cancel. Cancelations must be requested at least {self.policy_manager.min_advance_cancelation_minutes} minutes before.')

        to_warn = False
        reservation = self.get_reservation_by_start_time(start_time)
        if reservation is None:
            to_warn = True
            reservation = self._find_reservation_by_inner_time(start_time)
            if reservation is None:
                raise ValueError('Cannot find a reservation at the requested time')
        if reservation.user != user:
            raise NotPreviouslyBookedError('You dont have any reservation at the chosen time')
        if service_name is not None and reservation.service_name!=service_name:
            raise ValueError('Cannot cancel a different service than the previously booked one')
        

        cancel_output = self.cancel_reservation(reservation_id=reservation.reservation_id, 
                                                user=user,
                                                force_advance_cancelation=force_advance_cancelation, 
                                                force_past_slots=force_past_slots)
        if to_warn:
            warnings.warn(f'Original reservation was at {reservation.start_time}. Canceled anyway')
        return cancel_output


    def update_reservation(self, old_reservation_id, user, new_start_time, new_service_name=None, new_minutes_duration=None, force_past_slots=False, force_advance_cancelation=False,
    force_advance_reservation=False):
        from slots_utils import get_consecutive_slots_join
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
        new_start_time = validate_time(new_start_time)
        if not new_start_time:
            raise TypeError('start_time must be a datetime object containing date, hours and minutes of the reservation to cancel')
        if new_service_name is not None and new_service_name not in self.policy_manager.services:
            raise PolicyError('Unknown service')
        if new_start_time.minute % self.policy_manager.default_slot_duration:
            raise ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')

        if old_reservation_id not in self.reservations_id_mappings:
            raise NotPreviouslyBookedError(f'You dont have any reservation with booking id: {old_reservation_id}')
        old_reservation = self.reservations_id_mappings[old_reservation_id]
        old_user, old_service, old_start_time, old_end_time = old_reservation.user, old_reservation.service_name, old_reservation.start_time, old_reservation.end_time
        
        if old_user != user:
            raise NotPreviouslyBookedError(f'You dont have any reservation with booking id: {old_reservation_id}')
        if new_service_name is None:
            new_service_name = old_service
        if new_minutes_duration is None:
            new_minutes_duration = self.policy_manager.services[new_service_name].minutes_duration
        new_end_time = new_start_time+timedelta(minutes=new_minutes_duration)
        update_constraints_params = {'min_cancel_minutes': self.policy_manager.min_advance_cancelation_minutes,
                                     'min_reserve_minutes': self.policy_manager.min_advance_booking_minutes}
        if force_past_slots:
            update_constraints_params['min_cancel_minutes'] = - 500000
            update_constraints_params['min_reserve_minutes'] = - 500000

        if force_advance_cancelation:
            update_constraints_params['min_cancel_minutes'] = 0
            update_constraints_params['min_reserve_minutes'] = 0

        can_update, error_msg = check_update_constraints(old_start_time=old_start_time, old_end_time=old_end_time, 
                                                      new_start_time=new_start_time,
                                                      new_end_time = new_end_time, **update_constraints_params)
        if not can_update:
            raise PolicyError(error_msg)
        slots_to_book = self.calendar.get_slots(start_time=new_start_time, end_time=new_end_time, same_segment_only=True)
        slots_to_free = self.calendar.get_slots(start_time=old_start_time, end_time=old_end_time, same_segment_only=True)
        if not slots_to_book or not slots_to_free:
            raise ClosingTimeError('Out of working hours')
            
        slots_to_book, slots_to_free = get_consecutive_slots_join(slots_to_book, slots_to_free, how='diff')
        try:
            """ Locking slots, updating slots status (setting previous slots as "unbooked", new slots as "booked). 
            Finally updating current mappings (removing old reservation, inserting new one).
            If anything goes wrong, return to previous status (set the old slots as booked, and keep the old reservation among reservations).
            Unlock slots.
            """
            self.calendar._lock_slots(slots_to_free)
            self.calendar._lock_slots(slots_to_book)
            self.calendar.free_slots(slots_to_free)
            self.calendar.reserve_slots(slots_to_book)
            self.__remove_reservation_mapping__(old_reservation)
            new_reservation_id = old_reservation_id#self.generate_new_reservation_id()
            new_reservation = Reservation(reservation_id=new_reservation_id, user=user, start_time=new_start_time, end_time=new_end_time, service_name=new_service_name)
            self.__insert_reservation_mapping__(new_reservation)
            self.calendar._unlock_slots(slots_to_free)
            self.calendar._unlock_slots(slots_to_book)
            return (True, new_reservation_id, old_service, old_start_time)
        except Exception as e:
            #self.calendar.free_slots(slots_to_book)
            self.calendar.reserve_slots(slots_to_free)
            self.__insert_reservation_mapping__(old_reservation)
            self.calendar._unlock_slots(slots_to_free)
            self.calendar._unlock_slots(slots_to_book)
            raise e


    def update_reservation_by_time(self, old_start_time, new_start_time, user, new_service_name=None, new_minutes_duration=None, force_past_slots=False, force_advance_cancelation=False):
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
        new_start_time, old_start_time = validate_time(new_start_time), validate_time(old_start_time)
        if not new_start_time or not old_start_time:
            raise TypeError('start_time and old_start_time must be valid datetime objects containing date, hours and minutes')
        if new_service_name is not None and new_service_name not in self.policy_manager.services:
            raise PolicyError('Unknown service')
        if new_start_time.minute%self.policy_manager.default_slot_duration:
            raise ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')
        
        curr_time = validate_time(datetime.datetime.now())
        if not force_past_slots and (new_start_time<curr_time or old_start_time<curr_time):
            raise PastTimeError('Cannot cancel on a past timeframe!')
        
        to_warn = False
        reservation = self.get_reservation_by_start_time(old_start_time)
        if reservation is None:
            to_warn = True
            reservation = self._find_reservation_by_inner_time(old_start_time)
            if reservation is None:
                raise NotPreviouslyBookedError('You dont have any reservation at the chosen time')
        if reservation.user != user:
            raise NotPreviouslyBookedError('You dont have any reservation at the chosen time')

        update_output = self.update_reservation(old_reservation_id=reservation.reservation_id, 
                                                user=user,
                                                new_service_name=new_service_name,
                                                new_start_time=new_start_time,
                                                new_minutes_duration=new_minutes_duration,
                                                force_advance_cancelation=force_advance_cancelation, 
                                                force_past_slots=force_past_slots)
        if to_warn:
            warnings.warn(f'Original reservation was at {reservation.start_time}. Updated anyway')
        return update_output

    def get_available_slots(self, start_time, end_time, service_name, minutes_duration=None, force_past_slots=False):
        from datetimes_utils import map_datetime_to_next_slot_datetime
        if service_name not in self.policy_manager.services:
            raise PolicyError('Unknown service')
        if not validate_time(start_time) or not validate_time(end_time):
            raise TypeError('start_time and old_start_time must be valid datetime objects containing date, hours and minutes')
        if end_time<start_time:
            raise ValueError('end_time cannot be a previous datetime than start_time')
        curr_time = map_datetime_to_next_slot_datetime(validate_time(datetime.datetime.now()))
        if not force_past_slots:
            if end_time < curr_time+timedelta(minutes=self.policy_manager.min_advance_booking_minutes):
                raise PastTimeError('Cannot look for availabilities on past timeframes')
            if start_time < curr_time+timedelta(minutes=self.policy_manager.min_advance_booking_minutes):
                warnings.warn('Providing availabilities on future slots only.')
                start_time = curr_time+timedelta(minutes=self.policy_manager.min_advance_booking_minutes)
        
        
        if minutes_duration is None:
            minutes_duration = self.policy_manager.services[service_name].minutes_duration
        return self.calendar.get_available_booking_slots(min_start_time=start_time, max_start_time=end_time, minutes_duration=minutes_duration, 
                                                              minutes_grid_span=15)

    def get_reservations_by_user(self, user):
        reservation_ids = self.reservations_by_user.get(user, [])
        return [reservation for res_id,reservation in self.reservations_id_mappings.items() if res_id in reservation_ids]
    def get_reservations_by_date(self, date):
        reservation_ids = self.reservations_by_date.get(date, {}).values()
        return [reservation for res_id,reservation in self.reservations_id_mappings.items() if res_id in reservation_ids]

    def get_reservation_by_start_time(self, start_time):
        date = start_time.date()
        daily_reservations = self.reservations_by_date.get(date, {})
        reservation_id = daily_reservations.get(start_time, None)
        return self.get_reservation(reservation_id)

    def get_reservation(self, reservation_id):
        return self.reservations_id_mappings.get(reservation_id, None)

    def get_reservation_ids(self):
        return list(self.reservations_id_mappings.keys())

    

    def _find_reservation_by_inner_time(self, inner_time):
        from bisect import bisect_right
        daily_reservations = sorted(self.reservations_by_date.get(inner_time.date(), {}).items(), key=lambda x: x[0])
        matching_index = bisect_right(daily_reservations, inner_time, key=lambda x: x[0]) - 1
        if matching_index>=0:
            potential_match_id = daily_reservations[matching_index][1]
            potential_match_reservation = self.reservations_id_mappings[potential_match_id]
            if potential_match_reservation.start_time <= inner_time < potential_match_reservation.end_time:
                return potential_match_reservation
        return None
        
        
def check_update_constraints(old_start_time, old_end_time, new_start_time, new_end_time, min_cancel_minutes, min_reserve_minutes):
    now = datetime.datetime.now()

    too_close_to_cancel = (old_start_time - now) < timedelta(minutes=min_cancel_minutes)        
    if too_close_to_cancel:
        # Can only change end time or service, not start time
        if new_start_time != old_start_time:
            return False, "Too late to update booking start_time."
    else:
        # Treat like a new booking: must respect min_reserve_minutes
        if (new_start_time - now) < timedelta(minutes=min_reserve_minutes):
            return False, "New start time too close."
    
    return True, ""