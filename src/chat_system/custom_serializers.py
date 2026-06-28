import json
import utils
from storage.serializers import RecordToStringSerializer
import storage.serializers as serialize_utils
from chat_system.message_responses import ReceivedMessage , BotResponse, SentResponseFS
from chat_system.metadata import RecoveryCheckpoint



class ReceivedMessageSerializer(RecordToStringSerializer[ReceivedMessage]):
    
    @staticmethod
    def encode(obj: ReceivedMessage) -> str:

        if not isinstance(obj, ReceivedMessage):
            raise TypeError(f'Invalid object in input: {type(obj)}. The object to encode must be a ReceivedMessage')
            
        payload = {
            "update_id": obj.update_id,
            "user_id": obj.user_id,
            "chat_id": obj.chat_id,
            "text": obj.text,
            "sent_at": serialize_utils.encode_datetime(obj.sent_at),
            "received_at": serialize_utils.encode_datetime(obj.received_at),
        }

        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def decode(data: str) -> ReceivedMessage:

        payload = json.loads(data)

        return ReceivedMessage(
            update_id=payload["update_id"],
            user_id=payload["user_id"],
            chat_id=payload["chat_id"],
            text=payload["text"],
            sent_at=serialize_utils.decode_datetime(payload["sent_at"]),
            received_at=serialize_utils.decode_datetime(payload["received_at"]),
        )
        
        
        
class BotResponseSerializer(RecordToStringSerializer[BotResponse]):
    
    @staticmethod
    def encode(obj: BotResponse) -> str:
        if not isinstance(obj, BotResponse):
            raise TypeError(f'Invalid object in input: {type(obj)}. The object to encode must be a BotResponse')

        payload = {
            "update_ids": obj.update_ids,
            "user_id": obj.user_id,
            "chat_id": obj.chat_id,
            "text": obj.text,
            "process_status": serialize_utils.encode_enum(obj.process_status),
            "send_status": serialize_utils.encode_enum(obj.send_status),
            "reply_text": obj.reply_text,
            "created_at": serialize_utils.encode_datetime(obj.created_at),
            "replied_at": serialize_utils.encode_datetime(obj.replied_at),
            "sent_at": serialize_utils.encode_datetime(obj.sent_at),
            "last_msg_ts": serialize_utils.encode_datetime(obj.last_msg_ts),
            "response_id": obj.response_id,
            "reply_type": serialize_utils.encode_enum(obj.reply_type),
        }

        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def decode(data: str) -> BotResponse:
        from chat_system.message_responses import ProcessStatus, SendStatus, ResponseKind

        payload = json.loads(data)

        return BotResponse( 
            update_ids=payload["update_ids"],
            user_id=payload["user_id"],
            chat_id=payload["chat_id"],
            text=payload["text"],
            process_status=serialize_utils.decode_enum(payload["process_status"], ProcessStatus),
            send_status=serialize_utils.decode_enum(payload["send_status"], SendStatus),
            reply_text=payload["reply_text"],
            created_at=serialize_utils.decode_datetime(payload["created_at"]),
            replied_at=serialize_utils.decode_datetime(payload["replied_at"]),
            sent_at=serialize_utils.decode_datetime(payload["sent_at"]),
            last_msg_ts=serialize_utils.decode_datetime(payload["last_msg_ts"]),
            response_id=payload["response_id"],
            reply_type=serialize_utils.decode_enum(payload["reply_type"], ResponseKind),
        )
        
        
class SentResponseSerializer(RecordToStringSerializer[SentResponseFS]):
    @staticmethod
    def encode(obj: SentResponseFS) -> str:
        if not isinstance(obj, SentResponseFS):
            raise TypeError(f'Invalid object in input: {type(obj)}. The object to encode must be a SentResponseFS')
        
        payload = {
            "response_id": str(obj.response_id),
            "sent_at": serialize_utils.encode_datetime(obj.sent_at),
            "corresponding_processed_filepath": str(obj.corresponding_processed_filepath),
        }

        return json.dumps(payload, ensure_ascii=False)
    
    @staticmethod
    def decode(data: str) -> SentResponseFS:        
        payload = json.loads(data)

        return SentResponseFS( 
            response_id=payload["response_id"],
            sent_at=serialize_utils.decode_datetime(payload["sent_at"]),
            corresponding_processed_filepath=payload["corresponding_processed_filepath"],
        )
        
        
        
class StringSerializer(RecordToStringSerializer[str]):
    @staticmethod
    def encode(obj: str) -> str:
        return str(obj)
    
    @staticmethod
    def decode(data: str) -> str:
        return data
        
        
class RecoveryCheckpointSerializer(RecordToStringSerializer[RecoveryCheckpoint]):
    
    @staticmethod
    def encode(obj: str) -> str:
        if not isinstance(obj, RecoveryCheckpoint):
            raise TypeError(f'Invalid object in input: {type(obj)}. The object to encode must be a RecoveryCheckpoint')
        
        payload = {
            "last_handled_processing_update_id": obj.last_handled_processing_update_id,
            "last_handled_sending_response_id": obj.last_handled_sending_response_id,
            "last_messages_file": str(obj.last_messages_file) if obj.last_messages_file else None,
            "last_processed_responses_file": str(obj.last_processed_responses_file) if obj.last_processed_responses_file else None,
            "last_sent_responses_file": str(obj.last_sent_responses_file) if obj.last_sent_responses_file else None,
            "files_containing_unprocessed_msgs_errors": [str(f) for f in obj.files_containing_unprocessed_msgs_errors],
            "files_containing_unsent_responses_errors": [str(f) for f in obj.files_containing_unsent_responses_errors],
            "last_checkpoint": serialize_utils.encode_datetime(obj.last_checkpoint)
        }

        return json.dumps(payload, ensure_ascii=False)
    
    @staticmethod
    def decode(data: str) -> RecoveryCheckpoint:
        payload = json.loads(data)
        payload["last_checkpoint"] = serialize_utils.decode_datetime(payload["last_checkpoint"])
        for k in ['last_messages_file', 'last_processed_responses_file', 'last_sent_responses_file']:
            if not bool(payload[k]):
                pass#payload.pop(k)
        return RecoveryCheckpoint(**payload)