import re, json
import asyncio
import aiofiles
from pathlib import Path, WindowsPath
from filelock import FileLock
from collections.abc import Callable
from collections import defaultdict
from typing import Any
from chat_system.message_responses import ReceivedMessage, BotResponse, normalize_id
from enum import Enum


class ErrorPolicy(Enum):
    EXCLUDE = "exclude"
    INCLUDE = "include"
    FUTURE_ONLY = "future_only"
"""
Invariant 1: update_id appears either in raw messages OR in exactly one batch
Invariant 2: batches are immutable once created
Invariant 3: response_id ordering defines causal order
"""  

class DiskDirType(Enum):
    USER_DEFAULT = "user_{user_id}/"
    USER_STATE = "user_{user_id}/state/"
    USER_UNPROCESSED = "unprocessed/user_{user_id}"
    
class UserFileType(Enum):
    MESSAGES = 'messages'
    PROCESSED_RESPONSES = 'processed_responses'
    SENT_RESPONSES = 'sent_responses'
    PROCESS_ERRORS = 'process_errors'
    PROCESS_ERRORS_RESOLVED = 'process_errors_solved'
    SEND_ERRORS = 'send_errors'
    SEND_ERRORS_RESOLVED = 'send_errors_solved'
    METADATA = 'metadata'
    
class LOAD_TYPE(Enum):
    SUCCESSFUL_ONLY = 0
    ERRORS_ONLY = 1
    ALL = 2

__JSON_CONVERTERS__ = {
    Path: str,
    set: list,
    WindowsPath: str
}
def _json_convert(v):
    if type(v) not in __JSON_CONVERTERS__:
        return v
    return __JSON_CONVERTERS__[type(v)](v)

def _jsonl_serializer(obj):
    return json.dumps({k:_json_convert(v) for k,v in obj.__dict__.items() if not k.startswith('_')}, ensure_ascii=False, default=str)

def _str_serializer(obj):
    return str(obj)+'\n'
            
filetypes_scheme = {
    UserFileType.MESSAGES: ('jsonl', _jsonl_serializer, json.loads, DiskDirType.USER_DEFAULT),
    UserFileType.PROCESSED_RESPONSES: ('jsonl', _jsonl_serializer, json.loads, DiskDirType.USER_DEFAULT),
    UserFileType.SENT_RESPONSES: ('txt', _str_serializer, str, DiskDirType.USER_DEFAULT),
    UserFileType.PROCESS_ERRORS: ('jsonl', _jsonl_serializer, json.loads, DiskDirType.USER_DEFAULT),
    UserFileType.PROCESS_ERRORS_RESOLVED: ('txt', _str_serializer, str, DiskDirType.USER_DEFAULT),
    UserFileType.SEND_ERRORS: ('txt', _str_serializer, str, DiskDirType.USER_DEFAULT),
    UserFileType.SEND_ERRORS_RESOLVED: ('txt', _str_serializer, str, DiskDirType.USER_DEFAULT),
    UserFileType.METADATA: ('jsonl', _jsonl_serializer, json.loads, DiskDirType.USER_STATE),
}



LAST_UPDATE_DIRNAME_PREFIX = "last_processed_update_"
LAST_RESPONSE_DIRNAME_PREFIX = "last_sent_response_"

all_users_format = r"([A-Za-z0-9]+)"
filename_pattern_str = r"^user_{user_id}_{filename}(?:_(\d+))?\.{file_format}$"
allusers_pattern_str = filename_pattern_str.format(user_id=all_users_format, filename="{filename}", file_format="{file_format}")
userid_pattern = re.compile(r"^(?:user_)([A-Za-z0-9]+)(?:_|$)")

MAX_FILE_LINES = 500

    
        
def get_all_user_ids(base_dir: Path):
    """
    Returns the set of all of the user_ids who have a folder stored on disk
    """
    user_ids = set()
    for x in Path(base_dir).iterdir():
        """curr_match = allusers_messages_pattern.match(x.name)
        if not curr_match:
            continue
        user_id, file_number = curr_match.groups()
        user_ids.add(user_id)
        """
        if x.is_dir() and (id_match:=userid_pattern.match(x.name)):
            user_id = id_match.group(1)
            user_ids.add(user_id)
    
    return user_ids
    


def get_all_users_files(base_dir: Path , filetype: UserFileType = None, ):
    """
    Returns the dict {user_id: list_of_files} containing all the user_ids who have a file (of type @filetype) stored on disk (e.g. user_userid_message.jsonl)
    """
    user_files = dict()
    if filetype is None:
        filetype = list(UserFileType)
    else:
        filetype = [filetype]
    for subpath in Path(base_dir).iterdir():
        if not subpath.is_dir() or not (id_match:=userid_pattern.match(subpath.name)):
            continue
        user_id = id_match.group(1)
        user_files[user_id] = dict()
        for ft in filetype:
            user_files[user_id][ft] = __get_matching_files__(dirpath=subpath, filetype=ft, user_id=user_id)
    
    return user_files


def get_user_files(user_id: str, base_dir: Path, filetype: UserFileType = None, ):
    """
    Returns the list_of_filetype_files stored on disk for user_id (e.g. [user_userid_message_0.jsonl, user_userid_message_1.jsonl])
    """
    if filetype is None:
        filetypes = list(UserFileType)
    else:
        filetypes = [filetype]
    
    user_files_by_type = {ft: [filename for filename, filenumb in __get_matching_files__(user_id=user_id, dirpath=_get_user_dir(user_id=user_id, dirtype=filetypes_scheme[ft][3], base_dir=base_dir), filetype=ft)] for ft in filetypes}
    if filetype is not None:
        return user_files_by_type[filetype]
    return user_files_by_type
        


def get_n_of_lines_file(filepath: Path):
    def _make_gen(reader):
        while True:
            b = reader(2 ** 16)
            if not b: break
            yield b

    with open(filepath, "rb") as f:
        count = sum(buf.count(b"\n") for buf in _make_gen(f.raw.read))
    return count




async def _await_coroutine_with_optional_filelock(coroutine, filepath: Path, use_filelock: bool,):
    if not use_filelock:
        return await coroutine()

    lock = FileLock(str(filepath) + ".lock")
    with lock:
        return await coroutine()


async def _load_from_disk(filepath: Path, reader_fn: Callable[[Any], str], use_filelock: bool = False) -> list:
    async def _load():
        items = []
        if not filepath.exists():
            return items
        async with aiofiles.open(filepath, "r") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(reader_fn(line))
                except Exception as e:
                    continue
        return items
    
    return await _await_coroutine_with_optional_filelock(_load, filepath, use_filelock,)


async def _store_obj_to_disk_queue(obj, serializer: Callable[[Any], str], filepath: Path, overwrite: bool = False, use_filelock: bool = True):
    async def _write(serialized_objects, filepath, mode):
        async with aiofiles.open(filepath, mode) as f:
            await f.writelines(serialized_objects)
    
    if not isinstance(obj, (set, list)):
        obj = [obj]
    
    if not filepath.exists():
        filepath.parent.mkdir(exist_ok=True, parents=True)
        filepath.touch()
    
    serialized_objects = [serializer(o) for o in obj]
    
    write_mode = 'w' if overwrite else 'a'
    if (is_binary := isinstance(serialized_objects[0], bytes)):
        write_mode+='b'
    
    await _await_coroutine_with_optional_filelock(_write(serialized_objects, filepath, write_mode), filepath=filepath, use_filelock=use_filelock)
    return filepath
    

    
async def append_line_to_user_file(obj, user_id: str, filetype: UserFileType, base_dir: Path ):
    import math
    if not isinstance(obj, (set, list)):
        obj = [obj]
    if not obj:
        return
    current_path = _get_last_user_file(user_id=user_id, filetype=filetype, base_dir=base_dir)
    serializer = filetypes_scheme[filetype][1]
    if not current_path: 
        current_path = _create_new_user_file(user_id=user_id, filetype=filetype, base_dir=base_dir)
    last_file_lines_left = MAX_FILE_LINES - get_n_of_lines_file(current_path)
    needed_files = bool(obj) + math.ceil(max(0, len(obj) - last_file_lines_left) / MAX_FILE_LINES)
    files_mapping = {current_path: obj[:last_file_lines_left]}
    curr_ind = last_file_lines_left
    for _ in range(1, needed_files):
        fp = _create_new_user_file(user_id=user_id, filetype=filetype, base_dir=base_dir) 
        files_mapping[fp] = obj[curr_ind:curr_ind+MAX_FILE_LINES]
        curr_ind+=MAX_FILE_LINES
    
    return await asyncio.gather(* [_store_obj_to_disk_queue(obj=o, serializer=serializer, filepath=p, overwrite=False, use_filelock=True) for p,o in files_mapping.items()])


async def read_user_files(user_id: str, filetype: UserFileType, base_dir: Path, last_only: bool = False, use_filelock: bool = False):
    files_paths = get_user_files(user_id=user_id, filetype=filetype, base_dir=base_dir)
    if not files_paths:
        return []
    if last_only:
        files_paths = [files_paths[-1]]
    read_fn = filetypes_scheme[filetype][2]

    single_files_tasks = [_load_from_disk(filepath=p, reader_fn=read_fn, use_filelock=use_filelock) for p in files_paths]
    results = await asyncio.gather(*single_files_tasks)
    return [single_line for lst in results for single_line in lst]


def store_user_as_unprocessed(user_id: str, base_dir: Path):
    user_unprocessed_dir = _get_user_dir(user_id=user_id, dirtype=DiskDirType.USER_UNPROCESSED, base_dir=base_dir)
    user_unprocessed_dir.mkdir(parents=True, exist_ok=True)
    return

def store_user_as_processed(user_id: str, base_dir: Path):
    user_unprocessed_dir = _get_user_dir(user_id=user_id, dirtype=DiskDirType.USER_UNPROCESSED, base_dir=base_dir)
    try:
        user_unprocessed_dir.rmdir()
    finally:
        return
    
def is_user_unprocessed_ondisk(user_id: str, base_dir: Path):
    return _get_user_dir(user_id=user_id, dirtype=DiskDirType.USER_UNPROCESSED, base_dir=base_dir).exists()
    
async def has_user_unsent_responses_ondisk(user_id: str, base_dir: Path, include_errors: bool = False, ):
    user_msg_file = _get_user_filename(user_id=user_id, filetype=UserFileType.MESSAGES, base_dir=base_dir) 
    user_response_file = _get_user_filename(user_id=user_id, filetype=UserFileType.PROCESSED_RESPONSES, base_dir=base_dir)
    if get_n_of_lines_file(user_msg_file)!=get_n_of_lines_file(user_response_file):
        return True
    return bool (await get_unsent_responses_fromdisk(user_id = user_id, include_errors = include_errors, base_dir = base_dir))
 

def _get_user_dir(user_id: str, dirtype: DiskDirType, base_dir: Path):
    curr_user_dir = dirtype.value.format(user_id=user_id)
    return base_dir.joinpath(curr_user_dir)


def _get_user_filename(user_id: str, filetype: UserFileType, base_dir: Path ):
    user_dir = _get_user_dir(user_id=user_id, dirtype=filetypes_scheme[filetype][3], base_dir=base_dir)
    return user_dir.joinpath(f"user_{user_id}_{filetype.value}.{filetypes_scheme[filetype][0]}")
    

def _get_last_user_file(user_id: str, filetype: UserFileType, base_dir: Path ):
    curr_files = get_user_files(user_id=user_id, filetype=filetype, base_dir=base_dir)
    return curr_files[-1] if curr_files else None
    
    
def _create_new_user_file(user_id: str, filetype: UserFileType, base_dir: Path ):
    current_last_filenumber = __get_last_filenumber__(user_id=user_id, filetype=filetype, dirpath=_get_user_dir(user_id=user_id, dirtype=filetypes_scheme[filetype][3], base_dir=base_dir))
    filename = _get_user_filename(user_id=user_id, filetype=filetype, base_dir=base_dir)
    curr_dir = filename.parent
    new_file = curr_dir.joinpath(f"{filename.stem}_{current_last_filenumber+1}{filename.suffix}")
    if not curr_dir.exists():
        curr_dir.mkdir(exist_ok=True, parents=True)
    new_file.touch()
    return new_file
    
    
def __get_matching_files__(dirpath: Path, filetype: UserFileType, user_id: str = None) -> list[tuple[str,str]]:
    """ Returns all the files of type "filetype" in the current dirpath directory. 
    If user_id is not None, will look for all of the files of type user_userid_filetype.fileformat.
    Otherwise will look for all of the user_%s_filetype.fileformat files
    """
    if not dirpath.exists():
        return []
    if user_id is None:
        user_id = all_users_format
    curr_pattern = re.compile(filename_pattern_str.format(user_id=user_id, filename=filetype.value, file_format=filetypes_scheme[filetype][0]))
    sorted_matching_files = sorted( 
        [(f,match.groups()[-1]) 
             for f in dirpath.iterdir() if (match:=curr_pattern.match(f.name))
        ], 
    key=lambda x: (int(x[1]) if x[1] else -1, x[0].name)
    )

    return sorted_matching_files #list of tuples: [(filename, filenumber)]    
    
    
def __get_last_filenumber__(dirpath: Path, filetype: UserFileType, user_id: str = None):
    last_file = -1
    curr_files = __get_matching_files__(user_id=user_id, filetype=filetype, dirpath=dirpath)
    if curr_files and curr_files[-1][1]:
        last_file = int(curr_files[-1][1])
    return last_file
    
def _get_filenumber_from_fileshard_path_(filepath: Path):
    return int (str(filepath).split(filepath.suffix)[0].split('_')[-1])