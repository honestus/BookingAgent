import datetime, uuid, warnings
from datetime import timedelta
from collections import defaultdict
from datetimes_utils import validate_time, minutes_between
from policy import PolicyManager, Service
from business_calendar import BusinessCalendar
from reservations import *
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

        
    def can_reserve(self, start_time: datetime.datetime, service_name: str, minutes_duration: int=None, user: str=None, force_default_grid: bool=True, force_advance_reservation: bool=False, force_past_slots: bool=False)  -> bool|Exception:
        """Returns True if the slots are free, False otherwise.
        Including user as a parameter because, in the future, checks on the user might be added (e.g. only limit to max n active reservations by user)
        """
        if service_name not in self.policy_manager.services:
            return PolicyError(f'Cannot make the reservation: unknown service {service_name}')
        if minutes_duration is None:
            minutes_duration = self._get_duration_from_service_name(service_name)
           
        if not force_past_slots:
            min_reserve_minutes = self.policy_manager.min_advance_booking_minutes * int(not force_advance_reservation)
            valid_times, msg = check_new_reservation_constraints(start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration), min_reserve_minutes=min_reserve_minutes)
            if not valid_times:
                return PolicyError(msg)
    
        grid_params = {'minutes_grid_span': self.default_grid_minutes if force_default_grid else 5}
        available_slot_grids = self.calendar.get_available_booking_slots(min_start_time=start_time, max_start_time=start_time, 
                                                                         minutes_duration=minutes_duration, 
                                                                         **grid_params)
        if len(available_slot_grids)!=1 or not any(available_slot_grids[0]):
            return AlreadyBookedError('Cannot reserve on the requested time.')
        return True#Reservation(reservation_id='', start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration), user=user, service_name=service_name, status=PENDING_CONFIRMATION_STATUS)
        
    def _can_cancel_reservation(self, reservation: Reservation, user: str, force_advance_cancelation: bool=False, force_past_slots: bool=False) -> bool|Exception:
        if not isinstance(reservation, Reservation) or reservation.user!=user:
            return NotPreviouslyBookedError('Cannot cancel.')
            
        if not force_past_slots:
            time_constraints = {'min_cancel_minutes': self.policy_manager.min_advance_cancelation_minutes * int(not force_advance_cancelation)}

            valid_times, msg = check_cancel_reservation_constraints(reservation_start_time=reservation.start_time, **time_constraints)
            if not valid_times:
                return PolicyError(msg)
        return True#reservation
     
     
    def can_cancel(self, user: str, reservation_id: str = None, start_time: datetime.datetime = None, service_name: str = None, force_advance_cancelation: bool=False, force_past_slots: bool=False)  -> bool|Exception:
        if bool(reservation_id) == bool(start_time):
            return ValueError('Must provide exactly one between reservation_id and start_time')
        
        if reservation_id is None:
            reservation = self.reservation_manager._find_reservation_by_inner_time(start_time)
        else:
            reservation = self.reservation_manager.get_reservation(reservation_id)
        if service_name is not None and service_name!=reservation.service_name:
            return ValueError('Cannot cancel. Different service than the booked one')
        return self._can_cancel_reservation(reservation=reservation, user=user, force_advance_cancelation=force_advance_cancelation, force_past_slots=force_past_slots)
        
        
    def can_update(self, user: str, new_start_time: datetime.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, old_reservation_id: str = None, old_start_time: datetime.datetime = None, force_default_grid: bool = True, force_past_slots: bool=False, force_advance_reservation: bool=False, force_advance_cancelation: bool=False)  -> bool|Exception:
        if old_reservation_id is None and old_start_time is None:
            return ValueError('Must provide exactly one between reservation_id and old_start_time')
        
        if old_reservation_id is None:
            reservation = self.reservation_manager._find_reservation_by_inner_time(old_start_time)
        else:
            reservation = self.reservation_manager.get_reservation(old_reservation_id)
        
        if reservation is None or reservation.user!=user:
            return NotPreviouslyBookedError('Cannot update. You dont have any reservation with the specified parameters')
        if all([p is None for p in [new_start_time, new_service_name, new_minutes_duration]]):
            return ValueError('Nothing to update. The reservation is exactly the same as the old one')
        
        validated_params = self.__validate_missing_data__(old_reservation=reservation, start_time=new_start_time, service_name=new_service_name, minutes_duration=new_minutes_duration)
        new_start_tim, new_service_name, new_minutes_duration = validated_params['start_time'], validated_params['service_name'], validated_params.pop('minutes_duration')
        new_end_time = new_start_time+timedelta(minutes=new_minutes_duration)
        if not force_past_slots:    
            time_constraints = {'min_reserve_minutes':self.policy_manager.min_advance_booking_minutes * int(not force_advance_reservation),
                                'min_cancel_minutes':self.policy_manager.min_advance_cancelation_minutes * int(not force_advance_cancelation)
                               }

            update_valid_times, msg = check_update_constraints(old_start_time=reservation.start_time, 
                                            old_end_time=reservation.end_time, 
                                            new_start_time=new_start_time, 
                                            new_end_time=new_end_time, 
                                            **time_constraints)
            if not update_valid_times:
                return PolicyError(msg)
        
        if reservation.start_time==new_start_time: ##NEW RES HAS SAME START TIME OF THE OLD ONE
            if new_end_time<=reservation.end_time: ##new reservation lasts less than previous (i.e. needs less slots)
                return True
            return self.can_reserve(start_time=reservation.end_time, service_name=new_service_name,
                                    minutes_duration=new_minutes_duration-minutes_between(reservation.start_time, reservation.end_time),
                                    force_default_grid=False, 
                                    force_past_slots=force_past_slots,
                                    force_advance_reservation=True) ##new reservation is longer than previous... need to check that the new needed slots (i.e. those after old end_time) are free
        
        if new_start_time>=reservation.end_time or new_end_time<=reservation.start_time: ## no overlap->just handled as new reservation
            return self.can_reserve(start_time=new_start_time, service_name=new_service_name,
                                    minutes_duration=new_minutes_duration,
                                    force_default_grid=force_default_grid,
                                    force_advance_reservation=force_advance_reservation,
                                    force_past_slots=force_past_slots
                                    )
        ### OVERLAP (new reservat partially overlap with old reservation) -> checking if the not overlapping slots are free and, if force_default_grid, the reservation start_time matches the 'grid' 
         ##since the two reservations overlap, the only involved segment will be the one containing the new reservation
        segment_involved = self.calendar.find_segment_containing(start_time=new_start_time, end_time=new_end_time, return_index=False)
        if segment_involved is None:
            return ClosingTimeError('Out of working hours')
        print('qui da can update')
        tmp_calendar = BusinessCalendar(slot_minutes_duration=self.calendar.slot_minutes_duration)
        tmp_calendar.add_segment(segment_involved.copy()) ##instantiating a new calendar made by only the involved segment (copy)
        old_res_slots = tmp_calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time)
        tmp_calendar.free_slots(old_res_slots) ##mimicking the cancelation of old_res slots in order to check if the new reservation is available
        grid_params = {}
        if not force_default_grid:
            grid_params = {'minutes_grid_span':5}
        available_slot_grids = tmp_calendar.get_available_booking_slots(min_start_time=new_start_time, max_start_time=new_start_time, 
                                                                         minutes_duration=new_minutes_duration, 
                                                                         **grid_params)
        if len(available_slot_grids)!=1 or not any(available_slot_grids[0]):
            return AlreadyBookedError('Cannot update. The requested time is already booked.')
        return True
            

    def make_reservation(self, service_name: str, start_time: datetime.datetime, user: str, minutes_duration: int=None, force_past_slots: bool=False, force_advance_reservation: bool=False, force_default_grid: bool=True, ):
        start_time = validate_time(start_time)
        if not start_time:
            return TypeError('start_time must be a datetime object containing date, hours and minutes of the requested reservation')
        if service_name not in self.policy_manager.services:
            return PolicyError(f'Cannot make the reservation: unknown service {service_name}')
        if start_time.minute % self.calendar.slot_minutes_duration:
            return ValueError('Cannot make the reservation: Invalid start time — please select a 5-minute aligned slot (e.g. 11:00, 12:25, 16:30...)')
        if user in ['admin', 'system']:
           return PermissionError('Cannot use admin here')
        
        if minutes_duration is None:
            minutes_duration = self._get_duration_from_service_name(service_name)
        end_time = start_time+timedelta(minutes=minutes_duration)
        
        if not force_past_slots:
            min_reserve_minutes = self.policy_manager.min_advance_booking_minutes * (not force_advance_reservation)
            valid_reserve_times, error_msg = check_new_reservation_constraints(start_time=start_time, end_time=end_time, min_reserve_minutes=min_reserve_minutes)
            if not valid_reserve_times:
                return PolicyError(f'Cannot reserve the reservation: Too late to reserve. Reservations must be requested at least {self.policy_manager.min_advance_booking_minutes} minutes before the start of the reservation.')

        
        
        slots_to_book = self.calendar.get_slots(start_time=start_time, end_time=end_time, same_segment_only=True)
        if not slots_to_book:
            return ClosingTimeError('Cannot make the reservation: Out of working hours')
        try:
            self.calendar._lock_slots(slots_to_book)
            self.calendar.reserve_slots(slots=slots_to_book)
            reservation_id = __generate_new_reservation_id__()
            new_reservation = Reservation(reservation_id=reservation_id, user=user, 
                                          service_name=service_name,
                                          start_time=start_time, end_time=end_time)
            self.reservation_manager.__insert_reservation_mapping__(new_reservation)
            return new_reservation

        except Exception as e:
            err_msg = 'Cannot make the reservation: ' + e.message
            return type(e)(err_msg)
        
        finally:
            self.calendar._unlock_slots(slots_to_book)
        

    def cancel_reservation(self, reservation_id: str, user: str, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        if force_past_slots:
            force_advance_cancelation=True
        try:
            reservation = self.reservation_manager.reservations_id_mappings[reservation_id]
            booked_user, booked_service, booked_start_time, booked_end_time = reservation.user, reservation.service_name, reservation.start_time, reservation.end_time
        except KeyError:
            return NotPreviouslyBookedError(f'Cannot cancel the reservation: You dont have any reservation with booking id: {reservation_id}')
        if booked_user!=user:
            return NotPreviouslyBookedError(f'Cannot cancel the reservation: You dont have any reservation with booking id: {reservation_id}')
               
        if not force_past_slots:
            min_cancel_minutes = self.policy_manager.min_advance_cancelation_minutes * (not force_advance_cancelation)
            valid_cancel_times, error_msg = check_cancel_reservation_constraints(reservation_start_time=reservation.start_time, min_cancel_minutes=min_cancel_minutes)
            if not valid_cancel_times:
                return PolicyError(f'Cannot cancel the reservation: Too late to cancel. Cancelations must be requested at least {self.policy_manager.min_advance_cancelation_minutes} minutes before the start of the reservation.')
           
       
        slots_to_unbook = self.calendar.get_slots(start_time=booked_start_time, end_time=booked_end_time, same_segment_only=True)
        try:
            self.calendar._lock_slots(slots_to_unbook)
            self.calendar.free_slots(slots_to_unbook)
            self.reservation_manager.__remove_reservation_mapping__(reservation)
            return reservation
        except Exception as e: 
            print(e) ##should never happen as far as a whole segment is deleted or a cancelation is done not-properly
            return e
        finally:
            self.calendar._unlock_slots(slots_to_unbook)

    
    def cancel_reservation_by_time(self, start_time: datetime.datetime, user: str, service_name: str=None, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        if force_past_slots:
            force_advance_cancelation=True
        start_time = validate_time(start_time)
        if not start_time:
            return TypeError('start_time must be a datetime object containing date, hours and minutes of the reservation to cancel')
        if service_name is not None and service_name not in self.policy_manager.services:
            return PolicyError(f'Cannot cancel. Unknown service {service_name}')
        if start_time.minute%self.policy_manager.default_slot_duration:
            return ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')
        
        if not force_past_slots:
            min_cancel_minutes = self.policy_manager.min_advance_cancelation_minutes * (not force_advance_cancelation)
            valid_cancel_times, error_msg = check_cancel_reservation_constraints(reservation_start_time=reservation.start_time, min_cancel_minutes=min_cancel_minutes)
            if not valid_cancel_times:
                return PolicyError(f'Cannot cancel the reservation: Too late to cancel. Cancelations must be requested at least {self.policy_manager.min_advance_cancelation_minutes} minutes before the start of the reservation.')
            
        to_warn = False
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
        if reservation.start_time!=start_time:
            warnings.warn(f'Original reservation was at {reservation.start_time}. Canceled anyway')
        return cancel_output


    def update_reservation(self, user: str, old_reservation_id: str, new_start_time: datetime.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, force_past_slots: bool=False, force_advance_cancelation: bool=False, force_advance_reservation=False):
        from slots_utils import get_consecutive_slots_join

        
                
        old_reservation = self.reservation_manager.get_reservation(old_reservation_id)
        if old_reservation is None or old_reservation.user != user:
            return NotPreviouslyBookedError(f'You dont have any reservation with booking id: {old_reservation_id}')
        
        old_user, old_service, old_start_time, old_end_time = old_reservation.user, old_reservation.service_name, old_reservation.start_time, old_reservation.end_time
        
        validated_missing_params = self.__validate_missing_data__(old_reservation=old_reservation, start_time=new_start_time, service_name=new_service_name, minutes_duration=new_minutes_duration)
        new_start_time, new_service_name, new_minutes_duration = validated_missing_params['start_time'], validated_missing_params['service_name'], validated_missing_params['minutes_duration']
        new_end_time = new_start_time+timedelta(minutes=new_minutes_duration)
        if old_service==new_service_name and old_start_time==new_start_time and old_end_time==new_end_time:
            return ValueError('Nothing to update. The new reservation parameters are the same as the old one')

        new_start_time = validate_time(new_start_time)
        if not new_start_time:
            return TypeError('new_start_time must be a datetime object containing date, hours and minutes of the reservation to cancel')
        if new_start_time.minute % self.policy_manager.default_slot_duration:
            return ValueError('Invalid start time — please select a 15-minute aligned slot (e.g. 11:00, 11:15, 11:30...)')
        if new_service_name not in self.policy_manager.services:
            return PolicyError('Unknown service')
        

        if not force_past_slots:
            update_constraints_params = {'min_cancel_minutes': self.policy_manager.min_advance_cancelation_minutes * (not force_advance_cancelation),
                                         'min_reserve_minutes': self.policy_manager.min_advance_booking_minutes * (not force_advance_reservation)
                                         }
            valid_update_times, error_msg = check_update_constraints(old_start_time=old_start_time, 
                                                                     old_end_time=old_end_time, 
                                                                     new_start_time=new_start_time,
                                                                     new_end_time = new_end_time, 
                                                                     **update_constraints_params)
            if not valid_update_times:
                return PolicyError(error_msg)
        
        slots_to_book = self.calendar.get_slots(start_time=new_start_time, end_time=new_end_time, same_segment_only=True)
        slots_to_free = self.calendar.get_slots(start_time=old_start_time, end_time=old_end_time, same_segment_only=True)
        if not slots_to_book or not slots_to_free:
            return ClosingTimeError('Out of working hours')
            
        slots_to_book, slots_to_free = get_consecutive_slots_join(slots_to_book, slots_to_free, how='diff')  ##slots_to_book and slots_to_free will be only the "exclusive" slots (i.e. not overlapping between them) to book/free
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
            new_reservation_id = __generate_new_reservation_id__()
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
        old_start_time = validate_time(old_start_time)
        if not old_start_time:
            return  TypeError('old_start_time must be valid datetime objects containing date, hours and minutes')
            
        if not force_past_slots:
            update_constraints_params = {'min_cancel_minutes': self.policy_manager.min_advance_cancelation_minutes * (not force_advance_cancelation),
                                         'min_reserve_minutes': self.policy_manager.min_advance_booking_minutes * (not force_advance_reservation)
                                         }
            valid_update_times, error_msg = check_update_constraints(old_start_time=old_start_time, 
                                                                     old_end_time=old_end_time, 
                                                                     new_start_time=new_start_time,
                                                                     new_end_time = new_end_time, 
                                                                     **update_constraints_params)
            if not valid_update_times:
                return PolicyError(error_msg)
        
        to_warn = False
        reservation = self.reservation_manager._find_reservation_by_inner_time(old_start_time)
        if reservation is None or reservation.user != user:
            return NotPreviouslyBookedError('You dont have any reservation at the chosen time')
        new_start_time = validate_time(new_start_time) if new_start_time is not None else validate_time(reservation.start_time)
        if reservation.start_time!=new_start_time:
            warnings.warn(f'Original reservation was at {reservation.start_time}.')
                
        return self.update_reservation(old_reservation_id=reservation.reservation_id, 
                                                user=user,
                                                new_service_name=new_service_name,
                                                new_start_time=new_start_time,
                                                new_minutes_duration=new_minutes_duration,
                                                force_advance_cancelation=force_advance_cancelation, 
                                                force_past_slots=force_past_slots)

    
        
        
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
        
    def __validate_missing_data__(self, start_time: datetime.datetime=None, service_name: str=None, minutes_duration: int=None, old_reservation: Reservation=None):
        final_dct = {'start_time':start_time, 'service_name':service_name, 'minutes_duration':minutes_duration}
        if old_reservation is None:
            if service_name is None:
                return final_dct
            final_dct['minutes_duration']=minutes_duration or self._get_duration_from_service_name(service_name)
        else:
            final_dct['start_time']=start_time or old_reservation.start_time
            final_dct['minutes_duration']=minutes_duration or (self._get_duration_from_service_name(service_name) if service_name is not None else minutes_between(old_reservation.start_time, old_reservation.end_time) )
            final_dct['service_name']=service_name or old_reservation.service_name
        return final_dct
    
       



class BusinessManagerWithConfirmation(BusinessManager):
    
    def __init__(self, reservation_manager: ReservationManager, calendar: BusinessCalendar, policy_manager: PolicyManager, default_grid_minutes: int=15, max_confirmation_minutes: int=15):
        super().__init__(reservation_manager=reservation_manager, calendar=calendar, policy_manager=policy_manager, default_grid_minutes=default_grid_minutes)
        self.max_confirmation_minutes = max_confirmation_minutes
        self.__updates_reservations__ = {}


    
    def cancel_all_not_confirmed_reservations(self, user: str=None, expired_only: bool=False):
        all_reservations = self.get_all_reservations() if user is None else self.get_user_reservations(user)
        all_not_confirmed_reservations = [r for r in all_reservations if not r.is_confirmed]
        if expired_only:
            all_not_confirmed_reservations = [r for r in all_not_confirmed_reservations if datetime.datetime.now() - r.status_change_timestamp > timedelta(minutes=self.max_confirmation_minutes)]
        for reservation in all_not_confirmed_reservations:
            reservation = super().cancel_reservation(reservation_id=reservation.reservation_id, user=reservation.user, force_advance_cancelation=True, force_past_slots=True) ##forcing cancelation of past reservations too
            try:
                reservation.status = DELETED_STATUS 
            except Exception as e: ##only happens if cancel fails... should never happen
                raise e
        all_not_confirmed_updates = [self.reservation_manager.get_reservation(r_id) for r_id in self.__updates_reservations__ if not expired_only or datetime.datetime.now() - self.__updates_reservations__[r_id] > timedelta(minutes=self.max_confirmation_minutes)]
        for reservation in all_not_confirmed_updates:
            self.__cancel_unconfirmed_inner_update__(reservation)
        return True
            
    
    def make_reservation(self, service_name: str, start_time: datetime.datetime, user: str, minutes_duration: int=None, force_past_slots: bool=False, force_advance_reservation: bool=False, force_default_grid: bool=True, ):
        reservation_result = super().make_reservation(user=user, service_name=service_name, start_time=start_time, minutes_duration=minutes_duration, 
                                        force_past_slots=force_past_slots, force_advance_reservation=force_advance_reservation,
                                        force_default_grid=force_default_grid) 
        if isinstance(reservation_result, Reservation):
            reservation_result.is_confirmed = False
            reservation_result.status = PENDING_CONFIRMATION_STATUS ##if reservation is correctly placed, it is a new reservation (i.e. no confirmation). 'pending' status by default
            
        return reservation_result
        """
        previously_requested_reservation = self.find_reservation(user=user, start_time=start_time, minutes_duration=minutes_duration, service_name=service_name)
        if previously_requested_reservation is not None:
            try:
                self.confirm_operation(reservation=previously_requested_reservation, operation='reserve', user=user)
                return previously_requested_reservation
            except:
                return AlreadyBookedError('Cannot reserve at the requested time. Already booked')
        return AlreadyBookedError('Cannot reserve at the requested time. Already booked')
        """
    
    def cancel_reservation(self, reservation_id: str, user: str, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        if force_past_slots:
            force_advance_cancelation=True
        reservation = self.find_reservation(reservation_id=reservation_id, user=user)
        if reservation is None:
            return NotPreviouslyBookedError('Cannot cancel. You dont have any reservation with the specified reservation_id')
        
        if not reservation.is_confirmed: ### cancelation on a not previously confirmed reservation
            return super().cancel_reservation(reservation_id=reservation_id, user=user, force_past_slots=force_past_slots, force_advance_cancelation=force_advance_cancelation) ###immediate cancelation - no confirmation
        """
        try:
            self.confirm_operation(reservation=reservation, operation='cancel', user=user) ##checking if cancelation was already requested and it's possible to confirm...
            return super().cancel_reservation(reservation_id=reservation_id, user=user, force_past_slots=force_past_slots, force_advance_cancelation=True) #in such case, proceed with cancelation
        except: #otherwise, check if can be canceled, and mark reservation as pending for cancelation (i.e. waiting for further confirmation)
        """   
        can_cancel_result = self.can_cancel(reservation_id=reservation_id, user=user, force_advance_cancelation=force_advance_cancelation, force_past_slots=force_past_slots)
        if not isinstance(can_cancel_result, Exception) and can_cancel_result is not False:
            reservation.status = PENDING_CANCELATION_STATUS ##marking as "pending cancelation" till user confirms it
            return reservation
        return can_cancel_result
    
            
        
    def update_reservation(self, user: str, old_reservation_id: str, new_start_time: datetime.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, force_past_slots: bool=False, force_advance_cancelation: bool=False, force_advance_reservation=False):
        from slots_utils import get_consecutive_slots_join
        
        old_reservation = self.find_reservation(reservation_id=old_reservation_id, user=user)
        if old_reservation is None:
            return NotPreviouslyBookedError('Cannot update. You dont have any reservation with the requested parameters')
        if not old_reservation.is_confirmed: ##if updating a not-previously confirmed reservations, just moving on with normal update (no confirmation), i.e. removing old reservation and creating the new one
            #return ConfirmationError('Cannot update a reservation that wasnt finalized')
            update_result = super().update_reservation(user=user, old_reservation_id=old_reservation_id, new_start_time=new_start_time, new_service_name=new_service_name, new_minutes_duration=new_minutes_duration, force_past_slots=force_past_slots, force_advance_cancelation=force_advance_cancelation, force_advance_reservation=force_advance_reservation)
            if not isinstance(update_result, Exception):
                old_reservation, new_reservation = update_result
                new_reservation.is_confirmed, new_reservation.status, old_reservation.status = False, PENDING_CONFIRMATION_STATUS, DELETED_STATUS
            return update_result
            
            
        validated_params = self.__validate_missing_data__(old_reservation=old_reservation, start_time=new_start_time, service_name=new_service_name, minutes_duration=new_minutes_duration)
        validated_params['end_time'] = validated_params['start_time'] + timedelta(minutes=validated_params['minutes_duration'])
        new_start_time, new_end_time, new_service_name, new_minutes_duration = validated_params['start_time'], validated_params['end_time'], validated_params['service_name'], validated_params.pop('minutes_duration')
        if old_reservation.service_name==new_service_name and old_reservation.start_time==new_start_time and old_reservation.end_time==new_end_time:
            return ValueError('Nothing to update. The new reservation parameters are the same as the old one')
        new_res_slots = self.calendar.get_slots(start_time=new_start_time, end_time=new_end_time, same_segment_only=True)
        old_res_slots = self.calendar.get_slots(start_time=old_reservation.start_time, end_time=old_reservation.end_time, same_segment_only=True)
        if not new_res_slots or not old_res_slots:
            return ClosingTimeError('Cannot update. Out of working hours')

        """
        slots_to_book, slots_to_free = get_consecutive_slots_join(slots_to_book, slots_to_free, how='diff')  ##slots_to_book and slots_to_free will be only the "exclusive" slots (i.e. not overlapping between them) to book/free

        previously_requested_reservation = getattr(old_reservation, 'update_reservation') ##previously requested update , if any
        if isinstance(previously_requested_reservation, Reservation) and previously_requested_reservation.start_time==new_start_time and previously_requested_reservation.service_name==new_service_name and previously_requested_reservation.minutes_duration==new_minutes_duration:
            try:
                self.confirm_operation(reservation=old_reservation, operation='update', user=user) ##confirming if update request was the same as the current one... only confirming if status was 'pending update'
                #raise NotImplementedError('free_slots(previously_requested_reservation.slots) ###TODO ...')
                self.calendar._lock_slots(slots_to_free)
                self.calendar.free_slots(slots_to_free) 
                self.reservation_manager.__insert_reservation_mapping__(previously_requested_reservation) ###proceding with update as normal update
                self.reservation_manager.__remove_reservation_mapping__(old_reservation)

                return super().update_reservation(user=user, old_reservation_id=old_reservation_id, new_start_time=new_start_time, new_service_name=new_service_name, new_minutes_duration=new_minutes_duration)
            except:
                pass
        
        """
            
                    
        ##creating a new update (will need further confirmation)
        try:
            prev_update_res_slots = self.calendar.get_slots(start_time=old_reservation.update_reservation.start_time, end_time=old_reservation.update_reservation.end_time, same_segment_only=True)
        except:
            prev_update_res_slots = []
            
        slots_to_book = get_consecutive_slots_join(get_consecutive_slots_join(new_res_slots, old_res_slots, how='diff')[0],
                                              prev_update_res_slots, 
                                              how='diff')[0]
        slots_to_free = get_consecutive_slots_join(get_consecutive_slots_join(prev_update_res_slots, old_res_slots, how='diff')[0],
                                              new_res_slots, 
                                              how='diff')[0]
        try:
            self.calendar._lock_slots(slots_to_book)
            self.calendar._lock_slots(slots_to_free)
            self.calendar.reserve_slots(slots_to_book) ### reserving the new slots. Only works if update is doable (i.e. slots are all free)

            self.calendar.free_slots(slots_to_free)

            new_reservation = Reservation(reservation_id=__generate_new_reservation_id__(), user=user, start_time=new_start_time, end_time=new_end_time, service_name=new_service_name, status=PENDING_CONFIRMATION_STATUS, is_confirmed=False)
            old_reservation.status = PENDING_UPDATE_STATUS
            old_reservation.update_reservation = new_reservation ###setting the new reservation as a parameter within the old one. This way the system will create it 
            self.__updates_reservations__[old_reservation.reservation_id] = new_reservation.timestamp
            return (old_reservation, new_reservation)
        except:
            return AlreadyBookedError('Cannot update. The requested time is not available for booking')
        finally:
            self.calendar._unlock_slots(slots_to_book)
            self.calendar._unlock_slots(slots_to_free)

        
                    
    
    
    def confirm_operation_by_details(self, operation: str, user: str, old_reservation_id: str=None, old_start_time: datetime.datetime=None, old_service_name: str=None, old_minutes_duration: int=None, new_start_time: datetime.datetime=None, new_service_name: str=None, new_minutes_duration: int=None):
        old_start_time, new_start_time = validate_time(old_start_time) or None, validate_time(new_start_time) or None
        old_reservation = self.find_reservation(match_notexact_time=False if operation=='reserve' else True,
                                                    user=user, 
                                                    start_time=old_start_time, 
                                                    reservation_id=old_reservation_id,
                                                    minutes_duration=old_minutes_duration,
                                                    service_name=old_service_name)
        if old_reservation is None:
            raise NotPreviouslyBookedError('Cannot confirm. Cannot find a reservation with the specified parameters')
        if operation=='update':
            new_res_details = self.__validate_missing_data__(start_time=new_start_time, service_name=new_service_name, minutes_duration=new_minutes_duration, old_reservation=old_reservation)
            new_res_details['end_time'] = new_res_details['start_time'] + timedelta(minutes=new_res_details.pop('minutes_duration'))
            if not isinstance(getattr(old_reservation, 'update_reservation', None), Reservation):
                raise ConfirmationError('Cannot update. You should call update_reservation with the chosen parameters to make a new update_request before confirming')
            if any(getattr(old_reservation.update_reservation, attr, -1)!=new_res_details[attr] for attr in new_res_details):
                raise ConfirmationError('Cannot update. The new update reservation parameters are different than the previously requested one. You should probably call update_reservation with such parameters')
        return self.confirm_operation(operation=operation, user=user, reservation=old_reservation)
    
    
    def confirm_operation(self, user: str, reservation: Reservation, operation: str, **kwargs):
        """
        Changes reservation status, if it respects confirmation status flow: 
        i.e. if its current status is waiting for confirmation, and confirmation was requested no more than max_confirmation_minutes ago. 
        operation must be one among {'reserve', 'cancel'} (for updates, should confirm 'cancel' on past_reservation and confirm 'reserve' on new_reservation)
        """
        from slots_utils import get_consecutive_slots_join

        def __apply_status_transition__(reservation, final_status, feasible_current_statuses):
            """
            if reservation.status == final_status:
                return reservation
            """
            if reservation.status not in feasible_current_statuses:
                raise ConfirmationError('Cannot confirm -- wrong status')
            if datetime.datetime.now() - reservation.status_change_timestamp > timedelta(minutes=self.max_confirmation_minutes):
                raise ConfirmationError(f'Cannot {operation}. Too much time after {operation} request')
            reservation.status = final_status
            return reservation
            
        allowed_statuses = {'reserve': (CONFIRMED_STATUS, [PENDING_CONFIRMATION_STATUS]),
                            'cancel':  (DELETED_STATUS, [PENDING_CANCELATION_STATUS]),
                            'update': (DELETED_STATUS, [PENDING_UPDATE_STATUS])
                            }
        if operation not in allowed_statuses:
            raise ValueError(f'Wrong confirm operation {operation}. Must be one of {feasible_current_statuses.keys()}')
        if user!=reservation.user:
            raise NotPreviouslyBookedError('You dont have any reservation with the specified details')
        
        
   
   
        if operation == 'update':
            if not isinstance(getattr(reservation, 'update_reservation', None), Reservation):
                raise ConfirmationError('Cannot confirm update. No update associated')
            
        
        final_status, feasible_current_statuses = allowed_statuses[operation]
        try:
            reservation = __apply_status_transition__(reservation=reservation, final_status=final_status, feasible_current_statuses=feasible_current_statuses)
            if operation == 'reserve':
                reservation.is_confirmed = True
                return reservation
            if operation == 'cancel':
                return super().cancel_reservation(reservation_id=reservation.reservation_id, user=user, force_advance_cancelation=True, force_past_slots=True)
            if operation=='update':
                new_start_time, new_service_name, new_end_time = reservation.update_reservation.start_time, reservation.update_reservation.service_name, reservation.update_reservation.end_time
                slots_to_book = self.calendar.get_slots(start_time=new_start_time, end_time=new_end_time, same_segment_only=True)
                slots_to_free = self.calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time, same_segment_only=True)
                if not slots_to_book or not slots_to_free:
                    return ClosingTimeError('Out of working hours')
                slots_to_book, slots_to_free = get_consecutive_slots_join(slots_to_book, slots_to_free, how='diff')  ##slots_to_book and slots_to_free will be only the "exclusive" slots (i.e. not overlapping between them) to book/free
                try:
                    self.calendar._lock_slots(slots_to_free)
                    self.calendar.free_slots(slots_to_free) ##releasing old reservation slots -- NO NEED TO BOOK NEW SLOTS. IT WAS ALREADY DONE AT THE PREVIOUS REQUEST
                    self.confirm_operation(user=user, reservation=reservation.update_reservation, operation='reserve') ##setting the status as 'confirmed' to the new reservation
                    self.reservation_manager.__insert_reservation_mapping__(reservation.update_reservation) ###inserting the new update reservation among reservations
                    self.reservation_manager.__remove_reservation_mapping__(reservation) ##removing old ones
                    self.__updates_reservations__.pop(old_reservation.reservation_id)
                finally:
                    self.calendar._unlock_slots(slots_to_free)
                
            
        except Exception as e:
            raise e
        
    
    def find_reservation(self, user: str, reservation_id: str=None, start_time: datetime.datetime=None,  minutes_duration: int=None, service_name: str=None, match_notexact_time: bool=False):
        if reservation_id is None and start_time is None:
            raise ValueError(f'Must provide one among {reservation_id} and {start_time}')
        if reservation_id is not None:
            reservation = self.reservation_manager.get_reservation(reservation_id)
        else:
            reservation = self.reservation_manager._find_reservation_by_inner_time(start_time) if match_notexact_time else self.reservation_manager.get_reservation_by_start_time(start_time)
        if reservation is None:
            return None
        
        validated_missing_params = self.__validate_missing_data__(old_reservation=reservation, start_time=start_time, service_name=service_name, minutes_duration=minutes_duration)
        start_time, service_name, minutes_duration = validated_missing_params['start_time'], validated_missing_params['service_name'], validated_missing_params['minutes_duration']
            
        reservation_duration = minutes_between(reservation.start_time, reservation.end_time)
        if reservation.user!=user or reservation_duration!=minutes_duration or reservation.service_name!=service_name:
            return None
        if start_time!=reservation.start_time:
            warnings.warn(f'Actual start time is {reservation.start_time}')
            start_time = reservation.start_time
        return reservation
        
            
    def __cancel_unconfirmed_inner_update__(self, reservation: Reservation):
        from slots_utils import get_consecutive_slots_join

        requested_update_reservation = getattr(reservation, 'update_reservation', None)
        if requested_update_reservation is None:
            return 
        res_slots = self.calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time, same_segment_only=True)
        update_res_slots = self.calendar.get_slots(start_time=requested_update_reservation.start_time, end_time=requested_update_reservation.end_time, same_segment_only=True)
        
        slots_to_free = get_consecutive_slots_join(update_res_slots, res_slots, how='diff')[0]
        try:
            self.calendar._lock_slots(slots_to_free)
            self.calendar.free_slots(slots_to_free) ##releasing old reservation slots -- NO NEED TO BOOK NEW SLOTS. IT WAS ALREADY DONE AT THE PREVIOUS REQUEST
            reservation.update_reservation = None
            if reservation.status == PENDING_UPDATE_STATUS:
                reservation.status = CONFIRMED_STATUS
            self.__updates_reservations__.pop(reservation.reservation_id)
        except Exception as e:
            raise e
        finally:
            self.calendar._unlock_slots(slots_to_free)
        return True