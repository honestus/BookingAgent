from __future__ import annotations
from collections import defaultdict
import llm_helper
import asyncio
from application.cache import SystemCache, UserCache
from application.request_response import StructuredRequest
from application import request_mapping, authenticator
from application.request_handler import RequestHandler
from shared.user_role import UserRole
from shared import globals_shared


"""
_SAVE_EACH_N_REQUESTS: int -- N.of requests that updates any data (insert,update,cancel) on backend before checkpoint.
_SAVE_EACH_N_REQUESTS: int -- N.of minutes to continuously run checkpoint (only if any new update_request run meanwhile)
"""
_SAVE_EVERY_N_REQUESTS = 5
_SAVE_EACH_MINUTES = 3

class ApplicationOrchestrator:
    
    
    
    def __init__(self, backend_manager: BookingService, users_db: UsersToRoleDB, llm_model: LLMModel, storage_manager: AppStoringManager):
        self.llm_model = llm_model
        self.request_handler = RequestHandler(backend_manager)
        self.users_db = users_db
        self.storage_manager = storage_manager
        self.cache = None      
        self._system_cache_init_lock = asyncio.Lock()
        self._active_backend_operations = 0
        self._need_to_freeze_to_checkpoint = False
        self.__schedule_checkpoint_task__ = None
        self.__checkpoint_task__ = None
        self._checkpoint_cond = asyncio.Condition()
        self._checkpoint_lock = asyncio.Lock()
        
    async def handle_message(self, user_id: str, message: str, past_conversation_messages: list[tuple[str, str]]):   
        import datetime as dt
        from backend import backend_storing_utils
        from backend.business_event import updates_backend_data
        from chat_system.telegram_disk_utils import _store_obj_to_disk_queue, _jsonl_serializer
        user_id = str(user_id)
        user = self.users_db.get_user(user_id) 
        if user is None:
            user = authenticator.User(user_id=user_id, user_role=UserRole.USER)
        
        await self._ensure_system_cache_init()
        await self._ensure_user_cache_init(user_id, is_admin=self.users_db.is_admin(user_id))
                          
        stringified_methods_params = await self._build_stringified_methods_to_expose(user)
        cached_backend_user_info = await self.cache.get_prompt_context(user_id)
        
        prompt = llm_helper.build_backend_request_prompt(
                user_message = llm_helper.preprocess_user_message(message), 
                username=user.user_role.value,
                allowed_methods=stringified_methods_params,
                past_conversation_messages=past_conversation_messages,
                ** cached_backend_user_info
        )
        
        print(prompt)
        llm_reply = await self.llm_model.run(prompt)
    
        raw_llm_request_dct = llm_helper.model_reply_to_dict(llm_reply)
        requests_to_run = request_mapping.get_requests_from_raw_dict(raw_llm_request_dct)
        any_change_to_backend = False
        request_responses = list()
        if requests_to_run:
            # need to run the requests -> enter the gate(if open: i.e. no checkpoint running) and increase n_processes_running_requests
            async with self._checkpoint_cond:
                while self._need_to_freeze_to_checkpoint:
                    await self._checkpoint_cond.wait()
                self._active_backend_operations += 1

            try:
                # Phase 3: Execute the changes immediately
                for request_dct in requests_to_run:
                    request_dct[globals_shared.USER_ATTRIBUTE] = user
                    structured_request = self.request_handler.build_structured_request(request_dct, raise_error=False)                            
                    structured_request._id = ApplicationOrchestrator.__generate_req_id__()
                    
                    executable_req, response = await self.request_handler.run(structured_request)
                    executable_req.timestamp = dt.datetime.now(dt.UTC)
                    if response.success and any(updates_backend_data(ev) for ev in response.events):
                        await self.storage_manager.append_request(executable_req)
                        any_change_to_backend=True
                    request_responses.append( (structured_request, response) )
            finally:
                async with self._checkpoint_cond:
                    self._active_backend_operations -= 1
                    if self._active_backend_operations == 0: ##notify to all the tasks waiting for the condition that requests running are now 0 (i.e. checkpoint is possible if requested)
                        self._checkpoint_cond.notify_all()
        
          
        for i, response in enumerate(map(lambda e: e[1], request_responses)):
            try:
                await self._update_cache_by_response_output(response)
                
                #print(f'Reservations Cache after {methods[i]} ->\n', (await self.cache.get_prompt_context(user_id))["reservations"],'\n')
            except Exception as e:
                print('\nException!!!!!!!', e,'\n')
                continue
        
        
        if not requests_to_run:
            reply_to_user = raw_llm_request_dct.get(globals_shared.REPLY_ATTRIBUTE, '')
        else: 
            
            reply_prompt = llm_helper.build_user_reply_prompt(user_language=raw_llm_request_dct.get(globals_shared.USER_LANGUAGE_ATTRIBUTE, None), 
                            user_nickname=user.nickname, actions_performed_and_outputs_info=[request_response_to_str_info(*e) for e in request_responses], 
                            past_conversation_messages=past_conversation_messages+[(user.nickname, message)],
                            services=cached_backend_user_info['services'], opening_hours=cached_backend_user_info['opening_hours']
                        )
            print(reply_prompt)
            
            reply_to_user = await self.llm_model.run(reply_prompt)
        print("\n\n Raw req and req_resp", raw_llm_request_dct, [tuple(e.__dict__ for e in t) for t in request_responses], reply_to_user)
        
        
        if any_change_to_backend:
            if self.storage_manager.n_requests>_SAVE_EVERY_N_REQUESTS:
                asyncio.create_task(self.checkpoint())
            else:
                self._ensure_checkpoint_scheduled()
        return reply_to_user
     
    
        
    
    async def checkpoint(self):
        if (self.__checkpoint_task__ is not None and not self.__checkpoint_task__.done()):
            return
        self.__checkpoint_task__ = asyncio.create_task(self._checkpoint())
    
    async def _checkpoint(self):
        import datetime as dt
        import copy 
        
        if self._checkpoint_lock.locked():
            return

        async with self._checkpoint_lock:
            print(f'\n\nBackend checkpoint started -- {dt.datetime.now(dt.UTC)}\n\n')
            # Phase A: Block new requests & wait for current ones to drain
            async with self._checkpoint_cond:
                print('0')
                self._need_to_freeze_to_checkpoint = True
                while self._active_backend_operations > 0:
                    await self._checkpoint_cond.wait()
            print('a')
            try:
                # Phase B: Take the snapshot as fast as possible
                
                manager_snapshot = copy.deepcopy(self.request_handler.business_manager.core)
                print('ok!')
                archived_requests_fp = await self.storage_manager.archive_requests()
            except:
                raise
            finally:
                # Optimization 2: Open the gate the INSTANT the snapshot is safe!
                async with self._checkpoint_cond:
                    self._need_to_freeze_to_checkpoint = False
                    self._checkpoint_cond.notify_all()
            print('b')
            # Phase C: Slow Disk I/O runs while users are already back to chatting!            
            n_tries_left= 3
            finished=False
            while not finished:
                try:
                    await self.storage_manager.store_manager(manager_snapshot)
                    finished=True
                    print(f'\nBackend checkpoint successful -- {dt.datetime.now(dt.UTC)}\n\n')
                except Exception as e:
                    if not n_tries_left:
                        await self._rollback_checkpoint_files(archived_requests_fp)
                        finished=True
                        print(f'\nBackend checkpoint ended UNSUCCESSFULLY -- Error: {e}.\t {dt.datetime.now(dt.UTC)}\n\n')
                    else:
                        n_tries_left-=1
                        await asyncio.sleep(2 ** (3 - n_tries_left)) ##backoff
        
            self.__checkpoint_task__ = None
            if self.__schedule_checkpoint_task__ and not self.__schedule_checkpoint_task__.done():
                self.__schedule_checkpoint_task__.cancel()
        
                
                    
      
    def _ensure_checkpoint_scheduled(self):
        if self.__schedule_checkpoint_task__ is None or self.__schedule_checkpoint_task__.done():
            self.__schedule_checkpoint_task__ = asyncio.create_task(self._schedule_checkpoint())
            
    async def _schedule_checkpoint(self, checkpoint_time: dt.datetime = None):
        import datetime as dt
        
        if checkpoint_time is None:
            time_to_sleep = dt.timedelta(minutes=_SAVE_EACH_MINUTES)
        else:
            time_to_sleep = checkpoint_time - dt.datetime.now()
        if time_to_sleep.total_seconds()<=0:
            raise ValueError('Wrong checkpoint_time, it is a past time')
        print(f'\nBackend checkpoint schedule to run at {dt.datetime.now(dt.UTC)+time_to_sleep}')
        await asyncio.sleep(time_to_sleep.total_seconds())
        await self.checkpoint()
        
        
    async def _rollback_checkpoint_files(self, backup_filepath: str):
        """
        Recovers the chronological requests file if a snapshot storage fails.
        Re-locks the orchestrator gate, reloads concurrent requests, and appends
        them to the restored historical file.
        """
        from pathlib import Path
        
        print("Checkpoint failed -- Initiating file rollback process...")
        if not isinstance(backup_filepath, (str, Path)):
            raise ValueError('Wrong backup_filepath')
        backup_filepath = Path(backup_filepath)
        if not backup_filepath.exists():
            raise ValueError('backup_filepath doesnt exist')
        # 1. Drop the gate to prevent new incoming traffic during file surgery
        async with self._checkpoint_cond:
            self._need_to_freeze_to_checkpoint = True
            while self._active_backend_operations > 0:
                await self._checkpoint_cond.wait()
                
        try:
            # 2. Safely inspect and swap files under the storage manager lock
            
            # Load the new requests generated after archiving
            new_requests = await self.storage_manager.load_requests()
            #moving archive back to requests_filepath
            backup_filepath.replace(self.storage_manager._requests_filepath)
            self.storage_manager._init_n_requests_from_disk()
            # 3. Append the new requests to the previously existing ones
            for req in new_requests:
                await self.storage_manager.append_request(req)

        finally:
            # 4. Open the gate again no matter what
            async with self._checkpoint_cond:
                self._need_to_freeze_to_checkpoint = False
                self._checkpoint_cond.notify_all()
        
        
        
    async def _ensure_system_cache_init(self):
        # 1. Fast path: If already initialized, exit immediately (No locking overhead)
        if self.cache is not None:
            return

        # 2. Slow path: Only hit on the very first message(s) at startup
        async with self._system_cache_init_lock:
            # Double-check: Did a previous task initialize it while we were waiting for the lock?
            if self.cache is not None:
                return
            
            system_user = authenticator.User(user_id=None, user_role=UserRole.SYSTEM)
            
            services_request = StructuredRequest(user=system_user, method='core.get_available_services')
            services = (await self.request_handler.run(services_request))[1].data[-1].new
            
            open_hours_request = StructuredRequest(user=system_user, method='core.get_default_opening_hours')
            opening_hours = (await self.request_handler.run(open_hours_request))[1].data[-1].new
            
            # Atomic assignment: SystemCache becomes alive all at once
            self.cache = SystemCache(services=services, opening_hours=opening_hours)
        
        
    async def _ensure_user_cache_init(self, user_id: str, is_admin: bool=False):
        if getattr(self, 'cache', None) is None:
            raise ValueError('System cache not initialized')
            
        if not is_admin and self.cache.get_user_cache(user_id):
            return
                
        system_user = authenticator.User(user_id=None, user_role=UserRole.SYSTEM)
        if is_admin:  
            user_reserv_request = StructuredRequest(user=system_user, method='core.get_all_reservations', params={})
        else:
            user_reserv_request = StructuredRequest(user=system_user, method='core.get_user_reservations', params={'user':user_id})
        user_reservations = (await self.request_handler.run(user_reserv_request))[1].data[-1].new
        
        user_cache = UserCache(reservations=user_reservations)
        self.cache.set_user_cache(user_id=user_id, cache=user_cache)
        
        
    async def _build_stringified_methods_to_expose(self, user: authenticator.USER) -> list[str]:
        exposed_methods, exposed_methods_str = self.request_handler._exposed_methods_params_by_role[user.user_role]
        user_cache = self.cache.get_user_cache(user.user_id)
        if user_cache is None:
            raise ValueError('User cache not initialized')
        
        reservations_state = await user_cache.get_reservations_state()
        filtered_methods_indexes = filter_exposed_methods(methods_names=[m.rsplit('.')[-1] for m in exposed_methods.keys()], return_indexes=True, data_state=reservations_state)

        return [exposed_methods_str[i] for i in filtered_methods_indexes]
        
    
    async def _update_cache_by_response_output(self, response):
        from backend.business_event import ReservationEventType, ServiceEventType, SystemEventType 
        if not response.success:
            return
        
        for ev_data, ev_type in zip(response.data, response.events):
            if ev_type.name=='NOOP':
                continue
            
            if isinstance(ev_type, ReservationEventType):
                old_data, updated_data = ev_data.old, ev_data.new
                if old_data:
                    if updated_data is None or updated_data.user!=old_data.user or updated_data.reservation_id!=old_data.reservation_id:
                        user_cache = self.cache.get_user_cache(old_data.user)
                        if user_cache is None:
                            await self._ensure_user_cache_init(old_data.user, is_admin=False)
                        else:
                            await user_cache.remove_reservation(old_data.reservation_id)

                
                if updated_data:
                    user_cache = self.cache.get_user_cache(updated_data.user)
                    if user_cache is None:
                        await self._ensure_user_cache_init(updated_data.user, is_admin=False)
                            
                    else:
                        await user_cache.upsert_reservation(updated_data)
                                  
                continue
            
            
            if isinstance(ev_type, ServiceEventType):
                old_data, updated_data = ev_data.old, ev_data.new
                if old_data:
                    if updated_data is None or updated_data.service_name!=old_data.service_name:
                        await self.cache.remove_service(old_data.service_name)
                if updated_data:
                    await self.cache.upsert_service(updated_data)                
                continue
                
            
            if isinstance(ev_type, SystemEventType):
                if ev_type.name in [OPENING_HOURS_UPDATED, CALENDAR_UPDATED]:
                    system_user = authenticator.User(user_id=None, user_role=UserRole.SYSTEM)
                    open_hours_request = StructuredRequest(user=system_user, method='core.get_default_opening_hours')
                    opening_hours = (await self.request_handler.run(open_hours_request)).data[-1].new
                    await self.cache.set_opening_hours(opening_hours)
                    continue
                
            raise NotImplementedError(f'{ev_type}')
        
        
    @staticmethod
    def __generate_req_id__():
        import uuid
        return str(uuid.uuid4())


def _request_to_str(request: StructuredRequest) -> str:
    params_str = ', '.join([f'{k}={v}' for k, v in request.params.items()])
    return f'operation={request.method}({params_str})'


def _response_error_to_str(response: StructuredResponse) -> str:
    from application.request_response import ResponseErrorCode
    
    if response.success:
        return '-- No error --'
    
    error_str = f'error_code={response.error_code.name}; '
    if response.error_code == ResponseErrorCode.PARAMETERS_ERROR:
        if len(response.error_msg.args)>1:
            req = response.error_msg.args[1]
            missing_params = req.missing_params
            extra_params = req.extra_params
        
            
            param_errors_str = (f'missing_parameters={missing_params} ' if missing_params else '') + (f'unknown_parameters={extra_params} ' if extra_params else '')
            return error_str + param_errors_str

    return error_str + f'error_message={response.error_msg}'


def _reservation_confirmation_info_str(reservation: Reservation) -> str:
    from backend.domain_logic import is_reservation_confirmed_nopending
    if is_reservation_confirmed_nopending(reservation):
        return 'confirmation_required=False'

    return 'confirmation_required=True'


def _event_to_str(event_type, data) -> str:
    from backend.business_event import ReservationEventType, ServiceEventType

    if event_type in [ReservationEventType.REPLACED, ServiceEventType.UPDATED]:
        previous_object = data.old
        updated_object = data.new

        return (
            f'event={event_type.value}; '
            f'previous_object={previous_object}; '
            f'updated_object={updated_object}'
        )

    if event_type == ReservationEventType.PENDING_UPDATE_CREATED:
        active_reservation = data.new
        pending_updated_reservation = data.new.get_associated_update_reservation()

        return (
            f'event={event_type.value}; '
            f'current_active_reservation={active_reservation}; '
            f'pending_updated_reservation={pending_updated_reservation}; '
            f'confirmation_required=True'
        )

    if event_type == ServiceEventType.PENDING_UPDATE_CREATED:
        active_service = data.old
        pending_updated_service = data.new

        return (
            f'event={event_type.value}; '
            f'current_active_service={active_service}; '
            f'pending_updated_service={pending_updated_service}; '
            f'confirmation_required=True'
        )

    impacted_object = data.new if data.new is not None else data.old
        

    if isinstance(event_type, ReservationEventType):
        if not isinstance(impacted_object, list):
            impacted_object = [impacted_object]
        confirmation_infos = [_reservation_confirmation_info_str(o) for o in impacted_object]
        obj_and_confirmation_str = "; ".join(str(o) + ' - '+confirmed for o,confirmed in zip(impacted_object,confirmation_infos))
        return f'event={event_type.value}; object(s)=[{obj_and_confirmation_str}]'

    return f'event={event_type.value}; object(s)={impacted_object}'


def request_response_to_str_info(request: StructuredRequest, response: StructuredResponse) -> str:
    request_str = _request_to_str(request)

    if not response.success:
        error_str = _response_error_to_str(response)
        return f'{request_str}; success=False; {error_str}'

    final_event = response.events[-1] if response.events else None
    final_data = response.data[-1] if response.data else None

    if final_event is None:
        return f'{request_str}; success=True; event=None'

    event_str = _event_to_str(final_event, final_data)

    return f'{request_str}; success=True; {event_str}'
    
    
    
def filter_exposed_methods(methods_names: list[str], data_state: UserDataState, return_indexes: bool=False) -> dict[MethodDetails, list[ExposedParam]]|list[int]:
    filtered_lst = []
    for i, method_name in enumerate(methods_names):
        """
        if any(method_name.startswith(del_str) for del_str in ['update_', 'cancel_', 'remove_',]) and not data_state.has_any_data:
            continue
        """
        if any(method_name.startswith(del_str) for del_str in ['finalize_make', 'finalize_add']) and not data_state.has_pending_adds:
            continue
        if any(method_name.startswith(del_str) for del_str in ['finalize_cancel', 'finalize_remove']) and not data_state.has_pending_cancelations:
            continue
        if any(method_name.startswith(del_str) for del_str in ['finalize_update']) and not data_state.has_pending_updates:
            continue

        filtered_lst.append(method_details if not return_indexes else i)
        
    return filtered_lst