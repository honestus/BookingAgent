from __future__ import annotations    
from typing import Generic, TypeVar
from enum import Enum
from pathlib import Path

        
import asyncio 
from storage.shard_organizer import IntShardOrganizer
from storage.serializers import RecordSerializer
from dataclasses import dataclass

T = TypeVar('T')

@dataclass
class LoadedObject(Generic[T]):
    filepath: Path
    obj: T




class ShardedFilesManager(Generic[T]):
    
    def __init__(self, shard_organizer: ShardOrganizer, serializer: RecordSerializer[T], max_records_per_file: int = 1000):
        self.shard_organizer = shard_organizer
        self.serializer = serializer
        self._file_storer = _get_storer_by_serializer_type(self.serializer)
        self._max_file_records = max_records_per_file
        self._init_last_file_records()
        self._lock = asyncio.Lock() #self.shard_organizer._dirpath / f'{self.shard_organizer._file_stem}.lock'        

    def _init_last_file_records(self):
        from utils.io_utils import get_n_of_lines_file
        self._last_file_records = 0 if (self.shard_organizer.last_file is None) else get_n_of_lines_file(self.shard_organizer.last_file)
    
    
    
    async def append(self, obj: T, *, _already_locked: bool = False):
        def _append_serialized_no_lock(serialized_obj):
            if self.shard_organizer.last_file is None:
                self.shard_organizer.create_next_file()
                self._last_file_records = 0
            if self._last_file_records >= self._max_file_records:
                self.shard_organizer.create_next_file()
                self._last_file_records = 0
            self._file_storer.write(filepath=self.shard_organizer.last_file, obj=serialized_obj, overwrite=False)
            self._last_file_records += 1
            return self.shard_organizer.last_file

        if not obj:
            return None

        serialized_obj = self.serializer.encode(obj)
        
        if _already_locked:
            assert self._lock.locked()
            return _append_serialized_no_lock(serialized_obj)

        async with self._lock:
            return _append_serialized_no_lock(serialized_obj)


    async def extend(self, objects: list[T], *, _already_locked: bool = False):
        def _extend_serialized_no_lock(serialized_objects):
            if self.shard_organizer.last_file is None:
                self.shard_organizer.create_next_file()
                self._last_file_records = 0
            if self._last_file_records >= self._max_file_records:
                self.shard_organizer.create_next_file()
                self._last_file_records = 0
            
            involved_files = []
            while serialized_objects:
                last_file_records_left = self._max_file_records - self._last_file_records
                chunk = serialized_objects[:last_file_records_left]
                self._file_storer.write_collection(filepath=self.shard_organizer.last_file, objs=chunk, overwrite=False)
                self._last_file_records += len(chunk)
                involved_files.append(self.shard_organizer.last_file)
                serialized_objects = serialized_objects[last_file_records_left:]
                if serialized_objects:
                    self.shard_organizer.create_next_file()
                    self._last_file_records = 0
            return involved_files

        if not objects:
            return []

        serialized_objects = [self.serializer.encode(obj) for obj in objects]

        if _already_locked:
            assert self._lock.locked()
            return _extend_serialized_no_lock(serialized_objects)

        async with self._lock:
            return _extend_serialized_no_lock(serialized_objects)


    async def erase_all(self, *, _already_locked: bool = False):
        def _erase_all_no_lock():
            current_existing_files = self.shard_organizer.files
            for filepath in current_existing_files:
                filepath.unlink()
            self.shard_organizer.build_state_from_disk()
            self._last_file_records = 0

        if _already_locked:
            assert self._lock.locked()
            return _erase_all_no_lock()

        async with self._lock:
            return _erase_all_no_lock()


    async def read_last(self, *, _already_locked: bool = False) -> list[LoadedObject[T]]:
        def _read_last_no_lock():
            if self.shard_organizer.last_file is None:
                return []
            return self._read_shard_file(self.shard_organizer.last_file)

        if _already_locked:
            assert self._lock.locked()
            return _read_last_no_lock()

        async with self._lock:
            return _read_last_no_lock()


    async def read_all(self, *, _already_locked: bool = False) -> list[LoadedObject[T]]:
        def _read_all_no_lock():
            all_loaded_objs = []
            for fp in self.shard_organizer.files:
                all_loaded_objs.extend(self._read_shard_file(fp))
            return all_loaded_objs

        if _already_locked:
            assert self._lock.locked()
            return _read_all_no_lock()

        async with self._lock:
            return _read_all_no_lock()


    async def read_files(self, shard_files: list[Path], *, _already_locked: bool = False) -> list[LoadedObject[T]]:
        from utils.general_utils import is_collection
        
        def _read_files_no_lock(shard_files):
            unknown_files = [fp for fp in shard_files if not self.shard_organizer.contains(fp)]
            if unknown_files:
                raise ValueError(f'Invalid filenames {unknown_files} for this shard.')
            all_loaded_objs = []
            for fp in shard_files:
                all_loaded_objs.extend(self._read_shard_file(fp))
            return all_loaded_objs

        if not is_collection(shard_files):
            shard_files = [shard_files]
        shard_files = [Path(s) for s in shard_files]
            
        if _already_locked:
            assert self._lock.locked()
            return _read_files_no_lock(shard_files)

        async with self._lock:
            return _read_files_no_lock(shard_files)

    def _read_shard_file(self, fp: Path) -> list[LoadedObject[T]]:
        objects = [self.serializer.decode(o) for o in self._file_storer.read(fp)]
        return [LoadedObject(obj=o, filepath=fp) for o in objects]
  

def _get_storer_by_serializer_type(serializer_cls):
    from storage.file_storers import StringRecordStorage, BinaryRecordStorage
    from storage.serializers import RecordToBytesSerializer, RecordToStringSerializer
    serializer_cls = serializer_cls if isinstance(serializer_cls, type) else type(serializer_cls)
    
    if not issubclass(serializer_cls, (RecordToStringSerializer, RecordToBytesSerializer)):
        raise ValueError(f'Undefined storer object for {type(serializer_cls)} type')
    if issubclass(serializer_cls, RecordToBytesSerializer):
        return BinaryRecordStorage
    return StringRecordStorage



class UserFileType(Enum):
    MESSAGES = 'messages'
    PROCESSED_RESPONSES = 'processed_responses'
    SENT_RESPONSES = 'sent_responses'
    PROCESS_ERRORS = 'process_errors'
    PROCESS_ERRORS_RESOLVED = 'process_errors_solved'
    SEND_ERRORS = 'send_errors'
    SEND_ERRORS_RESOLVED = 'send_errors_solved'
    METADATA = 'metadata'
    
class ErrorType(Enum):
    PROCESS_ERRORS = 'process_errors'
    SEND_ERRORS = 'send_errors'
    
_file_suffixes: dict[UserFileType, str] = {
    UserFileType.MESSAGES: '.jsonl',
    UserFileType.PROCESSED_RESPONSES: '.jsonl',
    UserFileType.SENT_RESPONSES: '.jsonl',
    UserFileType.PROCESS_ERRORS: '.jsonl',
    UserFileType.PROCESS_ERRORS_RESOLVED: '.txt',
    UserFileType.SEND_ERRORS: '.jsonl',
    UserFileType.SEND_ERRORS_RESOLVED: '.txt',
    UserFileType.METADATA: '.jsonl'
}


from chat_system.custom_serializers import *

class UserStorageManager:
    
    def __init__(self, user_id, path: Path, unprocessed_dirpath: Path):
        self.user_id = user_id
        self.path = Path(path)
        self._unprocessed_path = Path(unprocessed_dirpath) / f'user_{self.user_id}'
        self._metadata_fp = self.path / f'state/user_{self.user_id}_metadata.jsonl'
        self._build_shard_managers()
        backup_dirpath = self.path / 'backup'
        self._backup = TmpFilesBackup(backup_path=backup_dirpath / 'tmp_backup')
        
        self._abandoned_process_errors_fp = backup_dirpath / (f'abandoned/{self.process_errors.shard_organizer._file_stem}{self.process_errors.shard_organizer._suffix}')
        self._abandoned_send_errors_fp = backup_dirpath /(f'abandoned/{self.send_errors.shard_organizer._file_stem}{self.send_errors.shard_organizer._suffix}')
        
        
        self._lock = asyncio.Lock()
        
        
        
    def _build_shard_managers(self):
        self.messages = ShardedFilesManager[ReceivedMessage](shard_organizer=IntShardOrganizer(dirpath=self.path, file_stem=f'user_{self.user_id}_{UserFileType.MESSAGES.value}', suffix=_file_suffixes[UserFileType.MESSAGES]), serializer=ReceivedMessageSerializer)
        self.processed_responses = ShardedFilesManager[BotResponse](shard_organizer=IntShardOrganizer(dirpath=self.path, file_stem=f'user_{self.user_id}_{UserFileType.PROCESSED_RESPONSES.value}', suffix=_file_suffixes[UserFileType.PROCESSED_RESPONSES]), serializer=BotResponseSerializer)
        self.process_errors = ShardedFilesManager[BotResponse](shard_organizer=IntShardOrganizer(dirpath=self.path, file_stem=f'user_{self.user_id}_{UserFileType.PROCESS_ERRORS.value}', suffix=_file_suffixes[UserFileType.PROCESS_ERRORS]), serializer=BotResponseSerializer)
        self.solved_process_errors = ShardedFilesManager[str](shard_organizer=IntShardOrganizer(dirpath=self.path, file_stem=f'user_{self.user_id}_{UserFileType.PROCESS_ERRORS_RESOLVED.value}', suffix=_file_suffixes[UserFileType.PROCESS_ERRORS_RESOLVED]), serializer=StringSerializer)
        self.sent_responses = ShardedFilesManager[SentResponseFS](shard_organizer=IntShardOrganizer(dirpath=self.path, file_stem=f'user_{self.user_id}_{UserFileType.SENT_RESPONSES.value}', suffix=_file_suffixes[UserFileType.SENT_RESPONSES]), serializer=SentResponseSerializer)
        self.send_errors = ShardedFilesManager[SentResponseFS](shard_organizer=IntShardOrganizer(dirpath=self.path, file_stem=f'user_{self.user_id}_{UserFileType.SEND_ERRORS.value}', suffix=_file_suffixes[UserFileType.SEND_ERRORS]), serializer=SentResponseSerializer)
        self.solved_send_errors = ShardedFilesManager[str](shard_organizer=IntShardOrganizer(dirpath=self.path, file_stem=f'user_{self.user_id}_{UserFileType.SEND_ERRORS_RESOLVED.value}', suffix=_file_suffixes[UserFileType.SEND_ERRORS_RESOLVED]), serializer=StringSerializer)
        
    def store_user_as_unprocessed(self):
        #async with self.lock:
        self._unprocessed_path.mkdir(parents=True, exist_ok=True)
        return

    def store_user_as_processed(self):
        #async with self.lock:
        try:
            self._unprocessed_path.rmdir()
        finally:
            return
            
    def has_unprocessed_dir(self):
        #async with self.lock:
        return self._unprocessed_path.exists()
        
    def load_checkpoint(self):
        from chat_system.custom_serializers import RecoveryCheckpointSerializer
        file_storer = _get_storer_by_serializer_type(RecoveryCheckpointSerializer)
        #async with self.lock:
        if not self._metadata_fp.exists():
            return None
        return RecoveryCheckpointSerializer.decode(file_storer.read(self._metadata_fp)[-1])
        
    def write_checkpoint(self, obj: RecoveryCheckpoint, overwrite: bool=True):
        from chat_system.custom_serializers import RecoveryCheckpointSerializer
        file_storer = _get_storer_by_serializer_type(RecoveryCheckpointSerializer)
        #async with self.lock:
        if not overwrite:
            if self._metadata_fp.exists():                
                old_fp = self._metadata_fp.parent / f'{(self._metadata_fp.name.split(self._metadata_fp.suffix)[0])}_old{self._metadata_fp.suffix}'
                if not old_fp.exists():
                    self._metadata_fp.rename(old_fp)
                else:
                    old_content = file_storer.read(self._metadata_fp)[-1]
                    file_storer.write(filepath=old_fp, obj=old_content, overwrite=False)
        self._metadata_fp.parent.mkdir(exist_ok=True, parents=True)
        self._metadata_fp.touch(exist_ok=True)
        file_storer.write(filepath=self._metadata_fp, obj=RecoveryCheckpointSerializer.encode(obj), overwrite=True)
        
        
    async def overwrite_process_errors(self, active_errors: list, abandoned_errors: list):
        print(active_errors)
        async with self.process_errors._lock:
            async with self.solved_process_errors._lock:
                old_errors_files = list(self.process_errors.shard_organizer.files)
                old_solved_files = list(self.solved_process_errors.shard_organizer.files)
                files_to_backup = (old_errors_files + old_solved_files)
                
                self._backup.move_files_to_backup(files_to_backup) ###temporarily backing-up error files -- recover from them if anything goes wrong
                self.process_errors.shard_organizer.build_state_from_disk()
                self.process_errors._last_file_records = 0
                self.solved_process_errors.shard_organizer.build_state_from_disk()
                self.solved_process_errors._last_file_records = 0

                await self.process_errors.extend(active_errors, _already_locked=True) ##overwriting errors
                self._backup.cleanup() ##overwrite successfully completed -- removing sentinel

        if abandoned_errors:
            self._abandoned_process_errors_fp.parent.mkdir(exist_ok=True)
            ###appending abandoned errors to abandoned file --- if it crashes here, no recovery (abandoned files not used anywhere)
            errors_file_storer = self.process_errors._file_storer
            errors_file_serializer = self.process_errors.serializer
            serialized_errors = [errors_file_serializer.encode(e) for e in abandoned_errors]
            errors_file_storer.write_collection(objs=serialized_errors, filepath=self._abandoned_process_errors_fp, overwrite=False)
        
        return True
    
    async def overwrite_send_errors(self, active_errors: list, abandoned_errors: list):
        print(active_errors)
        async with self.send_errors._lock:
            async with self.solved_send_errors._lock:
                old_errors_files = list(self.send_errors.shard_organizer.files)
                old_solved_files = list(self.solved_send_errors.shard_organizer.files)
                files_to_backup = (old_errors_files + old_solved_files)
                
                self._backup.move_files_to_backup(files_to_backup) ###temporarily backing-up error files -- recover from them if anything goes wrong
                self.send_errors.shard_organizer.build_state_from_disk()
                self.send_errors._last_file_records = 0
                self.solved_send_errors.shard_organizer.build_state_from_disk()
                self.solved_send_errors._last_file_records = 0

                await self.send_errors.extend(active_errors, _already_locked=True) ##writing active errors only
                self._backup.cleanup() ##overwrite successfully completed -- removing sentinel and backup files
        if abandoned_errors:
            ###appending abandoned errors to abandoned file
            self._abandoned_send_errors_fp.parent.mkdir(exist_ok=True)
            errors_file_storer = self.send_errors._file_storer
            errors_file_serializer = self.send_errors.serializer
            serialized_errors = [errors_file_serializer.encode(e) for e in abandoned_errors]
            errors_file_storer.write_collection(objs=serialized_errors, filepath=self._abandoned_send_errors_fp, overwrite=False)


        return True
    
    
    def append_abandoned_process_error(self, process_error: BotResponse):
        self._abandoned_process_errors_fp.touch(exist_ok=True)
        storer = self.process_error._file_storer
        serializer = self.process_error.serializer
        storer.write(obj=serializer.encode(process_error), filepath=self._abandoned_process_errors_fp, overwrite=False)
        return self._abandoned_process_errors_fp
        
    def append_abandoned_send_error(self, send_error: SentResponseFS):
        self._abandoned_send_errors_fp.touch(exist_ok=True)
        storer = self.send_errors._file_storer
        serializer = self.send_errors.serializer
        storer.write(obj=serializer.encode(send_error), filepath=self._abandoned_send_errors_fp, overwrite=False)
        return self._abandoned_send_errors_fp
    
from enum import Enum, auto
from pathlib import Path


class TmpBackupStatus(Enum):
    NONE = auto()
    PARTIAL = auto()
    COMPLETE = auto()

class TmpFilesBackup:
    _partial_sentinel_name = 'STARTED'
    _complete_sentinel_name = 'RENAMED'

    def __init__(self, backup_path: Path):
        self.backup_path = Path(backup_path)
        self._is_initialized = False
        
    def _ensure_mkdir(self):
        if self._is_initialized:
            return
        self.backup_path.mkdir(exist_ok=True, parents=True)
        self._is_initialized=True


    @property
    def partial_sentinel_fp(self):
        return self.backup_path / self._partial_sentinel_name

    @property
    def complete_sentinel_fp(self):
        return self.backup_path / self._complete_sentinel_name

    @property
    def status(self):
        if self.complete_sentinel_fp.exists():
            return TmpBackupStatus.COMPLETE
        if self.partial_sentinel_fp.exists():
            return TmpBackupStatus.PARTIAL

        return TmpBackupStatus.NONE

    @property
    def has_pending_backup(self):
        return self.status != TmpBackupStatus.NONE

    def move_files_to_backup(self, files: list[Path]):
        if self.has_pending_backup:
            raise RuntimeError(
                f'Backup directory {self.backup_path} already contains a backup '
                f'(status={self.status}). Recovery should be performed first.'
            )
        self._ensure_mkdir()
        self.partial_sentinel_fp.touch(exist_ok=False)
        files_mapping = {}
        for fp in files:
            tmp_fp = self.backup_path / fp.name
            fp.rename(tmp_fp)
            files_mapping[fp] = tmp_fp

        self.partial_sentinel_fp.rename(self.complete_sentinel_fp)
        return files_mapping

    def cleanup(self):
        if self.status != TmpBackupStatus.COMPLETE:
            raise RuntimeError(f'Cannot cleanup backup with status {self.status}')

        for fp in self.backup_path.iterdir():
            fp.unlink()
        self.backup_path.rmdir()
        return True
