def validate_service_inputs(service_name: str, service_price: int|float|None, service_minutes_duration: int|None, service_description: str|None):
    vars_types = [(service_name, [str] ),
                  (service_price, [int, float, type(None)] ),
                  (service_minutes_duration, [int, type(None)] ),
                  (service_description, [str, type(None)] )]
    any_invalid =  any(type(v) not in t for v,t in vars_types)
    return not any_invalid