from __future__ import annotations
import datetime as dt
from collections import deque
from enum import Enum

class Role(Enum):
    ASSISTANT = 'assistant'
    SYSTEM = 'system'
    USER = 'user'
    

class ConversationMessage:
    def __init__(self, role: Role, text: str, timestamp: dt.datetime, dialogue_turn_id: int = None):
        self.role = role
        self.text = text
        self.timestamp = timestamp
        self.dialogue_turn_id = dialogue_turn_id

    def __str__(self):
        return f'{self.role}: \t {self.text}'
        
    def __repr__(self):
        return self.__str__()

class ConversationManager:
    def __init__(self, messages: deque[ConversationMessage] = [], max_turns: int = 10):
        self.max_turns = max_turns
        self.messages = deque()
        for msg in messages:
            self.insert(msg)

    
    def insert(self, msg: ConversationMessage):
        idx = self.__get_insertion_idx__(msg.timestamp)
        if idx==0 and self.messages and (n_turns := 1+(self.messages[-1].dialogue_turn_id - self.messages[0].dialogue_turn_id)) == self.max_turns: ##this msg is before first msg and n of turns is already full
            return
        if idx==len(self.messages):
            return self.__append__(msg)
        
        curr_msgs = list(self.messages)
        msg_fn, next_msgs_fn = self.__get_turns__(msg, idx)
        msg.dialogue_turn_id = msg_fn(msg.dialogue_turn_id)
        if next_msgs_fn is not None:
            for m in curr_msgs[idx:]:
                m.dialogue_turn_id = next_msgs_fn(m.dialogue_turn_id)
        
        self.messages = deque(curr_msgs[:idx]+[msg]+curr_msgs[idx:])
        self._trim_turn()        
        
    def __append__(self, msg: ConversationMessage):
        msg_turn_fn, _ = self.__get_turns__(msg, idx=len(self.messages))
        msg.dialogue_turn_id = msg_turn_fn(msg.dialogue_turn_id)
        self.messages.append(msg)
        self._trim_turn()

    def get_messages(self, as_string = False, role=None, n_turns: int = None, max_ts: dt.datetime = None) -> list[ConversationMessage]:
        msgs = list(self.messages)
        if max_ts is not None:
            idx = self.__get_insertion_idx__(max_ts)
            msgs = msgs[:idx]
        if role is not None:
            msgs = [m for m in msgs if m.role == role]
        if n_turns is not None:
            if n_turns==0:
                return []
            last_turn = msgs[-1].dialogue_turn_id
            for i, curr_msg in enumerate(msgs[-2::-1]):
                if (last_turn-curr_msg.dialogue_turn_id) >= max_turns:
                    break
                elif i==len(msgs[-2::-1])-1: #including first element of list if break wasnt reached
                    i+=1
            msgs = msgs[-i-1:]
        if as_string:
            return _format_messages(msgs)
        return msgs

    
    def __get_insertion_idx__(self, timestamp: dt.datetime):
        from bisect import bisect_right
        if not self.messages:
            return 0
        if timestamp>=self.messages[-1].timestamp:
            return len(self.messages)
        return bisect_right(self.messages, timestamp, key=lambda x: x.timestamp)
        
    def __get_turns__(self, msg: ConversationMessage, idx: int):
        current_msg_fn, following_elements_fn = None, None
        if not self.messages:
             current_msg_fn = lambda x: 0
             return (current_msg_fn, following_elements_fn)
        prev_msg, next_msg = self.messages[idx-1] if idx>0 else None, self.messages[idx] if idx<len(self.messages) else None
        if prev_msg and msg.role==prev_msg.role:
            current_msg_fn = lambda x: prev_msg.dialogue_turn_id
            return (current_msg_fn, following_elements_fn)
        if next_msg and msg.role==next_msg.role:
            current_msg_fn = lambda x: next_msg.dialogue_turn_id
            return (current_msg_fn, following_elements_fn)
        if msg.role==Role.USER:
            if prev_msg:
                current_msg_fn = lambda x: prev_msg.dialogue_turn_id+1
                following_elements_fn = lambda x: x+1
            else:
                current_msg_fn = lambda x: next_msg.dialogue_turn_id
            
        elif msg.role==Role.ASSISTANT:
            if next_msg:
                current_msg_fn = lambda x: next_msg.dialogue_turn_id
                following_elements_fn = lambda x: x+1
            else:
                current_msg_fn = lambda x: prev_msg.dialogue_turn_id
        return (current_msg_fn, following_elements_fn)

    def get_n_current_turns(self):
        if not self.messages:
            return 0
        return 1+self.messages[-1].dialogue_turn_id-self.messages[0].dialogue_turn_id
        
    def _trim_turn(self):
        if not self.messages:
            return
        last_turn = self.messages[-1].dialogue_turn_id

        first_allowed_turn = 1+last_turn - self.max_turns
        while self.messages and self.messages[0].dialogue_turn_id < first_allowed_turn : ##removing whole turn from conversation
            self.messages.popleft()
   
    @classmethod
    async def from_disk(cls, storage_manager: UserStorageManager, max_turns: int, snapshot_time: dt.datetime = None) -> ConversationManager:
        """
        Reconstructs a ConversationManager from disk, iterating shards in reverse
        (most recent first) until max_turns is reached or all shards are exhausted.

        For each shard index i, reads:
          - sent_responses[i]: determines which responses were actually sent and their timestamps
          - processed_responses[i]: the corresponding bot responses
          - extra response files: sent responses may reference older processed_response files
            not covered by the current shard index — these are fetched explicitly
          - process_errors[i]: failed processing attempts, included as user messages only

        snapshot_time acts as an upper bound: only objects with timestamp <= snapshot_time
        are considered, allowing point-in-time reconstruction.
        """
        from chat_system.recovery_utils import build_conversation_manager_from_disk_responses
        
        if snapshot_time is None:
            snapshot_time = dt.datetime.now(dt.UTC)
        elif snapshot_time.tzinfo is None:
            snapshot_time = snapshot_time.replace(tzinfo=dt.UTC)

        conv_manager = cls(max_turns=max_turns + 1)
        

        sent_files = list(reversed(storage_manager.sent_responses.shard_organizer.files))
        resp_files = list(reversed(storage_manager.processed_responses.shard_organizer.files))
        error_files = list(reversed(storage_manager.process_errors.shard_organizer.files))
        max_n_shards = max([len(sent_files), len(resp_files), len(error_files)], default=0)
        
        seen_response_ids: set[int] = set()
        previously_read_files: set[Path] = set()
        
        for i in range(max_n_shards):
            if conv_manager.get_n_current_turns() >= max_turns + 1:
                break

            curr_batch_sent_ids_mapping: dict[int, SentResponseFS] = {} #{resp_id: SentResponseFS}
            
            curr_send_file = sent_files[i]  if i < len(sent_files) else None
            curr_resp_file = resp_files[i] if i < len(resp_files) else None
            curr_error_file = error_files[i] if i < len(error_files) else None
            
            # sent responses may reference processed_response files outside the current
            # shard index — fetch those explicitly to avoid missing assistant messages
            # corresponding_process_files_to_read includes all the referring - still unread - process_responses files for these sent_responses 
            corresponding_process_files_to_read = set()
            
            if curr_send_file:
                loaded_sent = await storage_manager.sent_responses.read_files([curr_send_file])
                for loaded in loaded_sent:
                    s = loaded.obj
                    if s.sent_at <= snapshot_time:
                        curr_batch_sent_ids_mapping[s.response_id] = s
                corresponding_process_files_to_read = (
                    set(s.corresponding_processed_filepath for s in curr_batch_sent_ids_mapping.values()) - previously_read_files
                )
                corresponding_process_files_to_read.difference_update({curr_resp_file, curr_error_file})
            
            
            
            ###sent files only contain references. Now loading the BotResponse from the corresponding process_files.
            ###Also loading the BotResponse (either processed/process_errors) with ts < snapshot_time, since they refer to correctly received msgs.
            

            candidate_responses: list[LoadedObject[BotResponse]] = []

            if corresponding_process_files_to_read:
                curr_referred_process_responses_batch = await storage_manager.processed_responses.read_files(corresponding_process_files_to_read)
                for loaded_resp in curr_referred_process_responses_batch:
                    if loaded_resp.obj.response_id not in curr_batch_sent_ids_mapping:
                        continue
                    if loaded_resp.obj.response_id in seen_response_ids:
                        continue
                    seen_response_ids.add(loaded_resp.obj.response_id)
                    candidate_responses.append(loaded_resp)
                    
                previously_read_files |= corresponding_process_files_to_read
            
            # A response may temporarily exist in both processed_responses and
            # process_errors until error files are flushed.
            # In such cases processed_responses is the source of truth, therefore
            # successful responses are loaded first and process_errors only fill
            # missing response_ids.
            if curr_resp_file:
                curr_processed_resp_batch = await storage_manager.processed_responses.read_files([curr_resp_file])
                for loaded_resp in curr_processed_resp_batch:
                    if loaded_resp.obj.last_msg_ts > snapshot_time:
                        continue
                    if loaded_resp.obj.response_id in seen_response_ids:
                        continue
                    seen_response_ids.add(loaded_resp.obj.response_id)
                    candidate_responses.append(loaded_resp)
                    
                previously_read_files |= {curr_resp_file}

            
            if curr_error_file:
                error_batch = await storage_manager.process_errors.read_files([curr_error_file])
                for loaded_resp in error_batch:
                    if loaded_resp.obj.last_msg_ts > snapshot_time:
                        continue
                    if loaded_resp.obj.response_id in seen_response_ids:
                        continue
                    seen_response_ids.add(loaded_resp.obj.response_id)
                    candidate_responses.append(loaded_resp)
                    
                previously_read_files |= {curr_error_file}

            conv_manager = build_conversation_manager_from_disk_responses(
                conv_manager, candidate_responses, curr_batch_sent_ids_mapping
            )

        # restore actual max_turns and trim any excess accumulated during loading
        conv_manager.max_turns -= 1
        conv_manager._trim_turn()
        return conv_manager
        
        
def _format_messages(messages: list[ConversationMessage]):
    s = ''
    for msg in messages:
        s += f'{msg.role.value}: {msg.text}\n'
    return s
        
        
