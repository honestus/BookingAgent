import datetime
def cast_str_to_datetime(input_str: str, target_type: type = datetime.datetime):
    if target_type not in (datetime.datetime, datetime.date, datetime.time):
        raise TypeError(f'target_type must be datetime, date or time, got {target_type}')
    
    def _to_target(dt: datetime.datetime):
        if target_type == datetime.date:
            return dt.date()
        if target_type == datetime.time:
            return dt.time()
        return dt  # datetime

    try:
        return _to_target(datetime.datetime.fromisoformat(input_str))
    except ValueError:
        pass

    try:
        from dateutil import parser
        return _to_target(parser.parse(input_str))
    except Exception:
        pass

    try:
        parsed = eval(input_str)  # eval if value is something like 'datetime.now()'
        if isinstance(parsed, target_type):
            return parsed
        if isinstance(parsed, datetime.datetime):
            return _to_target(parsed)
        
        try:
            return _to_target(datetime.datetime(parsed))
        except Exception:
            raise ValueError(f'Could not cast "{input_str}" to {target_type.__name__}')
    except NameError:
        raise ValueError(f'Could not cast "{input_str}" to {target_type.__name__}')


def cast_to_int(value) -> int:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return cast_int(float(value))
    raise TypeError(f'Invalid int {value}')
    
    
    
def _loose_cast(value, expected_type: type):
    try:
        if expected_type == bool:
            return bool(value)
        if expected_type == int:
            return int(value)
        if expected_type == float:
            return float(value)
        if expected_type == str:
            return str(value)
        if expected_type in (datetime.datetime, datetime.date, datetime.time):
            if isinstance(value, str):
                return cast_str_to_datetime(value, target_type=expected_type)
        # LIST
        if expected_type == list:
            if isinstance(value, list):
                return value
            return [value]
        # GENERIC FALLBACK
        return expected_type(value)
    except Exception:
        raise

def _strict_cast(value, expected_type: type):
    def _raise_error():
        raise ValueError(f"Cannot cast {value} to {expected_type}")
        
    if type(value) == expected_type:
        return value
    # IS ALREADY CASTED -> Only handling special cases
    if isinstance(value, expected_type):
        if expected_type in (int, float) and isinstance(value, bool):
            pass
        else:
            return value

    # INT (lossless)
    if expected_type == int:
        try:
            cast_to_int(value)
        except:
            _raise_error()
    # FLOAT
    if expected_type == float:
        if isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        _raise_error()
    # BOOL
    if expected_type == bool:
        if isinstance(value, bool):
            return value
        _raise_error()
    # STR
    if expected_type == str:
        return str(value)
    # DATETIME
    if expected_type in (datetime.datetime, datetime.date, datetime.time):
        if isinstance(value, str):
            return cast_str_to_datetime(value, target_type=expected_type)
        _raise_error()
    # LIST
    if expected_type == list:
        if isinstance(value, list):
            return value
        return [value]
    # GENERIC FALLBACK
    try:
        casted = expected_type(value)
        if isinstance(casted, expected_type):
            return casted
    except:
        pass
    _raise_error()

def _safe_str_to_primitive(value: str):
    import ast
    try:
        return ast.literal_eval(value)
    except Exception:
        return value

def cast_value(value, expected_type: type | None, strict: bool = True, raise_error: bool = True):
    if expected_type is None:
        return value
    if type(value)==expected_type:
        return value
    # STRING ON A NON-STRING EXPECTED TYPE → EVAL+RECURSION
    if isinstance(value, str) and expected_type!=str: 
        parsed_value = _safe_str_to_primitive(value)
        if parsed_value != value:
            return cast_value(value=parsed_value, expected_type=expected_type, strict=strict, raise_error=raise_error)
    
    try:
        if strict:
            return _strict_cast(value, expected_type)
        else:
            return _loose_cast(value, expected_type)
    except:
        if raise_error:
            raise
        return value  # fallback safe