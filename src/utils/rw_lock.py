import asyncio
from contextlib import asynccontextmanager

from enum import Enum

class LockMode(Enum):
    READ = "read"
    WRITE = "write"


class RWLock:
    def __init__(self):
        self._readers = 0
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def _acquire_read(self):
        async with self._read_lock:
            self._readers += 1
            if self._readers == 1:
                await self._write_lock.acquire()

    async def _release_read(self):
        async with self._read_lock:
            self._readers -= 1
            if self._readers == 0:
                self._write_lock.release()

    async def _acquire_write(self):
        await self._write_lock.acquire()

    def _release_write(self):
        self._write_lock.release()
      
    @asynccontextmanager
    async def get_lock(self, mode: LockMode):
        if mode == LockMode.READ:
            await self._acquire_read()
            try:
                yield
            finally:
                await self._release_read()
                return
        if mode == LockMode.WRITE:
            await self._acquire_write()
            try:
                yield
            finally:
                self._release_write()
                return
        raise ValueError('mode must be a valid LockMode object')
            
            
class NoLock:
    @asynccontextmanager
    async def get_lock(self, mode: LockMode=None):
        yield
        
class AsyncLockWrapper:
    def __init__(self):
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def get_lock(self, mode: LockMode=None):
        async with self._lock:
            yield
            
