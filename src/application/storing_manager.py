from pathlib import Path
import asyncio
from storage.file_storers import BinaryRecordStorage
from storage.serializers import RecordPickleSerializer


REQUESTS_FILENAME = "requests.jsonl"
MANAGER_FILENAME = "manager.jsonl"

_REQUESTS_STEM, _REQUESTS_SUFFIX = Path(REQUESTS_FILENAME).stem, Path(REQUESTS_FILENAME).suffix
_MANAGER_STEM, _MANAGER_SUFFIX = Path(MANAGER_FILENAME).stem, Path(MANAGER_FILENAME).suffix

class AppStoringManager:
    
   # _backend_manager_serializer : type[RecordSerializer[StructuredRequest]] = RecordPickleSerializer
    _requests_serializer: "RecordSerializer[StructuredRequest]" = RecordPickleSerializer
    _requests_storer = BinaryRecordStorage
    
    
    def __init__(self, requests_filepath: Path, backend_manager_filepath: Path):
        self._requests_filepath = Path(requests_filepath)
        self._requests_filepath.parent.mkdir(parents=True, exist_ok=True)
        self._backend_manager_filepath = Path(backend_manager_filepath)
        self._backend_manager_filepath.parent.mkdir(parents=True, exist_ok=True)
        self._init_archived_shard()
        self._init_n_requests_from_disk()
        self._requests_lock = asyncio.Lock() 
        self._backend_manager_lock = asyncio.Lock()
        
        
    @property
    def n_requests(self):
        return self._n_requests_on_disk
        
    def _init_archived_shard(self):
        from storage.shard_organizer import IntShardOrganizer
        
        archived_dirpath = self._requests_filepath.parent / "archived_requests/"
        
        self._archived_requests_organizer = IntShardOrganizer(dirpath=archived_dirpath, file_stem=self._requests_filepath.stem, suffix=self._requests_filepath.suffix)
        
    def _init_n_requests_from_disk(self):
        from utils.io_utils import get_n_of_lines_file
        self._n_requests_on_disk = 0 if not self._requests_filepath.exists() else get_n_of_lines_file(self._requests_filepath)
    
        
    async def append_request(self, request):        
        serialized_request = AppStoringManager._requests_serializer.encode(request)
        
        async with self._requests_lock:
            self._requests_filepath.touch(exist_ok=True)
            AppStoringManager._requests_storer.write(obj=serialized_request, filepath=self._requests_filepath, overwrite=False)
            self._n_requests_on_disk+=1
        return self._requests_filepath
        
        
    async def load_requests(self):
        import pickle
        async with self._requests_lock:
            encoded_requests = AppStoringManager._requests_storer.read(self._requests_filepath)
        structured_requests = [AppStoringManager._requests_serializer.decode(r) for r in encoded_requests]
        return structured_requests
        
        
    async def archive_requests(self):
        async with self._requests_lock:
            if not self._requests_filepath.exists():
                return False
            new_archived_req_filepath = self._archived_requests_organizer.create_next_file()
            self._requests_filepath.replace(new_archived_req_filepath)
            self._n_requests_on_disk=0
        return new_archived_req_filepath
        
        
    async def store_manager(self, manager):
        from backend import backend_storing_utils
        async with self._backend_manager_lock:
            backend_storing_utils.store_business_core(manager, self._backend_manager_filepath)
        return
        
    
    async def load_manager(self):
        from backend import backend_storing_utils
        async with self._backend_manager_lock:
            return await backend_storing_utils.load_business_core(self._backend_manager_filepath)
        
        
    #async def checkpoint(self):        
    
    
def request_serializer(request):
    import pickle
    return pickle.dumps(request)
    """
    user_attr_dct = {'user_id': request.user.user_id, 'user_role': request.user.user_role.value}
    return json.dumps({k:(v if k!='user' else user_attr_dct) for k,v in request.__dict__.items() if k not in ['_input_errors']}, ensure_ascii=False, default=str)
    """
    
def _request_from_json_dict(json_dict: dict):
    from application.request_response import StructuredRequest
    from application.authenticator import User
    from shared.user_role import validate_role

    json_dict['user'] = User(**{k:(v if k!='user_role' else validate_role(v)) for k,v in json_dict['user'].items()})
    request= StructuredRequest(**{k:v for k,v in json_dict.items() if k not in ['_input_errors']})
    return request