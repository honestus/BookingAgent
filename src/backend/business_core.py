from __future__ import annotations    
import datetime as dt, warnings
from datetime import timedelta
from collections import defaultdict
from utils.datetimes_utils import  map_datetime_to_default
from backend.policy import PolicyManager, Service
from backend.business_calendar import BusinessCalendar, Slot
from backend.reservations import *
from backend.domain_errors import *
from backend.business_event import *
from shared.user_role import UserRole

from enum import Enum
        

class BusinessCore:
    

    class ReservationOperationContext:
        def __init__(self, new_reservation: Reservation = None, existing_reservation_id: str=None, new_res_slots: list[Slot]=None, existing_res_slots: list[Slot]=None):
            self.new_reservation = new_reservation
            self.new_res_slots = new_res_slots
            self.existing_reservation_id = existing_reservation_id
            self.existing_res_slots = existing_res_slots
            
    class ServiceOperationContext:
        def __init__(self, old: Service = None, new: Service = None):
            self.old = old
            self.new = new

    def __init__(self, reservation_manager: ReservationManager, calendar: BusinessCalendar, policy_manager: PolicyManager, default_grid_minutes: int = 15):
        self.calendar = calendar
        self.policy_manager = policy_manager
        self.reservation_manager = reservation_manager
        self.default_grid_minutes = default_grid_minutes


        
    def _prepare_make_reservation(self, start_time: dt.datetime, service_name: str, minutes_duration: int=None, user: str=None, force_default_grid: bool=True, force_advance_reservation: bool=False, force_past_slots: bool=False)  -> bool|Exception:
        """Returns ReservationOperationContext if the slots are free, False otherwise.
        Including user as a parameter because, in the future, checks on the user might be added (e.g. only limit to max n active reservations by user)
        """
        from backend.domain_logic import check_reserve_time_constraints
        
        
        if service_name not in self.policy_manager.services:
            return False, PolicyError(f'Cannot make the reservation: unknown service {service_name}')
        
        start_time = map_datetime_to_default(start_time, ignore_seconds=True)
        if minutes_duration is None:
            minutes_duration = self._get_duration_from_service_name(service_name)
           
        if not force_past_slots:
            min_advance_minutes = self.policy_manager.min_advance_booking_minutes * int(not force_advance_reservation)
            valid_times, msg = check_reserve_time_constraints(start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration), min_advance_minutes=min_advance_minutes)
            if not valid_times:
                return False, PolicyError(msg)
    
        segment_to_reserve = self.calendar.find_segment_containing(start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration))
        if segment_to_reserve is None:
            return False, ClosingTimeError('Cannot reserve on the requested time. Out of working hours')
        if bool(segment_to_reserve.timedelta_mismatch_from_previous_default(start_time, default_minutes_grid_range=self.default_grid_minutes if force_default_grid else None)):
            return False, PolicyError(f'Cannot reserve at {start_time}. Time not aligned to default expected slot times')
        
        slots_to_book = segment_to_reserve.get_slots_slice(start_time, start_time+timedelta(minutes=minutes_duration))
        if any(slot.is_booked() for slot in slots_to_book):
            return False, AlreadyBookedError('Cannot reserve. Already booked')
            
        reservation = Reservation(reservation_id=generate_new_reservation_id(), start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration), user=user, service_name=service_name)
        return True, BusinessCore.ReservationOperationContext(new_reservation=reservation, new_res_slots=slots_to_book)
        
    def _prepare_cancel_reservation(self, reservation_id: str, force_advance_cancelation: bool=False, force_past_slots: bool=False) -> bool|Exception:
        from backend.domain_logic import check_delete_time_constraints
        
        reservation = self.reservation_manager.get_reservation(reservation_id)
        if not isinstance(reservation, Reservation):# or reservation.user!=user:
            return False, NotPreviouslyBookedError('Cannot cancel. No reservation booked with the specified details.')
            
        if not force_past_slots:
            time_constraints = {'min_advance_minutes': self.policy_manager.min_advance_cancelation_minutes * int(not force_advance_cancelation)}
            valid_times, msg = check_delete_time_constraints(reservation_start_time=reservation.start_time, **time_constraints)
            if not valid_times:
                return False, PolicyError(msg)
        
        return True, BusinessCore.ReservationOperationContext(existing_reservation_id=reservation.reservation_id)
        
        
    def _prepare_update_reservation(self, existing_reservation_id: str, new_user: str=None, new_start_time: dt.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, force_default_grid: bool = True, force_past_slots: bool=False, force_advance_reservation: bool=False, force_advance_cancelation: bool=False)  -> bool|Exception:
        from backend.slots_utils import get_consecutive_slots_join
        from backend.domain_logic import check_update_time_constraints
        
        if all([p is None for p in [new_start_time, new_service_name, new_minutes_duration]]):
            return False, ValueError("Nothing to update. Please provide at least one among 'new_start_time', 'new_service_name', 'new_minutes_duration' ")
          
        old_reservation = self.reservation_manager.get_reservation(existing_reservation_id)
        if old_reservation is None:# or old_reservation.user!=new_user:
            return False, NotPreviouslyBookedError('Cannot update. You dont have any reservation with the specified parameters')
        
        if new_start_time is not None:
            new_start_time = map_datetime_to_default(new_start_time, ignore_seconds=True)
        reservation_params = self._resolve_reservation_params_with_defaults(existing_reservation=old_reservation, start_time=new_start_time, service_name=new_service_name, minutes_duration=new_minutes_duration)
        new_user, new_start_time, new_service_name, new_minutes_duration = reservation_params['user'], reservation_params['start_time'], reservation_params['service_name'], reservation_params['minutes_duration']
        new_end_time = new_start_time+timedelta(minutes=new_minutes_duration)
        
        if old_reservation.user==new_user and old_reservation.service_name==new_service_name and old_reservation.start_time==new_start_time and old_reservation.end_time==new_end_time:
            return False, ValueError('Nothing to update. The new reservation parameters are the same as the old one')
        if new_service_name not in self.policy_manager.services:
            return PolicyError('Unknown service')
        
        if not force_past_slots:    
            time_constraints = {'min_advance_reserve_minutes': self.policy_manager.min_advance_booking_minutes * int(not force_advance_reservation),
                                'min_advance_cancel_minutes': self.policy_manager.min_advance_cancelation_minutes * int(not force_advance_cancelation)
                               }

            update_valid_times, msg = check_update_time_constraints(old_start_time=old_reservation.start_time, 
                                            old_end_time=old_reservation.end_time, 
                                            new_start_time=new_start_time, 
                                            new_end_time=new_end_time, 
                                            **time_constraints)
            if not update_valid_times:
                return False, PolicyError(msg)
        
        
        new_res_slots = self.calendar.get_slots(new_start_time, new_end_time, same_segment_only=True)
        if not new_res_slots:
            return False, ClosingTimeError('Cannot reserve. Out of working hours')
        
        if new_start_time!=old_reservation.start_time:
            segment_to_reserve = self.calendar.find_segment_containing(start_time=new_start_time, end_time=new_end_time, return_index=False)
            if bool(segment_to_reserve.timedelta_mismatch_from_previous_default(new_start_time, default_minutes_grid_range=self.default_grid_minutes if force_default_grid else None)):
                return False, PolicyError(f'Cannot reserve at {new_start_time}. Time not aligned to default expected slot times')
        
        old_res_slots = self.calendar.get_slots(old_reservation.start_time, old_reservation.end_time, same_segment_only=True)
        slots_to_free, slots_to_book = get_consecutive_slots_join(old_res_slots, new_res_slots, how='difference')
        
        if (prev_inner_upd := old_reservation.get_associated_update_reservation()) and not prev_inner_upd.is_confirmation_expired():
            prev_inner_update_slots = self.calendar.get_slots(prev_inner_upd.start_time, prev_inner_upd.end_time, same_segment_only=True)
            slots_to_book, _ = get_consecutive_slots_join(slots_to_book, prev_inner_update_slots, how='difference')
        
        if any(s.is_booked() for s in slots_to_book):
            return False, AlreadyBookedError('Cannot reserve at the requested time. Already booked')
        new_reserv = Reservation(reservation_id=generate_new_reservation_id(), start_time=new_start_time, end_time=new_end_time, user=new_user, service_name=new_service_name)
        return True, BusinessCore.ReservationOperationContext(new_reservation=new_reserv, existing_reservation_id=old_reservation.reservation_id, new_res_slots=new_res_slots, existing_res_slots=old_res_slots)
        

    

    def _prepare_add_service(self, service_name: str, price: float, minutes_duration: int, description: str = None):
        from backend.validate_utils import validate_service_params
        
        if service_name in self.policy_manager.services:
            return (False, AlreadyBookedError(f'Cannot add. {service_name} already existing'))
        
        if description is None:
            description=''
        validate_service_params(service_name, price, minutes_duration, description)            
        new_service = Service(service_name=service_name, price=price, minutes_duration=minutes_duration, description=description)
        return (True, BusinessCore.ServiceOperationContext(new=new_service))            
  

    def _prepare_remove_service(self, service_name: str):
        if (existing_service:=self.policy_manager.services.get(service_name, None)) is None:
            return (False, KeyError(f'Cannot remove. Service {service_name} not present'))
        return (True, BusinessCore.ServiceOperationContext(old=existing_service))
        
  
    def _prepare_update_service(self, existing_service_name: str, price: float = None, minutes_duration: int = None, description: str = None):
        from backend.validate_utils import validate_service_params

        if (existing_service:=self.policy_manager.services.get(existing_service_name, None)) is None:
            return (False, KeyError(f'Cannot update. Service {existing_service_name} not present'))
        
        curr_service_params = BusinessCore._resolve_service_params_with_defaults(existing_service=existing_service, price=price, minutes_duration=minutes_duration, description=description)    
        validate_service_params(**curr_service_params)        
        new_service = Service(**curr_service_params)
        return (True, BusinessCore.ServiceOperationContext(old=existing_service, new=new_service))
        

    async def make_reservation(self, service_name: str, start_time: dt.datetime, user: str, minutes_duration: int=None, actor: UserRole = UserRole.USER, force_past_slots: bool=False, force_advance_reservation: bool=False, force_default_grid: bool=True, ):     
        is_reserv_possible, reserv_context = self._prepare_make_reservation(user=user, service_name=service_name, start_time=start_time, minutes_duration=minutes_duration, force_past_slots=force_past_slots, force_advance_reservation=force_advance_reservation, force_default_grid=force_default_grid)
        if not is_reserv_possible:
            raise reserv_context

        return await self._make_reservation(reserv_context, actor=actor)  
            

    async def cancel_reservation(self, reservation_id: str, actor: UserRole = UserRole.USER, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        is_delete_possible, reserv_context = self._prepare_cancel_reservation(reservation_id=reservation_id, force_past_slots=force_past_slots, force_advance_cancelation=force_advance_cancelation)
        if not is_delete_possible:
            raise reserv_context
                   
        return await self._cancel_reservation(reserv_context, actor=actor)      


    async def update_reservation(self, existing_reservation_id: str, new_start_time: dt.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, actor: UserRole = UserRole.USER, force_default_grid: bool=True, force_past_slots: bool=False, force_advance_cancelation: bool=False, force_advance_reservation=False):                    
        is_update_possible, update_context = self._prepare_update_reservation(existing_reservation_id=existing_reservation_id, 
                                                            new_start_time=new_start_time, 
                                                            new_minutes_duration=new_minutes_duration, 
                                                            new_service_name=new_service_name,
                                                            force_default_grid=force_default_grid,
                                                            force_past_slots=force_past_slots, 
                                                            force_advance_cancelation=force_advance_cancelation, 
                                                            force_advance_reservation=force_advance_reservation)
        if not is_update_possible:
            raise update_context
        return await self._update_reservation(update_context, actor=actor)
        
        

    def add_service(self, service_name: str, price: float, minutes_duration: int, description: str = '', actor: UserRole = UserRole.ADMIN):
        add_doable, obj = self._prepare_add_service(service_name=service_name, price=price, minutes_duration=minutes_duration, description=description)
        if not add_doable:
            raise obj
        service = self.policy_manager.add_service(service=obj)
        
        return BusinessEvent(ServiceEventType.CREATED, data=BusinessEvent.EventData(new=service), actor=actor)
         
         
    def update_service(self, existing_service_name: str, new_price: float = None, new_minutes_duration: int = None, new_description: str = None, actor: UserRole = UserRole.ADMIN):
        update_doable, obj = self._prepare_update_service(existing_service_name = existing_service_name, price = new_price, minutes_duration = new_minutes_duration, description = new_description)
        if not update_doable:
            raise obj
        old_s, new_s = self.policy_manager.update_service(service_name=obj.old.service_name, price=new_price, minutes_duration=new_minutes_duration, description=new_description)
        return BusinessEvent(ServiceEventType.REPLACED, data=BusinessEvent.EventData(old=old_s, new=new_s), actor=actor)
        
        
    def remove_service(self, service_name: str, actor: UserRole = UserRole.ADMIN):
        remove_doable, obj = self._prepare_remove_service(service_name)
        if not remove_doable:
            raise obj
        self.policy_manager.remove_service(service_name=obj.old.service_name)
        return BusinessEvent(ServiceEventType.DELETED, data=BusinessEvent.EventData(old=obj.old), actor=actor)


    def get_available_services(self, actor: UserRole = UserRole.USER) -> list[Service]:
        services = list(self.policy_manager.services.values())
        return BusinessEvent(event_type=ServiceEventType.NOOP, actor=actor, data=BusinessEvent.EventData(new=services))
        
        
    def add_new_calendar(self, calendar: BusinessCalendar, actor: UserRole = UserRole.ADMIN):
        self.calendar = self.calendar.join(calendar)
        return BusinessEvent(SystemEventType.CALENDAR_UPDATED, data=BusinessEvent.EventData(new=calendar), actor=actor)
        
        
    def remove_time_from_calendar(self, start_time: dt.datetime, end_time: dt.datetime, actor: UserRole = UserRole.ADMIN):
        raise NotImplementedError('')
        if end_time<=start_time:
            raise ValueError('End time must be after start_time')
        self.calendar.remove_segment(start_time=start_time, end_time=end_time, raise_error_if_any_booking=True)
        return BusinessEvent(SystemEventType.CALENDAR_UPDATED, data=BusinessEvent.EventData(old=(start_time, end_time), actor=actor))
        
        
    def get_user_reservations(self, user: str, actor: UserRole = UserRole.USER) -> list[Reservation]:
        user_reservations = self.reservation_manager.get_reservations_by_user(user)
        return BusinessEvent(event_type=ReservationEventType.NOOP, actor=actor, data=BusinessEvent.EventData(new=user_reservations))


    def get_daily_reservations(self, date: dt.date, actor: UserRole = UserRole.USER) -> list[Reservation]:
        daily_reservations = self.reservation_manager.get_reservations_by_date(date)
        return BusinessEvent(event_type=ReservationEventType.NOOP, actor=actor, data=BusinessEvent.EventData(new=daily_reservations))

    def get_default_opening_hours(self, actor: UserRole = UserRole.USER):
        op_hours = list(self.policy_manager.opening_hours)
        return BusinessEvent(event_type=SystemEventType.NOOP, actor=actor, data=BusinessEvent.EventData(new=op_hours))
        
        
    def get_daily_opening_hours(self, date: dt.date, actor: UserRole = UserRole.USER):
        from utils.datetimes_utils import to_default_tz
        
        date_start, date_end = to_default_tz(dt.datetime.combine(date, dt.time.min)), to_default_tz(dt.datetime.combine(date, dt.time.max))
        segments_involved = self.calendar._get_segments_involved(start_time= date_start, end_time = date_end)
        if segments_involved:
            op_hours = [(max(date_start, segment.start_time), min(date_end, segment.end_time)) for segment in segments_involved]
        else:
            op_hours = []
        return BusinessEvent(event_type=SystemEventType.NOOP, actor=actor, data=BusinessEvent.EventData(new=op_hours))


    def get_all_reservations(self, actor: UserRole = UserRole.ADMIN):
        reservations = sorted(self.reservation_manager.reservations_id_mappings.values(), key=lambda x: x.start_time)
        return BusinessEvent(event_type=ReservationEventType.NOOP, actor=actor, data=BusinessEvent.EventData(new=reservations))
        
        
    """
    def is_available(self, start_time: dt.datetime, service_name: str, minutes_duration: int = None, as_error: bool = True):
        if service_name not in self.policy_manager.services:
            return PolicyError('Cannot check for availability. Unknown service') if as_error else False
        
        start_time = map_datetime_to_default(start_time, ignore_seconds=True)
        if minutes_duration is None:
            minutes_duration = self._get_duration_from_service_name(service_name)
        
        is_available = self.calendar.is_available_timeframe(start_time=start_time, end_time=start_time+timedelta(minutes=minutes_duration), as_int_error=True)
        
        if is_available==-1:
            return ClosingTimeError('The chosen datetime is not available. We are closed') if as_error else False
        elif not is_available:
            return AlreadyBookedError('The chosen datetime is not available. Already booked')  if as_error else False
        return True
    """
        
    def get_available_datetimes(self, service_name: str, min_start_time: dt.datetime, max_start_time: dt.datetime = None, minutes_duration: int = None, force_past_slots: bool = False, force_advance_reservation: bool = False, actor: UserRole = UserRole.USER):
        from utils.datetimes_utils import map_datetime_to_default
        if max_start_time is None:
            max_start_time = min_start_time
            
        try:
            min_start_time = map_datetime_to_default(min_start_time, ignore_seconds=True)
            max_start_time = map_datetime_to_default(max_start_time, ignore_seconds=True)
        except:
            raise TypeError('min_start_time and end_time must be valid datetime objects containing date, hours and minutes')
        if max_start_time<min_start_time:
            raise ValueError('max_start_time cannot be a previous datetime than min_start_time')
        
        
        curr_time = map_datetime_to_default(dt.datetime.now(tz=min_start_time.tzinfo), ignore_seconds=True, map_to_default_tz=False)
        
        if not force_past_slots:
            min_adv_delta = timedelta(minutes=0 if force_advance_reservation else self.policy_manager.min_advance_booking_minutes)
            if max_start_time < curr_time+min_adv_delta:
                raise PastTimeError('Cannot look for availabilities on past/close timeframes')
            if min_start_time < curr_time+min_adv_delta:
                warnings.warn('Providing availabilities on future slots only.')
                min_start_time = curr_time+min_adv_delta
        
        
        available_slots = self.calendar.get_available_booking_slots(min_start_time=min_start_time, max_start_time=max_start_time, minutes_duration=minutes_duration, 
                                                              minutes_grid_span=15)
        default, special = [str(slot.start_time) for s in list(map(lambda x: x[0], available_slots)) for slot in s], \
                           [str(slot.start_time) for s in list(map(lambda x: x[1], available_slots)) for slot in s]
        return BusinessEvent(event_type=SystemEventType.NOOP, actor=actor, data=BusinessEvent.EventData(new=(default, special)) )


        
    async def _make_reservation(self, reservation_context: ReservationOperationContext, actor: UserRole, expiry_time: dt.datetime=None):
        reservation = reservation_context.new_reservation
        slots_to_book = reservation_context.new_res_slots or self.calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time, same_segment_only=True) #safe check -> retrieving slots from reservation times.
        
        if not slots_to_book:
            raise ClosingTimeError('Cannot make the reservation: Out of working hours')
        
        are_slots_reserved = False
        try:
            are_slots_reserved = await self.calendar.reserve_slots(slots=slots_to_book, expiry_time=expiry_time)
            await self.reservation_manager.insert_reservation(reservation)
            return BusinessEvent(event_type=ReservationEventType.CREATED, actor=actor, data=BusinessEvent.EventData(new=reservation))
        except Exception as e:
            if are_slots_reserved:
                await self.calendar.free_slots(slots=slots_to_book)
            err_msg = 'Cannot make the reservation: ' + e.message
            raise type(e)(err_msg)
            
            
            
    async def _cancel_reservation(self, reservation_context: ReservationOperationContext, actor: UserRole):
        res_id = reservation_context.existing_reservation_id
        
        try:
            reservation = await self.reservation_manager.remove_reservation(res_id)
        except ValueError:
            raise NotPreviouslyBookedError('Cannot cancel. The reservation does not exist')

        if reservation_context.existing_res_slots is not None:
            slots_to_unbook = reservation_context.existing_res_slots
        else:
            slots_to_unbook = self.calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time, same_segment_only=True) #safe check -> retrieving slots from reservation times.
        
        #if not slots_to_unbook: 
            #await self.reservation_manager.insert_reservation(reservation)
            #raise ClosingTimeError('Cannot cancel the reservation: Out of working hours')
        
        await self.calendar.free_slots(slots_to_unbook)        
        return BusinessEvent(event_type=ReservationEventType.DELETED, actor=actor, data=BusinessEvent.EventData(old=reservation))         
        
    
    async def _update_reservation(self, reservation_context: ReservationOperationContext, actor: UserRole, expiry_time: dt.datetime=None):
        from backend.slots_utils import get_consecutive_slots_join
        
        old_reservation = self.reservation_manager.get_reservation(reservation_context.existing_reservation_id)
        new_reservation = reservation_context.new_reservation
        if not old_reservation:
            raise NotPreviouslyBookedError('Cannot update. The old_reservation is not booked.')
        
        if old_reservation.start_time == new_reservation.start_time and old_reservation.end_time == new_reservation.end_time and expiry_time is None:
            ### NO TIME DIFFERENCE -> NO NEED TO MAKE ANY OPERATION ON SLOTS!
            await self.reservation_manager.remove_reservation(old_reservation.reservation_id)
            await self.reservation_manager.insert_reservation(new_reservation)
            return BusinessEvent(event_type=ReservationEventType.REPLACED, actor=actor, data=BusinessEvent.EventData(old = old_reservation, new = new_reservation))
           
       
        old_res_slots = reservation_context.existing_res_slots if reservation_context.existing_res_slots is not None else self.calendar.get_slots(start_time=old_reservation.start_time, end_time=old_reservation.end_time, same_segment_only=True) #safe check -> retrieving slots from reservation times.
        new_res_slots = reservation_context.new_res_slots if reservation_context.new_res_slots  is not None else self.calendar.get_slots(start_time=new_reservation.start_time, end_time=new_reservation.end_time, same_segment_only=True) #safe check -> retrieving slots from reservation times.
        if not old_res_slots or not new_res_slots:
            raise ClosingTimeError('Out of working hours')
            
        #slots_to_book, slots_to_free = get_consecutive_slots_join(new_res_slots, old_res_slots, how='difference')  ##slots_to_book and slots_to_free will be only the "exclusive" slots (i.e. not overlapping between old_res_slots and new_res_slots)
        slots_to_lock = sorted(set(new_res_slots + old_res_slots), key=lambda x: x.start_time)
        locked_slots = await self.calendar._lock_slots(slots_to_lock)
        prev_reserv_slots_canceled, new_reserv_slots_booked = False, False
        try:
            """ Locking slots, updating slots status (setting previous slots as "unbooked", new slots as "booked"). 
            Finally updating current mappings (removing old reservation, inserting new one).
            If anything goes wrong, return to previous status (set the old slots as booked, and keep the old reservation among reservations).
            Unlock slots.
            """
            prev_reserv_slots_canceled = self.calendar._free_slots_no_lock(old_res_slots)
            new_reserv_slots_booked = self.calendar._reserve_slots_no_lock(new_res_slots, expiry_time)
                   
        except Exception as e:
            if prev_reserv_slots_canceled:
                self.calendar._reserve_slots_no_lock(old_res_slots)
            if new_reserv_slots_booked:
                 self.calendar._free_slots_no_lock(new_res_slots)
            raise e
        finally:
            self.calendar._unlock_slots(locked_slots)
            
        await self.reservation_manager.remove_reservation(old_reservation.reservation_id)
        await self.reservation_manager.insert_reservation(new_reservation)
        return BusinessEvent(event_type=ReservationEventType.REPLACED, actor=actor, data=BusinessEvent.EventData(old = old_reservation, new = new_reservation))
        
        
    def _get_duration_from_service_name(self, service_name):
        try:
            return self.policy_manager.services[service_name].minutes_duration
        except:
            return ValueError(f'Unknown service {service_name}')
        
    def _resolve_reservation_params_with_defaults(self, user: str=None, start_time: dt.datetime=None, service_name: str=None, minutes_duration: int=None, existing_reservation: Reservation=None):
        from utils.datetimes_utils import minutes_between
        
        final_dct = {'start_time':start_time, 'service_name':service_name, 'minutes_duration':minutes_duration}
        if existing_reservation is None:
            if service_name is None:
                return final_dct
            final_dct['minutes_duration']=minutes_duration or self._get_duration_from_service_name(service_name)
        else:
            final_dct['minutes_duration']=minutes_duration or (self._get_duration_from_service_name(service_name) if service_name is not None else minutes_between(existing_reservation.start_time, existing_reservation.end_time) )
            inputs = {'user': user, 'start_time': start_time, 'service_name': service_name}
            for attr in inputs:
                final_dct[attr] = inputs[attr] if inputs[attr] is not None else getattr(existing_reservation, attr)
                      
        return final_dct
        
    @staticmethod
    def _resolve_service_params_with_defaults(existing_service: Service, price: float = None, minutes_duration: int = None, description: str = None):
        if not isinstance(existing_service, Service):
            raise TypeError(f'Unknown service {existing_service}')
        params = {'service_name':existing_service.service_name,
                  'price': price if price is not None else existing_service.price,
                  'minutes_duration': minutes_duration  if minutes_duration is not None else  existing_service.minutes_duration,
                  'description': description  if description is not None else existing_service.description
                  }
        return params
        
    @staticmethod
    def _resolve_matching_user_reservations(candidates_reservations: list[Reservation]):
        if not candidates_reservations:
            return None
        if len(candidates_reservations)>1:
            raise ValueError('Multiple corresponding reservations found')
        return candidates_reservations[0]
    

class BusinessOperation(Enum):
    MAKE = 'reserve'
    DELETE = 'cancel'
    UPDATE = 'update'


class BusinessCoreWithConfirmation(BusinessCore):
    """ Keeps status of final operations: need for confirmation in order to finalize such operations (i.e. reservations, cancelations, adds/removal of services).
    When running a final operation, a new object is created and added to the data, but it waits for confirmation and wont be considered confirmed till a confirm_operation is run.
    The confirmation operation must run within max_confirmation_minutes from the original operation. Otherwise there will be an error when a confirmation is run > orig_operation + max_confirmation_minutes
    For updates, an update is run as a new reserve_operation. if the old_reservation was not confirmed, it will be immediately deleted, otherwise it will still be the current reservation till r2 confirmed.
    I.E. if r1 = reserve->update (before r1 is confirmed)->r2 is reserved (waiting for confirmation) and r1 is erased.
         if r1 -> confirm -> update -> r2 is reserved but r1 keeps living (slots still booked). Once r2 is confirmed, r1 is deleted. If r2 is not confirmed, r1 keeps living.
    For cancelations, a (non confirmed) cancel operation only sets the inner timestamp (self.status_change_timestamp) to the current timestamp, in order  to check if the confirmation is allowed at confirmation_timestamp. 
    This means multiple cancel requests can be done without any error. But no cancelation is performed till a confirm_cancel is performed.
    """    
    
    ALLOWED_STATUSES_TO_CONFIRM_OP: dict [BusinessOperation, list[ReservationStatus]] = {
        BusinessOperation.MAKE: [ReservationStatus.PENDING_CONFIRMATION_STATUS],
        BusinessOperation.DELETE: [ReservationStatus.PENDING_CANCELATION_STATUS],
        BusinessOperation.UPDATE: [ReservationStatus.PENDING_UPDATE_STATUS]
    }
                            
    
    def __init__(self, reservation_manager: ReservationManager, calendar: BusinessCalendar, policy_manager: PolicyManager, default_grid_minutes: int=15, max_confirmation_minutes: int=5):
        super().__init__(reservation_manager=reservation_manager, calendar=calendar, policy_manager=policy_manager, default_grid_minutes=default_grid_minutes)
        self.max_confirmation_minutes = max_confirmation_minutes
        self.__unconfirmed_updates_timestamps__ = {}
        self.__unconfirmed_services_timestamps__ = {}


    def delete_all_not_confirmed_services(self, expired_only: bool, actor: UserRole = UserRole.ADMIN):
        not_confirmed_services = {serv_name: (req_time:=v[2]) for serv_name, v in self.__unconfirmed_services_timestamps__.items()}
        events = []
        if expired_only:
            not_confirmed_services = [s for s, req_time in not_confirmed_services.items() if not self.is_within_allowed_confirmation_time(request_time=req_time)]
        for s in not_confirmed_services:
            serv = self.__unconfirmed_services_timestamps__.pop(s)
            events.append(ServiceEventType.DELETED, data=BusinessEvent.EventData(old=serv), actor=actor)
        return events
    
    async def delete_all_not_confirmed_reservations(self, expired_only: bool, user: str=None, actor: UserRole = UserRole.ADMIN):
        all_reservations = self.get_all_reservations() if user is None else self.get_user_reservations(user)
        all_not_confirmed_reservations = [r for r in all_reservations if not r.is_confirmed]
        
        all_canceled_reservations_events = []
        for reservation in all_not_confirmed_reservations: ##if a reservation is not confirmed, it has no inner update. hence we can safely remove it without taking care of its inner update.
            if expired_only and not reservation.is_confirmation_expired():
                continue
                
            cancel_operat_context = BusinessCore.ReservationOperationContext(existing_reservation_id=reservation.reservation_id)
            
            if reservation.is_confirmation_expired():
                cancel_operat_context.existing_res_slots = []  ##no need to free slots
                            
            cancel_ev = await super()._cancel_reservation(cancel_operat_context, actor=actor)
            all_canceled_reservations_events.append(cancel_ev)
        
        all_not_confirmed_updates = self.__unconfirmed_updates_timestamps__.keys() ##the reservations with inner updates are confirmed, hence we are sure we are actually handling all the existing inner updates here.
        reservations_with_pending_inner_updates = [self.reservation_manager.get_reservation(res_id) for res_id in all_not_confirmed_updates]
        
        for reservation in reservations_with_pending_inner_updates:
            inner_reservation = reservation.get_associated_update_reservation()
            if not isinstance(inner_reservation, Reservation):
                raise RuntimeError(f'Not existing inner update for reservation with id: {reservation.reservation_id}')
            if expired_only and not inner_reservation.is_confirmation_expired():
                continue
            inner_cancel_event = await self.cancel_pending_update_reservation(reservation_id=reservation.reservation_id, actor=actor)
            all_canceled_reservations_events.append(inner_cancel_event)
        return all_canceled_reservations_events
            
    
    async def _make_reservation(self, reservation_context: BusinessCore.ReservationOperationContext, actor: UserRole, expiry_time: dt.datetime=None):
        if expiry_time is None:
            expiry_time = map_datetime_to_default(dt.datetime.now()+timedelta(minutes=self.max_confirmation_minutes), ignore_seconds=False)
        reservation_ev = await super()._make_reservation(reservation_context=reservation_context, actor=actor, expiry_time=expiry_time)
        if isinstance(reservation_ev, BusinessEvent):
            reservation = reservation_ev.data.new
            reservation.mark_as_pending_confirmation(expiry_time) ##if reservation is correctly placed, it is a new reservation (i.e. no confirmation). 'pending' status by default
            return BusinessEvent(BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.MAKE, pending_op=PendingOperation.REQUESTED, object_type=Reservation), data=BusinessEvent.EventData(new=reservation), actor=actor)
        return reservation_ev
        
        
    async def _cancel_reservation(self, reservation_context: BusinessCore.ReservationOperationContext, actor: UserRole):
        curr_existing_res=self.reservation_manager.get_reservation(reservation_context.existing_reservation_id)
        if not curr_existing_res:
            raise NotPreviouslyBookedError('Cannot cancel the input reservation, it is not booked.')
            
        old_res = curr_existing_res.copy()
        events = []
        try:
            inn_upd_event = await self.cancel_pending_update_reservation(reservation_id=curr_existing_res.reservation_id, actor=UserRole.SYSTEM)
            events.append(inn_upd_event) ##if a cancel request is done, we delete (if any) the update request associated to this reservation
        except:
            pass
        
        
        if curr_existing_res.is_confirmed:
            confirmation_expiry_time = map_datetime_to_default(dt.datetime.now()+timedelta(minutes=self.max_confirmation_minutes), ignore_seconds=False )
            curr_existing_res.mark_as_pending_delete(confirmation_expiry_time) ##cancelation on a confirmed reservation -> marking as "pending cancelation" till user confirms it
            
            events.append(BusinessEvent(ReservationEventType.PENDING_DELETE_CREATED, data=BusinessEvent.EventData(old=old_res, new=curr_existing_res), actor=actor))
            return events    
            
        else:
            error = None
            if curr_existing_res.is_confirmation_expired(): ##expired reserv, only removing from reservations. Slots will be "lazily" released (only when a new booking request will include such slots)
                actor = UserRole.SYSTEM
                reservation_context.existing_res_slots = []
                error = ExpiryError('Reservation expired (never confirmed). Now deleted.')
            
            cancel_ev = await super()._cancel_reservation(reservation_context, actor=actor) ##immediate cancelation on a not previously confirmed reservation
            cancel_ev.message = error
            events.append(cancel_ev)
            return events
    
    
    
    async def _update_reservation(self, reservation_context: BusinessCore.ReservationOperationContext, actor: UserRole, new_reservation_expiry_time: dt.datetime=None):
        from backend.slots_utils import get_consecutive_slots_join
        old_reservation = self.reservation_manager.get_reservation(reservation_context.existing_reservation_id)
        new_reservation = reservation_context.new_reservation
        if not old_reservation:
            raise NotPreviouslyBookedError('Cannot update: the old_reservation is not booked in the system.')
            
        curr_time = map_datetime_to_default(dt.datetime.now(), ignore_seconds=False)
        if new_reservation_expiry_time is None:
            new_reservation_expiry_time = curr_time+timedelta(minutes=self.max_confirmation_minutes) ###default expiry-time = reservation_ts (time it was built) + max_confirmation_time 
        
        ##IMMEDIATE UPDATE ON A NON-CONFIRMED OLD_RESERVATION -> removing old_res and creating a new one (for the update), having status='unconfirmed'
        if not old_reservation.is_confirmed:
            if old_reservation.is_confirmation_expired():
                cancel_events = await self._cancel_reservation(reservation_context, actor=UserRole.SYSTEM)
                e = ExpiryError('Reservation was not confirmed and was already canceled.')
                e.events = cancel_events
                raise e
            if isinstance(old_reservation.get_associated_update_reservation(), Reservation):
                raise RuntimeError('Should never happen. Inner update on a non-confirmed reservation--->Inconsistent status!')
            
            update_event = await super()._update_reservation(reservation_context, actor=actor, expiry_time=new_reservation_expiry_time) 
            if not isinstance(update_event, Exception):
                old_reservation, new_reservation = update_event.data.old, update_event.data.new
                new_reservation.mark_as_pending_confirmation(new_reservation_expiry_time)
                old_reservation.mark_as_deleted()
            return update_event
        
        
        ##CONFIRMED OLD_RESERVATION -> the update details will be stored as a old_reservation attribute! (i.e. a Reservation)
        events = []
        
        old_res_unedited = old_reservation.copy()
        unconfirmed_previous_requested_update = old_reservation.get_associated_update_reservation()
        """
        if unconfirmed_previous_requested_update==new_reservation:
            warnings.warn('The update requested is the same as the previous update request. You should probably call confirm_reserve')
            return BusinessEvent(event_type=ReservationEventType.NOOP, actor=actor, data=BusinessEvent.EventData(old=old_reservation, new=old_reservation))
        """
        if not isinstance(unconfirmed_previous_requested_update, Reservation) or unconfirmed_previous_requested_update.is_confirmation_expired():
            prev_inner_update_slots_to_release = []
        else:
            prev_inner_update_slots_to_release = self.calendar.get_slots(start_time=unconfirmed_previous_requested_update.start_time, end_time=unconfirmed_previous_requested_update.end_time, same_segment_only=True)
        
        new_res_slots = reservation_context.new_res_slots if reservation_context.new_res_slots is not None else self.calendar.get_slots(start_time=new_reservation.start_time, end_time=new_reservation.end_time, same_segment_only=True)
        old_res_slots = reservation_context.existing_res_slots if reservation_context.existing_res_slots is not None else self.calendar.get_slots(start_time=old_reservation.start_time, end_time=old_reservation.end_time, same_segment_only=True) 
        ###slots_to_free -> exclusive slots for the previous update request (if any) related to this old_res.
        slots_to_free = get_consecutive_slots_join(prev_inner_update_slots_to_release, old_res_slots, how='difference')[0]
        ###slots_to_book -> exclusive slots for the current update request (i.e. new_res).
        slots_to_book = get_consecutive_slots_join(new_res_slots, old_res_slots, how='difference')[0]
        locked_slots = await self.calendar._lock_slots(sorted(set(slots_to_book+slots_to_free), key=lambda x: x.start_time))
        new_res_slots_booked, old_res_slots_unbooked = False, False                                      
        try: 
            old_res_slots_unbooked = self.calendar._free_slots_no_lock(slots_to_free) ##always freeing old inner update slot    
            new_res_slots_booked = self.calendar._reserve_slots_no_lock(slots_to_book, expiry_time=new_reservation_expiry_time) ### reserving the new slots. Only works if update is doable (i.e. slots are all free)     
        except:
            old_reservation.mark_as_confirmed() ##current update failed -> resetting status to confirmed (the only feasible status here)
            
            if new_res_slots_booked:
                self.calendar._free_slots_no_lock(slots_to_book)
            if old_res_slots_unbooked:
                pass ##no need to rebook them->they were part of the previous inner_update only. it is fine to free them.
            raise AlreadyBookedError('Cannot update. The requested time is not available for booking')
        finally:
            self.calendar._unlock_slots(locked_slots)
            try:
                events.append(self._cancel_inner_update_reference(old_reservation, actor=UserRole.SYSTEM)) ##removing previous inner update reference
            except:
                pass
        if not new_res_slots:
            raise ClosingTimeError('Cannot update at the requested time slots. Out of working hours')
        old_reservation.mark_as_pending_update(updated_reservation=new_reservation, expires_at=new_reservation_expiry_time) ###setting the new reservation as a parameter within the old one. This way the system will create it -> old res will have status='pending update', new res will have status='pending confirmation'
        self.__unconfirmed_updates_timestamps__[old_reservation.reservation_id] = curr_time
        events.append(
            BusinessEvent(event_type=BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.UPDATE, pending_op=PendingOperation.REQUESTED, object_type=Reservation), 
                          actor=actor, data=BusinessEvent.EventData(old=old_res_unedited, new=old_reservation),
                          )
        )
        return events


    
    async def confirm_pending_make_reservation(self, reservation_id: str, actor: UserRole = UserRole.USER): 
        can_confirm, error, reservation = self._validate_existing_reservation_pending_op(reservation_id = reservation_id, operation=BusinessOperation.MAKE)
        if not can_confirm:
            if isinstance(error, ExpiryError):
                cancel_ev = await self._cancel_reservation(BusinessCore.ReservationOperationContext(existing_reservation_id=reservation_id, existing_res_slots=[]), actor=UserRole.SYSTEM, )
                error.events = cancel_ev
            raise error
        
        old_res = reservation.copy()
        reservation.mark_as_confirmed()
        res_slots = self.calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time, same_segment_only=True)
        await self.calendar._update_slots_expiry_time(res_slots, expiry_time=None) ##removing expiry time from reservation' slots -> it is confirmed!
        return BusinessEvent(event_type=BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.MAKE, pending_op=PendingOperation.PENDING_CONFIRMED, object_type=Reservation), actor=actor, data=BusinessEvent.EventData(old=old_res, new=reservation))
        
        
        
    async def cancel_pending_make_reservation(self, reservation_id: str, actor: UserRole=UserRole.USER):
        can_cancel, error, _ = self._validate_existing_reservation_pending_op(reservation_id = reservation_id, operation=BusinessOperation.MAKE)
        if not can_cancel:
            if isinstance(error, ExpiryError):
                actor = UserRole.SYSTEM
            else:
                raise error
            
        cancel_events = await self._cancel_reservation(BusinessCore.ReservationOperationContext(existing_reservation_id=reservation_id), actor=actor, )
        cancel_events[-1].message = error
        cancel_events[-1].event_type = BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.MAKE, pending_op=PendingOperation.PENDING_CANCELED, object_type=Reservation)
        return cancel_events

        
    
    async def confirm_pending_cancel_reservation(self, reservation_id: str, actor: UserRole = UserRole.USER): 
        can_confirm, error, _ = self._validate_existing_reservation_pending_op(reservation_id=reservation_id, operation=BusinessOperation.DELETE)
        if not can_confirm:
            if isinstance(error, ExpiryError):
                cancel_events = await super()._cancel_reservation(BusinessCore.ReservationOperationContext(existing_reservation_id=reservation_id, existing_res_slots=[]), actor=UserRole.SYSTEM)
                error.events = cancel_events
            raise error
        
        canceled_reservation_ev = await super()._cancel_reservation(BusinessCore.ReservationOperationContext(existing_reservation_id=reservation_id), actor=actor)
        if isinstance(canceled_reservation_ev, BusinessEvent):
            old_res = canceled_reservation_ev.data.old
            new_res = old_res.copy()
            new_res.mark_as_deleted()
            return BusinessEvent(event_type=BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.DELETE, pending_op=PendingOperation.PENDING_CONFIRMED, object_type=Reservation), actor=actor, data=BusinessEvent.EventData(old=old_res, new=new_res))
        raise ValueError('Error deleting')

    
    
    async def cancel_pending_cancel_reservation(self, reservation_id: str, actor: UserRole=UserRole.USER):
        can_cancel, error, reservation = self._validate_existing_reservation_pending_op(reservation_id=reservation_id, operation=BusinessOperation.DELETE)
        if not can_cancel:
            if isinstance(error, ExpiryError):
                actor = UserRole.SYSTEM
            else:
                raise error
            
        old_res = reservation.copy()
        reservation.mark_as_confirmed()
        return BusinessEvent(event_type=BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.DELETE, pending_op=PendingOperation.PENDING_CANCELED, object_type=Reservation), actor=actor, data=BusinessEvent.EventData(old=old_res, new=reservation), message = error)
        
        
    
    async def confirm_pending_update_reservation(self, reservation_id: str, actor: UserRole = UserRole.USER):
        from backend.slots_utils import get_consecutive_slots_join
        
        can_confirm, error, existing_reservation = self._validate_existing_reservation_pending_op(reservation_id=reservation_id, operation=BusinessOperation.UPDATE)
        if not can_confirm:
            if isinstance(error, ExpiryError):
                event = self._cancel_inner_update_reference(existing_reservation, author=UserRole.SYSTEM)
                e.events = [event]
            raise e
        
        pending_update_reserv = existing_reservation.get_associated_update_reservation() 
        
        new_res_slots = self.calendar.get_slots(start_time=pending_update_reserv.start_time, end_time=pending_update_reserv.end_time, same_segment_only=True)
        old_res_slots = self.calendar.get_slots(start_time=existing_reservation.start_time, end_time=existing_reservation.end_time, same_segment_only=True)
        if not new_res_slots or not old_res_slots:
            raise ClosingTimeError('Out of working hours') ##should never happen as far as there is no modification to the calendar while reservation was "pending_update"
        slots_to_free = get_consecutive_slots_join(new_res_slots, old_res_slots, how='difference')[1]  #slots_to_free will be only the "exclusive" old slots (i.e. old slots - new slots) to free
        locked_slots = await self.calendar._lock_slots(slots_to_free+new_res_slots)
        self.calendar._set_slots_expiry_time_no_lock(new_res_slots, expiry_time=None) ##removing expiry time from new_reservation' slots -> it is confirmed!
        self.calendar._free_slots_no_lock(slots_to_free) ##releasing old reservation slots
        self.calendar._unlock_slots(locked_slots)
        existing_reservation.mark_as_confirmed_update() ##setting the status as 'confirmed' to the new reservation and 'deleted' to the old one
        await self.reservation_manager.remove_reservation(existing_reservation.reservation_id) ##removing old reservation from "db"
        await self.reservation_manager.insert_reservation(pending_update_reserv) ###inserting the new update reservation among reservations
        self.__unconfirmed_updates_timestamps__.pop(existing_reservation.reservation_id)                    
        return BusinessEvent(event_type=BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.UPDATE, pending_op=PendingOperation.PENDING_CONFIRMED, object_type=Reservation), actor=actor, data=BusinessEvent.EventData(old=existing_reservation, new=pending_update_reserv))
  
   
    
    async def cancel_pending_update_reservation(self, reservation_id: str, actor: UserRole=UserRole.USER):
        """ Releases slots of the update, removes update_reservation from __unconfirmed_updates_timestamps__ , removes update_reservation from attributes of the current reservation """
        from backend.slots_utils import get_consecutive_slots_join

        can_cancel, error, existing_reservation = self._validate_existing_reservation_pending_op(reservation=existing_reservation, operation=BusinessOperation.UPDATE)
        if not can_cancel:
            if isinstance(error, ExpiryError):
                actor = UserRole.SYSTEM
            else:
                raise e
        
        pending_update_reserv = old_reservation.get_associated_update_reservation() 
        pending_update_canc_ev = self._cancel_inner_update_reference(reservation=existing_reservation, actor=actor)
        
        if pending_update_reserv.is_confirmation_expired():
            update_reservation_slots = []
        else:
            update_reservation_slots = self.calendar.get_slots(start_time=pending_update_reserv.start_time, end_time=pending_update_reserv.end_time, same_segment_only=True)
        
        existing_reservation_slots = self.calendar.get_slots(start_time=existing_reservation.start_time, end_time=existing_reservation.end_time, same_segment_only=True)
        slots_to_free = get_consecutive_slots_join(update_reservation_slots, existing_reservation_slots, how='difference')[0]
        await self.calendar.free_slots(slots_to_free) ##releasing "exclusive" inner update reservation slots. (i.e. new_res_slots - old_res_slots)
        
        return BusinessEvent(event_type=BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.UPDATE, pending_op=PendingOperation.PENDING_CANCELED, object_type=Reservation), actor=actor, data=pending_update_canc_ev.data, message=error)
    
    
    
    def add_service(self, service_name: str, price: float, minutes_duration: int, description: str = '', actor : UserRole = UserRole.ADMIN):
        add_doable, obj = self._prepare_add_service(service_name=service_name, price=price, minutes_duration=minutes_duration, description=description)
        if not add_doable:
            raise obj
        return self._set_pending_service_req(service_name=service_name, operation=BusinessOperation.MAKE, request_data=obj.new, request_time=dt.datetime.now(tz=get_global_timezone()), actor=actor, force_overwrite=True )        
    
    
    def update_service(self, existing_service_name: str, new_price: float = None, new_minutes_duration: int = None, new_description: str = None, actor : UserRole = UserRole.ADMIN):
        update_doable, obj = self._prepare_update_service(existing_service_name = existing_service_name, price = new_price, minutes_duration = new_minutes_duration, description = new_description)
        if not update_doable:
            raise obj
        
        pending_upd_ev = self._set_pending_service_req(service_name=existing_service_name, operation=BusinessOperation.UPDATE, request_data=obj.new, request_time=dt.datetime.now(tz=get_global_timezone()), actor=actor, force_overwrite=True )
        return BusinessEvent(pending_upd_ev.event_type, actor=actor, data=BusinessEvent.EventData(old=obj.old, new=obj.new))
                
    
    def remove_service(self, service_name: str, actor : UserRole = UserRole.ADMIN):
        remove_doable, obj = self._prepare_remove_service(service_name)
        if not remove_doable:
            raise obj
        pending_canc_ev = self._set_pending_service_req(service_name=service_name, operation=BusinessOperation.DELETE, request_data=obj.old, request_time=dt.datetime.now(tz=get_global_timezone()), actor=actor, force_overwrite=True )
        return BusinessEvent(pending_canc_ev.event_type, actor=actor, data=BusinessEvent.EventData(old=obj.old))
    
    
    def confirm_pending_add_service(self, service_name: str, actor: UserRole=UserRole.ADMIN):
        can_confirm, error, pending_service = self._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.MAKE)
        if not can_confirm:
            if isinstance(error, ExpiryError): 
                error.events = [self._remove_pending_service_req(service_name, BusinessOperation.MAKE, actor=UserRole.SYSTEM)]
            raise error
                        
        events = []
        added_serv = self.policy_manager.add_service(service=pending_service)
        if added_serv is not False:
            ev_type = BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.MAKE, pending_op=PendingOperation.PENDING_CONFIRMED, object_type=Service)
            events.append(BusinessEvent(ev_type, actor=actor, data=BusinessEvent.EventData(old=pending_service, new=added_serv)))
            events.append(self._remove_pending_service_req(service_name, BusinessOperation.MAKE, actor=actor))
            return list(reversed(events))
            
        raise KeyError(f'Cannot add. Service {service_name} already existing') #should never happen
        
        
    def cancel_pending_add_service(self, service_name: str, actor: UserRole=UserRole.ADMIN):
        can_cancel, error, pending_service = self._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.MAKE)
        if not can_cancel:
            if isinstance(error, ExpiryError): 
                actor = UserRole.SYSTEM
            else:
                raise error      
            
        cancel_ev = self._remove_pending_service_req(service_name, BusinessOperation.MAKE, actor=actor)
        cancel_ev.message = error
        return cancel_ev
        
    
    def confirm_pending_remove_service(self, service_name: str, actor: UserRole=UserRole.ADMIN):
        can_confirm, error, pending_service = self._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.DELETE)
        if not can_confirm:
            if isinstance(error, ExpiryError):
                
                error.events = [self._remove_pending_service_req(service_name, BusinessOperation.DELETE, actor=UserRole.SYSTEM)]
            raise error
            
        events = []
        deleted_serv = self.policy_manager.remove_service(service_name)
        if deleted_serv is not False:
            ev_type = BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.DELETE, pending_op=PendingOperation.PENDING_CONFIRMED, object_type=Service)
            events.append(BusinessEvent(ev_type, actor=actor, data=BusinessEvent.EventData(old=serv_to_del, new=deleted_serv)) )
            events.append(self._remove_pending_service_req(service_name, BusinessOperation.DELETE, actor=actor))
            return list(reversed(events))
        raise KeyError(f'Cannot cancel. Service {service_name} not existing') #should never happen


    def cancel_pending_remove_service(self, service_name: str, actor: UserRole=UserRole.ADMIN):
        can_cancel, error, pending_service = self._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.DELETE)
        if not can_cancel:
            if isinstance(error, ExpiryError): 
                actor = UserRole.SYSTEM
            else:
                raise error
            
        canc_ev = self._remove_pending_service_req(service_name, BusinessOperation.DELETE, actor=actor)
        canc_ev.message = error
        return canc_ev
        
        
    def confirm_pending_update_service(self, service_name: str, actor: UserRole=UserRole.ADMIN):
        can_confirm, error, pending_service = self._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.UPDATE)
        if not can_confirm:
            if isinstance(error, ExpiryError):
                error.events = [self._remove_pending_service_req(service_name, BusinessOperation.UPDATE, actor=UserRole.SYSTEM)]
            raise error
            
        events = []
        update_result = self.policy_manager.update_service(**pending_service.__dict__)
        if update_result is not False:
            ev_type = BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.UPDATE, pending_op=PendingOperation.PENDING_CONFIRMED, object_type=Service)
            events.append(BusinessEvent(ev_type, actor=actor, data=BusinessEvent.EventData(old=update_result[0], new=update_result[1])))
            events.append(self._remove_pending_service_req(service_name, BusinessOperation.UPDATE, actor=actor))
            return list(reversed(events))
        raise ValueError(f'Cannot update. Service {service_name} not existing') #should never happen
        
        
    def cancel_pending_update_service(self, service_name: str, actor: UserRole=UserRole.ADMIN):
        can_cancel, error, pending_service = self._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.UPDATE)
        if not can_cancel:
            if isinstance(error, ExpiryError): 
                actor = UserRole.SYSTEM
            else:
                raise error
            
        canc_ev = self._remove_pending_service_req(service_name, BusinessOperation.UPDATE, actor=actor)
        canc_ev.message = error
        return canc_ev
        
    
    
    def _can_confirm_reservation_op(self, reservation: Reservation, operation: BusinessOperation):
        """
        Checks if operation is valid, reservation has feasible status (for the input operation). 
        Returns tuple: (can_confirm: bool, error: Exception|None)
        """
        if operation not in BusinessCoreWithConfirmation.ALLOWED_STATUSES_TO_CONFIRM_OP.keys():
            return False, ValueError(f'Wrong confirm operation {operation}. Must be one of {BusinessCoreWithConfirmation.ALLOWED_STATUSES_TO_CONFIRM_OP.keys()}')
        feasible_current_statuses = BusinessCoreWithConfirmation.ALLOWED_STATUSES_TO_CONFIRM_OP[operation]
        
        if reservation.status not in feasible_current_statuses:
            return False, ConfirmationError('Cannot confirm -- wrong status')
        if reservation.is_confirmation_expired():
            return False, ExpiryError(f'Cannot confirm -- Confirmation time expired')
        if operation==BusinessOperation.UPDATE:
            if not isinstance((inner_upd_res:=reservation.get_associated_update_reservation()), Reservation):
                return False, TypeError(f'Cannot confirm -- Invalid update reservation.')
            if inner_upd_res.is_confirmation_expired():
                return False, ExpiryError(f'Cannot confirm -- Confirmation time expired')
        return True, None
        
        
    def _validate_existing_reservation_pending_op(self, reservation_id: str, operation: BusinessOperation):
        """
        Checks the corresponding reservation exists in the system, and that the operation can run on such reservation (i.e. feasible status)
        Returns tuple (is_valid: bool, error: Exception|None, reservation: Reservation|None)
        """
        reservation = self.reservation_manager.get_reservation(reservation_id)
        if not isinstance(reservation, Reservation):
            return False, NotPreviouslyBookedError('Cannot find a reservation with the specified details'), None
        
        can_run_confirmation, err_msg = self._can_confirm_reservation_op(operation=operation, reservation=reservation)    
        return can_run_confirmation, err_msg, reservation
            
           
    def _can_confirm_pending_service_op(self, pending_service_req: tuple[BusinessOperation, Service, dt.datetime], operation: BusinessOperation) -> tuple[bool, Exception]:
        """
        Checks the input pending_service_req and operation are valid (type checking, value checking).
        Checks the pending_service_req' operation corresponds to the input operation
        Returns tuple (is_valid: bool, error: Exception|None, reservation: Reservation|None)
        """
        if operation not in set(BusinessOperation):
            raise ValueError(f'Unknown operation. Must be one among: {set(BusinessOperation)}')
        try:
            pending_op, pending_serv, req_ts = pending_service_req
        except:
            return False, TypeError('Wrong pending_service_req in input')
        if pending_op!=operation:
            return False, ConfirmationError('Cannot confirm -- wrong status')
        if not self.is_within_allowed_confirmation_time(request_time=req_ts):
            return False, ExpiryError(f'Cannot confirm. Confirmation time expired: it must be done within {self.max_confirmation_minutes} minutes after the original request')
        return True, None
        
        
        
    def _validate_existing_service_pending_op(self, service_name: str, operation: BusinessOperation) -> tuple[bool, Exception, Service|dict]:
        pending_service_req = self._get_pending_service_req(service_name)
        if pending_service_req is None or not isinstance(pending_service_req[1], Service):
            return False, KeyError(f'Service {service_name} has no pending {operation.value} requests.' ), None
        
        service_data = pending_service_req[1]
        can_run_confirmation, err_msg = self._can_confirm_pending_service_op(operation=operation, pending_service_req=pending_service_req)
        return can_run_confirmation, err_msg, service_data
    
    def _validate_new_service_req(self, service_name: str, operation: BusinessOperation, force_overwrite: bool) -> tuple[bool, Exception]:
        """Checks the input operation is allowed (valid operation value), that service exists (if operation is UPDATE/CANCEL) in the system, and that it has no pending operations associated.
        Returns tuple (can_add_req: bool, error: Exception|None)
        """
        if operation not in set(BusinessOperation):
            return False, ValueError(f'Unknown operation. Must be one among: {set(BusinessOperation)}')
        if operation in [BusinessOperation.DELETE, BusinessOperation.UPDATE] and service_name not in self.policy_manager.services:
            return False, KeyError(f'Service {service_name} not existing . Cannot {operation.value}')
        if not force_overwrite and service_name in self.__unconfirmed_services_timestamps__:
            return False, PolicyError(f'Service {service_name} has already a previous unconfirmed request')
        return True, None
    
    
    def _get_pending_service_req(self, service_name: str) -> tuple[BusinessOperation, Service|dict, datetime.datetime]:
        return self.__unconfirmed_services_timestamps__.get(service_name, None)
        
    def _set_pending_service_req(self, service_name: str, request_data: Service|dict, operation: BusinessOperation, actor: UserRole, request_time: dt.datetime=None, force_overwrite: bool = False) -> BusinessEvent:
        can_set, err = self._validate_new_service_req(service_name=service_name, operation=operation, force_overwrite=force_overwrite)
        if not can_set:
            raise err
        
        if request_time is None:
            request_time = dt.datetime.now(tz=get_global_timezone())
        pending_req = (operation, request_data, request_time)
        
        self.__unconfirmed_services_timestamps__[service_name] = pending_req
        event_type = BusinessCoreWithConfirmation._get_event_type(operation=operation, pending_op=PendingOperation.REQUESTED, object_type=Service)
        return BusinessEvent(event_type=event_type, data=BusinessEvent.EventData(new=request_data), actor=actor)
        
   
    def _remove_pending_service_req(self, service_name: str, operation: BusinessOperation, actor: UserRole) -> BusinessEvent:
        try:
            curr_pending_op = self._get_pending_service_req(service_name)[0]
            if curr_pending_op==operation:
                removed_req = self.__unconfirmed_services_timestamps__.pop(service_name)
                event_type = BusinessCoreWithConfirmation._get_event_type(operation=operation, pending_op=PendingOperation.PENDING_CANCELED, object_type=Service)
                return BusinessEvent(event_type, actor=actor, data=BusinessEvent.EventData(old=removed_req[1]))
            else:
                raise ValueError('Operation mismatch')
        except:
            raise
            
        
        
    def is_within_allowed_confirmation_time(self, request_time: dt.datetime, curr_time: dt.datetime = None) -> bool:
        if curr_time is None:
            curr_time = dt.datetime.now(tz=request_time.tzinfo)
        return curr_time - request_time <= timedelta(minutes=self.max_confirmation_minutes)
              
      
    def _cancel_inner_update_reference(self, reservation: Reservation, actor: UserRole) -> BusinessEvent:
        if reservation.get_associated_update_reservation() is None or reservation.reservation_id not in self.__unconfirmed_updates_timestamps__:
            raise ValueError('Cannot cancel. Invalid associated update')
        
        unchanged_res = reservation.copy()
        reservation.pop_associated_update_reservation()
        self.__unconfirmed_updates_timestamps__.pop(reservation.reservation_id, None)
        
        if reservation.is_confirmed:
            reservation.mark_as_confirmed()  
        else: ###should never happen
            reservation.mark_as_pending_confirmation()
            
        event = BusinessCoreWithConfirmation._get_event_type(operation=BusinessOperation.UPDATE, pending_op=PendingOperation.PENDING_CANCELED, object_type=Reservation)
        return BusinessEvent(event, actor=actor, data=BusinessEvent.EventData(old=unchanged_res, new=reservation))  


    @staticmethod
    def _resolve_matching_user_reservations(candidates_reservations: list[Reservation]) -> Reservation:
        from backend.domain_logic import is_reservation_active
        """
        candidates_reservations: list of user reservations matching a specific COMPLETE set of parameters (i.e. user, start_time, service_name, end_time)
        """
        if not candidates_reservations:
            return None
        if len(candidates_reservations)==1:
            return candidates_reservations[0]
        
        active_matching_reservations = [r for r in candidates_reservations if is_reservation_active(r)]
        if not active_matching_reservations:
            return max(candidates_reservations, key=lambda r: r.status_change_timestamp) ##returning the last (expired) reservation
        if len(active_matching_reservations)>1:
            raise ValueError('More than 1 actives: ', [r.reservation_id for r in active_matching_reservations]) ##should never happen
        return active_matching_reservations[0]
        
    @staticmethod
    def _get_event_type(operation: BusinessOperation, object_type: type, pending_op: PendingOperation):
        if object_type not in [Service, Reservation]:
            raise ValueError(f'"object_type" must be in {[Service, Reservation]}')
        
        if object_type==Service:
            event_dct = _service_pending_oper_to_events
        elif object_type==Reservation:
            event_dct = _reserv_pending_oper_to_events
            
        return event_dct[operation][pending_op]
       

class PendingOperation(Enum):
    REQUESTED = 'requested'
    PENDING_CONFIRMED = 'confirmed'
    PENDING_CANCELED = 'canceled'
    PLACED_FINAL = 'done'
    
        
_service_pending_oper_to_events = {
    BusinessOperation.MAKE: {
        PendingOperation.REQUESTED: ServiceEventType.PENDING_ADD_CREATED,
        PendingOperation.PENDING_CONFIRMED: ServiceEventType.CREATED,
        PendingOperation.PENDING_CANCELED:  ServiceEventType.PENDING_ADD_CANCELED,
        PendingOperation.PLACED_FINAL: ServiceEventType.CREATED
        },
        
    BusinessOperation.DELETE: {
        PendingOperation.REQUESTED: ServiceEventType.PENDING_DELETE_CREATED,
        PendingOperation.PENDING_CONFIRMED: ServiceEventType.DELETED,
        PendingOperation.PENDING_CANCELED:  ServiceEventType.PENDING_DELETE_CANCELED,
        PendingOperation.PLACED_FINAL: ServiceEventType.DELETED
        },

    BusinessOperation.UPDATE: {
        PendingOperation.REQUESTED: ServiceEventType.PENDING_UPDATE_CREATED,
        PendingOperation.PENDING_CONFIRMED: ServiceEventType.UPDATED,
        PendingOperation.PENDING_CANCELED:  ServiceEventType.PENDING_UPDATE_CANCELED,
        PendingOperation.PLACED_FINAL: ServiceEventType.UPDATED
        },
}



_reserv_pending_oper_to_events = {
    BusinessOperation.MAKE: {
        PendingOperation.REQUESTED: ReservationEventType.PENDING_ADD_CREATED,
        PendingOperation.PENDING_CONFIRMED: ReservationEventType.CREATED,
        PendingOperation.PENDING_CANCELED:  ReservationEventType.PENDING_ADD_CANCELED,
        PendingOperation.PLACED_FINAL: ReservationEventType.CREATED
        },
        
    BusinessOperation.DELETE: {
        PendingOperation.REQUESTED: ReservationEventType.PENDING_DELETE_CREATED,
        PendingOperation.PENDING_CONFIRMED: ReservationEventType.DELETED,
        PendingOperation.PENDING_CANCELED:  ReservationEventType.PENDING_DELETE_CANCELED,
        PendingOperation.PLACED_FINAL: ReservationEventType.DELETED
        },

    BusinessOperation.UPDATE: {
        PendingOperation.REQUESTED: ReservationEventType.PENDING_UPDATE_CREATED,
        PendingOperation.PENDING_CONFIRMED: ReservationEventType.REPLACED,
        PendingOperation.PENDING_CANCELED:  ReservationEventType.PENDING_UPDATE_CANCELED,
        PendingOperation.PLACED_FINAL: ReservationEventType.REPLACED
        },
}
    
