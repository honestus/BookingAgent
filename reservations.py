class Reservation:
    def __init__(self, reservation_id, user, start_time,end_time, service_name):
        object.__setattr__(self, 'reservation_id', reservation_id)
        object.__setattr__(self, 'user', user)
        object.__setattr__(self, 'start_time', start_time)
        object.__setattr__(self, 'end_time', end_time)
        object.__setattr__(self, 'service_name', service_name)

    def __setattr__(self, attribute, value):
        raise ValueError(f'Cannot set attribute {attribute}. It is final')

    def to_dict(self):
        return self.__dict__#{k:v if not isinstance(v, datetime.datetime) else v.to_isoformat() for k,v in self.__dict__.items() }

    def __repr__(self):
        rep_str = ' - '.join(f'{k}:{v}' for k,v in self.to_dict().items())
        return rep_str
