from __future__ import annotations
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class RecordStorage(ABC,Generic[T],):

    @staticmethod
    @abstractmethod
    def write(filepath: Path, obj: T, overwrite: bool = False) -> None:
        """
        Append a single record to filepath.
        """
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def write_collection(filepath: Path, objs: list[T], overwrite: bool = False) -> None:
        """
        Append multiple records to filepath.
        """
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def read(filepath: Path) -> list[T]:
        """
        Read all records contained in filepath.
        """
        raise NotImplementedError


class StringRecordStorage(RecordStorage[str]):
    @staticmethod
    def write(filepath: Path, obj: str, overwrite: bool = False) -> None:
        write_mode = 'w' if overwrite else 'a'
        with open(filepath, write_mode) as f:
            f.write(obj + "\n")
    
    @staticmethod
    def write_collection(filepath: Path, objs: list[str], overwrite: bool = False) -> None:
        write_mode = 'w' if overwrite else 'a'
        with open(filepath, write_mode) as f:
            f.writelines([o + "\n" for o in objs])

    @staticmethod
    def read(filepath: Path) -> list[T]:
        if not filepath.exists():
            return []
        with open(filepath, "r") as f:
            return f.readlines()
            
            
            
import pickle

class BinaryRecordStorage(RecordStorage[bytes]):
    
    _LENGTH_BYTES = 8
    
    @staticmethod
    def write(filepath: Path, obj: bytes, overwrite: bool = False) -> None:
        write_mode = 'wb' if overwrite else 'ab'
        
        obj_len = len(obj)
        with open(filepath, write_mode) as f:
            f.write(obj_len.to_bytes(BinaryRecordStorage._LENGTH_BYTES))
            f.write(obj)
            
    @staticmethod
    def write_collection(filepath: Path, objs: list[bytes], overwrite: bool = False) -> None:
        write_mode = 'wb' if overwrite else 'ab'
        with open(filepath, write_mode) as f:
            for obj in objs:
                obj_len = len(obj)
                f.write(obj_len.to_bytes(BinaryRecordStorage._LENGTH_BYTES))
                f.write(obj)
    
    @staticmethod
    def read(filepath: Path,) -> list[bytes]:
        if not filepath.exists():
            return []
        
        records = []
        with open(filepath, "rb") as f:
            while True:
                try:
                    next_obj_len_bytes = f.read(BinaryRecordStorage._LENGTH_BYTES)
                    if not next_obj_len_bytes:
                        break
                    if len(next_obj_len_bytes)!=BinaryRecordStorage._LENGTH_BYTES:
                        raise IOError('Corrupted file structure. Invalid len bytes')
                    next_obj_len = int.from_bytes(next_obj_len_bytes)
                    next_obj = f.read(next_obj_len)
                    if len(next_obj)!=next_obj_len:
                        raise IOError('Corrupted file structure. Incomplete record.')
                    
                    records.append(next_obj)
                except EOFError:
                    break

        return records