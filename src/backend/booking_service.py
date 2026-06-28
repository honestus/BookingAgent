from backend.business_core import BusinessCore, BusinessCoreWithConfirmation
from shared.user_role import UserRole
from backend.reservations import Reservation
from backend.policy import Service
from backend.domain_errors import *
from utils.datetimes_utils import map_datetime_to_default, minutes_between

import datetime as dt, warnings
from enum import Enum


class BookingService:    
    
    class CoreOperation(Enum):
        ### list of tuple: (operation, finalize_confirm_operation, finalize_cancel_operation)
        MAKE_RESERVATION = ('make_reservation', 'confirm_pending_make_reservation', 'cancel_pending_make_reservation')
        UPDATE_RESERVATION = ('update_reservation', 'cancel_pending_update_reservation', 'cancel_pending_update_reservation')
        CANCEL_RESERVATION = ('cancel_reservation', 'confirm_pending_cancel_reservation', 'cancel_pending_cancel_reservation')
        ADD_SERVICE = ('add_service',  'confirm_pending_add_service', 'cancel_pending_add_service')
        UPDATE_SERVICE = ('update_service', 'confirm_pending_update_service', 'cancel_pending_update_service')
        CANCEL_SERVICE = ('remove_service', 'confirm_pending_remove_service', 'cancel_pending_remove_service')
    
    class FinalizeAction(Enum):
        CONFIRM = 'confirm'
        CANCEL = 'cancel'
    
    
    
    def __init__(self, core: BusinessCore):
        self.core = core
        self._build_dispatch()
            
            
    async def make_reservation(self, user: str, service_name: str, start_time: dt.datetime, minutes_duration: int=None, actor: UserRole = UserRole.USER, force_past_slots: bool=False, force_advance_reservation: bool=False, force_default_grid: bool=True,):
        res_inputs = {'start_time': start_time, 'service_name':service_name, 'user':user}
        if minutes_duration is not None:
            res_inputs.update({'minutes_duration':minutes_duration})
        self._validate_reservation_inputs(**res_inputs)        
        
        if actor not in [UserRole.SYSTEM, UserRole.ADMIN]:
            force_advance_reservation=False
            force_past_slots=False
        
        return await self.__run__(self.CoreOperation.MAKE_RESERVATION, 
            user=user, service_name=service_name, 
            start_time=start_time, minutes_duration=minutes_duration, 
            actor=actor, 
            force_advance_reservation=force_advance_reservation, 
            force_past_slots=force_past_slots, 
            force_default_grid=force_default_grid)
        
    async def cancel_reservation(self, user: str, reservation_id: str=None, start_time: dt.datetime=None, service_name: str=None, actor: UserRole = UserRole.USER, force_past_slots: bool=False, force_advance_cancelation: bool=False):
        res_inputs = {'reservation_id': reservation_id, 'start_time': start_time, 'service_name':service_name}
        res_inputs = {k:v for k,v in res_inputs.items() if v is not None} | {'user':user}
        self._validate_reservation_inputs(**res_inputs)
        
        if start_time is not None:
            start_time = map_datetime_to_default(start_time, ignore_seconds=True)
        
        reservation = self.find_reservation(reservation_id=reservation_id, start_time=start_time, user=user, service_name=service_name, match_inner_time=True)
        if not isinstance(reservation, Reservation):
            raise NotPreviouslyBookedError('You dont have any reservation at the chosen time')
        if service_name is not None and reservation.service_name!=service_name:
            raise PolicyError('Cannot cancel a different service than the previously booked one')
        if start_time is not None and reservation.start_time!=start_time:
            warnings.warn(f'Original reservation was actually at {reservation.start_time}.')
        
        if actor not in [UserRole.SYSTEM, UserRole.ADMIN]:
            force_advance_cancelation=False
            force_past_slots=False
            
        
        return await self.__run__(self.CoreOperation.CANCEL_RESERVATION, 
            reservation_id=reservation.reservation_id, 
            actor=actor, force_past_slots=force_past_slots, 
            force_advance_cancelation=force_advance_cancelation)
        
        
    async def update_reservation(self, user: str, existing_reservation_id: str=None, existing_reservation_start_time: dt.datetime=None, existing_reservation_service_name: str=None, new_start_time: dt.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, actor: UserRole = UserRole.USER, force_default_grid: bool=True, force_past_slots: bool=False, force_advance_reservation: bool=False, force_advance_cancelation: bool=False):
        #VALIDATING PARAMS FOR THE EXISTING RESERVATION
        old_res_inputs = {'reservation_id': existing_reservation_id, 'start_time': existing_reservation_start_time, 'service_name':existing_reservation_service_name}
        old_res_inputs = {k:v for k,v in old_res_inputs.items() if v is not None} | {'user':user}
        self._validate_reservation_inputs(**old_res_inputs)
        #VALIDATING PARAMS FOR THE NEW RESERVATION
        new_res_inputs = {'start_time':new_start_time,'service_name': new_service_name, 'minutes_duration': new_minutes_duration}
        new_res_inputs = {k:v for k,v in new_res_inputs.items() if v is not None} | {'user':user}
        self._validate_reservation_inputs(**new_res_inputs)
        
        if existing_reservation_start_time is not None:
            existing_reservation_start_time = map_datetime_to_default(existing_reservation_start_time, ignore_seconds=True)
        if new_start_time is not None:
            new_start_time = map_datetime_to_default(new_start_time, ignore_seconds=True)


        old_reservation = self.find_reservation(user=user, 
            reservation_id=existing_reservation_id, 
            start_time=existing_reservation_start_time,  
            service_name=existing_reservation_service_name,
            match_inner_time=True)
        if not isinstance(old_reservation, Reservation):
            raise NotPreviouslyBookedError(f'You dont have any reservation with the specified details')
        
        if existing_reservation_start_time is not None and old_reservation.start_time!=existing_reservation_start_time:
            warnings.warn(f'Original reservation was actually at {old_reservation.start_time}.')
               
        if actor not in [UserRole.SYSTEM, UserRole.ADMIN]:
            force_advance_reservation=False
            force_advance_cancelation=False
            force_past_slots=False
            
        
        return await self.__run__(self.CoreOperation.UPDATE_RESERVATION, 
            existing_reservation_id=old_reservation.reservation_id, 
            new_service_name=new_service_name,
            new_start_time=new_start_time,
            new_minutes_duration=new_minutes_duration,
            actor = actor,
            force_default_grid=force_default_grid,
            force_advance_reservation=force_advance_reservation,
            force_advance_cancelation=force_advance_cancelation, 
            force_past_slots=force_past_slots)
                                                
    
    async def finalize_make_reservation(self, finalize_operation: FinalizeAction, user: str, reservation_id: str=None, start_time: dt.datetime=None, service_name: str=None, minutes_duration: int=None, actor: UserRole = UserRole.USER):
        finalize_action = BookingService._validate_finalize_action(finalize_operation)           
        res_inputs = {'reservation_id': reservation_id, 'start_time': start_time, 'service_name':service_name, 'minutes_duration':minutes_duration}
        res_inputs = {k:v for k,v in res_inputs.items() if v is not None} | {'user':user}
        self._validate_reservation_inputs(**res_inputs)
        
        if start_time is not None:
            start_time = map_datetime_to_default(start_time, ignore_seconds=True)
        reservation = self.find_reservation(user=user, reservation_id=reservation_id, start_time=start_time, 
                                            service_name=service_name, minutes_duration=minutes_duration,  
                                            match_inner_time=False)
        if not isinstance(reservation, Reservation):
            raise NotPreviouslyBookedError('Cannot confirm the booking. Cannot find a reservation with the specified parameters')
        
        return await self.__run__(self.CoreOperation.MAKE_RESERVATION, finalize=finalize_action,
        reservation_id=reservation.reservation_id, actor=actor
        )
                                                
                                                
    async def finalize_cancel_reservation(self, finalize_operation: FinalizeAction, user: str, reservation_id: str=None, start_time: dt.datetime=None, service_name: str=None, actor: UserRole = UserRole.USER):
        finalize_action = BookingService._validate_finalize_action(finalize_operation)      
        res_inputs = {'reservation_id': reservation_id, 'start_time': start_time, 'service_name':service_name}
        res_inputs = {k:v for k,v in res_inputs.items() if v is not None} | {'user':user}
        self._validate_reservation_inputs(**res_inputs)
        
        
        if start_time is not None:
            start_time = map_datetime_to_default(start_time, ignore_seconds=True)
        reservation = self.find_reservation(user=user, reservation_id=reservation_id, start_time=start_time, service_name=service_name, match_inner_time=False)
        if not isinstance(reservation, Reservation):
            raise NotPreviouslyBookedError('Cannot confirm the cancelation. Cannot find a reservation with the specified parameters')
        
        return await self.__run__(self.CoreOperation.CANCEL_RESERVATION, finalize=finalize_action,
        reservation_id=reservation.reservation_id, actor=actor
        )
        
        
        
    async def finalize_update_reservation(self, finalize_operation: FinalizeAction, user: str, 
            existing_reservation_id: str=None, existing_reservation_start_time: dt.datetime=None, existing_reservation_service_name: str=None,
            new_start_time: dt.datetime=None, new_service_name: str=None, new_minutes_duration: int=None, 
            actor: UserRole = UserRole.USER):                   
        from datetime import timedelta
        
        finalize_action = BookingService._validate_finalize_action(finalize_operation)
        #VALIDATING PARAMS FOR THE EXISTING RESERVATION
        old_res_inputs = {'reservation_id': existing_reservation_id, 'start_time': existing_reservation_start_time, 'service_name':existing_reservation_service_name}
        old_res_inputs = {k:v for k,v in old_res_inputs.items() if v is not None} | {'user':user}
        self._validate_reservation_inputs(**old_res_inputs)
        #VALIDATING PARAMS FOR THE NEW RESERVATION
        new_res_inputs = {'start_time':new_start_time,'service_name': new_service_name, 'minutes_duration': new_minutes_duration}
        new_res_inputs = {k:v for k,v in new_res_inputs.items() if v is not None} | {'user':user}
        self._validate_reservation_inputs(**new_res_inputs)
                
        if existing_reservation_start_time is not None:
            existing_reservation_start_time = map_datetime_to_default(existing_reservation_start_time, ignore_seconds=True)
        if new_start_time is not None:
            new_start_time = map_datetime_to_default(new_start_time, ignore_seconds=True) 
        
        old_reservation = self.find_reservation(user=user, 
            reservation_id=existing_reservation_id, 
            service_name=existing_reservation_service_name,
            start_time=existing_reservation_start_time, 
            match_inner_time=False)
        if not isinstance(old_reservation, Reservation):
            raise NotPreviouslyBookedError('Cannot confirm the update. Cannot find a reservation with the specified parameters')
        
        existing_update_reserv = old_reservation.get_associated_update_reservation() 
        if not isinstance(existing_update_reserv, Reservation):
            raise ConfirmationError('Cannot confirm update. No previously requested update to confirm.')
        if old_reservation.user!=user or existing_update_reserv.user!=user:
            raise NotPreviouslyBookedError(f'Cannot update. You dont have any reservation with id: {existing_reservation_id}')
        
        confirm_reserv_details = self.core._resolve_reservation_params_with_defaults(start_time=new_start_time, service_name=new_service_name, minutes_duration=new_minutes_duration, existing_reservation=old_reservation)
        confirm_reserv_details['end_time'] = confirm_reserv_details['start_time'] + timedelta(minutes=confirm_reserv_details.pop('minutes_duration'))
        if any(getattr(existing_update_reserv, attr, -1)!=confirm_reserv_details[attr] for attr in confirm_reserv_details):
            raise ConfirmationError('Cannot update. The new update reservation parameters are different than the previously required ones.\
                                    You should probably call update_reservation with such parameters')

        return await self.__run__(self.CoreOperation.UPDATE_RESERVATION, finalize=finalize_action,
        reservation_id=old_reservation.reservation_id, actor=actor
        )
        
        
    async def add_service(self, service_name: str, price: float, minutes_duration: int, description: str='', actor: UserRole = UserRole.ADMIN):        
        self._is_allowed_service_operation(actor)
        
        return await self.__run__(self.CoreOperation.ADD_SERVICE, 
            service_name=service_name, 
            price=price,
            minutes_duration=minutes_duration, 
            description=description,
            actor=actor
        )
        
        
    async def remove_service(self, service_name: str, actor: UserRole = UserRole.ADMIN):        
        self._is_allowed_service_operation(actor)
        
        return await self.__run__(self.CoreOperation.CANCEL_SERVICE, 
            service_name=service_name, 
            price=price,
            minutes_duration=minutes_duration, 
            description=description,
            actor=actor
        )
        
    async def update_service(self, existing_service_name: str, new_price: int|float=None, new_minutes_duration: int=None, new_description: str = None, actor: UserRole = UserRole.ADMIN):        
        self._is_allowed_service_operation(actor)
        if all(e is None for e in [new_price, new_minutes_duration, new_description]):
            raise ValueError('Nothing to update')
        
        return await self.__run__(self.CoreOperation.UPDATE_SERVICE, 
            existing_service_name=existing_service_name, 
            new_price=new_price,
            new_minutes_duration=new_minutes_duration, 
            new_description=new_description,
            actor=actor
        )
        
        
    async def finalize_add_service(self, finalize_operation: FinalizeAction, service_name: str, price: float=None, minutes_duration: int=None, description: str = '', actor: UserRole=UserRole.ADMIN):
        finalize_action = BookingService._validate_finalize_action(finalize_operation)
        self._is_allowed_service_operation(actor)
        
        can_finalize, err, existing_pending_service = self.core._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.MAKE)
        if not can_finalize:
            raise err
        
        curr_req_service = Service(**BusinessCore._resolve_service_params_with_defaults(existing_service=existing_pending_service, price=price, minutes_duration=minutes_duration, description=description))
        if curr_req_service!=existing_pending_service:
            raise ConfirmationError(f'Cannot confirm adding {service_name}. Previous requested service parameters were different than current ones')
            
        return await self.__run__(self.CoreOperation.ADD_SERVICE, finalize=finalize_action, 
            service_name = service_name, actor=actor)
       
    async def finalize_remove_service(self, finalize_operation: FinalizeAction, service_name: str, price: float=None, minutes_duration: int=None, description: str = '', actor: UserRole=UserRole.ADMIN):
        finalize_action = BookingService._validate_finalize_action(finalize_operation)
        self._is_allowed_service_operation(actor)
        
        can_finalize, err, existing_pending_service = self.core._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.DELETE)
        if not can_finalize:
            raise err
            
        curr_req_service = Service(**BusinessCore._resolve_service_params_with_defaults(existing_service=existing_pending_service, price=price, minutes_duration=minutes_duration, description=description))
        if curr_req_service!=existing_pending_service:
            raise ConfirmationError(f'Cannot confirm the removal of {service_name}. Previous requested service parameters were different than current ones')
            
        return await self.__run__(self.CoreOperation.CANCEL_SERVICE, finalize=finalize_action, 
            service_name = service_name, actor=actor)
        
        
    async def finalize_update_service(self, finalize_operation: FinalizeAction, service_name: str, price: float=None, minutes_duration: int=None, description: str = '', actor: UserRole=UserRole.ADMIN):
        finalize_action = BookingService._validate_finalize_action(finalize_operation)
        self._is_allowed_service_operation(actor)

        can_finalize, err, existing_pending_service = self.core._validate_existing_service_pending_op(service_name=service_name, operation=BusinessOperation.UPDATE)
        if not can_finalize:
            raise err
            
        curr_req_service = Service(**BusinessCore._resolve_service_params_with_defaults(existing_service=existing_pending_service, price=price, minutes_duration=minutes_duration, description=description))
        if curr_req_service!=existing_pending_service:
            raise ConfirmationError(f'Cannot confirm the update of {service_name}. Previous requested service parameters were different than current ones')
        
        return await self.__run__(self.CoreOperation.UPDATE_SERVICE, finalize=finalize_action, 
            service_name = service_name, actor=actor)
        
        
    def find_reservation(self, user: str, reservation_id: str=None, start_time: dt.datetime=None,  minutes_duration: int=None, service_name: str=None, match_inner_time: bool=False):
        matching_reservations = self._find_matching_reservations(user=user, reservation_id=reservation_id, start_time=start_time, minutes_duration=minutes_duration, service_name=service_name, match_inner_time=match_inner_time)             
        return self.core._resolve_matching_user_reservations(matching_reservations)
        
    
    def _find_matching_reservations(self, user: str, reservation_id: str=None, start_time: dt.datetime=None,  minutes_duration: int=None, service_name: str=None, match_inner_time: bool=False):
        if reservation_id is None and start_time is None:
            raise ValueError(f'Must provide one among reservation_id and start_time')
        
        if start_time is not None:
            start_time = map_datetime_to_default(start_time, ignore_seconds=True)
        if reservation_id is not None:
            matching_res = self.core.reservation_manager.get_reservation(reservation_id) 
            potential_matching_reservations = [matching_res] if matching_res is not None else []
        else:
            potential_matching_reservations = self.core.reservation_manager._find_reservations_by_inner_time(start_time) if match_inner_time else self.core.reservation_manager.get_reservations_by_start_time(start_time)
        
        if not potential_matching_reservations:
            return None
        matching_reservations = []
        for reservation in potential_matching_reservations:
            reservation_params = self.core._resolve_reservation_params_with_defaults(existing_reservation=reservation, start_time=start_time, service_name=service_name, minutes_duration=minutes_duration)
            validated_start_time, validated_service_name, validated_minutes_duration = reservation_params['start_time'], reservation_params['service_name'], reservation_params['minutes_duration']
                
            reservation_duration = minutes_between(reservation.start_time, reservation.end_time)
            if reservation.user!=user or reservation_duration!=validated_minutes_duration or reservation.service_name!=validated_service_name:
                continue
            if reservation.start_time!=validated_start_time:
                if reservation_id is not None:
                    if not match_inner_time or validated_start_time<reservation.start_time or validated_start_time>reservation.end_time:
                        continue
                warnings.warn(f'Actual start time is {reservation.start_time}')  ##only happens if match_inner_time==True
                validated_start_time = reservation.start_time
            matching_reservations.append(reservation)
        return matching_reservations
        
        
        
    def _validate_reservation_inputs(self, **kwargs):
        user = kwargs.get('user', None)
        service_name =  kwargs.get('service_name', None)
        reservation_id = kwargs.get('reservation_id', None)
        start_time = kwargs.get('start_time', None)
        minutes_duration = kwargs.get('minutes_duration', None)
        from backend.validate_utils import is_reservation_inputs_valid
        is_valid, valid_params = is_reservation_inputs_valid(user=user, service_name=service_name, reservation_id=reservation_id, start_time=start_time, minutes_duration=minutes_duration)
        if not is_reservation_inputs_valid:
            invalids = {k: v[1] for k,v in valid_params.items() if not v[0]}
            raise TypeError('Wrong parameters:' + '; '.join([f'{k}, must be {v}' for k,v in invalids.items()]) )
        if 'service_name' in kwargs and kwargs['service_name'] not in self.core.policy_manager.services:
            raise PolicyError(f'Unknown service {service_name}')
            
    @staticmethod
    def _is_allowed_service_operation(actor: UserRole):
        from backend.validate_utils import is_service_inputs_valid
        if actor not in [UserRole.ADMIN, UserRole.SYSTEM]:
            raise NotAllowedError('Cannot add service. You are not allowed')
        return True
        
            
    @staticmethod
    def _validate_finalize_action(action: str|FinalizeAction):
        if not isinstance(action, BookingService.FinalizeAction) and action not in [e.value for e in list(BookingService.FinalizeAction)]:
            raise ValueError(f'Unknown finalize action {action}')
        return BookingService.FinalizeAction(action)
        
        
    def __setattr__(self, attr, value):
        if attr=='manager' and hasattr(self, 'manager'):
            raise ValueError('Cannot reset manager. It is final')
        return super().__setattr__(attr, value)
     
    def _build_dispatch(self):
        import inspect
        # dispatch table finale
        self._dispatch = {}

        for op in self.CoreOperation:
            for action in list(self.FinalizeAction)+[None]:

                method_name = self._map_operation_to_method_name(op, action)
                method = getattr(self.core, method_name, None)

                if method is None:
                    raise ValueError(f'Unknown method: {method_name}')

                is_async = inspect.iscoroutinefunction(method)

                self._dispatch[method_name] = (method, is_async)
        
    
    @staticmethod
    def _map_operation_to_method_name(operation: CoreOperation, finalize: FinalizeAction=None):
        if finalize is None:
            finalize_idx = 0  
        else:
            finalize = BookingService._validate_finalize_action(finalize)
            finalize_idx = 1 if finalize==BookingService.FinalizeAction.CONFIRM else 2
        
        operation_str =  (BookingService.CoreOperation(operation).value)[finalize_idx]
        return operation_str
    
        
    async def __run__(self, operation: CoreOperation, finalize: FinalizeAction=None, **kwargs):
        method_name = self._map_operation_to_method_name(operation, finalize)
        try:
            method, is_async = self._dispatch[method_name]
        except:
            raise ValueError('Cannot run. Non-existing method')
        res = method(**kwargs)
        if is_async:
            return await res
        return res
    
        

    