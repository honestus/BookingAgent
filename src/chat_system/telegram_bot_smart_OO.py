from __future__ import annotations
import sys
sys.path.append("C:/Users/onest/Documents/data_analysis/booking_agent/src")

import logging, time
import asyncio
import datetime as dt
from pathlib import Path
from telegram.ext import filters as telegram_filters, ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler
from collections import deque

from chat_system import message_responses, telegram_disk_utils
from chat_system.telegram_disk_utils import DiskDirType
from application.orchestrator import ApplicationOrchestrator
import config_loader

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
    
MAX_CONVERSATION_TURNS = 5
startup_time = dt.datetime.now(dt.UTC)


users_data_path = config_loader.get_users_messages_data_dir()
bot = None
app_system = None
error_manager = None
user_processors = dict()
        
        
class MessageSender:
    async def send(self, chat_id: int, text: str):
        raise NotImplementedError
        
class TelegramSender(MessageSender):
    def __init__(self, bot):
        self.bot = bot

    async def send(self, chat_id: int, text: str):        
        await self.bot.send_message(chat_id=chat_id, text=text)
            

    
def backend_startup():
    raise NotImplementedError('')
    global data_path

    
async def init_users_processors():
    from chat_system.user_processor import UserProcessor
    global user_processors, bot, users_data_path, startup_time, app_system
    all_users_ids = [message_responses.normalize_id(user_id) for user_id in telegram_disk_utils.get_all_user_ids(users_data_path)]
    users_processors = await asyncio.gather(*[
        UserProcessor.from_disk(user_id=user_id, sender=bot, app_system=app_system, max_conversation_turns=MAX_CONVERSATION_TURNS,
        user_path=telegram_disk_utils._get_user_dir(user_id=user_id, base_dir=users_data_path, dirtype=DiskDirType.USER_DEFAULT), 
         curr_time=startup_time, error_manager=_get_error_manager()) 
            for user_id in all_users_ids], return_exceptions=False)
    
    for user_id, us_processor in zip(all_users_ids, users_processors):
        if isinstance(us_processor, Exception):
            print(user_id, us_processor, 'startup')
            continue
        
        user_processors[user_id] = us_processor
    
    await asyncio.gather(*[us_processor._run_pending(startup_time) for us_processor in user_processors.values()])
        
    return
        
        
async def get_user_processor(user_id):
    from chat_system.user_processor import UserProcessor    
    global user_processors, bot, users_data_path, app_system
    
    user_id = message_responses.normalize_id(user_id)
    if user_id in user_processors:
        return user_processors[user_id]
    
    user_path = telegram_disk_utils._get_user_dir(user_id=user_id, dirtype=DiskDirType.USER_DEFAULT, base_dir=users_data_path)
    if user_path.exists():
        us_processor = await UserProcessor.from_disk(user_id=user_id, sender=bot, max_conversation_turns=MAX_CONVERSATION_TURNS,
        user_path=user_path, curr_time=dt.datetime.now(dt.UTC), error_manager=_get_error_manager(), app_system=app_system, )
    else:
        us_processor = build_new_user_processor(user_id)
    user_processors[user_id] = us_processor
    return us_processor


def _get_error_manager():
    from chat_system.error_manager import ErrorManager
    global error_manager
    if error_manager is None:
        error_manager=ErrorManager()
    return error_manager

def build_new_user_processor(user_id):
    from chat_system.user_processor import UserProcessor   
    from chat_system.metadata import RuntimeMetadataManager
    from chat_system.messages_queue_manager import MessageQueueManager
    from chat_system.conversation_manager import ConversationManager

    global users_data_path, bot
    
    queue_manager = MessageQueueManager()
    conv_manager = ConversationManager(max_turns=MAX_CONVERSATION_TURNS)
    recov_manager = RuntimeMetadataManager()
    
    user_path = telegram_disk_utils._get_user_dir(user_id=user_id, dirtype=DiskDirType.USER_DEFAULT, base_dir=users_data_path)
    user_storage_manager = UserProcessor.init_storage_manager(user_path=user_path, user_id=user_id)
    user_processor = UserProcessor(user_id=user_id, storage_manager=user_storage_manager, queue_manager=queue_manager, 
                conversation_manager=conv_manager, metadata_manager=recov_manager, sender=bot, 
                app_system=app_system, error_manager=_get_error_manager())
    return user_processor



async def on_message(update: Update, context):
    user_id = message_responses.normalize_id(update.message.from_user.id)

    processor = await get_user_processor(user_id)
    await processor.handle_message(update)
        




async def main():
    #STRUTTURE PRINCIPALI:
    # -data[user] 
    # -- messages_queue
    # -- batches_queue
    # -- open_batch, end_of_timeout_timestamp
    #1. CARICO MESSAGGI UNPROCESSED DA FILE -- popolo messages_queue
    #2. CARICO RESPONSES UNSENT DA FILE -- popolo batches_queue 
    #3. AVVIO BOT (inizializzo startup_time) E RICEVO MESSAGGI -- finchè ricevo (i.e. finchè non ho sleep di 10s o finchè msg.sent_at >= startup_time) store su disco e aggiunta a messages_queue   
    #4. AVVIO PROCESSOR PER OGNI USER IN data[user] 
    #4.1 PER OGNI MSG in messages_queue -> generate_batches(msg, open_batch=data[user][open_batch]) -> aggiorno data[user] (sia batches_queue, open_batch e end_of_timeout_timestamp), rimuovo msg da messages_queue.
    #4.2 PER OGNI BATCH in batches_queue -> generate_response(batch.text) -> salvo response -> rimuovo batch da batches_queue -> invio response ...
    
    from config_loader import initialize_application_orchestrator
    from pathlib import Path
    
    global bot, app_system
    
    Path(users_data_path).mkdir(exist_ok=True, parents=True)
    app_system = await initialize_application_orchestrator()
    
    tok_id = "8379699604:AAEkSDtpy8F89OsgIhpLf2_bAZbzSfvnD28"
    application = ApplicationBuilder().token(tok_id).build()   
    mimick_processing_handler = MessageHandler(telegram_filters.TEXT, on_message)
    application.add_handler(mimick_processing_handler)
    
    bot = TelegramSender(application.bot)
    await init_users_processors()
    
    async with application:    
        await application.initialize()
        await application.start()
        await application.updater.start_polling(timeout=50)
        

        # Mantiene il main vivo finché il processo non viene interrotto
        # Questo permette ai create_task dello startup di continuare a girare
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            await application.stop()
  
  
if __name__ == '__main__':
    #global startup_time
    #startup_time = dt.datetime.now(dt.UTC)
    try:
        asyncio.run(main())
    except Exception as e:
        print(e, 'main')