from chat_system.message_responses import ReceivedMessage, BotResponse
from collections import deque

class MessageQueueManager:
    def __init__(self, messages: deque[ReceivedMessage] = deque(), pending_responses: deque[BotResponse] = deque(), process_errors: deque[BotResponse] = deque(), send_errors: deque[BotResponse] = deque()):
        self.messages = deque(messages)
        self.pending_responses = deque(pending_responses)
        self.process_errors = deque(process_errors)
        self.send_errors = deque(send_errors)
        
        
    def append_message(self, message: ReceivedMessage):
        self.messages.append(message)
        
    def pop_message(self):
        if not self.messages:
            return None
        return self.messages.popleft()
        
    def pop_all_messages(self):
        msgs = list(self.messages)
        self.messages.clear()
        return msgs
        
    def append_response(self, response: BotResponse):
        self.pending_responses.append(response)
        
    def pop_response(self):
        if not self.pending_responses:
            return None
        return self.pending_responses.popleft()
        
    def append_process_error(self, response: BotResponse):
        self.process_errors.append(response)
        
    def pop_process_error(self):
        if not self.process_errors:
            return None
        return self.process_errors.popleft()
        
    def pop_all_process_errors(self):
        errors = list(self.process_errors)
        self.process_errors.clear()
        return errors
        
    def append_send_error(self, response: BotResponse):
        self.send_errors.append(response)
        
    def pop_send_error(self):
        if not self.send_errors:
            return None
        return self.send_errors.popleft()
        
    def pop_all_send_errors(self):
        errors = list(self.send_errors)
        self.send_errors.clear()
        return errors
     
    @property
    def any_new_message(self):
        return bool(self.messages)
       
    @property
    def any_pending_response(self):
        return bool(self.pending_responses)
        
    @property
    def any_process_error(self):
        return bool(self.process_errors)
    
    @property
    def any_send_error(self):
        return bool(self.send_errors)
    
    @property
    def any_error(self):
        return self.any_process_error or self.any_send_error
        
        
        


