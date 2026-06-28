from __future__ import annotations
import datetime as dt
from chat_system.message_responses import ProcessStatus, SendStatus, normalize_id
from pathlib import Path

class RecoveryCheckpoint:
    def __init__(self, last_handled_processing_update_id: int = -1, last_handled_sending_response_id: int = -1, last_messages_file: Path = None, last_processed_responses_file: Path = None, last_sent_responses_file: Path = None, files_containing_unprocessed_msgs_errors: set[Path] = None, files_containing_unsent_responses_errors: set[Path] = None, **kwargs):
        """
        @filepath : Path of the current RecoveryCheckpoint on disk
        @last_handled_processing_update_id: last checkpointed update_id processed by processor method (i.e. which is part of a BotResponse and has either a response generated, or a related error when the response generation was run)
        @last_handled_sending_response_id: last checkpointed response_id processed and sent by processor method (i.e. which has either been successfully sent, or has a related error when the sending was run)
        @last_messages_file: first msgs_files containing (potential) update_ids>last_handled_processing_update_id
        @last_processed_responses_file: first responses_files containing (potential) response_ids>last_handled_sending_response_id
        @files_containing_unprocessed_msgs_errors: msgs_files containing (potential) update_ids in unprocessed_error_update_ids
        @files_containing_unprocessed_msgs_errors: responses_files containing (potential) response_ids in unsent_error_response_ids
        """
        files_containing_unprocessed_msgs_errors = files_containing_unprocessed_msgs_errors or set()
        files_containing_unsent_responses_errors = files_containing_unsent_responses_errors or set()
        
        self.last_handled_processing_update_id = RecoveryCheckpoint.validate_attribute("last_handled_processing_update_id", last_handled_processing_update_id)
        self.last_handled_sending_response_id = RecoveryCheckpoint.validate_attribute("last_handled_sending_response_id", last_handled_sending_response_id)
        self.last_messages_file = RecoveryCheckpoint.validate_attribute("last_messages_file", last_messages_file)
        self.last_processed_responses_file = RecoveryCheckpoint.validate_attribute("last_processed_responses_file", last_processed_responses_file)
        self.last_sent_responses_file = RecoveryCheckpoint.validate_attribute("last_sent_responses_file", last_sent_responses_file)
        self.files_containing_unprocessed_msgs_errors = RecoveryCheckpoint.validate_attribute("files_containing_unprocessed_msgs_errors", files_containing_unprocessed_msgs_errors)
        self.files_containing_unsent_responses_errors = RecoveryCheckpoint.validate_attribute("files_containing_unsent_responses_errors", files_containing_unsent_responses_errors)
        self.last_checkpoint = kwargs.get('last_checkpoint', dt.datetime.now(dt.UTC))
        
    def update(self, last_handled_processing_update_id: int = None, last_handled_sending_response_id: int = None, last_messages_file: Path = None, last_processed_responses_file: Path = None, last_sent_responses_file: Path = None, files_containing_unprocessed_msgs_errors: set[Path] = None, files_containing_unsent_responses_errors: set[Path] = None):
        for attr_name in ["last_messages_file", "last_processed_responses_file", "last_sent_responses_file", "last_handled_processing_update_id", "last_handled_sending_response_id", "files_containing_unprocessed_msgs_errors", "files_containing_unsent_responses_errors"]:
            if (attr_value:=eval(attr_name)) is not None:
                setattr(self, attr_name, RecoveryCheckpoint.validate_attribute(attr_name, attr_value))
        self.last_checkpoint = dt.datetime.now(dt.UTC)
        
    def copy(self):
        return RecoveryCheckpoint(**self.__dict__)
            
    @staticmethod
    def validate_attribute(attr_name, attr_value):
        if attr_name  in ['last_messages_file', 'last_processed_responses_file', 'last_sent_responses_file']:
            if not bool(attr_value):
                return None
            if not isinstance(attr_value, (str, Path)):
                raise ValueError(f'{attr_name} must be a Path object')
            return Path(attr_value)
        if attr_name in ['last_handled_processing_update_id', 'last_handled_sending_response_id']:
            if isinstance(attr_value, float) and not attr_value.is_integer() or isinstance(attr_value, bool):
                raise ValueError(f"{attr_name} must be an integer.")
            if isinstance(attr_value, str):
                return RecoveryCheckpoint.validate_attribute(attr_name, float(attr_value))
            return int(attr_value)        
        if attr_name in ["files_containing_unprocessed_msgs_errors", "files_containing_unsent_responses_errors"]:
            if not isinstance(attr_value, (set, list)):
                attr_value = [attr_value]
            return list(set([Path(f) for f in attr_value]))
        return attr_value    
    
    
    
    
class MsgResponsesFileMapping:
    
    def __init__(self, updates_files_mapping : dict[int, Path] = None, responses_files_mapping: dict[int, tuple[Path, Path]] = None):
        self.updates_files_mapping = updates_files_mapping or dict() #update_id -> msg_filepath
        
        if responses_files_mapping is None:
            self.processed_responses_files_mapping = dict() #response_id -> (processed_fp, sent_fp)
            self.send_responses_files_mapping = dict() #response_id -> (processed_fp, sent_fp)
        else:
            self.processed_responses_files_mapping = {resp_id: fm[0] for resp_id, fm in responses_files_mapping.items()}
            self.send_responses_files_mapping = {resp_id: fm[1] for resp_id, fm in responses_files_mapping.items()}
    
    def upsert_msg_file_mapping(self, update_id: int, message_filepath: Path):
        self.updates_files_mapping[update_id] = MsgResponsesFileMapping.validate_filepath(message_filepath)
        
    def upsert_response_file_mapping(self, response_id: int, processed_response_filepath: Path, send_response_filepath: Path):
        self.upsert_processed_file_mapping(response_id, processed_response_filepath)
        self.upsert_send_file_mapping(response_id, send_response_filepath)
        
    def upsert_processed_file_mapping(self, response_id: int, processed_response_filepath: Path):
       self.processed_responses_files_mapping[response_id] = MsgResponsesFileMapping.validate_filepath(processed_response_filepath)
    
    def upsert_send_file_mapping(self, response_id: int, send_response_filepath: Path):
        self.send_responses_files_mapping[response_id] = MsgResponsesFileMapping.validate_filepath(send_response_filepath)
        
    def get_msg_file_mapping(self, update_id: int):
        return self.updates_files_mapping.get(update_id, None)
        
    def get_response_file_mapping(self, response_id: int):
        return (self.get_response_processed_file(response_id), self.get_response_send_file(response_id))
        
    def get_response_processed_file(self, response_id: int):
        return self.processed_responses_files_mapping.get(response_id, None)
        
    def get_response_send_file(self, response_id: int):
        return self.send_responses_files_mapping.get(response_id, None)
        
    @staticmethod
    def validate_filepath(filepath):
        return Path(filepath).absolute()
        
    
    
    
class RuntimeMetadataManager:
    def __init__(self, last_checkpoint: RecoveryCheckpoint = None, last_process_error_ts: dt.datetime=None, last_send_error_ts: dt.datetime=None):
        self.last_checkpoint = last_checkpoint or RecoveryCheckpoint()
        self.files_mapping = MsgResponsesFileMapping()
        self.current_run_received_msgs: list[int] = list()
        self.current_run_process_successes: list[int] = list() #list[response_id]
        self.current_run_process_errors: list[int] = list() #list[response_id]
        self.current_run_send_successes: list[int] = list() #list[response_id]
        self.current_run_send_errors: list[int] = list() #list[response_id]
        self.process_errors_counts : dict[int, int] = {} # response_id -> count
        self.send_errors_counts: dict[int, int] = {} # response_id -> count
        self._abandoned_response_ids: set[int] = set()
        self._responses_update_ids: dict[int, list[int]] = {}
        self._prev_runs_counters: dict[str, int] = {'process_successes':0, 'process_errors': 0, 'send_successes':0, 'send_errors': 0} #
    
    @property
    def any_error(self) -> bool:
        return bool(self.process_errors_counts) or bool(self.send_errors_counts)
        
    def append_received_message(self, update_id: int, message_filepath: Path):    
        self.files_mapping.upsert_msg_file_mapping(update_id, message_filepath)
        self.current_run_received_msgs.append(update_id)
        
    def append_process_success(self, response_id: int, inner_update_ids: list[int], processed_response_filepath: Path):
        self.files_mapping.upsert_processed_file_mapping(response_id, processed_response_filepath)
        self._responses_update_ids[response_id] = list(inner_update_ids)
        self.current_run_process_successes.append(response_id)
        self.process_errors_counts.pop(response_id, None) ##removing from process_errors, if response_id was a previous error
        
    def append_process_error(self, response_id: int, inner_update_ids: list[int]):
        self._responses_update_ids[response_id] = list(inner_update_ids)
        self.current_run_process_errors.append(response_id)
        self.process_errors_counts[response_id] = self.process_errors_counts.get(response_id, 0)+1 ##increasing response_id' counter to process_errors_counts
        
    def append_send_success(self, response_id: int, send_response_filepath: Path, processed_response_filepath: Path = None):
        if processed_response_filepath is not None:
            if (existing_fp:=self.files_mapping.get_response_processed_file(response_id)) and existing_fp!=MsgResponsesFileMapping.validate_filepath(processed_response_filepath):
                raise ValueError(f'processed_response_filepath {processed_response_filepath} is different from the already known one {existing_fp}... Response_id: {response_id}')
            self.files_mapping.upsert_response_file_mapping(response_id, processed_response_filepath=processed_response_filepath, send_response_filepath=send_response_filepath)
        else:
            self.files_mapping.upsert_send_file_mapping(response_id, send_response_filepath)
        
        self.current_run_send_successes.append(response_id)
        self.send_errors_counts.pop(response_id, None)
            
    def append_send_error(self, response_id: int, processed_response_filepath: Path = None):
        if processed_response_filepath is not None:
            if (existing_fp:=self.files_mapping.get_response_processed_file(response_id)) and existing_fp!=MsgResponsesFileMapping.validate_filepath(processed_response_filepath):
                raise ValueError(f'processed_response_filepath {processed_response_filepath} is different from the already known one {existing_fp}... Response_id: {response_id}')
            self.files_mapping.upsert_processed_file_mapping(response_id, processed_response_filepath=processed_response_filepath)
        
        self.current_run_send_errors.append(response_id)
        self.send_errors_counts[response_id] = self.send_errors_counts.get(response_id, 0)+1       
        
    def mark_process_error_abandoned(self, response_id: int):
        """
        Called by reconcile once it has confirmed (via the disk-side
        overwrite) that this response_id's process error has been moved to
        the abandoned file. Removes it from active tracking immediately
        (so any_error / retry logic stop seeing it right away), but keeps
        files_mapping / _responses_update_ids intact until the next
        checkpoint -- _clear() will fold this id into resp_ids_to_forget
        and tear those down at that point, after last_* has had a chance
        to account for it.
        """
        self.process_errors_counts.pop(response_id, None)
        self._abandoned_response_ids.add(response_id)

    def mark_send_error_abandoned(self, response_id: int):
        """Same as mark_process_error_abandoned, for the send-error case."""
        self.send_errors_counts.pop(response_id, None)
        self._abandoned_response_ids.add(response_id)    
    
    
    def get_n_process_errors(self, from_checkpoint_only: bool = True):
        total_errors = len(self.current_run_process_errors)
        if not from_checkpoint_only:
            total_errors+=self._prev_runs_counters['process_errors']
        return total_errors
                
    def get_n_process_successes(self, from_checkpoint_only: bool = True):
        total_successes = len(self.current_run_process_successes)
        if not from_checkpoint_only:
            total_successes+=self._prev_runs_counters['process_successes']
        return total_successes
        
    def get_n_send_errors(self, from_checkpoint_only: bool = True):
        total_errors = len(self.current_run_send_errors)
        if not from_checkpoint_only:
            total_errors+=self._prev_runs_counters['send_errors']
        return total_errors
        
    def get_n_send_successes(self, from_checkpoint_only: bool = True):
        total_successes = len(self.current_run_send_successes)
        if not from_checkpoint_only:
            total_successes+=self._prev_runs_counters['send_successes']
        return total_successes
        
    def get_n_processed_responses(self, from_checkpoint_only: bool = True):
        n_processed = self.get_n_process_errors(from_checkpoint_only) + self.get_n_process_successes(from_checkpoint_only)
        return n_processed
        
    def get_n_sent_responses(self, from_checkpoint_only: bool = True):
        n_sent = self.get_n_send_errors(from_checkpoint_only) + self.get_n_send_successes(from_checkpoint_only)
        return n_sent
        
    def get_n_unique_responses_handled_from_checkpoint(self):
        return len ( 
                    set(self.current_run_process_errors + self.current_run_process_successes + \
                        self.current_run_send_successes + self.current_run_send_errors)
        )
            
    def get_handled_process_responses_ids(self):
        return set(self.current_run_process_successes+self.current_run_process_errors)
        
    def get_process_success_responses_ids(self):
        return set(self.current_run_process_successes)
    
    def get_process_errors_responses_ids(self):
        return set(self.current_run_process_errors)
        
    def get_handled_send_responses_ids(self):
        return set(self.current_run_send_successes+self.current_run_send_errors)
        
    def get_send_success_responses_ids(self):
        return set(self.current_run_send_successes)
    
    def get_send_errors_responses_ids(self):
        return set(self.current_run_send_errors)
    
    def checkpoint(self, msg_file_key: Callable[[Path], Any], processed_resp_file_key: Callable[[Path], Any], 
                   sent_resp_file_key: Callable[[Path], Any], replace_previous_checkpoint_error_files: bool,
        ):
        
        new_checkpoint = update_checkpoint_from_runtime_data(
            previous_checkpoint=self.last_checkpoint, runtime_metadata=self, 
            msg_file_key=msg_file_key, processed_resp_file_key=processed_resp_file_key, 
            sent_resp_file_key=sent_resp_file_key, replace_previous_checkpoint_error_files=replace_previous_checkpoint_error_files,
        )
        self.last_checkpoint = new_checkpoint
        self._clear(clear_all=False)

    def _clear_msg_references(self, update_id):
        self.files_mapping.updates_files_mapping.pop(update_id, None)
        
    def _clear_response_references(self, response_id):
        self.process_errors_counts.pop(response_id, None)
        self.send_errors_counts.pop(response_id, None)
        self.files_mapping.processed_responses_files_mapping.pop(response_id, None)
        self.files_mapping.send_responses_files_mapping.pop(response_id, None)
        inner_update_ids = self._responses_update_ids.pop(response_id, None)
        for upd_id in inner_update_ids:
            self._clear_msg_references(upd_id)
     

    def _clear(self, clear_all: bool = False):
        if clear_all:
            self.files_mapping = MsgResponsesFileMapping()
            self._responses_update_ids = {}
            self.process_errors_counts = {}
            self.send_errors_counts = {}
            self._abandoned_response_ids = set()   # NEW
        else:
            resp_ids_to_forget = self.get_send_success_responses_ids() | self._abandoned_response_ids   # CHANGED
            for resp_id in resp_ids_to_forget:
                self._clear_response_references(resp_id)
            self._abandoned_response_ids.clear()   # NEW

        self._prev_runs_counters['process_successes']+=self.get_n_process_successes(from_checkpoint_only=True)
        self._prev_runs_counters['process_errors']+=self.get_n_process_errors(from_checkpoint_only=True)
        self._prev_runs_counters['send_successes']+=self.get_n_send_successes(from_checkpoint_only=True)
        self._prev_runs_counters['send_errors']+=self.get_n_send_errors(from_checkpoint_only=True)

        self.current_run_process_successes = []
        self.current_run_process_errors = []
        self.current_run_send_successes = []
        self.current_run_send_errors = []
        self.current_run_received_msgs = []
        
        
        

    @classmethod
    def from_loaded_data(cls, received_messages: list[LoadedObject[ReceivedMessage]],
        processed_responses: list[LoadedObject[BotResponse]], 
        sent_responses: list[LoadedObject[SentResponseFS]],
        active_process_errors: list[LoadedObject[BotResponse]],
        abandoned_process_errors: list[LoadedObject[BotResponse]],
        active_send_errors: list[LoadedObject[SentResponseFS]],
        abandoned_send_errors: list[LoadedObject[SentResponseFS]],
        previous_checkpoint: RecoveryCheckpoint=None):
            
        previous_checkpoint = previous_checkpoint or RecoveryCheckpoint()
        cls = RuntimeMetadataManager(previous_checkpoint)
        for loaded_msg in received_messages:
            cls.append_received_message(loaded_msg.obj.update_id, loaded_msg.filepath)
        for loaded_resp in processed_responses:
            cls.append_process_success(loaded_resp.obj.response_id, inner_update_ids=loaded_resp.obj.update_ids, processed_response_filepath=loaded_resp.filepath)
        for loaded_resp in sent_responses:
            cls.append_send_success(loaded_resp.obj.response_id, send_response_filepath=loaded_resp.filepath, processed_response_filepath=loaded_resp.obj.corresponding_processed_filepath)
        for loaded_err in active_process_errors:
            cls.append_process_error(loaded_err.obj.response_id, inner_update_ids=loaded_err.obj.update_ids, )
        for loaded_err in abandoned_process_errors:
            resp_id = loaded_err.obj.response_id
            cls.append_process_error(resp_id, inner_update_ids=loaded_err.obj.update_ids, )
            cls.mark_process_error_abandoned(resp_id)
        for loaded_err in active_send_errors:
            cls.append_send_error(loaded_err.obj.response_id, processed_response_filepath=loaded_err.obj.corresponding_processed_filepath)
        for loaded_err in abandoned_send_errors:
            resp_id = loaded_err.obj.response_id
            cls.append_send_error(resp_id, processed_response_filepath=loaded_err.obj.corresponding_processed_filepath)
            cls.mark_send_error_abandoned(resp_id)
        return cls

def update_checkpoint_from_runtime_data(previous_checkpoint: RecoveryCheckpoint, runtime_metadata: RuntimeMetadataManager,
    msg_file_key: Callable[[Path], Any], processed_resp_file_key: Callable[[Path], Any], 
    sent_resp_file_key: Callable[[Path], Any], discard_previous_errors: bool = False) -> RecoveryCheckpoint:
    
    handled_processing_update_ids = set(upd_id for resp_id in runtime_metadata.get_handled_process_responses_ids() for upd_id in runtime_metadata._responses_update_ids[resp_id])
    last_handled_processing_update_id = max(handled_processing_update_ids.union([previous_checkpoint.last_handled_processing_update_id]) )
    handled_sending_response_ids = runtime_metadata.get_handled_send_responses_ids()
    last_handled_sending_response_id = max(handled_sending_response_ids.union([previous_checkpoint.last_handled_sending_response_id]) )
    
    handled_updatesids_files = set(runtime_metadata.files_mapping.updates_files_mapping[upd_id] for upd_id in handled_processing_update_ids)
    last_messages_file = max(handled_updatesids_files.union([previous_checkpoint.last_messages_file] if previous_checkpoint.last_messages_file else []), key=msg_file_key, default=None)
    
    handled_processed_responses_files = set(runtime_metadata.files_mapping.get_response_processed_file(resp_id) for resp_id in runtime_metadata.get_process_success_responses_ids())
    last_processed_responses_file = max(handled_processed_responses_files.union([previous_checkpoint.last_processed_responses_file] if previous_checkpoint.last_processed_responses_file else []), key=processed_resp_file_key, default=None)
    
    handled_sent_responses_files = set(runtime_metadata.files_mapping.get_response_send_file(resp_id) for resp_id in runtime_metadata.get_send_success_responses_ids())
    last_sent_responses_file = max(handled_sent_responses_files.union([previous_checkpoint.last_sent_responses_file] if previous_checkpoint.last_sent_responses_file else []), key=sent_resp_file_key, default=None)
    
    files_containing_unprocessed_msgs_errors = set()
    """
    TO INCLUDE IF WE WANT TO RECOVER RAW MESSAGES FROM PROCESS ERRORS
    existing_files_containing_unprocessed_msgs_errors = set(previous_checkpoint.files_containing_unprocessed_msgs_errors) if not discard_previous_errors else set()
    curr_files_containing_unprocessed_msgs_errors = set(runtime_metadata.updates_files_mapping[upd_id] for resp in runtime_metadata.process_errors for upd_id in resp.update_ids)
    files_containing_unprocessed_msgs_errors = existing_files_containing_unprocessed_msgs_errors.union(curr_files_containing_unprocessed_msgs_errors)
    """
    
    existing_files_containing_unsent_responses_errors = set(previous_checkpoint.files_containing_unsent_responses_errors) if not discard_previous_errors else set()
    curr_files_containing_unsent_responses_errors = set(runtime_metadata.files_mapping.get_response_processed_file(resp_id) for resp_id in runtime_metadata.get_send_errors_responses_ids())
    files_containing_unsent_responses_errors = existing_files_containing_unsent_responses_errors.union(curr_files_containing_unsent_responses_errors)
    
    new_checkpoint = RecoveryCheckpoint(last_handled_processing_update_id = last_handled_processing_update_id, \
                last_handled_sending_response_id = last_handled_sending_response_id, \
                last_messages_file = last_messages_file, \
                last_processed_responses_file = last_processed_responses_file, \
                last_sent_responses_file = last_sent_responses_file, \
                files_containing_unprocessed_msgs_errors = files_containing_unprocessed_msgs_errors, \
                files_containing_unsent_responses_errors = files_containing_unsent_responses_errors \
    )
    return new_checkpoint
    
    

def update_checkpoint_from_runtime_data(previous_checkpoint: RecoveryCheckpoint, runtime_metadata: RuntimeMetadataManager,
    msg_file_key: Callable[[Path], Any], processed_resp_file_key: Callable[[Path], Any],
    sent_resp_file_key: Callable[[Path], Any], replace_previous_checkpoint_error_files: bool = False) -> RecoveryCheckpoint:

    handled_processing_update_ids = set(upd_id for resp_id in runtime_metadata.get_handled_process_responses_ids() for upd_id in runtime_metadata._responses_update_ids[resp_id])
    last_handled_processing_update_id = max(handled_processing_update_ids.union([previous_checkpoint.last_handled_processing_update_id]))
    handled_sending_response_ids = runtime_metadata.get_handled_send_responses_ids()
    last_handled_sending_response_id = max(handled_sending_response_ids.union([previous_checkpoint.last_handled_sending_response_id]))

    handled_updatesids_files = set(runtime_metadata.files_mapping.updates_files_mapping[upd_id] for upd_id in handled_processing_update_ids)
    last_messages_file = max(handled_updatesids_files.union([previous_checkpoint.last_messages_file] if previous_checkpoint.last_messages_file else []), key=msg_file_key, default=None)

    handled_processed_responses_files = set(runtime_metadata.files_mapping.get_response_processed_file(resp_id) for resp_id in runtime_metadata.get_process_success_responses_ids())
    last_processed_responses_file = max(handled_processed_responses_files.union([previous_checkpoint.last_processed_responses_file] if previous_checkpoint.last_processed_responses_file else []), key=processed_resp_file_key, default=None)

    handled_sent_responses_files = set(runtime_metadata.files_mapping.get_response_send_file(resp_id) for resp_id in runtime_metadata.get_send_success_responses_ids())
    last_sent_responses_file = max(handled_sent_responses_files.union([previous_checkpoint.last_sent_responses_file] if previous_checkpoint.last_sent_responses_file else []), key=sent_resp_file_key, default=None)

    files_containing_unprocessed_msgs_errors = set()
    """
    TO INCLUDE IF WE WANT TO RECOVER RAW MESSAGES FROM PROCESS ERRORS -- I.E. rebatch messages instead of keeping batches immutable
    existing_files_containing_unprocessed_msgs_errors = set(previous_checkpoint.files_containing_unprocessed_msgs_errors) if not replace_previous_checkpoint_error_files else set()
    curr_files_containing_unprocessed_msgs_errors = set(
        runtime_metadata.files_mapping.updates_files_mapping[upd_id]
        for resp_id in runtime_metadata.process_errors_counts.keys()
        for upd_id in runtime_metadata._responses_update_ids[resp_id]
    )
    files_containing_unprocessed_msgs_errors = existing_files_containing_unprocessed_msgs_errors.union(curr_files_containing_unprocessed_msgs_errors)
    """
    
    existing_files_containing_unsent_responses_errors = set(previous_checkpoint.files_containing_unsent_responses_errors) if not replace_previous_checkpoint_error_files else set()
    curr_files_containing_unsent_responses_errors = set(
        runtime_metadata.files_mapping.get_response_processed_file(resp_id)
        for resp_id in runtime_metadata.send_errors_counts.keys()
    )
    files_containing_unsent_responses_errors = existing_files_containing_unsent_responses_errors.union(curr_files_containing_unsent_responses_errors)

    new_checkpoint = RecoveryCheckpoint(last_handled_processing_update_id = last_handled_processing_update_id, \
                last_handled_sending_response_id = last_handled_sending_response_id, \
                last_messages_file = last_messages_file, \
                last_processed_responses_file = last_processed_responses_file, \
                last_sent_responses_file = last_sent_responses_file, \
                files_containing_unprocessed_msgs_errors = files_containing_unprocessed_msgs_errors, \
                files_containing_unsent_responses_errors = files_containing_unsent_responses_errors \
    )
    return new_checkpoint    
