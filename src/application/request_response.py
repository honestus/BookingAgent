from __future__ import annotations
from enum import Enum


class StructuredRequestError:
    INVALID_REQUEST = 'invalid'
    INPUT_TYPES_ERRORS = 'type_error' ##StructuredRequest attributes dont match expected types. e.g. non_str method, non_dict params.
    MISSING_PARAMETERS = 'missing_parameters'
    UNKNOWN_PARAMETERS = 'extra_parameters'
    PARAMETERS_VALUE_ERROR = 'value_error'
    VALID_REQUEST = 'ok'
    
class StructuredRequest:
    def __init__(self, method: str, params: dict[str: object] = None, missing_params: list[str] = None, extra_params: list[str] = None, user: authenticator.User = None, errors: list[StructuredRequestError] = None):
        object.__setattr__(self, 'method', method)
        object.__setattr__(self, 'params', dict() if params is None else params)
        object.__setattr__(self, 'missing_params', list() if missing_params is None else missing_params)
        object.__setattr__(self, 'extra_params', list() if extra_params is None else extra_params)
        object.__setattr__(self, 'user', user)
        object.__setattr__(self, '_input_errors', list() if errors is None else list(errors))
        self.validate()

    def validate(self):
        errors = getattr(self, '_input_errors', list())
        if not StructuredRequest.validate_method(self.method) or not StructuredRequest.validate_missing_params(self.missing_params) or not StructuredRequest.validate_params(self.params):
            errors.append(StructuredRequestError.INVALID_REQUEST)
        else:
            if self.missing_params:
                errors.append(StructuredRequestError.MISSING_PARAMETERS)
            if self.extra_params:
                errors.append(StructuredRequestError.UNKNOWN_PARAMETERS)

        object.__setattr__(self, 'errors', set(errors))
        return
        
    @property
    def is_valid(self):
        return not self.errors

    def __setattr__(self, attribute, value):
        object.__setattr__(self, attribute, value)
        if attribute in ['method', 'params', 'missing_params', 'extra_params']:
            self.validate()
        return
        
    def __eq__(self, other):
        return self.user==other.user and self.method==other.method and self.params==other.params and set(self.missing_params)==set(other.missing_params) and set(self.extra_params)==set(other.extra_params)     


    def copy(self):
        import copy
        new = object.__new__(type(self))
        new.__dict__ = copy.deepcopy(self.__dict__)
        return new

    @staticmethod
    def validate_method(method):
        return bool(method) and isinstance(method, str)
    
    @staticmethod
    def validate_params(params):
        if not isinstance(params, dict):
            return False
        return all(isinstance(param_name, str) for param_name in params.keys())
        
    @staticmethod
    def validate_missing_params(missing_params):
        if not isinstance(missing_params, list):
            return False
        return all(isinstance(param_name, str) for param_name in missing_params)

        


class ResponseErrorCode(Enum):
    NOT_ALLOWED_ERROR = 'operation_not_allowed'
    INVALID_REQUEST_ERROR = 'invalid_request'
    PARAMETERS_ERROR = 'invalid_input_parameters'
    RUNTIME_ERROR = 'runtime_error'


from dataclasses import dataclass

@dataclass
class StructuredResponse:
    def __init__(self, data: list, error_code: ResponseErrorCode=None, error_msg: str=None, events: list=None, extra_events: list=None):
        #self.request = request
        self.data = data
        self.error_code = error_code
        self.error_msg = error_msg
        self.events = events or []
        self.extra_events = extra_events or []
        
    @property
    def success(self):
        return not bool(self.error_code)