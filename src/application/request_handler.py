from __future__ import annotations
from application.business_validator import BusinessValidator
from application import request_mapping
from shared.user_role import UserRole
from shared import globals_shared
from application.request_response import StructuredRequest, StructuredResponse, StructuredRequestError, ResponseErrorCode
from typing import Any
from utils.general_utils import flatten


# =========================
# INJECTION POLICY
# =========================

class ParamInjectionRule: ##interface -> create a new ParamInjectionRule subclass for a param to inject
    def match(self, param_name: str, method: str) -> bool:
        raise NotImplementedError

    def resolve(self, request: StructuredRequest):
        raise NotImplementedError


class UserInjectionRule(ParamInjectionRule):
    def match(self, param_name: str, method: str = None) -> bool:
        return param_name.endswith("user")

    def resolve(self, request: StructuredRequest):
        return request.user.user_id
        
class ActorInjectionRule(ParamInjectionRule):
    def match(self, param_name: str, method: str = None) -> bool:
        return param_name == "actor"

    def resolve(self, request: StructuredRequest):
        return request.user.user_role

        
class ForceGridRule(ParamInjectionRule):
    def match(self, param_name: str, method: str = None) -> bool:
        return param_name=='force_default_grid'

    def resolve(self, request: StructuredRequest):
        if getattr(request.user, 'user_role', UserRole.USER)==UserRole.USER:
            return True
        return False


class InjectionPolicy:
    def __init__(self, rules: list[ParamInjectionRule] = []):
        self.rules = set(rules)

    def add_rule(self, rule: ParamInjectionRule):
        self.rules.add(rule)
        
    def remove_rule(self, rule: ParamInjectionRule):
        try:
            self.rules.remove(rule)
            return True
        except:
            return False

    def get_injected_value(self, param_name: str, request: StructuredRequest):
        method = request.method
        for rule in self.rules:
            if rule.match(param_name, method):
                return rule.resolve(request)
        raise KeyError(f"No injection rule defined for param '{param_name}'")

"""
@dataclass
class ExecutableRequest:
    method: str
    params: dict[str, Any]
    
    def __dict__(self):
        return  {'method':self.method, 'params':self.params}
"""

# =========================
# REQUEST HANDLER
# =========================

class RequestHandler:

    def __init__(self, business_manager: BookingService):
        self.business_manager = business_manager
        self._business_validator = BusinessValidator(business_manager)
        self._build_cached_exposed_params()

        # injection policy setup
        self.injection_policy = InjectionPolicy([UserInjectionRule(), ActorInjectionRule(), ForceGridRule()])
    # -------------------------
    
    
    def build_structured_request(self, request_dict: dict, raise_error: bool = True):
        from application.request_mapping import MappingError
        expected_params_types = None
        
        try:
            validated_request_dict={}
            validated_request_dict[globals_shared.USER_ATTRIBUTE] = request_dict[globals_shared.USER_ATTRIBUTE]
            user_role = validated_request_dict[globals_shared.USER_ATTRIBUTE].user_role
            
            if globals_shared.METHOD_ATTRIBUTE in request_dict:
                method_whole_name = self._methods_names_mapping_by_role[user_role][request_dict[globals_shared.METHOD_ATTRIBUTE]]
                validated_request_dict[globals_shared.METHOD_ATTRIBUTE]=method_whole_name
                
                method_params = self._business_validator.get_method_params(method_whole_name)
                method_params_names = [p.name for p in method_params]
                
                method_exposed_params = self._exposed_methods_params_by_role[user_role][0][method_whole_name]
                allowed_params = flatten([p.exposed_params for p in method_exposed_params])
                allowed_params_names = [p.name for p in allowed_params]
                
                extra_unknown_params = [p for p in request_dict[globals_shared.PARAMS_ATTRIBUTE].keys() if p not in set(allowed_params_names+method_params_names)]
                if extra_unknown_params:
                    if raise_error:
                        raise ValueError(f'Unknown parameters: {extra_unknown_params} for method {request_dict[globals_shared.METHOD_ATTRIBUTE]}')
                    
                    valid_params = {p_name:p_value for p_name, p_value in request_dict[globals_shared.PARAMS_ATTRIBUTE].items() if p_name not in extra_unknown_params}
                    return StructuredRequest(method=request_dict[globals_shared.METHOD_ATTRIBUTE], params=valid_params, extra_params=extra_unknown_params, user=request_dict[globals_shared.USER_ATTRIBUTE], errors=[StructuredRequestError.UNKNOWN_PARAMETERS])
                
                ###EXCLUDING NON-EXPOSED PARAMETERS FROM THE REQUEST. THEY WILL BE SET VIA INJECTION
                validated_request_dict[globals_shared.PARAMS_ATTRIBUTE] = {p_name:p_value for p_name,p_value in request_dict[globals_shared.PARAMS_ATTRIBUTE].items() if p_name in allowed_params_names}
                
                expected_params_types = get_expected_types_from_params(allowed_params)
        
            structured_req = request_mapping.dict_to_structured_request(validated_request_dict, expected_params_types)
            return structured_req            
                                       
        except MappingError as e:
            if raise_error:
                raise
            sr = e.structured_request
            return sr
        except Exception:
            if raise_error:
                raise
            return StructuredRequest(method=None, errors=[StructuredRequestError.INVALID_REQUEST])
    
    
    async def run(self, request: StructuredRequest):        
        try:
            request = self._build_executable_request(request)
            method_output = await self._execute_request(request)    
        except Exception as e:
            method_output = e
            
        response = _map_execute_output_to_response(method_output)
        
        return (request, response)
        
        
    def _build_executable_request(self, request: StructuredRequest) -> StructuredRequest:
        import datetime as dt            
        exec_req = request.copy()
        ## REMAPPING EXPOSED_METHOD_NAME -> WHOLE_METHOD_NAME
        ## REMAPPING EXPOSED_PARAMS -> RUN_PARAMS
        if exec_req.params:
            method_whole_name = self._methods_names_mapping_by_role[exec_req.user.user_role].get(exec_req.method, exec_req.method)
            updated_params_dict = _reconstruct_run_params(params=exec_req.params, missing_params=exec_req.missing_params, method_exposed_params=self._exposed_methods_params_by_role[exec_req.user.user_role][0][method_whole_name])
            exec_req.params = updated_params_dict['params']
            exec_req.missing_params = updated_params_dict['missing_params']
            if updated_params_dict['error']:
                exec_req._input_errors.append(StructuredRequestError.PARAMETERS_VALUE_ERROR)

        exec_req.params = self._inject_params(exec_req)
        unknown_params = [p for p in exec_req.params if p not in map(lambda p: p.name, self._business_validator.get_method_params(exec_req.method))]
        exec_req.extra_params = unknown_params
        exec_req.validate()
        exec_req._timestamp = dt.datetime.now()
        
        if not exec_req.is_valid:
            raise ValueError('Invalid request', exec_req)
        
        return exec_req
        

    async def _execute_request(self, exec_request: StructuredRequest, replay_mode: bool = False):
        import inspect                
        # validate method access
        if not self._business_validator.is_method_allowed(exec_request.method, exec_request.user.user_role):
            raise PermissionError(f"Method {exec_request.method} not allowed for user {exec_request.user.user_id}", exec_request)
        
        if replay_mode:
            for p in dict(exec_request.params):
                if 'force' in p:
                    exec_request.params[p] = True
        
        # execute method
        current_obj, method_name = self.business_manager, exec_request.method
        while '.' in method_name:
            inner_obj_name, method_name = method_name.split('.', 1)
            current_obj = getattr(current_obj, inner_obj_name)
        
        business_manager_method = getattr(current_obj, method_name, None)
        if business_manager_method is None:
            raise ValueError(f"Method '{exec_request.method}' not found in {type(self.business_manager)}")
        if inspect.iscoroutinefunction(business_manager_method):
            return await business_manager_method(**exec_request.params)
        else:
            return business_manager_method(**exec_request.params)                
    
    
    
        
    # -------------------------
    # INJECTION
    # -------------------------

    def _inject_params(self, request: StructuredRequest) -> dict[str, Any]:
        input_params = request.params
        injected_params = {}
        method_params = self._business_validator.get_method_params(request.method)

        for param in method_params:
            request_user_role = getattr(request.user, 'user_role', None)
            # if param not visible -> must be injected or defaulted
            if request_user_role not in param.visible_to:
                try:
                    injected_params[param.name] = self.injection_policy.get_injected_value(param_name = param.name, request = request)
                except (KeyError, AttributeError):         
                    if param.required:
                        raise ValueError(f"Missing required param: {param.name}")                    
                    injected_params[param.name] = param.default_value
            ## explicitly setting missing params'values to default_value, if any
            elif param.name not in input_params:
                if not param.required:
                    injected_params[param.name] = param.default_value
        return input_params|injected_params
        
        
    def _build_cached_exposed_params(self):
        from application.business_methods_exposure import map_param_to_exposed_param
        from application.business_validator import stringify_methods_params, Method
        #from application.business_methods_exposure import ExposedParam
        if getattr(self, '_exposed_methods_params_by_role', None) is not None:
            return
        
        exposed_methods_params_by_role, methods_names_mapping_by_role  = {}, {}
        for role in self._business_validator.role_methods.keys():
            role_methods= self._business_validator.get_filtered_allowed_methods_params(role)
            exposed_methods_params: dict[str, list[ExposedParam]] = {m.whole_name: [map_param_to_exposed_param(p) for p in m.params] for m in role_methods}
            
            extended_exposed_methods = [Method(name, flatten([exp_p.exposed_params for exp_p in exp_pars]) ) for name, exp_pars in exposed_methods_params.items()]
            try:
                stringified_methods_params = stringify_methods_params(extended_exposed_methods, remove_prefix_for_inner_methods=True)
                methods_names_mapping_by_role[role] = {m.name: m.whole_name for m in extended_exposed_methods}
            except:
                stringified_methods_params = stringify_methods_params(extended_exposed_methods, remove_prefix_for_inner_methods=False)
                methods_names_mapping_by_role[role] = {m.whole_name: m.whole_name for m in extended_exposed_methods}
                
            exposed_methods_params_by_role[role] = (exposed_methods_params, stringified_methods_params)
        
        self._exposed_methods_params_by_role: dict[UserRole, dict[str, list[ExposedParam]]] = exposed_methods_params_by_role
        self._methods_names_mapping_by_role: dict[UserRole, dict[str, str]] = methods_names_mapping_by_role
 

 
        
def _reconstruct_run_params(params: dict[str, Any], missing_params: list[str], method_exposed_params: list[ExposedParam]) -> dict[str, Any]:
        """
        if not params:
            return
        """
        params = dict(params) if params else {}
        missing_params = list(missing_params) if missing_params else []
        
        all_exposed_params_names_to_replace = []
        any_reconstruction_error: bool = False
        for exposed_param in method_exposed_params:
            exposed_names = [p.name for p in exposed_param.exposed_params]
            run_name = exposed_param.param.name
            if len(exposed_names)==1 and exposed_names[0]==run_name: #exposed_param == run_param, no need to reconstruct
                continue
            
            ##EXPOSED PARAMETERS FLOW -> parameters shown to the user with different names than the "real" ones
            params.pop(run_name, None) ##removing param_run_name from input paramters, if any
            try:
                mapped_value = exposed_param.reconstruct(params)
                all_exposed_params_names_to_replace.extend(exposed_names)
                params[run_name] = mapped_value
            except Exception:
                curr_param_missing_values = [name for name in exposed_names if name not in params]
                if curr_param_missing_values:
                    missing_params.extend(curr_param_missing_values)
                else:
                    all_exposed_params_names_to_replace.extend(exposed_names)
                    params[run_name] = tuple(params.get(name, None) for name in exposed_names)
                    any_reconstruction_error = True
        
        params = {k:v for k,v in params.items() if k not in all_exposed_params_names_to_replace}
        missing_params = [p for p in set(missing_params) if p not in all_exposed_params_names_to_replace]
        return {'params': params, 'missing_params': missing_params, 'error': any_reconstruction_error}
    

    
def _map_execute_output_to_response(method_output):
    from backend.business_event import BusinessEvent
    
    method_output = _snapshot_output(method_output)
    if isinstance(method_output, Exception):
        err_code = _method_output_to_response_error_type(method_output)
        response = StructuredResponse(data=None, error_code=err_code, error_msg=method_output, extra_events=getattr(method_output, 'events', None))
        return response
        
    if isinstance(method_output, BusinessEvent):
        method_output = [method_output]
    
    if isinstance(method_output, list) and all(isinstance(o, BusinessEvent) for o in method_output):
        resp_data, resp_events = zip(*[(e.data, e.event_type) for e in method_output])
        return StructuredResponse(data=resp_data, events=resp_events)
    
    return StructuredResponse(data=method_output)
        

        
        
def _snapshot_output(obj):
    from backend.business_event import BusinessEvent
    from application import snapshots
    import copy
    
    if isinstance(obj, Exception):
        return copy.deepcopy(obj)

    if isinstance(obj, BusinessEvent):
        return BusinessEvent(
            event_type=obj.event_type,
            actor=obj.actor,
            message=obj.message,
            timestamp=obj.timestamp,
            data=BusinessEvent.EventData(
                old=snapshots.map_object_to_snapshot(obj.data.old),
                new=snapshots.map_object_to_snapshot(obj.data.new),
            )
        )
        
    if isinstance(obj, (list, tuple)):
        return [_snapshot_output(o) for o in obj]

    return snapshots.map_object_to_snapshot(obj)
    
    
_req_errors_priorities = [
    StructuredRequestError.INVALID_REQUEST, 
    StructuredRequestError.INPUT_TYPES_ERRORS,
    StructuredRequestError.MISSING_PARAMETERS,
    StructuredRequestError.PARAMETERS_VALUE_ERROR,
    StructuredRequestError.UNKNOWN_PARAMETERS,
]
                
    
_request_error_to_response_error = {
    StructuredRequestError.INVALID_REQUEST: ResponseErrorCode.INVALID_REQUEST_ERROR,
    StructuredRequestError.MISSING_PARAMETERS: ResponseErrorCode.PARAMETERS_ERROR,
    StructuredRequestError.INPUT_TYPES_ERRORS: ResponseErrorCode.INVALID_REQUEST_ERROR,
    StructuredRequestError.PARAMETERS_VALUE_ERROR: ResponseErrorCode.INVALID_REQUEST_ERROR,
    StructuredRequestError.UNKNOWN_PARAMETERS:  ResponseErrorCode.PARAMETERS_ERROR,
    StructuredRequestError.VALID_REQUEST: None,
    
    }

_REQUEST_ERRORS_TO_RESPONSE = {
    StructuredRequestError.INVALID_REQUEST: ResponseErrorCode.INVALID_REQUEST_ERROR,
    StructuredRequestError.INPUT_TYPES_ERRORS: ResponseErrorCode.INVALID_REQUEST_ERROR,
    StructuredRequestError.PARAMETERS_VALUE_ERROR: ResponseErrorCode.PARAMETERS_ERROR,
    StructuredRequestError.MISSING_PARAMETERS: ResponseErrorCode.PARAMETERS_ERROR,
    StructuredRequestError.UNKNOWN_PARAMETERS: ResponseErrorCode.PARAMETERS_ERROR
}

def _method_output_to_response_error_type(o) -> ResponseErrorCode:
    if not isinstance(o, Exception):
        return ResponseErrorCode.NO_ERROR
    if isinstance(o, PermissionError):
        return ResponseErrorCode.NOT_ALLOWED_ERROR
    if len(o.args)>1 and isinstance(o.args[1], StructuredRequest):
        
        req = o.args[1]
        if req.is_valid:
            return ResponseErrorCode.RUNTIME_ERROR
        
        for error in _req_errors_priorities:
            if error in req.errors:
                return _request_error_to_response_error[error]
                        
        
        return ResponseErrorCode.RUNTIME_ERROR
        
    return ResponseErrorCode.RUNTIME_ERROR
        
def get_expected_types_from_params(params: list["Param"]):
    expected_params_types = {}
    for p in params:
        expected_params_types.update(p.to_dict())
    return expected_params_types
    