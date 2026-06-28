def _is_int(v):
    return isinstance(v, int) or (isinstance(v,float) and v.is_integer())

def is_service_inputs_valid(service_name: str = None, price: int|float = None, minutes_duration: int = None, description: str = None):
    vars_checks = {
                    'service_name': (isinstance(service_name, str), str),
                    'price': (isinstance(price, (int, float,)) , float),
                    'minutes_duration': ( (_is_int(minutes_duration) and minutes_duration>0), int),
                    'description': (isinstance(description, str) or description is None, str)
                  }

    any_invalid =  any(not e[0] for e in vars_checks.values())
    return not any_invalid, vars_checks
    
    
def is_reservation_inputs_valid(user: str = None, service_name: str = None, reservation_id: str|int = None, start_time: "datetime.datetime" = None, minutes_duration: int = None):
    import datetime as dt
    
    vars_checks = {
                    'user': ( isinstance(user, str) or user is None, str ),
                    'reservation_id': ( isinstance(reservation_id, (str, int)) or reservation_id is None, str ),
                    'service_name': ( isinstance(service_name, str) or service_name is None, str ),
                    'start_time': ( isinstance(start_time, dt.datetime) or start_time is None, dt.datetime ),
                    'minutes_duration': ( (_is_int(minutes_duration) and minutes_duration>0) or minutes_duration is None , int ),
                }    
              
    any_invalid =  any(not e[0] for e in vars_checks.values())
    return not any_invalid, vars_checks
    
    
def validate_service_params(service_name: str, price: float, minutes_duration: int, description: str):
    from backend.validate_utils import is_service_inputs_valid

    is_valid, valid_params = is_service_inputs_valid(service_name=service_name, price=price, minutes_duration=minutes_duration, description=description)
    if not is_valid:
        invalids = {k: v[1] for k,v in valid_params.items() if not v[0]}
        raise TypeError('Wrong parameters:' + '; '.join([f'{k}, must be {v}' for k,v in invalids.items()]) )
    return True