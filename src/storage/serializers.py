from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


import datetime as dt
from enum import Enum


def encode_datetime(value: dt.datetime | None):
    return None if value is None else value.isoformat()


def decode_datetime(value: str):
    return None if value is None else dt.datetime.fromisoformat(value)


def encode_enum(value):
    return None if value is None else value.name
    
def decode_enum(value: str, dest_enum_class: type):
    if value is None:
        return None
    #return value
    return dest_enum_class[value]


class RecordSerializer(ABC, Generic[T]):

    @staticmethod
    @abstractmethod
    def encode(obj):
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def decode(data):
        raise NotImplementedError


class RecordToStringSerializer(RecordSerializer[T], ABC):

    @staticmethod
    @abstractmethod
    def encode(obj: T) -> str:
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def decode(data: str) -> T:
        raise NotImplementedError
        
        
class RecordToBytesSerializer(RecordSerializer[T], ABC):

    @staticmethod
    @abstractmethod
    def encode(obj: T) -> bytes:
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def decode(data: bytes) -> T:
        raise NotImplementedError
        
        
class RecordPickleSerializer(RecordToBytesSerializer[T]):
    
    @staticmethod
    def encode(obj: T) -> bytes:
        import pickle
        return pickle.dumps(obj)
        
    @staticmethod
    def decode(data: bytes) -> T:
        import pickle
        return pickle.loads(data)
        
        
        

        
        
