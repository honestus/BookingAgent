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
    