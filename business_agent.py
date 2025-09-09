import inspect, re
from typing import Any, Callable, Union

def get_all_method_names(obj):
    return [m[0] for m in inspect.getmembers(obj, predicate=inspect.ismethod)]

def has_param(obj, method_name: str, param: str):
    method = validate_method(obj, method_name)
    if not method:
        raise AttributeError(f"{obj.__class__.__name__} has no method '{method_name}'")
    return param in inspect.signature(method).parameters

def validate_method(obj, method_name: str):
    method = getattr(obj, method_name, None)
    if method is None:
        return False
    return method


def method_to_dict(
    obj,
    method_name: str,
    exclude: Union[list[str], str, Callable[[str], bool], None] = None,
    default_values: dict[str, Any] = {}
) -> dict[str, dict[str, Any]]:
    """
    Convert method signature into a dict of:
    { 'method': method_name,
    'params': {param_name: {"type": type, "default": value (only if defined)} }
    }
    the "exclude" parameter is used to explicitly exclude method parameters from the final dict
    exclude can be:
      - list of parameters to exclude -> e.g. ["p1", "arg2"]
      - regex pattern -> e.g. r"^p1"
      - callable -> e.g. lambda name: name.startswith("p1")
      - None -> no exclusion
    """

    method = validate_method(obj, method_name)
    if not method:
        raise AttributeError(f"{obj.__class__.__name__} has no method '{method_name}'")

    sig = inspect.signature(method)

    # normalize exclude
    if isinstance(exclude, str):
        regex = re.compile(exclude)
        exclude_fn = lambda name: regex.search(name)
    elif callable(exclude):
        exclude_fn = exclude
    elif isinstance(exclude, (list, set)):
        exclude_set = set(exclude)
        exclude_fn = lambda name: name in exclude_set
    else:
        exclude_fn = lambda name: False

    result = {method_name: {}}
    for name, param in sig.parameters.items():
        if exclude_fn(name):
            continue
        # type annotation
        param_type = param.annotation if param.annotation is not inspect._empty else Any
        if hasattr(param_type, "__name__"):
            param_type = param_type.__name__

        entry = {"type": param_type}

        # default value
        if name in default_values:
            entry["default"] = default_values[name]
        elif param.default is not inspect._empty:
            entry["default"] = param.default

        result[method_name][name] = entry
    return result


def method_dict_to_str(method_dict: dict[str, dict[str, Any]]) -> str:
    """
    Convert method dict into method string representation. 
    i.e. from {'method':method_name, 'params':{p_name:(p_type, p_default)}} to -> "method_name(p_name: p_type=p_default)"
    """
    if len(key_lst := list(method_dict.keys()))!=1:
        raise TypeError('method_dict must contain exactly one key, which is the method name itself')
    method_name = key_lst[0]
    params_dct, params_str = method_dict[method_name], []
    for name, info in params_dct.items():
        param_type = info["type"]
        if "default" in info:
            params_str.append(f"{name}: {param_type} = {repr(info['default'])}")
        else:
            params_str.append(f"{name}: {param_type}")

    return f"{method_name}({', '.join(params_str)})"
    
    
    
    
import datetime
from datetimes_utils import map_datetime_to_next_slot_datetime
from globals_shared import *
from business_manager import BusinessManager, BusinessManagerWithConfirmation
from booking_errors import *
from collections import defaultdict

final_operations_mapping = {'make_reservation':'confirm_reserve', 
                            'update_reservation': 'confirm_update',
                            'update_reservation_by_time': 'confirm_update',
                            'cancel_reservation': 'confirm_cancel', 
                            'cancel_reservation_by_time': 'confirm_cancel',
                           'add_service': 'confirm_add_service',
                           'update_service': 'confirm_update_service',
                           'remove_service': 'confirm_remove_service'}
PENDING = 'W'
SUCCESS = 'S'
FAILED_CONFLICT = 'F_C'
FAILED_INVALID = 'F_I'
FAILED_POLICY = 'F_P'
ERROR = 'E'


    
    
class BusinessAgent:
    def __init__(self, business_manager: BusinessManager):
        object.__setattr__(self, 'business_manager', business_manager)
        
        self.status = PENDING
        self.request = defaultdict(list)
        self.__init_exposed_methods__()

    def set_status(self, status):
        object.__setattr__(self, 'status', self.__validate_status__(status))

    def get_status(self):
        return self.status

    def make_action(self, request, force_role=False):
        """
        Validates request based on the user who sends it (i.e. only allows to run methods "exposed" to the user/admin).
        If 'user' is needed in the called method, validates 'user' by the user sent in the request from the NLUAgent, otherwise sets it to ''(i.e. not admin).
        Builds the request and sends it to the business manager.
        Returns the response as tuple: (bool, business_manager_response_from_method)
        """
        if not request.get(METHOD_ATTRIBUTE):
            response = TypeError(f'{METHOD_ATTRIBUTE} must be provided')
            self.__update_status__(response)
            return (False, response, TypeError(''), '')
        if not force_role and request.get(USER_ATTRIBUTE)=='system':
            return (False, response, PermissionError('Not allowed role for user'), '')

        current_user_available_methods = self.get_exposed_methods(request.get(USER_ATTRIBUTE, ''))
        #all_available_methods = self.get_exposed_methods('system') + current_user_available_methods
        if request.get(METHOD_ATTRIBUTE) not in current_user_available_methods:
            response = PermissionError('')
            self.__update_status__(response)
            return (False, response, PermissionError('Operation not allowed'), '')
        user_role = self._map_user_to_role(request.get(USER_ATTRIBUTE, ''))
        user_param_needed = 'user' in self.__exposed_methods_params__[user_role][request[METHOD_ATTRIBUTE]]
        if user_param_needed and 'user' not in request.get(PARAMS_ATTRIBUTE, []): ##adding user if needed as param since it was "hidden" in methods shown to llm
            request[PARAMS_ATTRIBUTE]['user'] = request.get(USER_ATTRIBUTE, '')
        print(request)
        output_extra_msg = ''
        if (user_method_request:=request[METHOD_ATTRIBUTE]) in final_operations_mapping:
            corresponding_confirmation_method = final_operations_mapping[user_method_request]
            confirmation_request = request.copy()
            confirmation_request[METHOD_ATTRIBUTE] = corresponding_confirmation_method
            confirmation_request[USER_ATTRIBUTE] = 'system'
            """
            if user_method_request.endswith('_service'):
                confirmation_request[PARAMS_ATTRIBUTE]['operation']=user_method_request.split('_service')[0]
            """
            confirmation_action_response = self.make_action(request=confirmation_request, force_role=True)
            if confirmation_action_response[1]:
                return confirmation_action_response
                
        """
        if request.get(USER_ATTRIBUTE):
            self.request[request[USER_ATTRIBUTE]].append({k:request.get(k) for k in [METHOD_ATTRIBUTE, PARAMS_ATTRIBUTE]})
        """
        try:
            request_str = self.__build_method_request__(request)
            response = eval(request_str)
        except Exception as e:
            response = e
        finally:
            self.__update_status__(response)
            if self.status==SUCCESS:
                if output_extra_msg:
                    output_extra_msg+=f"The {user_method_request}(**{user_params_request}) can be performed. \
                    Ask the user if he wants to proceed by including the details of this method in human friendly language."
                if request[METHOD_ATTRIBUTE] in final_operations_mapping:
                    self.request[request[USER_ATTRIBUTE]]=[]
        print('\nPerforming booking-manager action...', request, '\n')
        return (request, not isinstance(response, Exception), response, output_extra_msg)

    def set_request(self, request):
        self.request=request

    def __validate_status__(self, status):
        if status.upper() not in [PENDING, SUCCESS, FAILED_CONFLICT, FAILED_INVALID, FAILED_POLICY, ERROR]:
            raise ValueError(f"Status must be one of: [{PENDING}, {SUCCESS}, {FAILED_CONFLICT}, {FAILED_POLICY}, {FAILED_INVALID}, {WRONG_METHOD}, {ERROR}]")
        return status.upper()
        
    def __update_status__(self, response):
        """Based on the response dict, updates status, output message and error_type attributes
        """
        print(response, type(response))

        if not isinstance(response, Exception):
            self.status = SUCCESS
            self.output_msg = response
            self.error_type = None
            return

        self.error_type = type(response)
        self.output_msg = response
        if isinstance(response, PolicyError):
            self.status = FAILED_POLICY
            return
        if isinstance(response, (ClosingTimeError, AlreadyBookedError)):
            self.status = FAILED_CONFLICT
            return
        if isinstance(response, NotPreviouslyBookedError):
            self.status = FAILED_INVALID
            return
        if isinstance(response, (TypeError, ValueError)):
            self.status = FAILED_INVALID
            return
        self.status = ERROR

    def __build_method_request__(self, request):
        """Builds the string to eval in order to call the proper method on the business_manager"""
        req_str = f"self.business_manager.{request[METHOD_ATTRIBUTE]}(**{request.get(PARAMS_ATTRIBUTE, {})})"
        return req_str

    def __init_exposed_methods__(self):
        """Creates the exposed_methods dict.
        I.e. each method available in the business manager together with its parameters names, types and defaults 
        """
        user_exposed_methods = self.get_exposed_methods('user')
        admin_exposed_methods = self.get_exposed_methods('admin')
        system_exposed_methods = self.get_exposed_methods('system')
        user_methods_dct, admin_methods_dct, system_methods_dct =  {}, {}, {}
        for method in user_exposed_methods:# + system_exposed_methods:
            user_methods_dct.update(method_to_dict(obj=self.business_manager, method_name=method, 
                                                  default_values={'user':''})
                                   )
        
        for method in admin_exposed_methods:
            admin_methods_dct.update(method_to_dict(obj=self.business_manager, method_name=method)
                                    )

        for method in system_exposed_methods:
            system_methods_dct.update(method_to_dict(obj=self.business_manager, method_name=method)
                                    )
        object.__setattr__(self, '__exposed_methods_params__',
                       {'user': user_methods_dct, 
                        'admin': admin_methods_dct,
                       'system': system_methods_dct}
                          )

    
    def get_exposed_methods(self, user):
        """
        Returns the methods that a user(i.e. user or admin) can call/run
        """

        if user=='system':
            system_exposed_methods = ['get_default_opening_hours', 'get_user_reservations', 'get_available_services', 
                               'can_cancel', 'can_update', 'can_reserve', 
                               'can_add_service', 'can_update_service', 'can_remove_service']
            if isinstance(self.business_manager, BusinessManagerWithConfirmation):
                system_exposed_methods += ['confirm_reserve', 'confirm_cancel', 'confirm_update', 'confirm_add_service', 'confirm_remove_service', 'confirm_update_service']
            return system_exposed_methods
            
        
        exposed_methods = ['make_reservation', 'cancel_reservation', 'cancel_reservation_by_time',
                        'update_reservation', 'update_reservation_by_time',
                        'get_available_datetimes', 'get_daily_opening_hours',
                       'is_available']
        
        if user=='admin':
            exposed_methods += ['get_daily_reservations', 'get_all_reservations', 'get_user_reservations',
                            'get_available_services','add_service', 'remove_service', 'update_service', 
                             'get_default_opening_hours', 'add_new_calendar', 'remove_time_from_calendar',
                            ]            
            
        return exposed_methods
    
    def get_exposed_methods_params(self, user, as_string=False):
        """
        Returns the methods/parameters to expose to user, by excluding parameters that are in such methods but that dont get shown to external callers.
        This way it avoid not-allowed operations, such as booking for other users, deleting other users' cancelations, booking on past timeslots etc.
        Returns the output as dict: {method_name: {param_name: {'type':type, 'default_value':value} }}.
        If as_string, returns the output as list [method_name (param_name: param_type = default_value)] (i.e. same as method definition in python)
        """
        exposed_methods = self.get_exposed_methods(user)
        user_role = self._map_user_to_role(user)
        if user_role=='user':
            exclude=lambda x: 'force' in x or 'user' in x or x=='minutes_duration'
        else:
            exclude=lambda x: 'force' in x
        all_filtered_methods_dct = {method_name: {param_k: v for param_k,v in method_params_dct.items() if not exclude(param_k)}
                                for method_name, method_params_dct in self.__exposed_methods_params__[user_role].items()
                                    if method_name in exposed_methods
                               }
        if not as_string:
            return all_filtered_methods_dct
        
        return [method_dict_to_str({k:v}) for k,v in all_filtered_methods_dct.items()]
            

    def __setattr__(self, attr, value):
        if attr=='status':
            return self.set_status(value)
        if attr in ['business_manager', '__exposed_methods_params__']:
            raise ValueError(f"Cannot update the attribute {attr}")
        return super.__setattr__(self, attr, value)
        
        
    @staticmethod
    def _map_user_to_role(user):
        if user in ['admin', 'system']:
            return user
        return 'user'
    