from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass
import datetime as dt
from utils.inspect_utils import *
from shared.user_role import UserRole, validate_role

from typing import Any, Callable


# =========================
# PARAM DEFINITION
# =========================
@dataclass
class Param:
    def __init__(self, name: str, param_type: type, visible_to: List[UserRole], default_value: Any = None, required: bool = True,):
        self.name = name
        self.param_type = param_type
        self.visible_to = visible_to
        self.default_value = default_value
        self.required = required        
    
        
    def __repr__(self):
        default_value_str = f'default value: {self.default_value}' if not self.required else 'no default value'
        return f"Param '{self.name}'; type: {self.param_type}; {default_value_str}; visible to: {self.visible_to}"
        
    def __str__(self):
        from enum import Enum
        if isinstance(self.param_type, type) and issubclass(self.param_type, Enum):
            values = [e.value for e in self.param_type]
            type_str = f"Enum{values}"
        else:
            type_str = str(self.param_type)
            
        result = f"{self.name}: {type_str}"
        if not self.required:
            result += f" = {self.default_value}"
        
        return result
        
    
    def to_dict(self):
        return {self.name: {'type': self.param_type, 'visible_to': self.visible_to, 'required': self.required} | ({} if self.required else {'default': self.default_value}) }
    
@dataclass    
class Method:
    def __init__(self,whole_name: str, params: list[Param]):
        self.path, _, self.name = whole_name.rpartition('.')
        self.whole_name = whole_name
        self.params = params
        
    def __repr__(self):
        return f'{self.whole_name} - params: {[p for p in self.params]}'
        
    def __str__(self):
        return f'{self.whole_name}({', '.join([str(param) for param in self.params])})'

# =========================
# BUSINESS VALIDATOR
# =========================

class BusinessValidator:

    def __init__(self, business_manager):
        from backend.booking_service import BookingService
        if not isinstance(business_manager, BookingService):
            raise TypeError('business_manager must be of type BookingService')
        self.__build_role_methods__(business_manager)
        # param registry per method
        self.__build_methods_params__(business_manager)
    
    
    def __build_role_methods__(self, business_manager):
        #creates the list of methods to expose to each UserRole
        self.role_methods = {}
        
        user_methods = [
            "make_reservation",
            "cancel_reservation",
            "update_reservation",
            "finalize_make_reservation",
            "finalize_cancel_reservation",
            "finalize_update_reservation",
            "core.get_available_datetimes",
            "core.get_daily_opening_hours",           
        ]
        
        admin_methods = user_methods + [
            "add_service",
            "update_service",
            "remove_service",
            "finalize_add_service",
            "finalize_update_service",
            "finalize_remove_service",
            "core.get_all_reservations",
            "core.get_daily_reservations",
            "find_reservation",
        ]
        
        system_methods = admin_methods + [
            "core.get_default_opening_hours",
            "core.get_available_services",
            "core.get_user_reservations",
            "core.add_new_calendar",
            "core.remove_time_from_calendar",
            "core.delete_all_not_confirmed_services",
            "core.delete_all_not_confirmed_reservations",
        ]
        
        
        all_existing_business_methods = set(get_all_method_names(business_manager))

        # inner objects methods 
        nested_method_paths = [m 
            for m in set(user_methods+admin_methods+system_methods)
                if len(m.split('.')) > 1
        ]

        for method_path in nested_method_paths:
            parts = method_path.split('.')
            
            method_name = parts[-1]
            inner_obj_path = parts[:-1]

            current_obj = business_manager
            valid_path = True

            for attr_name in inner_obj_path: ##iteratively "validating" methods on inner objects, if any
                current_obj = getattr(current_obj, attr_name, None)

                if current_obj is None:
                    valid_path = False
                    break

            if not valid_path:
                continue

            inner_methods = get_all_method_names(current_obj)

            # re-prefix for the inner methods
            prefixed_methods = {
                '.'.join(inner_obj_path + [m])
                for m in inner_methods
            }

            all_existing_business_methods.update(prefixed_methods)
        
        
        self.role_methods[UserRole.USER] = [m for m in user_methods if m in all_existing_business_methods]
        self.role_methods[UserRole.ADMIN] = [m for m in admin_methods if m in all_existing_business_methods]
        self.role_methods[UserRole.SYSTEM] = [m for m in system_methods if m in all_existing_business_methods]

        

    def __build_methods_params__(self, business_manager) -> Dict[str, Dict[str, Param]]:

        def get_nested_attr(obj, path: str):
            from functools import reduce
            if not path:
                return obj
            return reduce(getattr, path.split('.'), obj)
        
        
        #creates the list of Param for each method defined in self.role_methods
        self.methods_params = {}
        all_exposed_methods = set([m for method_lst in self.role_methods.values() for m in method_lst])
        for method_whole_name in all_exposed_methods:
            method_path, _, method_name = method_whole_name.rpartition('.')
            current_obj, method_name = get_nested_attr(business_manager, method_path), method_name
            
            method_params_dict = method_to_dict(current_obj, method_name)[method_name]
            method_params = []
            for p_name, p_details in method_params_dict.items():
                param_type = p_details.get("type", Any)
                param_default = p_details.get("default")
                visible_to = self._get_param_visibility(p_name)

                method_params.append (
                    Param(name=p_name, param_type=param_type, visible_to=visible_to, default_value=param_default, required='default' not in p_details)
                )

            self.methods_params[method_whole_name] = Method(whole_name=method_whole_name, params=method_params)


    def get_allowed_methods_list(self, user_role: UserRole, remove_prefix_for_inner_methods: bool = False) -> List[str]:
        user_role = BusinessValidator.map_user_to_role(user_role)
        methods_list = self.role_methods[user_role]
            
        return methods_list


    def get_filtered_allowed_methods_params(self, user_role: UserRole, ) -> List[Method]:
        user_role = BusinessValidator.map_user_to_role(user_role)
        methods_names  = self.get_allowed_methods_list(user_role)

        filtered_methods = []
        for m in methods_names:
            method = self.methods_params.get(m, [])
            filtered_method = Method(whole_name=method.whole_name, params=[param for param in method.params if user_role in param.visible_to])
            filtered_methods.append(filtered_method)
        
        return filtered_methods
        

    # -------------------------

    def get_method_params(self, method_name: str) -> List[Param]:
        return self.methods_params[method_name].params

    # -------------------------

    def is_method_allowed(self, method_name: str, user_role: UserRole) -> bool:
        user_role = BusinessValidator.map_user_to_role(user_role)
        return method_name in self.role_methods[user_role]
        
        
        
    @staticmethod
    def map_user_to_role(user):
        try:
            return validate_role(user)
        except:
            return UserRole.USER


    @staticmethod
    def _get_param_visibility(param_name: str) -> List[UserRole]:
        base = BusinessValidator._get_base_param(param_name)

        if base == 'actor':
            return []
        # user param restricted
        if base == "user":
            return [UserRole.ADMIN, UserRole.SYSTEM]
        # duration restricted
        if base == "minutes_duration":
            return [UserRole.ADMIN, UserRole.SYSTEM]
        # force params restricted to system
        if base.startswith("force_"):
            return [UserRole.SYSTEM]
        # default: visible to all
        return [UserRole.USER, UserRole.ADMIN, UserRole.SYSTEM]

    @staticmethod
    def _get_base_param(param_name: str) -> str:
        for prefix in ["old_", "new_", "existing_"]:
            if param_name.startswith(prefix):
                suffix = param_name.split(prefix, 1)[1]
                return suffix
        return param_name
        
        
def stringify_methods_params(methods: list[Method], remove_prefix_for_inner_methods: bool = False) -> List[str]:
    from enum import Enum
    
    if remove_prefix_for_inner_methods:
        if len(set([m.name for m in methods])) != len(set(m.whole_name for m in methods)):
            raise RuntimeError('Method names overlap. Cannot remove prefix to avoid any misbehavior')
        
    method_name_attr = 'name' if remove_prefix_for_inner_methods else 'whole_name'
    str_results = []
    for method in methods:
        params = method.params
        stringified_method = f"{getattr(method, method_name_attr)}" + f"({(', '.join(str(p) for p in params))})"
        str_results.append(stringified_method)
    return str_results    

    






"""

# =========================================================
# STRINGIFY
# =========================================================

def _stringify_projection(projection: list[ParamProjection]) -> str:
    parts = []

    for p in projection:
        param = p.param

        # =========================
        # datetime split
        # =========================

        if param.param_type == dt.datetime:

            parts.extend([
                f"{p.exposed_names[0]}: date",
                f"{p.exposed_names[1]}: time"
            ])

            continue

        # =========================
        # default
        # =========================

        parts.append(str(param))

    return ", ".join(parts)



def stringify_methods_projection(methods_projection: dict[MethodDetails, list[ParamProjection]], remove_prefix_for_inner_methods: bool = False) -> list[str]:
    if remove_prefix_for_inner_methods:
        if len(set(m.name for m in methods_projection)) != len(methods_projection):
            raise RuntimeError("Method names overlap. Cannot remove prefix.")

    method_name_attr = ("name" if remove_prefix_for_inner_methods else "whole_name")

    result = []
    for method, projection in methods_projection.items():
        params_str = _stringify_projection(projection)
        result.append(
            f"{getattr(method, method_name_attr)}({params_str})"
        )

    return result

"""

