import ast

def parse_to_dict(expr: str) -> dict:
    """
    Parse string → Python dict safely.
    """
    try:
        tree = ast.parse(expr, mode='eval')
        return _safe_eval(tree.body)
    except Exception:
        raise ValueError(f"Invalid expression: {expr}")

def _safe_eval(node):
    if isinstance(node, ast.Dict):
        return {_safe_eval(k): _safe_eval(v)
            for k, v in zip(node.keys, node.values)
        }

    elif isinstance(node, ast.List):
        return [_safe_eval(el) for el in node.elts]

    elif isinstance(node, ast.Tuple):
        return tuple(_safe_eval(el) for el in node.elts)

    elif isinstance(node, ast.Constant):
        return node.value

    # fallback: stringify
    return ast.unparse(node)


        

def _resolve_expected_type(path: str, schema: dict[str, type]) -> type | None:
    """
    Match path against schema with wildcard support.
    Priority:
    1. exact match
    2. parent wildcard (a.b.*)
    3. global wildcard (*)
    """
    if path in schema:
        return schema[path]

    parts = path.split('.')
    # eg: a.b.c → tries a.b.*, then a.*, finally *
    for i in range(len(parts), 0, -1):
        candidate = '.'.join(parts[:i-1] + ['*'])
        if candidate in schema:
            return schema[candidate]

    return schema.get('*', None)
    




        

def cast_data(data, schema: dict[str, type], path: str = '',
              strict: bool = True, drop_unexpected: bool = False, map_keys_to_str: bool = False, map_keys_to_lower: bool = False):
    """
    Recursively casts dict/list elements according to expected_types.
    
    Parameters:
        data: dict, list, or primitive to cast
        expected_types: mapping of hierarchical keys to target types (e.g., "a.d1", "requests.*.method")
        path: current hierarchical path used internally for recursion
        strict: whether to use strict casting
        drop_unexpected: if True, remove keys not defined in expected_types
    
    Returns:
        Casted structure with the same shape, potentially dropping unexpected keys
    """
    from utils.general_utils import map_dict_to_lower_keys
    from utils import cast_utils
    
    expected_type = _resolve_expected_type(path=path, schema=schema)
    if expected_type:
        data = cast_utils.cast_value(data, expected_type, strict=strict, raise_error=True)
    
    # If the value is a dict/list, recursion
    if isinstance(data, dict):
        if map_keys_to_str:
            data = {str(k):v for k,v in data.items()}
        if map_keys_to_lower:
            data = map_dict_to_lower_keys(data, force_non_str_keys=False)            
           
        result = {}
        for k, v in data.items():
            child_path = f"{path}.{k}" if path else k

            # Drop key if not in expected_types and drop_unexpected is True
            if drop_unexpected and (child_path not in schema) and (f"{path}.*" not in schema):
                continue

            # Recursively process child value
            casted_v = cast_data(v, schema=schema, path=child_path, strict=strict, drop_unexpected=drop_unexpected, map_keys_to_str=map_keys_to_str, map_keys_to_lower=map_keys_to_lower)

            # Apply cast_value on the final value (not dict/list)
            if not isinstance(casted_v, (dict, list)):
                expected_type = _resolve_expected_type(path=child_path, schema=schema)
                if expected_type:
                    casted_v = cast_utils.cast_value(casted_v, expected_type, strict=strict, raise_error=True)

            result[k] = casted_v
        return result

    elif isinstance(data, list):
        result = []
        for v in data:
            child_path = f"{path}.*" if path else "*"

            # Recursively process list element
            casted_v = cast_data(v, schema=schema, path=child_path, strict=strict, drop_unexpected=drop_unexpected, map_keys_to_str=map_keys_to_str, map_keys_to_lower=map_keys_to_lower)

            # Apply cast_value on the final value
            if not isinstance(casted_v, (dict, list)):
                expected_type = _resolve_expected_type(path=child_path, schema=schema)
                if expected_type:
                    casted_v = cast_utils.cast_value(casted_v, expected_type, strict=strict, raise_error=True)

            result.append(casted_v)
        return result

    # Primitive value → return casted value
    return data