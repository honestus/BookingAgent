from __future__ import annotations    
import datetime as dt
from enum import Enum

def _map_to_datetime(curr_datetime: dt.datetime):
    from utils import datetimes_utils
    from utils import cast_utils

    if curr_datetime is None:
        curr_datetime = dt.datetime.now() 
    try:
        casted_dt = cast_utils.cast_value(curr_datetime, expected_type= dt.datetime)
        return datetimes_utils.map_datetime_to_default(casted_dt, ignore_seconds=False)
    except TypeError as e:
        raise e    

class ReceivedMessage:
    def __init__(self, update_id: int, user_id: int, chat_id: int, text: str, sent_at: dt.datetime, received_at: dt.datetime = None):
        self.update_id = normalize_id(update_id)  # o da Update se disponibile
        self.user_id = user_id
        self.chat_id = chat_id
        self.text = text
        self.sent_at = _map_to_datetime(sent_at)
        self.received_at = _map_to_datetime(received_at)
        
        
class ResponseKind(Enum):
    NORMAL = 1
    NOTICE = 2
    ERROR = -1
    
class ProcessStatus(Enum):
    UNPROCESSED = 0
    PROCESSED = 1
    ERROR_PROCESS = -1
    SKIPPED  = 2

class SendStatus(Enum):
    UNSENT = 0
    SENT = 1
    ERROR_SEND = -1
    
class BotResponse:
    def __init__(self, update_ids: list, user_id: int, chat_id: int, text: str, process_status: ProcessStatus = ProcessStatus.UNPROCESSED, send_status: SendStatus = SendStatus.UNSENT, reply_text: str = None, created_at: dt.datetime = None, replied_at: dt.datetime = None, sent_at: dt.datetime = None, last_msg_ts: dt.datetime = None, response_id=None, reply_type: ResponseKind = ResponseKind.NORMAL, **kwargs):
        self.response_id = normalize_id(response_id) if response_id else BotResponse.generate_response_id(user_id=user_id, update_ids=update_ids)
        self.update_ids = sorted([normalize_id(upd_id) for upd_id in update_ids])
        self.user_id = normalize_id(user_id)
        self.chat_id = normalize_id(chat_id)
        self.text = text
        self.reply_text = reply_text
        self.reply_type = reply_type
        self.process_status = process_status
        self.send_status = send_status
        self.created_at = _map_to_datetime(created_at)
        self.replied_at = _map_to_datetime(replied_at)
        self.sent_at = _map_to_datetime(sent_at)
        self.last_msg_ts = _map_to_datetime(last_msg_ts)
        self._to_skip = kwargs.get('to_skip', kwargs.get('_to_skip', False))
        if '_processed_response_filepath' in kwargs:
            self._processed_response_filepath = kwargs['_processed_response_filepath']
        
    def mark_to_skip(self, reply_text=None,):
        self._to_skip = True
        if reply_text is not None:
            self.reply_text=reply_text
        self.reply_type = ResponseKind.NOTICE
        
    def mark_as_skipped(self, reply_text=None, timestamp: dt.datetime = None):
        self.mark_to_skip(reply_text)
        self.process_status = ProcessStatus.SKIPPED
        self.replied_at = _map_to_datetime(timestamp)
        
        

    def mark_as_replied(self, reply_text, timestamp: dt.datetime = None):
        self.process_status = ProcessStatus.PROCESSED
        self.reply_text = reply_text
        self.replied_at = _map_to_datetime(timestamp)
        
    def mark_as_sent(self, timestamp: dt.datetime = None):
        self.send_status = SendStatus.SENT  
        self.sent_at = _map_to_datetime(timestamp)

    def mark_as_reply_error(self, timestamp: dt.datetime = None):
        self.process_status = ProcessStatus.ERROR_PROCESS
        self.replied_at = _map_to_datetime(timestamp)

    def mark_as_send_error(self, timestamp: dt.datetime = None):
        self.send_status = SendStatus.ERROR_SEND
        self.sent_at = _map_to_datetime(timestamp)
        
    @property
    def to_skip(self):
        return self._to_skip
        
    def __setattr__(self, k, v):
        if k == 'process_status':
            if not isinstance(v, ProcessStatus):
                raise ValueError('process_status must be of type ProcessStatus')
        if k == 'send_status':
            if not isinstance(v, SendStatus):
                raise ValueError('send_status must be of type SendStatus')
            if v!=SendStatus.UNSENT and self.process_status in [ProcessStatus.UNPROCESSED, ProcessStatus.ERROR_PROCESS]:
                #print(self.response_id)
                raise ValueError('send_status must be unsent till the object is not processed', self.response_id)
        super.__setattr__(self,k,v)
        
    @staticmethod
    def generate_response_id(user_id: str, update_ids: list[int]) -> int:
        # Current ts (milliseconds)
        now_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
        # Relative ts (~40 bit only)
        timestamp = now_ms - _CUSTOM_EPOCH  # 40 bit sufficienti

        user_hash = _stable_hash(str(user_id).encode(), 12) # 12 bit
        update_hash = _stable_hash(str(tuple(sorted(update_ids))).encode(), 12)  # 12 bit

        # 4. Combined hash: ts + user_hash + update_hash
        return (timestamp << 24) | (user_hash << 12) | update_hash
    
    
class TextBatcher:
    def __init__(self, text, separator: str = '\n'):
        self.text = text
        self.separator = separator
        
    def enqueue(self, text):
        self.text += (self.separator + text)
        
    def prepend(self, text):
        self.text = text + self.separator + self.text
    
class ChatMessagesBatch:
    def __init__(self, msg_text: str, msg_ts: dt.datetime, msg_update_id: int, msg_separator='\n'):
        self._text_batcher = TextBatcher(msg_text, msg_separator)
        self._timestamps = [msg_ts]
        self.update_ids = [msg_update_id]

    @property
    def text(self):
        return self._text_batcher.text
        
    @property
    def first_msg_ts(self):
        return self._timestamps[0]
        
    @property
    def last_msg_ts(self):
        return self._timestamps[-1]

    def _enqueue_following_message(self, msg_text: str, msg_ts: dt.datetime, msg_update_id: int):
        """ msg is meant to have ts > batch.last_msg_ts """
        self._text_batcher.enqueue(msg_text)
        self._timestamps.append(msg_ts)
        self.update_ids.append(msg_update_id)
        
        
    def append_unsorted(self, msg_text: str, msg_ts: dt.datetime, msg_update_id: int):
        if msg_ts>=self.last_msg_ts:
            self.enqueue_following_message(msg_text, msg_update_id, msg_ts)
        else:
            self._text_batcher.prepend(msg_text)
            self._timestamps.insert(0, msg_ts)
            self.update_ids.insert(0, msg_update_id)
        
    def __repr__(self):
        return f'({self.text} - derived by update_ids: {self.first_msg_ts})'
        
        
class SentResponseFS:
    def __init__(self, response_id: int, sent_at: dt.datetime, corresponding_processed_filepath: Path):
        from chat_system.message_responses import normalize_id

        self.response_id = normalize_id(response_id)
        self.sent_at = sent_at
        self.corresponding_processed_filepath = str(corresponding_processed_filepath)       
    def __str__(self):
        return ' '.join([str(self.response_id), self.sent_at.isoformat(), str(self.corresponding_processed_filepath)])
        
def normalize_id(value) -> int:
    return int(value)

def _stable_hash(value: str, bits: int) -> int:
    import hashlib
    return int(hashlib.md5(value).hexdigest(), 16) & ((1 << bits) - 1)

_CUSTOM_EPOCH = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)

    