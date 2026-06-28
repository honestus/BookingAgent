from __future__ import annotations
from chat_system.conversation_manager import ConversationMessage, ConversationManager, Role
from chat_system.message_responses import ResponseKind, ProcessStatus, SendStatus

class ConversationRules:
    @staticmethod
    def should_include_user_message(bot_response: BotResponse) -> bool:
        if bot_response.reply_type in [ResponseKind.NOTICE, ResponseKind.ERROR]:
            return False
        if bot_response.process_status in [ProcessStatus.ERROR_PROCESS, ProcessStatus.SKIPPED]:
            return False
        if bot_response.to_skip:
            return False

        return True

    @staticmethod
    def should_include_assistant_message(bot_response: BotResponse) -> bool:
        if bot_response.reply_type in [ResponseKind.NOTICE, ResponseKind.ERROR]:
            return False
        if bot_response.send_status != SendStatus.SENT:
            return False
        if bot_response.to_skip:
            return False

        return True

class ConversationPolicy:
    @classmethod
    async def get_context(cls, processor: UserProcessor, bot_response: BotResponse,) -> list[ConversationMessage]:
        cm = processor.conversation_manager

        if bot_response.process_status == ProcessStatus.ERROR_PROCESS:
            recovered_cm = await ConversationManager.from_disk(
                storage_manager=processor.storage_manager, snapshot_time=bot_response.last_msg_ts, max_turns=cm.max_turns
            )
            return recovered_cm.get_messages()

        return cm.get_messages(max_ts=bot_response.last_msg_ts)

    @classmethod
    def update_context(cls, processor: UserProcessor, bot_response: BotResponse, role: Role):
        cm = processor.conversation_manager

        if role == Role.USER:
            if not ConversationRules.should_include_user_message(bot_response):
                return
            msg = cls.build_user_message(bot_response)
        
        elif role == Role.ASSISTANT:
            if not ConversationRules.should_include_assistant_message(bot_response):
                return
            msg = cls.build_assistant_message(bot_response)

        else:
            return

        cm.insert(msg)

    @staticmethod
    def build_user_message(bot_response: BotResponse) -> ConversationMessage:
        return ConversationMessage(role=Role.USER, text=bot_response.text, timestamp=bot_response.last_msg_ts)

    @staticmethod
    def build_assistant_message(bot_response: BotResponse) -> ConversationMessage:
        return ConversationMessage(role=Role.ASSISTANT, text=bot_response.reply_text, timestamp=bot_response.sent_at ) 
    