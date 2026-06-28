from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from chat_system.message_responses import normalize_id


@dataclass(frozen=True)
class LoadedErrorOccurrence:
    loaded_obj: LoadedObject
    count: int

    def __post_init__(self):
        from chat_system.user_storage_manager import LoadedObject
        if not isinstance(self.loaded_obj, LoadedObject):
            raise ValueError('loaded must be a LoadedObject instance')
        if not isinstance(self.count, int) or self.count < 1:
            raise ValueError('count must be a positive integer')


class ErrorState:
    def __init__(self, raw_errors: list[LoadedObject], raw_solved: list[LoadedObject], active_count_threshold: int):
        if not isinstance(active_count_threshold, int) or active_count_threshold < 1:
            raise ValueError('active_count_threshold must be a positive integer')

        solved_ids = frozenset(normalize_id(s.obj) for s in raw_solved)
        counts, first_occurrence = _count_error_occurrences(raw_errors)

        unsolved = tuple(
            LoadedErrorOccurrence(loaded_obj=first_occurrence[rid], count=n)
            for rid, n in counts.items()
            if rid not in solved_ids
        )

        object.__setattr__(self, 'active', tuple(e for e in unsolved if e.count <= active_count_threshold))  #tuple[LoadedErrorOccurrence]
        object.__setattr__(self, 'abandoned', tuple(e for e in unsolved if e.count > active_count_threshold)) #tuple[LoadedErrorOccurrence]
        object.__setattr__(self, '_solved_ids', solved_ids)
        object.__setattr__(self, 'active_count_threshold', active_count_threshold)
        object.__setattr__(self, 'needs_reconciliation', bool(self.abandoned) or bool(solved_ids))
        object.__setattr__(self, 'solved_todel', bool(solved_ids))

    def __setattr__(self, name, value):
        raise AttributeError(f'ErrorState is immutable. Cannot set {name!r}.')

    @property
    def all_unsolved(self) -> tuple[LoadedErrorOccurrence, ...]:
        return self.active + self.abandoned


def _count_error_occurrences(raw_errors: list[LoadedObject]) -> tuple[dict[int, int], dict[int, LoadedObject]]:
    counts: dict[int, int] = {}
    first_occurrence: dict[int, LoadedObject] = {}
    for loaded in raw_errors:
        resp_id = loaded.obj.response_id
        counts[resp_id] = counts.get(resp_id, 0) + 1
        if resp_id not in first_occurrence:
            first_occurrence[resp_id] = loaded
    return counts, first_occurrence
    
    
    
async def _load_errors_state(storage_manager: UserStorageManager, active_count_threshold: int) -> tuple[ErrorState, ErrorState]:
    """Returns (process_errors: ErrorState, send_errors: ErrorState)"""
    import asyncio
    
    raw_process, raw_solved_process, raw_send, raw_solved_send = await asyncio.gather(
        storage_manager.process_errors.read_all(),
        storage_manager.solved_process_errors.read_all(),
        storage_manager.send_errors.read_all(),
        storage_manager.solved_send_errors.read_all(),
    )
    return (
        ErrorState(raw_process, raw_solved_process, active_count_threshold),
        ErrorState(raw_send, raw_solved_send, active_count_threshold),
    )
    
    
async def read_and_reconcile_errors_on_disk(storage_manager: UserStorageManager, active_count_threshold: int) -> tuple[ErrorState, ErrorState]:
    process_errors, send_errors = await _load_errors_state(storage_manager=storage_manager, active_count_threshold=active_count_threshold,
    )
            
    pending_tasks = []
    if process_errors.needs_reconciliation:
        active_errors_lists = [[loaded_err.loaded_obj.obj]*loaded_err.count for loaded_err in process_errors.active]
        await storage_manager.overwrite_process_errors(
                active_errors=[obj for obj_lst in active_errors_lists for obj in obj_lst],
                abandoned_errors=[loaded_err.loaded_obj.obj for loaded_err in process_errors.abandoned],
            )
        
    if send_errors.needs_reconciliation:
        active_errors_lists = [[loaded_err.loaded_obj.obj]*loaded_err.count for loaded_err in send_errors.active]
        await storage_manager.overwrite_send_errors(
                active_errors=[obj for obj_lst in active_errors_lists for obj in obj_lst],
                abandoned_errors=[loaded_err.loaded_obj.obj for loaded_err in send_errors.abandoned],
            )

    for task in pending_tasks:
        asyncio.create_task(task)

    return process_errors, send_errors
    
    
def resolve_files_to_load(storage_manager: UserStorageManager, checkpoint: RecoveryCheckpoint) -> tuple[list[Path], list[Path], list[Path]]:
    """Returns (msg_files, resp_files, sent_files)"""
    msg_files_containing_candidates_unseen = list(storage_manager.messages.shard_organizer.get_files_after(checkpoint.last_messages_file) if checkpoint.last_messages_file else storage_manager.messages.shard_organizer.files)
    msg_files_to_read = list(checkpoint.files_containing_unprocessed_msgs_errors) + msg_files_containing_candidates_unseen
    
    resp_files_containing_candidates_unseen = list(storage_manager.processed_responses.shard_organizer.get_files_after(checkpoint.last_processed_responses_file) if checkpoint.last_processed_responses_file else storage_manager.processed_responses.shard_organizer.files)
    resp_files_to_read = list(checkpoint.files_containing_unsent_responses_errors) + resp_files_containing_candidates_unseen
    
    sent_files_to_read = storage_manager.sent_responses.shard_organizer.get_files_after(checkpoint.last_sent_responses_file) if checkpoint.last_sent_responses_file else storage_manager.sent_responses.shard_organizer.files
    return msg_files_to_read, resp_files_to_read, sent_files_to_read


async def load_processed_responses_from_send_error_refs(send_error_refs: list[SentResponseFS], storage_manager: UserStorageManager,) -> list[LoadedObject]:
    """
    Each send-error reference already carries the exact file its processed
    response lives in (corresponding_processed_filepath) -- this is a direct,
    targeted read per referenced file, filtered to the ids wanted from
    each file.
    """
    if not send_error_refs:
        return []

    send_error_refs = {i.response_id: i for i in send_error_refs} ## {resp_id: SentResponseFS}
    files_to_scan = set(send_ref.corresponding_processed_filepath for send_ref in send_error_refs.values())

    loaded_processed_responses: list[LoadedObject] = []
    loaded_batches = await asyncio.gather(
        *[storage_manager.processed_responses.read_files([f]) for f in files_to_scan]
    )
    
    for loaded_batch in loaded_batches:
        loaded_processed_responses.extend(loaded_resp for loaded_resp in loaded_batch if loaded_resp.obj.response_id in send_error_refs)
    
    missing = set(send_error_refs.keys()) - set(r.obj.response_id for r in loaded_processed_responses)
    if missing:
        warnings.warn(f'Could not load processed_responses for send_error ids: {missing}.', RuntimeWarning)
    return loaded_processed_responses
    
    
async def recover_missing_send_error_responses(missing_ids: set[int], storage_manager: UserStorageManager, previous_checkpoint: RecoveryCheckpoint, already_loaded_files: set[Path] = None,
) -> list[LoadedObject[BotResponse]]:
    """
    Fallback: recupera processed_responses per response_ids presenti in active_send_errors
    ma non coperti dai file già caricati. Accade solo se il checkpoint è incompleto/manomesso.
    """
    import asyncio
    
    already_loaded_files = already_loaded_files or set()
    all_resp_files = storage_manager.processed_responses.shard_organizer.files
    files_to_scan = [
        f for f in all_resp_files
        if f not in already_loaded_files
    ]
    if not files_to_scan:
        return []

    recovered_responses: list[LoadedObject[BotResponse]] = []
    remaining = set(missing_ids)

    for loaded_batch in await asyncio.gather(
        *[storage_manager.processed_responses.read_files([f]) for f in files_to_scan]
    ):
        for loaded_resp in loaded_batch:
            if loaded_resp.obj.response_id in remaining:
                recovered_responses.append(loaded_resp)
                remaining.discard(loaded_resp.obj.response_id)
        if not remaining:
            break

    if remaining:
        # Should never happen
        import warnings
        warnings.warn(
            f'Could not recover processed_responses for send_error ids: {remaining}. '
            'These will be skipped.',
            RuntimeWarning,
        )

    return recovered_responses
    
    
    
def build_conversation_manager_from_disk_responses(conv_manager: ConversationManager, loaded_responses: list[LoadedObject[BotResponse]], sent_ids_mapping: dict[int, SentResponseFS],):
    from chat_system.conversation_policy import ConversationRules
    from chat_system.conversation_manager import ConversationMessage, Role
    for loaded_resp in loaded_responses:
        br = loaded_resp.obj
        if br.response_id in sent_ids_mapping:
            br.mark_as_replied(getattr(br, 'reply_text', None))
            br.mark_as_sent(timestamp=sent_ids_mapping[br.response_id].sent_at)
        
        if ConversationRules.should_include_user_message(br):
            conv_manager.insert(ConversationMessage(role=Role.USER, text=br.text, timestamp=br.last_msg_ts,) )
        
        if ConversationRules.should_include_assistant_message(br):
            conv_manager.insert(ConversationMessage(
                role=Role.ASSISTANT,
                text=br.reply_text,
                timestamp=br.sent_at,
            ))
            
    return conv_manager