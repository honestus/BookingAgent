import datetime, uuid, warnings
from datetime import timedelta
from collections import defaultdict
from datetimes_utils import validate_time
from policy import PolicyManager, Service
from business_calendar import BusinessCalendar
from reservations import ReservationManager, Reservation
from booking_errors import *
from validate_utils import validate_service_inputs


def check_new_reservation_constraints(start_time, end_time, min_reserve_minutes):
    if end_time<=start_time:
        return False, "Wrong inputs"
    now = datetime.datetime.now()
    too_close_to_book = (start_time - now) < timedelta(minutes=min_reserve_minutes)        
    if too_close_to_book:
        return False, "Cannot book. Start time too close"
    return True, ""
    
def check_cancel_reservation_constraints(reservation_start_time, min_cancel_minutes):
    now = datetime.datetime.now()
    too_close_to_cancel = (reservation_start_time - now) < timedelta(minutes=min_cancel_minutes)        
    if too_close_to_cancel:
        return False, "Too late to cancel."
    return True, ""
        
def check_update_constraints(old_start_time, old_end_time, new_start_time, new_end_time, min_cancel_minutes, min_reserve_minutes):
    valid_cancel_time, _ = check_cancel_reservation_constraints(reservation_start_time=old_start_time, min_cancel_minutes=min_cancel_minutes)    
    if not valid_cancel_time:
        # Can only change end time or service, not start time
        if new_start_time != old_start_time:
            return False, "Too late to update booking start_time."
    else:
        valid_reserv_output = check_new_reservation_constraints(start_time=new_start_time, end_time=new_end_time, min_reserve_minutes=min_reserve_minutes)
        # Treat like a new booking: must respect min_reserve_minutes
        return valid_reserv_output
    
    return True, ""
    
    
def __generate_new_reservation_id__():
        return str(uuid.uuid4())


class BusinessManager:

    def __init__(self, reservation_manager: ReservationManager, calendar: BusinessCalendar, policy_manager: PolicyManager, default_grid_minutes: int = 15):
        
        self.calendar = calendar
        self.policy_manager = policy_manager
        self.reservation_manager = reservation_manager
        self.default_grid_minutes = default_grid_minutes

        
    def can_reserve(self, start_time: datetime.datetime, service_name: str, minutes_duration: int=None, user: str=None, force_default_grid: bool=True, force_advance_reservation: bool=False):
        """Returns True if the slots are free, False otherwise.
        Including user as a parameter as, in the future, future checks on the user might be added (e.g. only limit to max n active reservations by user)
        """
        
        if minutes_duration is None:
            minutes_duration = self._get_duration_from_service_name(service_name)

        time_constraints = {'min_reserve_minutes':self.policy_manager.min_advance_booking_minutes * int(not force_advance_reservation)}
        
        valid_times, msg = check_new_reservation_constraints(start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration), **time_constraints)
        if not valid_times:
            return PolicyError(msg)
    
        grid_params = {'minutes_grid_span': self.default_grid_minutes if force_default_grid else 5}
        available_slot_grids = self.calendar.get_available_booking_slots(min_start_time=start_time, max_start_time=start_time, 
                                                                         minutes_duration=minutes_duration, 
                                                                         **grid_params)
        if len(available_slot_grids)!=1 or not any(available_slot_grids[0]):
            return AlreadyBookedError('')
        return True
        
    def _can_cancel_reservation(self, reservation: Reservation, user: str):
        if reservation is None or reservation.user!=user:
            return NotPreviouslyBookedError('')
        valid_times, msg = check_cancel_reservation_constraints(reservation_start_time=reservation.start_time, min_cancel_minutes=self.policy_manager.min_advance_cancelation_minutes)
        if not valid_times:
            return PolicyError(msg)
        return True#reservation
        
    def can_cancel(self, user: str, reservation_id: str = None, start_time: datetime.datetime = None, service_name: str = None):
        if bool(reservation_id) == bool(start_time):
            return ValueError('Must provide exactly one between reservation_id and start_time')
        
        if reservation_id is None:
            reservation = self.reservation_manager._find_reservation_by_inner_time(start_time)
        else:
            reservation = self.reservation_manager.get_reservation(reservation_id)
        if service_name is not None and service_name!=reservation.service_name:
            return ValueError('Different service than the booked one')
        return self._can_cancel_reservation(reservation=reservation, user=user)
        
    def can_update(self, user: str, new_start_time: datetime.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, old_reservation_id: str = None, old_start_time: datetime.datetime = None, force_default_grid: bool = True):
        from datetimes_utils import minutes_between
        if bool(old_reservation_id) == bool(old_start_time):
            return ValueError('Must provide exactly one between reservation_id and old_start_time')
        
        if old_reservation_id is None:
            reservation = self.reservation_manager._find_reservation_by_inner_time(old_start_time)
        else:
            reservation = self.reservation_manager.get_reservation(old_reservation_id)
        
        if reservation is None or reservation.user!=user:
            return NotPreviouslyBookedError('')
        if all([p is None for p in [new_start_time, new_service_name, new_minutes_duration]]):
            return ValueError('Nothing to update. The reservation is exactly the same as the old one')
        if new_start_time is None:
            new_start_time = reservation.start_time
        if new_service_name is None:
            new_service_name = reservation.service_name
        if new_minutes_duration is None:
            new_minutes_duration = self._get_duration_from_service_name(new_service_name) if new_service_name!=reservation.service_name else minutes_between(reservation.start_time, reservation.end_time)
            
        update_valid_times, msg = check_update_constraints(old_start_time=reservation.start_time, 
                                        old_end_time=reservation.end_time, 
                                        new_start_time=new_start_time, 
                                        new_end_time=new_start_time+timedelta(minutes=new_minutes_duration), 
                                        min_cancel_minutes=self.policy_manager.min_advance_cancelation_minutes, 
                                        min_reserve_minutes=self.policy_manager.min_advance_booking_minutes)
        if not update_valid_times:
            return PolicyError(msg)
        
        if reservation.start_time==new_start_time: ##NEW RES HAS SAME START TIME OF THE OLD ONE
            if new_minutes_duration<=minutes_between(reservation.start_time, reservation.end_time): ##new reservation lasts less than previous (i.e. needs less slots)
                return True
            return self.can_reserve(start_time=reservation.end_time, service_name=new_service_name,
                                    minutes_duration=new_minutes_duration-minutes_between(reservation.start_time, reservation.end_time),
                                    force_default_grid=False,
                                    force_advance_reservation=True) ##new reservation is longer than previous... need to check that the following needed slots are free
        
        if new_start_time>=reservation.end_time or new_start_time+timedelta(minutes=new_minutes_duration)<=reservation.start_time: ## no overlap->just handle as new reservation
            return self.can_reserve(start_time=new_start_time, service_name=new_service_name,
                                    minutes_duration=new_minutes_duration,
                                    force_default_grid=force_default_grid
                                    )
        ### OVERLAP (NEW RESERVATION TIMES OVERLAP WITH OLD RESERVATION TIMES)
         ##since the two reservations overlap, the only involved segment will be the one containing the new reservation
        segment_involved = self.calendar.find_segment_containing(start_time=new_start_time, end_time=new_start_time+timedelta(minutes=new_minutes_duration), return_index=False)
        if segment_involved is None:
            return ClosingTimeError('Out of working hours')
        tmp_calendar = BusinessCalendar(slot_minutes_duration=self.calendar.slot_minutes_duration)
        tmp_calendar.add_segment(segment_involved.copy()) ##generating tmp_calendar made by only the involved segment (copy)
        old_res_slots = tmp_calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time)
        tmp_calendar.free_slots(old_res_slots) ##mimicking the cancelation of old_res slots in order to check if the new reservation is available
        grid_params = {}
        if not force_default_grid:
            grid_params = {'minutes_grid_span':5}
        available_slot_grids = tmp_calendar.get_available_booking_slots(min_start_time=new_start_time, max_start_time=new_start_time, 
                                                                         minutes_duration=new_minutes_duration, 
                                                                         **grid_params)
        if len(available_slot_grids)!=1 or not any(available_slot_grids[0]):
            return AlreadyBookedError('')
        return True
            

    def make_reservation(self, service_name: str, start_time: datetime.datetime, user: str, minutes_duration: int=None, force_past_slots: bool=False, force_advance_reservation: bool=False, force_default_grid: bool=True, ):
        if force_past_slots:
            force_advance_reservation=True
        start_time = validate_time(start_time)
        if not start_time:
            return TypeError('start_time must be a datetime object containing date, hours and minutes of the requested reservation')
        if service_name not in self.policy_manager.services:
            return PolicyError('Cannot make the reservation: unknown service')
        if start_time.minute % self.calendar.slot_minutes_duration:
            return ValueError('Cannot make the reservation: Invalid start time — please select a 5-minute aligned slot (e.g. 11:00, 12:25, 16:30...)')
        if user=='admin':
           return PermissionError('Cannot use admin here')
        curr_time = validate_time(datetime.datetime.now())
        if not force_past_slots and start_time<curr_time:
            return PastTimeError('Cannot make the reservation: impossible to book on a past timeframe!')
        if not force_advance_reservation and start_time<curr_time + timedelta(minutes=self.policy_manager.min_advance_booking_minutes):
            return PolicyError(f'Cannot make the reservation: Too late to book on the requested time. Reservation must be requested at least {self.policy_manager.min_advance_booking_minutes} minutes before.')
        
        if minutes_duration is None:
            minutes_duration = self._get_duration_from_service_name(service_name)
        end_time = start_time+timedelta(minutes=minutes_duration)
        slots_to_book = self.calendar.get_slots(start_time=start_time, end_time=end_time, same_segment_only=True)
        if not slots_to_book:
            return ClosingTimeError('Cannot make the reservation: Out of working hours')
        try:
            self.calendar._lock_slots(slots_to_book)
            self.calendar.reserve_slots(slots=slots_to_book)
        except Exception as e:
            self.calendar._unlock_slots(slots_to_book)
            err_msg = 'Cannot make the reservation: ' + e.message
            return type(e)(err_msg)

        reservation_id = __generate_new_reservation_id__()
        new_reservation = Reservation(reservation_id=reservation_id, user=user, start_time=start_time, end_time=end_time, service_name=service_name)
        self.reservation_manager.__insert_reservation_mapping__(new_reservation)
        self.calendar._unlock_slots(slots_to_book)

        return new_reservation


    def cancel_reservation(self, reservation_id: str, user: str, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
        try:
            prev_reservation = self.reservation_manager.reservations_id_mappings[reservation_id]
            booked_user, booked_service, booked_start_time, booked_end_time = prev_reservation.user, prev_reservation.service_name, prev_reservation.start_time, prev_reservation.end_time
        except KeyError:
            return NotPreviouslyBookedError(f'Cannot cancel the reservation: You dont have any reservation with booking id: {reservation_id}')
        if booked_user!=user:
            return NotPreviouslyBookedError(f'Cannot cancel the reservation: You dont have any reservation with booking id: {reservation_id}')
        if not force_advance_cancelation and booked_start_time - timedelta(minutes = self.policy_manager.min_advance_cancelation_minutes) <= datetime.datetime.now():
            return PolicyError(f'Cannot cancel the reservation: Too late to cancel. Cancelations must be requested at least {self.policy_manager.min_advance_cancelation_minutes} minutes before.')

        slots_to_unbook = self.calendar.get_slots(start_time=booked_start_time, end_time=booked_end_time, same_segment_only=True)
        try:
            self.calendar._lock_slots(slots_to_unbook)
            self.calendar.free_slots(slots_to_unbook)
        except Exception as e: 
            print(e) ##should never happen as far as a whole segment is deleted or a cancelation is done not-properly
            self.calendar._unlock_slots(slots_to_unbook)
            return e
        
        self.reservation_manager.__remove_reservation_mapping__(prev_reservation)
        self.calendar._unlock_slots(slots_to_unbook)
        return prev_reservation
    
    def cancel_reservation_by_time(self, start_time: datetime.datetime, user: str, service_name: str=None, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
        start_time = validate_time(start_time)
        if not start_time:
            return TypeError('start_time must be a datetime object containing date, hours and minutes of the reservation to cancel')
        if service_name is not None and service_name not in self.policy_manager.services:
            return PolicyError('Unknown service')
        if start_time.minute%self.policy_manager.default_slot_duration:
            return ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')
        
        curr_time = validate_time(datetime.datetime.now())
        if not force_past_slots and start_time<curr_time:
            return PastTimeError('Cannot cancel on a past timeframe!')
        if not force_advance_cancelation and start_time<curr_time + timedelta(minutes=self.policy_manager.min_advance_cancelation_minutes):
            return PolicyError(f'Too late to cancel. Cancelations must be requested at least {self.policy_manager.min_advance_cancelation_minutes} minutes before.')

        to_warn = False
        reservation = self.reservation_manager.get_reservation_by_start_time(start_time)
        if reservation is None:
            to_warn = True
            reservation = self.reservation_manager._find_reservation_by_inner_time(start_time)
            if reservation is None:
                return NotPreviouslyBookedError('Cannot find a reservation at the requested time')
        if reservation.user != user:
            return NotPreviouslyBookedError('You dont have any reservation at the chosen time')
        if service_name is not None and reservation.service_name!=service_name:
            return PolicyError('Cannot cancel a different service than the previously booked one')
        

        cancel_output = self.cancel_reservation(reservation_id=reservation.reservation_id, 
                                                user=user,
                                                force_advance_cancelation=force_advance_cancelation, 
                                                force_past_slots=force_past_slots)
        if to_warn:
            warnings.warn(f'Original reservation was at {reservation.start_time}. Canceled anyway')
        return cancel_output


    def update_reservation(self, user: str, old_reservation_id: str, new_start_time: datetime.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, force_past_slots: bool=False, force_advance_cancelation: bool=False,
    force_advance_reservation=False):
        from slots_utils import get_consecutive_slots_join
        from datetimes_utils import minutes_between

        if new_start_time is None and new_service_name is None and new_minutes_duration is None:
            return
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
            
        if old_reservation_id not in self.reservation_manager.reservations_id_mappings:
            return NotPreviouslyBookedError(f'You dont have any reservation with booking id: {old_reservation_id}')
        old_reservation = self.reservation_manager.get_reservation(old_reservation_id)
        old_user, old_service, old_start_time, old_end_time = old_reservation.user, old_reservation.service_name, old_reservation.start_time, old_reservation.end_time
        if old_user != user:
            return NotPreviouslyBookedError(f'You dont have any reservation with booking id: {old_reservation_id}')
            
        if new_start_time is None:
            new_start_time = old_start_time
        if new_service_name is None:
            new_service_name = old_service
        if new_minutes_duration is None:
            new_minutes_duration = self._get_duration_from_service_name(new_service_name) if new_service_name!=old_service else minutes_between(old_start_time, old_end_time)
        
        new_start_time = validate_time(new_start_time)
        
        if not new_start_time:
            return TypeError('new_start_time must be a datetime object containing date, hours and minutes of the reservation to cancel')
        if new_service_name not in self.policy_manager.services:
            return PolicyError('Unknown service')
        if new_start_time.minute % self.policy_manager.default_slot_duration:
            return ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')
        

        new_end_time = new_start_time+timedelta(minutes=new_minutes_duration)
        update_constraints_params = {'min_cancel_minutes': self.policy_manager.min_advance_cancelation_minutes,
                                     'min_reserve_minutes': self.policy_manager.min_advance_booking_minutes}
        if force_past_slots:
            update_constraints_params['min_cancel_minutes'] = - 500000
            update_constraints_params['min_reserve_minutes'] = - 500000

        if force_advance_cancelation:
            update_constraints_params['min_cancel_minutes'] = 0
            update_constraints_params['min_reserve_minutes'] = 0

        valid_update_times, error_msg = check_update_constraints(old_start_time=old_start_time, old_end_time=old_end_time, 
                                                      new_start_time=new_start_time,
                                                      new_end_time = new_end_time, **update_constraints_params)
        if not valid_update_times:
            return PolicyError(error_msg)
        
        slots_to_book = self.calendar.get_slots(start_time=new_start_time, end_time=new_end_time, same_segment_only=True)
        slots_to_free = self.calendar.get_slots(start_time=old_start_time, end_time=old_end_time, same_segment_only=True)
        if not slots_to_book or not slots_to_free:
            return ClosingTimeError('Out of working hours')
            
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
            self.reservation_manager.__remove_reservation_mapping__(old_reservation)
            new_reservation_id = old_reservation_id#__generate_new_reservation_id__()
            new_reservation = Reservation(reservation_id=new_reservation_id, user=user, start_time=new_start_time, end_time=new_end_time, service_name=new_service_name)
            self.reservation_manager.__insert_reservation_mapping__(new_reservation)
            self.calendar._unlock_slots(slots_to_free)
            self.calendar._unlock_slots(slots_to_book)
            return (old_reservation, new_reservation)
        except Exception as e:
            #self.calendar.free_slots(slots_to_book)
            self.calendar.reserve_slots(slots_to_free)
            self.reservation_manager.__insert_reservation_mapping__(old_reservation)
            self.calendar._unlock_slots(slots_to_free)
            self.calendar._unlock_slots(slots_to_book)
            return e


    def update_reservation_by_time(self, user: str, old_start_time: datetime.datetime, new_start_time: datetime.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        if user!='admin':
            force_past_slots = False
            force_advance_cancelation = False
        if force_past_slots:
            force_advance_cancelation=True
        old_start_time = validate_time(old_start_time)

        if not old_start_time:
            return  TypeError('old_start_time must be valid datetime objects containing date, hours and minutes')
        if new_start_time is not None:
            new_start_time = validate_time(new_start_time)
            if not new_start_time:
                return TypeError('old_start_time must be valid datetime objects containing date, hours and minutes')
            if new_start_time.minute%self.policy_manager.default_slot_duration:
                return ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')
        if new_service_name is not None and new_service_name not in self.policy_manager.services:
            return PolicyError('Unknown service')
        
        
        curr_time = validate_time(datetime.datetime.now())
        if not force_past_slots and old_start_time<curr_time:
            return PastTimeError('Cannot cancel on a past timeframe!')
        
        to_warn = False
        reservation = self.reservation_manager.get_reservation_by_start_time(old_start_time)
        if reservation is None:
            to_warn = True
            reservation = self.reservation_manager._find_reservation_by_inner_time(old_start_time)
            if reservation is None:
                return NotPreviouslyBookedError('You dont have any reservation at the chosen time')
        if reservation.user != user:
            return NotPreviouslyBookedError('You dont have any reservation at the chosen time')

        if new_start_time is None:
            new_start_time = reservation.start_time
        if new_service_name is None:
            new_service_name = reservation.service_name
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


    def is_available(self, start_time: datetime.datetime, service_name: str, minutes_duration: int = None):
        if minutes_duration is None:
            if service_name not in self.policy_manager.services:
                return False, PolicyError('Cannot check for availability. Unknown service')
            minutes_duration = self._get_duration_from_service_name(service_name)
        is_available = self.calendar.is_available_timeframe(start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration), as_int_error=True)
        if is_available==-1:
            return ClosingTimeError('The chosen datetime is not available. We are closed')
        elif not is_available:
            return AlreadyBookedError('The chosen datetime is not available. Already booked')
        return True
        
    def get_available_datetimes(self, min_start_time: datetime.datetime, service_name: str,  max_start_time: datetime.datetime = None, minutes_duration=None, force_past_slots=False):
        from datetimes_utils import map_datetime_to_next_slot_datetime
        if service_name not in self.policy_manager.services:
            return PolicyError('Unknown service')
        if max_start_time is None:
            max_start_time = min_start_time
        if not validate_time(min_start_time) or not validate_time(max_start_time):
            return TypeError('min_start_time and end_time must be valid datetime objects containing date, hours and minutes')
        if max_start_time<min_start_time:
            return ValueError('max_start_time cannot be a previous datetime than min_start_time')
        curr_time = map_datetime_to_next_slot_datetime(validate_time(datetime.datetime.now()))
        if not force_past_slots:
            if max_start_time < curr_time+timedelta(minutes=self.policy_manager.min_advance_booking_minutes):
                return False, PastTimeError('Cannot look for availabilities on past timeframes')
            if min_start_time < curr_time+timedelta(minutes=self.policy_manager.min_advance_booking_minutes):
                warnings.warn('Providing availabilities on future slots only.')
                min_start_time = curr_time+timedelta(minutes=self.policy_manager.min_advance_booking_minutes)
        
        
        if minutes_duration is None:
            minutes_duration = self._get_duration_from_service_name(service_name)
        available_slots = self.calendar.get_available_booking_slots(min_start_time=min_start_time, max_start_time=max_start_time, minutes_duration=minutes_duration, 
                                                              minutes_grid_span=15)
        default, special = [str(slot.start_time) for s in list(map(lambda x: x[0], available_slots)) for slot in s], \
                           [str(slot.start_time) for s in list(map(lambda x: x[1], available_slots)) for slot in s]
        return (default, special)


    def get_available_services(self) -> list[Service]:
        return list(self.policy_manager.services.values())
        
    def add_service(self, service_name: str, price: int|float, minutes_duration: int, description: str = ''):
        if not validate_service_inputs(service_name = service_name, service_price = price, service_minutes_duration = minutes_duration, service_description = description):
            return ValueError('Wrong input to create the new Service')
            
        service = Service(name=service_name, price=price, minutes_duration=minutes_duration, description=description)
        self.policy_manager.add_service(service=service)
        
    def update_service(self, service_name: str, price: int|float = None, minutes_duration: int = None, description: str = None):
        if not validate_service_inputs(service_name = service_name, service_price = price, service_minutes_duration = minutes_duration, service_description = description):
            return ValueError('Wrong input to create the new Service')
        return self.policy_manager.update_service(service_name=service_name, price=price, minutes_duration=minutes_duration, description=description)
        
    def remove_service(self, service_name):
        return self.policy_manager.remove_service(service_name=service_name)
        
    def can_make_service_change(self, operation: str, service_name: str, price: int|float = None, minutes_duration: int = None, description: str = None):
        allowed_operations = ['add', 'update', 'remove']
        if operation not in allowed_operations:
            return ValueError(f'Wrong operation. It must be one of: {allowed_operations}')
        if not validate_service_inputs(service_name = service_name, service_price = price, service_minutes_duration = minutes_duration, service_description = description):
            return ValueError(f'Wrong input parameters to {operation} the new Service')
        if minutes_duration%self.policy_manager.default_slot_duration:
            return ValueError(f'Minutes duration must be a multiple of {self.policy_manager.default_slot_duration}')
        if operation=='add':
            return service_name not in self.policy_manager.services
        return service_name in self.policy_manager.services
        
    def add_new_calendar(self, calendar: BusinessCalendar):
        self.calendar = self.calendar.join(calendar)
        
    def add_existing_calendar(self, calendar: BusinessCalendar):
        self.calendar = self.calendar.join(calendar)
        
    def remove_time_from_calendar(self, start_time: datetime.datetime, end_time: datetime.datetime):
        raise NotImplementedError('')
        if end_time<=start_time:
            raise ValueError('End time must be after start_time')
        self.calendar.remove_segment(start_time=start_time, end_time=end_time, raise_error_if_any_booking=True)
        
    def get_user_reservations(self, user: str) -> list[Reservation]:
        return self.reservation_manager.get_reservations_by_user(user)

    def get_daily_reservations(self, date: datetime.date) -> list[Reservation]:
        return self.reservation_manager.get_reservations_by_date(date)

    def get_default_opening_hours(self):
        return self.policy_manager.opening_hours
        
    def get_daily_opening_hours(self, date: datetime.date):
        date_start, date_end = datetime.datetime.combine(date, datetime.time.min), datetime.datetime.combine(date, datetime.time.max)
        segments_involved = self.calendar._get_segments_involved(start_time= date_start, end_time = date_end)
        if not segments_involved:
            return []
        opening_hours = [(max(date_start, segment.start_time), min(date_end, segment.end_time)) for segment in segments_involved]
        return opening_hours

    def get_all_reservations(self):
        return sorted(self.reservation_manager.reservations_id_mappings.values(), key=lambda x: x.start_time)
        
    def _get_duration_from_service_name(self, service_name):
        try:
            return self.policy_manager.services[service_name].minutes_duration
        except:
            return ValueError(f'Unknown service {service_name}')
        
    
       

