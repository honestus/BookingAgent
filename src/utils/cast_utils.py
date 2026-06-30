import ast
import datetime


def _ensure_int_like(value, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer, got bool")
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer, got {type(value).__name__}")
    return value


def _parse_ast_literal(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)) and isinstance(node.operand, ast.Constant):
        if not isinstance(node.operand.value, (int, float)):
            raise ValueError("Only numeric unary operations are allowed")
        return +node.operand.value if isinstance(node.op, ast.UAdd) else -node.operand.value
    raise ValueError(f"Unsupported literal node: {type(node).__name__}")


def _resolve_callable_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        if node.id.startswith("_"):
            raise ValueError("Private names are not allowed")
        return node.id

    if isinstance(node, ast.Attribute):
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            if current.attr.startswith("_"):
                raise ValueError("Private attributes are not allowed")
            parts.append(current.attr)
            current = current.value

        if not isinstance(current, ast.Name):
            raise ValueError("Only simple datetime attribute access is allowed")
        if current.id.startswith("_"):
            raise ValueError("Private names are not allowed")

        parts.append(current.id)
        return ".".join(reversed(parts))

    raise ValueError(f"Unsupported callable node: {type(node).__name__}")


def _build_call_args(node: ast.Call) -> tuple[list, dict]:
    args = [_parse_ast_literal(arg) for arg in node.args]
    kwargs = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise ValueError("Starred keyword arguments are not allowed")
        if kw.arg.startswith("_"):
            raise ValueError("Private keyword arguments are not allowed")
        kwargs[kw.arg] = _parse_ast_literal(kw.value)
    return args, kwargs


def _call_datetime_factory(call_name: str, args: list, kwargs: dict):
    aliases = {
        "datetime": "datetime.datetime",
        "dt": "datetime.datetime",
        "date": "datetime.date",
        "time": "datetime.time",
        "timedelta": "datetime.timedelta",
        "datetime.datetime": "datetime.datetime",
        "datetime.date": "datetime.date",
        "datetime.time": "datetime.time",
        "datetime.timedelta": "datetime.timedelta",
        "dt.datetime": "datetime.datetime",
        "dt.date": "datetime.date",
        "dt.time": "datetime.time",
        "dt.timedelta": "datetime.timedelta",
        "datetime.now": "datetime.datetime.now",
        "datetime.today": "datetime.datetime.today",
        "datetime.utcnow": "datetime.datetime.utcnow",
        "datetime.fromtimestamp": "datetime.datetime.fromtimestamp",
        "datetime.utcfromtimestamp": "datetime.datetime.utcfromtimestamp",
        "datetime.strptime": "datetime.datetime.strptime",
        "datetime.fromisoformat": "datetime.datetime.fromisoformat",
        "datetime.combine": "datetime.datetime.combine",
        "datetime.datetime.now": "datetime.datetime.now",
        "datetime.datetime.today": "datetime.datetime.today",
        "datetime.datetime.utcnow": "datetime.datetime.utcnow",
        "datetime.datetime.fromtimestamp": "datetime.datetime.fromtimestamp",
        "datetime.datetime.utcfromtimestamp": "datetime.datetime.utcfromtimestamp",
        "datetime.datetime.strptime": "datetime.datetime.strptime",
        "datetime.datetime.fromisoformat": "datetime.datetime.fromisoformat",
        "datetime.datetime.combine": "datetime.datetime.combine",
        "dt.datetime.now": "datetime.datetime.now",
        "dt.datetime.today": "datetime.datetime.today",
        "dt.datetime.utcnow": "datetime.datetime.utcnow",
        "dt.datetime.fromtimestamp": "datetime.datetime.fromtimestamp",
        "dt.datetime.utcfromtimestamp": "datetime.datetime.utcfromtimestamp",
        "dt.datetime.strptime": "datetime.datetime.strptime",
        "dt.datetime.fromisoformat": "datetime.datetime.fromisoformat",
        "dt.datetime.combine": "datetime.datetime.combine",
        "date.today": "datetime.date.today",
        "date.fromtimestamp": "datetime.date.fromtimestamp",
        "date.fromisoformat": "datetime.date.fromisoformat",
        "date.fromordinal": "datetime.date.fromordinal",
        "datetime.date.today": "datetime.date.today",
        "datetime.date.fromtimestamp": "datetime.date.fromtimestamp",
        "datetime.date.fromisoformat": "datetime.date.fromisoformat",
        "datetime.date.fromordinal": "datetime.date.fromordinal",
        "dt.date.today": "datetime.date.today",
        "dt.date.fromtimestamp": "datetime.date.fromtimestamp",
        "dt.date.fromisoformat": "datetime.date.fromisoformat",
        "dt.date.fromordinal": "datetime.date.fromordinal",
        "time.fromisoformat": "datetime.time.fromisoformat",
        "datetime.time.fromisoformat": "datetime.time.fromisoformat",
        "dt.time.fromisoformat": "datetime.time.fromisoformat",
    }

    normalized = aliases.get(call_name)
    if normalized is None:
        raise ValueError(f"Unsupported datetime expression: {call_name}")

    if normalized == "datetime.datetime":
        return datetime.datetime(*args, **kwargs)
    if normalized == "datetime.date":
        return datetime.date(*args, **kwargs)
    if normalized == "datetime.time":
        return datetime.time(*args, **kwargs)
    if normalized == "datetime.timedelta":
        return datetime.timedelta(*args, **kwargs)
    if normalized == "datetime.datetime.now":
        return datetime.datetime.now(*args, **kwargs)
    if normalized == "datetime.datetime.today":
        return datetime.datetime.today(*args, **kwargs)
    if normalized == "datetime.datetime.utcnow":
        return datetime.datetime.utcnow(*args, **kwargs)
    if normalized == "datetime.datetime.fromtimestamp":
        return datetime.datetime.fromtimestamp(*args, **kwargs)
    if normalized == "datetime.datetime.utcfromtimestamp":
        return datetime.datetime.utcfromtimestamp(*args, **kwargs)
    if normalized == "datetime.datetime.strptime":
        return datetime.datetime.strptime(*args, **kwargs)
    if normalized == "datetime.datetime.fromisoformat":
        return datetime.datetime.fromisoformat(*args, **kwargs)
    if normalized == "datetime.datetime.combine":
        return datetime.datetime.combine(*args, **kwargs)
    if normalized == "datetime.date.today":
        return datetime.date.today(*args, **kwargs)
    if normalized == "datetime.date.fromtimestamp":
        return datetime.date.fromtimestamp(*args, **kwargs)
    if normalized == "datetime.date.fromisoformat":
        return datetime.date.fromisoformat(*args, **kwargs)
    if normalized == "datetime.date.fromordinal":
        return datetime.date.fromordinal(*args, **kwargs)
    if normalized == "datetime.time.fromisoformat":
        return datetime.time.fromisoformat(*args, **kwargs)

    raise ValueError(f"Unsupported datetime expression: {call_name}")


def _safe_parse_datetime_expr(input_str: str):
    expr = input_str.strip()
    tree = ast.parse(expr, mode="eval")

    allowed_nodes = (
        ast.Expression,
        ast.Call,
        ast.Name,
        ast.Attribute,
        ast.Constant,
        ast.keyword,
        ast.Load,
        ast.UnaryOp,
        ast.UAdd,
        ast.USub,
    )

    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError(f"Disallowed expression: {type(node).__name__}")

    if not isinstance(tree.body, ast.Call):
        raise ValueError("Only datetime/date/time/timedelta constructor calls are allowed")

    call_name = _resolve_callable_name(tree.body.func)
    args, kwargs = _build_call_args(tree.body)
    return _call_datetime_factory(call_name, args, kwargs)


def cast_str_to_datetime(input_str: str, target_type: type = datetime.datetime):
    if target_type not in (datetime.datetime, datetime.date, datetime.time):
        raise TypeError(f"target_type must be datetime, date or time, got {target_type}")

    def _to_target(dt_value):
        if isinstance(dt_value, datetime.datetime):
            if target_type == datetime.date:
                return dt_value.date()
            if target_type == datetime.time:
                return dt_value.time()
            return dt_value
        if isinstance(dt_value, target_type):
            return dt_value
        raise ValueError(f'Could not cast "{input_str}" to {target_type.__name__}')

    try:
        return _to_target(datetime.datetime.fromisoformat(input_str))
    except ValueError:
        pass

    if target_type == datetime.date:
        try:
            return datetime.date.fromisoformat(input_str)
        except ValueError:
            pass

    if target_type == datetime.time:
        try:
            return datetime.time.fromisoformat(input_str)
        except ValueError:
            pass

    try:
        from dateutil import parser

        return _to_target(parser.parse(input_str))
    except Exception:
        pass

    try:
        parsed = _safe_parse_datetime_expr(input_str)
        return _to_target(parsed)
    except Exception as exc:
        raise ValueError(f'Could not cast "{input_str}" to {target_type.__name__}') from exc


def cast_to_int(value) -> int:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return cast_int(float(value))
    raise TypeError(f"Invalid int {value}")


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
        if expected_type == list:
            if isinstance(value, list):
                return value
            return [value]
        return expected_type(value)
    except Exception:
        raise


def _strict_cast(value, expected_type: type):
    def _raise_error():
        raise ValueError(f"Cannot cast {value} to {expected_type}")

    if type(value) == expected_type:
        return value
    if isinstance(value, expected_type):
        if expected_type in (int, float) and isinstance(value, bool):
            pass
        else:
            return value

    if expected_type == int:
        try:
            cast_to_int(value)
        except Exception:
            _raise_error()
    if expected_type == float:
        if isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        _raise_error()
    if expected_type == bool:
        if isinstance(value, bool):
            return value
        _raise_error()
    if expected_type == str:
        return str(value)
    if expected_type in (datetime.datetime, datetime.date, datetime.time):
        if isinstance(value, str):
            return cast_str_to_datetime(value, target_type=expected_type)
        _raise_error()
    if expected_type == list:
        if isinstance(value, list):
            return value
        return [value]
    try:
        casted = expected_type(value)
        if isinstance(casted, expected_type):
            return casted
    except Exception:
        pass
    _raise_error()


def _safe_str_to_primitive(value: str):
    try:
        return ast.literal_eval(value)
    except Exception:
        return value


def cast_value(value, expected_type: type | None, strict: bool = True, raise_error: bool = True):
    if expected_type is None:
        return value
    if type(value) == expected_type:
        return value
    if isinstance(value, str) and expected_type != str:
        parsed_value = _safe_str_to_primitive(value)
        if parsed_value != value:
            return cast_value(value=parsed_value, expected_type=expected_type, strict=strict, raise_error=raise_error)

    try:
        if strict:
            return _strict_cast(value, expected_type)
        return _loose_cast(value, expected_type)
    except Exception:
        if raise_error:
            raise
        return value
