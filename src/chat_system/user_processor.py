from __future__ import annotations
from pathlib import Path
from chat_system import conversation_manager, telegram_disk_utils
from chat_system.conversation_policy import ConversationPolicy
from chat_system.telegram_disk_utils import DiskDirType
from chat_system.user_storage_manager import UserStorageManager
from chat_system.message_responses import ReceivedMessage, BotResponse, ChatMessagesBatch, ProcessStatus, SendStatus, ResponseKind, SentResponseFS

#from nlu_agent import NLUAgent
import asyncio
import datetime as dt

import logging
logger = logging.getLogger(__name__)

NEW_MSG_SILENCE_TIMEOUT = dt.timedelta(seconds=5)
MAX_BATCH_TIMELAPSE = dt.timedelta(hours=12)
MAX_BATCH_TEXT_LENGTH = 1000
MESSAGE_EXPIRY_WINDOW = dt.timedelta(seconds=600)
RECONCILE_ERRORS_TIME_WINDOW = dt.timedelta(seconds=300)
ALREADY_PROCESSING_MESSAGE_ERROR = "Sorry, we are already replying to your past messages. Please re-send your message again once you get a reply from our side."
ACTIVE_ERRORS_THRESHOLD = 5
SINGLE_RUN_MAX_ERROR_RETRY = 3
STORE_EVERY_RESPONSES = 15
RETRY_CHECK_INTERVAL = dt.timedelta(seconds=10)


from telegram import Update
#nlu_agent: NLUAgent, business_agent: BusinessAgent, pending_requests: list=[]
class UserProcessor:
    def __init__(self, user_id: int, storage_manager: UserStorageManager, error_manager: ErrorManager, queue_manager: MessageQueueManager, conversation_manager: ConversationManager, metadata_manager: RuntimeMetadataManager, sender: MessageSender, app_system: ApplicationOrchestrator, ):
        self.user_id = user_id
        self.storage_manager = storage_manager
        self.queue_manager = queue_manager
        self.metadata_manager = metadata_manager
        self.conversation_manager = conversation_manager
        self.error_manager = error_manager
        self.sender = sender
        self.app_system = app_system
        self._conversation_policy = ConversationPolicy
        
        self._is_processing = False
        self.__is_expiry_msg_sent__ = False
        self.__user_lock__ = asyncio.Lock()
        self.__errors_files_lock__ = asyncio.Lock()
        self.__batcher_timeout_task__ = None
        self.__reconcile_task__ = None
        self.__retry_watchdog_task__ = None
       
         #boolean to avoid sending "expired_msg_error" reply multiple times
        
        
    @classmethod
    async def from_disk(cls, user_id: int, user_path: Path, sender: MessageSender, error_manager: ErrorManager,
        app_system: ApplicationOrchestrator, max_conversation_turns:int, curr_time: dt.datetime=None) -> "UserProcessor":
        from chat_system import recovery_utils
        from chat_system.conversation_manager import ConversationManager
        from chat_system.metadata import RecoveryCheckpoint, RuntimeMetadataManager

        if curr_time is None:
            curr_time = dt.datetime.now(dt.UTC)
        
        storage = UserProcessor.init_storage_manager(user_path=user_path, user_id=user_id)
        
        previous_checkpoint = storage.load_checkpoint() or RecoveryCheckpoint()
        ##loading errors
        process_errors, send_errors  = await recovery_utils.read_and_reconcile_errors_on_disk(storage, ACTIVE_ERRORS_THRESHOLD) #both are ErrorState objects
        ##loading needed files (i.e. files containing errors or containing ids > last_id)
        msg_files, resp_files, sent_files = recovery_utils.resolve_files_to_load(storage, previous_checkpoint)
        received_msgs, processed_responses, sent_responses, conversation_manager = await asyncio.gather(
            storage.messages.read_files(msg_files),
            storage.processed_responses.read_files(resp_files),
            storage.sent_responses.read_files(sent_files),
            ConversationManager.from_disk(storage_manager=storage, max_turns=max_conversation_turns, snapshot_time=curr_time)
        )
        
        loaded_resp_ids = {r.obj.response_id for r in processed_responses}
        missing_send_error_ids = set(loaded_err.loaded_obj.obj.response_id for loaded_err in send_errors.active) - loaded_resp_ids
        if missing_send_error_ids: ##ensuring all the send_errors are loaded
            extra_processed_resp = await recovery_utils.recover_missing_send_error_responses(
                missing_ids=missing_send_error_ids,
                storage_manager=storage,
                previous_checkpoint=previous_checkpoint,
                already_loaded_files=set(resp_files),
            )
            processed_responses.extend(extra_processed_resp)

        ### APPENDING LOADED DATA TO RUNTIME_METADATA IN ORDER TO UPDATE CHECKPOINT AND RELATED INFO (last_handled_ids, last_process_file, error_files, etc.)
        runtime = RuntimeMetadataManager.from_loaded_data(
            received_messages=received_msgs,
            processed_responses=processed_responses,
            sent_responses=sent_responses,
            active_process_errors=[e.loaded_obj for e in process_errors.active],
            abandoned_process_errors=[e.loaded_obj for e in process_errors.abandoned],
            active_send_errors=[e.loaded_obj for e in send_errors.active],
            abandoned_send_errors=[e.loaded_obj for e in send_errors.abandoned],
            previous_checkpoint=previous_checkpoint
        )

        runtime.checkpoint(msg_file_key=storage.messages.shard_organizer._get_file_idx,
            processed_resp_file_key=storage.processed_responses.shard_organizer._get_file_idx,
            sent_resp_file_key=storage.sent_responses.shard_organizer._get_file_idx,
            replace_previous_checkpoint_error_files=True,
        )
        storage.write_checkpoint(runtime.last_checkpoint)

        ##building queues from unprocessed/unsent/active errors data
        queue_manager = UserProcessor._build_queue_manager(
            loaded_received_msgs=received_msgs, loaded_processed_responses=processed_responses,
            active_process_errors=[loaded_err.loaded_obj for loaded_err in process_errors.active], 
            active_send_errors=[loaded_err.loaded_obj for loaded_err in send_errors.active], 
            expiring_time_check=curr_time,
            last_handled_processing_update_id=runtime.last_checkpoint.last_handled_processing_update_id,
            last_handled_sending_response_id=runtime.last_checkpoint.last_handled_sending_response_id,
        )

        
            
        return cls(
            user_id=user_id, storage_manager=storage, metadata_manager=runtime,
            queue_manager=queue_manager, conversation_manager=conversation_manager,
            sender=sender, app_system=app_system, error_manager=error_manager,
        )
    
        
    
    async def handle_message(self, update: Update):
        self.error_manager.network.record_success() ###connection ok, potentially helping error_manager and retries of network errors (e.g. send_errors)
        if self._is_processing:
            await self.sender.send(chat_id=update.message.chat.id, text=ALREADY_PROCESSING_MESSAGE_ERROR)
            return

        update_id = update.update_id
        msg_object = ReceivedMessage(update_id = update_id,
            user_id = self.user_id,
            chat_id = update.message.chat.id,
            text = update.message.text,
            sent_at = update.message.date,
            received_at = dt.datetime.now(dt.UTC)
        )

        msg_file = await self.storage_manager.messages.append(msg_object)
        self.storage_manager.store_user_as_unprocessed()

        async with self.__user_lock__:
            self.queue_manager.append_message(msg_object)
            self.metadata_manager.append_received_message(update_id=msg_object.update_id, message_filepath=msg_file)
            # !! RESET USER TIMER AND START BATCHER TASK !!
            if self.__batcher_timeout_task__ and not self.__batcher_timeout_task__.done():
                self.__batcher_timeout_task__.cancel()
            self.__batcher_timeout_task__ = asyncio.create_task(self.batch_pending_messages())


    async def batch_pending_messages(self, expiring_time_check: dt.datetime = None):
        """
        This coroutine's task is the one handle_message cancels on every new
        message. Everything here must either (a) happen before the sleep with
        no unprotected gap a cancel could land in, or (b) be handed off to a
        SEPARATE task before any wait, so a later cancel can't touch it.
        """
        expiring_time_check = expiring_time_check or dt.datetime.now(dt.UTC)

        async with self.__user_lock__:
            if not self.queue_manager.any_new_message:
                return
            all_msgs = self.queue_manager.messages

        last_user_msg = max(all_msgs, key=lambda m: m.sent_at)
        time_from_last_msg = (expiring_time_check - last_user_msg.sent_at).total_seconds()
        sleep_time = max(5, NEW_MSG_SILENCE_TIMEOUT.total_seconds() - time_from_last_msg)
        projected_check_time = expiring_time_check + dt.timedelta(seconds=sleep_time)

        any_pending, any_expired =True, False
        for m in all_msgs:
            if is_expired(m, projected_check_time):
                any_expired=True
                break

        if any_expired: ##IF USER HAS "EXPIRED MESSAGES", WE AVOID PROCESSING THEM AND SEND HIM A MESSAGE TO INFORM THAT SUCH MESSAGES WONT BE REPLIED.
            expired_msgs, any_pending = [], False
            async with self.__user_lock__:
                all_msgs = self.queue_manager.pop_all_messages()
                for m in all_msgs:
                    if is_expired(m, projected_check_time):
                        expired_msgs.append(m)
                    else:
                        self.queue_manager.append_message(m)
                        any_pending=True
            expired_batches = generate_batches(messages=expired_msgs, max_batch_timelapse=dt.timedelta(days=10000), max_message_length=10000000)
            for b in expired_batches:
                b.mark_to_skip(reply_text=get_expired_message_to_send())
            asyncio.create_task(self._enqueue_response_and_process(expired_batches))

        if not any_pending:
            return

        try:
            await asyncio.sleep(sleep_time) ##STARTING TIMER IN ORDER TO CALL PROCESSOR ONLY IF USER DOESNT SEND NEW MESSAGE WITHIN SILENCE
        except asyncio.CancelledError:
            return ##USER HAS SENT A NEW MESSAGE, CANCELING CURRENT TASK AND STARTING A NEW ONE

        async with self.__user_lock__:
            non_expired_msgs = self.queue_manager.pop_all_messages()
        if non_expired_msgs:
            batches = generate_batches(non_expired_msgs)
            asyncio.create_task(self._enqueue_response_and_process(batches))


    async def _enqueue_response_and_process(self, batches: list):
        async with self.__user_lock__:
            for b in batches:
                self.queue_manager.append_response(b)
        asyncio.create_task(self.process_pending_responses())
    

    
    
    
    async def process_pending_responses(self):
        async with self.__user_lock__:
            if self._is_processing:
                return
            self._is_processing = True
        
        any_expired_error_reply_sent = False 
        while self.queue_manager.any_pending_response:
            async with self.__user_lock__:
                br = self.queue_manager.pop_response()
            if not br.to_skip:
                self.__is_expiry_msg_sent__ = False #resetting to False as soon as we process a non expired msg.
            
            if br.process_status in [ProcessStatus.UNPROCESSED, ProcessStatus.ERROR_PROCESS, ProcessStatus.SKIPPED]:
                ### PROCESSING BRANCH -- GENERATING REPLY TO THE CURRENT BotResponse 
                await self._process_response(br, max_retry=SINGLE_RUN_MAX_ERROR_RETRY)
            
            if br.process_status in [ProcessStatus.PROCESSED, ProcessStatus.SKIPPED] and br.send_status in [SendStatus.UNSENT, SendStatus.ERROR_SEND]:
                ### SENDING BRANCH -- SENDING REPLY TO USER
                await self._send_response(br, max_retry=SINGLE_RUN_MAX_ERROR_RETRY)
        
        async with self.__user_lock__:
            self._is_processing=False
            if not self.queue_manager.any_new_message:
                self.storage_manager.store_user_as_processed()
            
            if self.metadata_manager.get_n_unique_responses_handled_from_checkpoint() >= STORE_EVERY_RESPONSES: 
                ### CHECKPOINT -- Storing metadata and system status on disk###
                asyncio.create_task(self.checkpoint())
           
            if self.queue_manager.any_pending_response: ##starting a new processor if any new response is waiting to get processed (i.e. was generated when this processor was handling store_checkpoint)
                asyncio.create_task(self.process_pending_responses())
        
    
    
    async def _process_response(self, response: BotResponse, max_retry: int):
        if response.process_status==ProcessStatus.PROCESSED:
            return
         
        processing_finished, processing_attempts = False, 0
        starting_process_status = response.process_status

        while not processing_finished:
            try:
                if response.to_skip:
                    response.mark_as_skipped(getattr(response, 'reply_text', get_expired_message_to_send()))
                
                else:
                    reply_text = await self._get_reply_msg(response=response)
                    response.mark_as_replied(reply_text)
                
                response._processed_response_filepath = await self.storage_manager.processed_responses.append(response)#(await telegram_disk_utils.append_line_to_user_file(obj=response, user_id=self.user_id, base_dir=self.user_path.parent, filetype=UserFileType.PROCESSED_RESPONSES))[0]
                if starting_process_status == ProcessStatus.ERROR_PROCESS:
                    async with self.__errors_files_lock__:
                        await self.storage_manager.solved_process_errors.append(response.response_id)#telegram_disk_utils.append_line_to_user_file(obj=response.response_id, user_id=self.user_id, base_dir=self.user_path.parent, filetype=UserFileType.PROCESS_ERRORS_RESOLVED) #former error->now marking as solved
                    self._schedule_reconcile()
                
                async with self.__user_lock__:
                    self.metadata_manager.append_process_success(response.response_id, response.update_ids, processed_response_filepath=response._processed_response_filepath)
                
                ##success -> we can restore backoff to default value
                ## and we can confidently retry to process previous errors
                self.error_manager.record_process_success() 
                if self.queue_manager.any_process_error:
                    asyncio.create_task(self._retry_all_queued_errors(error_type=ErrorType.PROCESS)) 
                processing_finished = True
            
            except Exception as e:
                logger.exception(f'error processing response with id {response.response_id}' )
                classification = await self.error_manager.classify_process_error(e)

                if not classification.retryable:
                    # Non-retryable -- abandon immediately, this occurrence,
                    response.mark_as_reply_error()
                    await self._finalize_non_retryable_process_error(response)
                    processing_finished = True
                    continue

                processing_attempts += 1
                if processing_attempts >= max_retry:
                    response.mark_as_reply_error()
                    # Local retries exhausted. Increment metadata's count and let the threshold decide abandon/active.
                    await self._finalize_retryable_process_error(response)
                    processing_finished = True
                else:
                    await asyncio.sleep(classification.backoff_seconds)
        
        self._conversation_policy.update_context(processor=self, bot_response=response, role=conversation_manager.Role.USER) ##updating conversation by adding this current response user msg
             
       
    async def _finalize_non_retryable_process_error(self, response: BotResponse):
        """
        This response_id terminally failing to send. Records to  disk, increments metadata's per-id count.
        If retryable=False, immediately appending to abandoned errors.
        Otherwise deciding abandoned based on count: if abandoned, scheduling reconcile task.
        """
        # Known permanently non-retryable (BadRequest, Forbidden, etc.).
        # write straight to the abandoned file. No count check needed --
        async with self.__errors_files_lock__:
            response._processed_response_filepath = self.storage_manager.append_abandoned_process_error(response)
        async with self.__user_lock__:
            self.metadata_manager.append_process_error(response.response_id, response.update_ids,)
            self.metadata_manager.mark_process_error_abandoned(response.response_id)
        
    async def _finalize_retryable_process_error(self, response: BotResponse):
        # Retryable, local attempts exhausted -- normal path, count vs threshold decides.
        async with self.__errors_files_lock__:
            response._processed_response_filepath = await self.storage_manager.process_errors.append(response)
        async with self.__user_lock__:
            self.metadata_manager.append_process_error(response.response_id, response.update_ids)
            to_abandon = self.metadata_manager.process_errors_counts.get(response.response_id, 0) > ACTIVE_ERRORS_THRESHOLD
            if to_abandon:
                self.metadata_manager.mark_process_error_abandoned(response.response_id)
                self._schedule_reconcile() ##abandoned error appended to error_files, we need to reconcile to skip abandoned from errors_files on disk
            else:
                self.queue_manager.append_process_error(response)
                self._ensure_retry_watchdog_running() ##active error, to retry in the future
        
       
    
    async def _send_response(self, response: BotResponse, max_retry: int):
        if response.send_status == SendStatus.SENT:
            return

        sending_finished, sending_attempts = False, 0
        starting_send_status = response.send_status
        while not sending_finished:
            try:
                if not response.to_skip or not self.__is_expiry_msg_sent__: ## is_expiry_msg_sent:bool -> only sending expired_msgs_error once
                    await self.sender.send(chat_id=response.chat_id, text=response.reply_text)
                    if response.to_skip:
                        self.__is_expiry_msg_sent__ = True

                response.mark_as_sent()
                response._sent_response_filepath = await self.storage_manager.sent_responses.append(
                    SentResponseFS(response_id=response.response_id, corresponding_processed_filepath=response._processed_response_filepath, sent_at=response.sent_at)
                )
                if starting_send_status == SendStatus.ERROR_SEND:
                    async with self.__errors_files_lock__:
                        await self.storage_manager.solved_send_errors.append(response.response_id)
                    self._schedule_reconcile()
                
                async with self.__user_lock__:
                    self.metadata_manager.append_send_success(response.response_id, send_response_filepath=response._sent_response_filepath)
                
                ##success-> we can restore backoff to default value
                ########### we can confidently retry to send previously failed responses
                self.error_manager.record_send_success()
                if self.queue_manager.any_send_error:
                    asyncio.create_task(self._retry_all_queued_errors(error_type=ErrorType.SEND)) 
                sending_finished = True

            except Exception as e:
                logger.exception(f'error sending response with id {response.response_id}' )
                classification = await self.error_manager.classify_send_error(e)

                if not classification.retryable:
                    # Non-retryable -- abandon immediately, this occurrence,
                    await self._finalize_non_retryable_send_error(response)
                    sending_finished = True
                    continue

                sending_attempts += 1
                if sending_attempts >= max_retry:
                    # Local retries exhausted. Increment metadata's count and let the threshold decide abandon/active.
                    await self._finalize_retryable_send_error(response)
                    sending_finished = True
                else:
                    await asyncio.sleep(classification.backoff_seconds)

        self._conversation_policy.update_context(processor=self, bot_response=response, role=conversation_manager.Role.ASSISTANT)
                            
    
    async def _finalize_non_retryable_send_error(self, response: BotResponse):
        response.mark_as_send_error()
        # Known permanently non-retryable (BadRequest, Forbidden, etc.) --
        # write straight to the abandoned file. No count check needed --
        async with self.__errors_files_lock__:
            response._sent_response_filepath = self.storage_manager.append_abandoned_send_error(
                SentResponseFS(response_id=response.response_id, corresponding_processed_filepath=response._processed_response_filepath, sent_at=dt.datetime.now(dt.UTC))
            )
        async with self.__user_lock__:
            self.metadata_manager.append_send_error(response.response_id)
            self.metadata_manager.mark_send_error_abandoned(response.response_id)

    async def _finalize_retryable_send_error(self, response: BotResponse):
        response.mark_as_send_error()
        # Retryable, local attempts exhausted -- normal path, count vs threshold decides.
        async with self.__errors_files_lock__:
            response._sent_response_filepath = await self.storage_manager.send_errors.append(
                SentResponseFS(response_id=response.response_id, corresponding_processed_filepath=response._processed_response_filepath, sent_at=dt.datetime.now(dt.UTC))
            )
        async with self.__user_lock__:
            self.metadata_manager.append_send_error(response.response_id)
            to_abandon = self.metadata_manager.send_errors_counts.get(response.response_id, 0) > ACTIVE_ERRORS_THRESHOLD
            if to_abandon:
                self.metadata_manager.mark_send_error_abandoned(response.response_id)
                self._schedule_reconcile() ##abandoned error appended to error_files, we need to reconcile to skip abandoned from errors_files on disk
            else:
                self.queue_manager.append_send_error(response)
                self._ensure_retry_watchdog_running() ##active error, to retry in the future 
    

    async def _get_reply_msg(self, response: BotResponse):
        #raise ValueError
        ## conv_context is built based on response status. 
        ## If error, assumes the context is not the last messages, thus it loads all the messages from disk 
        ## and builds the conversation with the closest previous msgs to this response 
        #return f'this is a fake reply to {response.text}'
        conv_context = await self._conversation_policy.get_context(processor=self, bot_response=response) 
        conversation_messages = [(str(self.user_id) if msg.role==conversation_manager.Role.USER else str(msg.role.value), msg.text) for msg in conv_context]
        user_id = self.user_id
        
        return await self.app_system.handle_message(user_id=user_id, message=response.text, past_conversation_messages=conversation_messages)
        

    async def _run_pending(self, runtime=None):
        await self.process_pending_responses()
        
        async with self.__user_lock__:
            should_start_batcher = (
                self.queue_manager.any_new_message and
                (self.__batcher_timeout_task__ is None or self.__batcher_timeout_task__.done())
            )
            if should_start_batcher:
                self.__batcher_timeout_task__ = asyncio.create_task(
                    self.batch_pending_messages(expiring_time_check=runtime)
                )
     
    async def checkpoint(self):    
        self.metadata_manager.checkpoint(replace_previous_checkpoint_error_files=True,
            msg_file_key=self.storage_manager.messages.shard_organizer._get_file_idx,
            processed_resp_file_key=self.storage_manager.processed_responses.shard_organizer._get_file_idx,
            sent_resp_file_key=self.storage_manager.sent_responses.shard_organizer._get_file_idx,
        )
        await self.storage_manager.write_checkpoint(self.metadata_manager.last_checkpoint, overwrite=True)
        
        
    async def shutdown(self):
        """
        Call this when the UserProcessor is being torn down (user session
        ended, app shutting down, etc.) so the reconcile task doesn't leak.
        """
        if self.__reconcile_task__ is not None:
            self.__reconcile_task__.cancel()
            try:
                await self.__reconcile_task__ ###wait till task (i.e. sleep_till_next_reconc+actually_reconcile) is actually stopped
            except asyncio.CancelledError:
                pass
                
        await asyncio.gather(self._reconcile_errors(),  self.checkpoint()) ##reconcile errors, store metadata_checkpoint before shutting down
            
            

    async def _retry_all_queued_errors(self, error_type: ErrorType):
        if error_type not in set(ErrorType):
            raise ValueError(f'Wrong error_type {error_type}')
        logger.info(f'retry started -- {dt.datetime.now().isoformat()}')
        async with self.__user_lock__:
            if error_type == ErrorType.PROCESS:
                all_errors = self.queue_manager.pop_all_process_errors()
            elif error_type == ErrorType.SEND:
                all_errors = self.queue_manager.pop_all_send_errors()
            else:
                timestamped_errors = (
                    [(resp, resp.sent_at) for resp in self.queue_manager.pop_all_send_errors()]
                    + [(resp, resp.replied_at) for resp in self.queue_manager.pop_all_process_errors()]
                )
                all_errors = [resp for resp, ts in sorted(timestamped_errors, key=lambda ts_err: ts_err[1])]
            for e in all_errors:
                self.queue_manager.append_response(e)

        if all_errors:
            asyncio.create_task(self.process_pending_responses())

        # Only place that stops the watchdog -- we just drained the error
        # queue (or it was already empty). If nothing's left, stop watching.
        if not self.queue_manager.any_error:
            if self.__retry_watchdog_task__ is not None and not self.__retry_watchdog_task__.done():
                self.__retry_watchdog_task__.cancel()
                self.__retry_watchdog_task__ = None
                
    def _ensure_retry_watchdog_running(self):
        """Called when a retryable error is first queued. Idempotent."""
        if self.__retry_watchdog_task__ is None or self.__retry_watchdog_task__.done():
            self.__retry_watchdog_task__ = asyncio.create_task(self._retry_watchdog())

    async def _retry_watchdog(self):
        """
        Runs until _retry_all_queued_errors stops it. Sleep interval is
        computed from the current resource backoff state at the moment each
        sleep begins -- simple, not dynamically updated mid-sleep, since:
        - success mid-sleep triggers _retry_all_queued_errors immediately anyway
        - new errors mid-sleep don't need to change the interval; the current
          backoff already reflects resource health correctly
        """
        try:
            while True:
                sleep_time = self._compute_retry_sleep_time()
                logger.info(f'\nretry scheduled to run at: {(dt.datetime.now()+dt.timedelta(seconds=sleep_time)).isoformat()}\n')
                await asyncio.sleep(sleep_time)
                asyncio.create_task(self._retry_all_queued_errors(error_type=ErrorType.ALL))
        except asyncio.CancelledError:
            raise

    def _compute_retry_sleep_time(self) -> float:
        candidates = [RETRY_CHECK_INTERVAL.total_seconds()]  # floor -- never retry faster than this
        if self.queue_manager.any_send_error:
            candidates.append(self.error_manager.telegram.current_backoff)
            candidates.append(self.error_manager.network.current_backoff)
        if self.queue_manager.any_process_error:
            candidates.append(self.error_manager.llm.current_backoff)
            candidates.append(self.error_manager.network.current_backoff)
        return max(candidates)
        
                    
    
    def _schedule_reconcile(self):
        if self.__reconcile_task__ is None or self.__reconcile_task__.done():
            self.__reconcile_task__ = asyncio.create_task(self._wait_and_reconcile())

    async def _wait_and_reconcile(self):
        try:
            logger.info(f'reconcile scheduled to run at: {(dt.datetime.now() + RECONCILE_ERRORS_TIME_WINDOW).isoformat()}\n')
            await asyncio.sleep(RECONCILE_ERRORS_TIME_WINDOW.total_seconds())
            await self._reconcile_errors()
        finally:
            self.__reconcile_task__ = None
        
    async def _reconcile_errors(self):
        from chat_system import recovery_utils
        try:
            logger.info('started reconciling')
            async with self.__errors_files_lock__:
                return await recovery_utils.read_and_reconcile_errors_on_disk(self.storage_manager, ACTIVE_ERRORS_THRESHOLD)
            logger.info('reconciliation on disk finished')
        except Exception:
            logger.exception("Error while reconciling errors")
        
     

    @staticmethod
    def _build_queue_manager(loaded_received_msgs: list[LoadedObject[ReceivedMessage]] = None, loaded_processed_responses: list[LoadedObject[BotResponse]] = None,
    active_process_errors: list[LoadedObject[BotResponse]] = None, active_send_errors: list[LoadedObject[SentResponseFS]] = None,
    last_handled_processing_update_id: int = -1, last_handled_sending_response_id: int = -1,
    expiring_time_check: dt.datetime = None) -> MessageQueueManager:
        
        from chat_system.messages_queue_manager import MessageQueueManager
        
        loaded_received_msgs = loaded_received_msgs or []
        loaded_processed_responses = loaded_processed_responses or []
        active_process_errors = active_process_errors or []
        active_send_errors = active_send_errors or []
        expiring_time_check = expiring_time_check or dt.datetime.now(dt.UTC)

        # Unprocessed msgs (update_id>last_handled_update_id)
        all_pending_msgs = [msg for loaded_msg in loaded_received_msgs if (msg:=loaded_msg.obj).update_id > last_handled_processing_update_id]
        non_expired_msgs = [msg for msg in all_pending_msgs if not is_expired(msg, expiring_time_check)]
        expired_msgs = [msg for msg in all_pending_msgs if is_expired(msg, expiring_time_check)]

        # Batched unprocessed responses (i.e. active process errors)
        ##expired_responses will be handled as skipped, without getting processed.
        non_expired_responses, expired_responses = [], []
        for loaded_resp in active_process_errors:
            resp = loaded_resp.obj
            resp._processed_response_filepath = loaded_resp.filepath
            if not is_expired(resp, expiring_time_check):
                non_expired_responses.append(resp)
            else:
                resp.mark_to_skip(reply_text=get_expired_message_to_send())
                expired_responses.append(resp)
        
        # Expired msgs -> appended as expired_responses to skip, no need to process them
        if expired_msgs:
            expired_as_responses = generate_batches(messages=expired_msgs,
                max_batch_timelapse=dt.timedelta(days=10000), max_message_length=10000000,
            )
            for resp in expired_as_responses:
                resp.mark_to_skip(reply_text=get_expired_message_to_send())
                expired_responses.append(resp)            

        # Unsent responses -> appended as non_expired
        active_send_errors_by_id = {loaded_resp.obj.response_id: loaded_resp.obj for loaded_resp in active_send_errors}

        for loaded_resp in loaded_processed_responses:
            r = loaded_resp.obj
            if r.response_id <= last_handled_sending_response_id and r.response_id not in active_send_errors_by_id:
                continue
            if r.response_id in active_send_errors_by_id:
                r.mark_as_send_error()
            r._processed_response_filepath = loaded_resp.filepath
            non_expired_responses.append(r)

        return MessageQueueManager(
            messages=sorted(non_expired_msgs, key=lambda x: x.update_id),
            pending_responses=sorted(expired_responses, key=lambda x: x.response_id) + sorted(non_expired_responses, key=lambda x: x.response_id)
        )
        
    @staticmethod
    def init_storage_manager(user_id, user_path):
        return UserStorageManager(path=user_path, user_id=user_id, unprocessed_dirpath=telegram_disk_utils._get_user_dir(user_id=user_id, dirtype=DiskDirType.USER_UNPROCESSED, base_dir=Path(user_path).parent))


"""
system_processor --- avrà services, opening hours, calendar... come variabili globali.
user_processor avrà reservations, e poi prenderà services/open_hours da system processor
ARRIVA MSG -> 
1.se non ci sono services/reservations/methods... li ottengo (run methods su backend), e li salvo in memoria per user
2.buildo prompt (message, conversation, services, reservations, etc)
3.invio prompt e prendo response da llm
4.trasformo llm response(str) in dict e in structured request
5.verifico che structured request sia valida -> altrimenti errore (syntax error? value error?)
6.verifico che method sia allowed -> altrimenti errore (not allowed error...)
7.verifico che params siano corretti e non missing values
7.aggiungo eventuali parametri nascosti prima (es user)
8.runno request con business_agent...
9.buildo response da 8.
10.aggiungo (request, response) a requests (solo se cambia system status? i.e. operazioni in "scrittura")
11.se cambio system status (i.e. services/reservations/hours), aggiorno variabili in memoria... (services e hours possono cambiare solo per system/admin. reservations per tutti)
12.buildo prompt per rispondere a user... runno prompt
13.invio risposta a user
"""
        
        
        
def generate_batches(messages: list[ReceivedMessage], max_batch_timelapse: dt.timedelta = MAX_BATCH_TIMELAPSE, max_message_length: int = MAX_BATCH_TEXT_LENGTH, msg_separator: str = '\n'):
    """ 
    NOTE: Assumes messages are all related to a single user_id/chat_id. No internal validation is performed.
    chat_messages must be a collection of ReceivedMessage.
    Will batch all the consecutive chat_messages into a BotResponse.
    A single BotResponse will contain text with len<=max_message_length and written within max_batch_timelapse (i.e. last_msg-first_msg <= max_batch_timelapse).
    Returns list[BotResponse], each being an "unprocessed" response -> contains the batched text, the update_ids of the single messages, and statuses=UNPROCESSED + UNSENT.
    """    
    
    def _init_botresponse_from_batch(batch: ChatMessagesBatch, chat_id, user_id) -> BotResponse:
        return BotResponse(update_ids = batch.update_ids, 
                           text = batch.text,
                           reply_text = None,
                           chat_id = chat_id, 
                           user_id = user_id, 
                           process_status = ProcessStatus.UNPROCESSED, 
                           send_status = SendStatus.UNSENT,
                           created_at = dt.datetime.now(dt.UTC).isoformat(),
                           replied_at = None, 
                           sent_at = None,
                           last_msg_ts = batch.last_msg_ts)
    
    
    if not messages:
        return []
    
    messages = sorted(messages, key=lambda x: x.sent_at)
    
    batched_responses = []
    
    curr_msg = messages[0]
    user_id = curr_msg.user_id
    chat_id = curr_msg.chat_id
    
    curr_batch = ChatMessagesBatch(curr_msg.text, curr_msg.sent_at, curr_msg.update_id, msg_separator=msg_separator)
    for curr_msg in messages[1:]:  
        
        """if curr_batch is None:
            last_msg_ts = curr_msg.sent_at
            curr_batch = ChatMessagesBatch(text=curr_msg.text, timestamp=curr_msg.sent_at, msg_separator=msg_separator)
            curr_batch.update_ids = [curr_msg.update_id]
        """
        if ( 
        ( len(curr_batch.text)+len(msg_separator)+len(curr_msg.text) <= max_message_length ) and 
        ( curr_msg.sent_at-curr_batch.first_msg_ts <= max_batch_timelapse )
        ):
            curr_batch._enqueue_following_message(curr_msg.text, curr_msg.sent_at, curr_msg.update_id)
        else:
            unprocessed_response = _init_botresponse_from_batch(batch=curr_batch, chat_id=chat_id, user_id=user_id)
            batched_responses.append(unprocessed_response)
            
            curr_batch = ChatMessagesBatch(curr_msg.text, curr_msg.sent_at, curr_msg.update_id, msg_separator=msg_separator)
    
    unprocessed_response = _init_botresponse_from_batch(batch=curr_batch, chat_id=chat_id, user_id=user_id)
    batched_responses.append(unprocessed_response)
    return batched_responses
    

    
def _format_timedelta_humanized_(td: dt.timedelta) -> str:
    """Converte timedelta in stringa leggibile."""
    total_seconds = int(td.total_seconds())
    
    if total_seconds < 60:
        return f"{total_seconds} secondi"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} minut{'o' if minutes == 1 else 'i'}"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours} or{'a' if hours == 1 else 'e'}"
    else:
        days = total_seconds // 86400
        return f"{days} giorn{'o' if days == 1 else 'i'}"

def get_expired_message_to_send(expiry_time: dt.timedelta = MESSAGE_EXPIRY_WINDOW):
    return f"Alcuni messaggi che hai inviato in questa chat sono più vecchi di {_format_timedelta_humanized_(expiry_time)} e non hanno ricevuto alcuna risposta dal sistema. Verranno ignorati dal bot. Se hai ancora bisogno di assistenza, invia un nuovo messaggio."
    
def is_expired(obj, check_time: dt.datetime, max_duration_time: dt.timedelta = MESSAGE_EXPIRY_WINDOW):
    if isinstance(obj, ReceivedMessage):
        ts_attr = 'sent_at'
    if isinstance(obj, BotResponse):
        ts_attr = 'last_msg_ts'
    return check_time - getattr(obj, ts_attr) > max_duration_time
    

    
from enum import Enum
class ErrorType(Enum):
    PROCESS = 'process'
    SEND = 'send'
    ALL = 'all'
    
    
