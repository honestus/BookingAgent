from utils import parsing_utils
from shared.globals_shared import *
from application.request_response import StructuredRequest
from typing import Any


_single_request_expected_keys = [METHOD_ATTRIBUTE, PARAMS_ATTRIBUTE, MISSING_PARAMS_ATTRIBUTE, USER_ATTRIBUTE]
_single_request_default_values = {PARAMS_ATTRIBUTE: {}, MISSING_PARAMS_ATTRIBUTE: [], USER_ATTRIBUTE: None}


def get_requests_from_raw_dict(raw_dict: dict):
    return raw_dict.get(REQUEST_ATTRIBUTE, {})
    
    
def _validate_request_dict_structure_with_defaults(single_request_dict: dict[str, Any], only_expected_keys: bool = False) -> dict[str, Any]:
    method = single_request_dict.get(METHOD_ATTRIBUTE, '')
    user = single_request_dict.get(USER_ATTRIBUTE, None)
    params = single_request_dict.get(PARAMS_ATTRIBUTE , _single_request_default_values[PARAMS_ATTRIBUTE])
    missing_params = single_request_dict.get(MISSING_PARAMS_ATTRIBUTE , _single_request_default_values[MISSING_PARAMS_ATTRIBUTE])

    validated_dct = {
        METHOD_ATTRIBUTE: method,
        USER_ATTRIBUTE: user,
        PARAMS_ATTRIBUTE: params,
        MISSING_PARAMS_ATTRIBUTE: missing_params
    }
    return validated_dct if only_expected_keys else single_request_dict|validated_dct
    

def map_request_dict_to_default_structure(single_request_dict: dict[str, Any]) -> dict[str, Any]:
    single_request_dict = _single_request_default_values | single_request_dict
        
    missing_request_keys = [k for k in _single_request_expected_keys if k not in single_request_dict]
    if missing_request_keys:
        raise KeyError(f'Invalid request; missing keys:  {missing_request_keys}')
        
    only_expected_keys_dict = {k:v for k,v in single_request_dict.items() if k in _single_request_expected_keys}
    return only_expected_keys_dict
        
   
    
def dict_to_structured_request(request_dict: dict, params_types_defaults_dict: dict = None) -> StructuredRequest:
    from utils.general_utils import map_dict_to_lower_keys
    from application.request_response import StructuredRequestError

    # --- validazione struttura grezza ---
    if not isinstance(request_dict, dict):
        sr = StructuredRequest(method='', actor=None, user=None, errors=[StructuredRequestError.INVALID_REQUEST])
        raise MappingError("Invalid input structure. Input is not a dict.", sr, MappingError.ErrorType.INVALID_STRUCTURE)

    try:
        request_dict = map_request_dict_to_default_structure(request_dict)
    
    except KeyError as e:
        request_dict = _validate_request_dict_structure_with_defaults(request_dict, only_expected_keys=True)
        sr = StructuredRequest(
            method=request_dict[METHOD_ATTRIBUTE],
            params=request_dict[PARAMS_ATTRIBUTE],
            missing_params=request_dict[MISSING_PARAMS_ATTRIBUTE],
            user=request_dict[USER_ATTRIBUTE],
            errors=StructuredRequestError.INVALID_REQUEST
        )
        raise MappingError(e.args[0], sr, MappingError.ErrorType.MISSING_KEYS)

    # --- normalize method ---
    try:
        request_dict[METHOD_ATTRIBUTE] = normalize_method(request_dict[METHOD_ATTRIBUTE])
    except (TypeError, ValueError):
        request_dict = _validate_request_dict_structure_with_defaults(request_dict, only_expected_keys=True)
        sr = StructuredRequest(
            method='',
            params=request_dict[PARAMS_ATTRIBUTE],
            missing_params=request_dict[MISSING_PARAMS_ATTRIBUTE],
            user=request_dict[USER_ATTRIBUTE],
            errors=[StructuredRequestError.INPUT_TYPES_ERRORS]
        )
        raise MappingError("Invalid method type", sr, MappingError.ErrorType.WRONG_KEY_TYPES)

    # --- normalize params ---
    try:
        request_dict[PARAMS_ATTRIBUTE] = map_dict_to_lower_keys(request_dict[PARAMS_ATTRIBUTE], force_non_str_keys=True)
    except (TypeError, ValueError):
        sr = StructuredRequest(
            method=request_dict[METHOD_ATTRIBUTE],
            params=request_dict[PARAMS_ATTRIBUTE],
            missing_params=request_dict[MISSING_PARAMS_ATTRIBUTE],
            user=request_dict.get(USER_ATTRIBUTE),
            errors=[StructuredRequestError.INPUT_TYPES_ERRORS]
        )
        raise MappingError("Wrong params structure", sr, MappingError.ErrorType.INVALID_PARAM_TYPE)

    if params_types_defaults_dict:
        try:
            request_dict[PARAMS_ATTRIBUTE] = normalize_params_dict(request_dict[PARAMS_ATTRIBUTE], params_types_defaults_dict)
            
            needed_params = [p for p, param_details in params_types_defaults_dict.items() if param_details.get('required', None) is True or (param_details.get('required', None) is not False and 'default' not in param_details)]
            #PROPERLY SETTING MISSING PARAMS BY USING THE EXPECTED TYPES
            request_dict[MISSING_PARAMS_ATTRIBUTE] = [p for p in needed_params if p not in request_dict[PARAMS_ATTRIBUTE]]
        
        except (TypeError, ValueError):
            sr = StructuredRequest(
                method=request_dict[METHOD_ATTRIBUTE],
                params=request_dict[PARAMS_ATTRIBUTE],
                missing_params=request_dict[MISSING_PARAMS_ATTRIBUTE],
                user=request_dict.get(USER_ATTRIBUTE),
                errors=[StructuredRequestError.PARAMETERS_VALUE_ERROR]
            )
            raise MappingError("Invalid parameter types", sr, MappingError.ErrorType.WRONG_VALUES)

    else:
        #REMOVING MISSING PARAMS THAT ARE ACTUALLY NOT MISSING (I.e. they are in params)
        request_dict[MISSING_PARAMS_ATTRIBUTE] = [p for p in set(request_dict[MISSING_PARAMS_ATTRIBUTE]) if p not in request_dict[PARAMS_ATTRIBUTE]]
    
    return StructuredRequest(
        method=request_dict[METHOD_ATTRIBUTE],
        params=request_dict[PARAMS_ATTRIBUTE],
        missing_params=request_dict[MISSING_PARAMS_ATTRIBUTE],
        user=request_dict.get(USER_ATTRIBUTE)
    )
    
    
    
def normalize_method(method: str):
    if not StructuredRequest.validate_method(method):
        raise TypeError('Invalid method value in input.')
    return str(method).lower()
    
def normalize_params_dict(params: dict[str, Any], params_types_defaults_dict: dict[str, dict[str, Any]]):
    """
    params_types_defaults_dict structure -> {param_name: {'type': type, 'default': default_value} }
    """
    import datetime
    
    if not StructuredRequest.validate_params(params):
        raise TypeError('Invalid params structure in input.')
    if (params.keys() - params_types_defaults_dict.keys()):
        raise ValueError('Params mismatch from the input expected_types')
    params_types_defaults_dict = {k:v for k,v in params_types_defaults_dict.items() if k in params}
        
    expected_types, default_values = {}, {}
    for param_name, param_dict in params_types_defaults_dict.items():
        if 'type' in param_dict:
            expected_types[param_name] = param_dict['type']
        if 'default' in param_dict:
            default_values[param_name] = param_dict['default']
    
    params_equal_to_default  = {
        param_name : param_default_value 
            for param_name, param_default_value in default_values.items() 
                if params[param_name]==param_default_value
    }
    params = {
        param_name : param_value 
            for param_name, param_value in params.items() 
                if param_name not in params_equal_to_default 
    }
    
    params = parsing_utils.cast_data(data=params, schema=expected_types, map_keys_to_lower=False, map_keys_to_str=False, drop_unexpected=True)
    for param_name in params:
        if expected_types[param_name]==datetime.datetime:
            params[param_name] = _cast_datetime_attribute(params[param_name])
        elif param_name in ['service_name', 'new_service_name', 'old_service_name']:
            continue
            #params[param_name] = params[param_name].lower()
    
    return params_equal_to_default | params
    
def _cast_datetime_attribute(value):
    import datetime
    from utils.cast_utils import cast_str_to_datetime
    if isinstance(value, str):
        try:
            value = cast_str_to_datetime(value)
        except:
            raise
    if not isinstance(value, datetime.datetime):
        raise TypeError(f'Cannot cast {value} to datetime')
    return value
    
def _cast_id_attribute(value):
    return str(value)
    
    
class MappingError(Exception):
    from enum import Enum
    
    class ErrorType(Enum):
        INVALID_STRUCTURE = 'invalid'
        MISSING_KEYS = 'missing_keys'
        WRONG_KEY_TYPES = 'wrong_keys'
        WRONG_VALUES = 'wrong_values'
        INVALID_PARAM_TYPE = 'wrong_params'
        
    
    def __init__(self, message: str, structured_request: StructuredRequest, error_type: ErrorType=None):
        super().__init__(message, structured_request)
        self.structured_request = structured_request  # accesso comodo oltre a e.args[1]