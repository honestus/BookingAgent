import datetime
class Service:
    def __init__(self, service_name, price, minutes_duration, description=''):
        self.price = Service.__validate_price__(price)
        self.minutes_duration = Service.__validate_duration__(minutes_duration)
        self.description = description
        object.__setattr__(self, 'service_name', service_name)

    @staticmethod
    def __validate_duration__(minutes_duration):
        if not isinstance(minutes_duration, (int, float)) or not (minutes_duration.is_integer()):
            raise TypeError('minutes_duration must be a integer') 
        if minutes_duration<=0 or minutes_duration%5:
            raise ValueError('minutes_duration must be a positive integer and multiple of 5')
        return minutes_duration
      
    @staticmethod
    def __validate_price__(price):
        if not isinstance(price, (int, float)):
            raise TypeError('Price must be a numeric value')
        if price<0:
            raise ValueError('Price must be greater than 0')
        return price

    def to_dict(self):
        return self.__dict__
        
    def copy(self):
        import copy
        return copy.copy(self)
    
    def __setattr__(self, attribute, value):
        if attribute=='service_name':
            raise TypeError('Cannot update service_name. It is final!')
        
        if attribute=='minutes_duration':
            value = Service.__validate_duration__(value)
        elif attribute=='price':
            value = Service.__validate_price__(value)
        return super().__setattr__(attribute, value)

    def __repr__(self):
        return f"Service: {self.service_name}. Price: {self.price}. Duration: {self.minutes_duration} mins." + (f"\n{self.description}" if self.description else '')
        
    def __eq__(self, other):
        return all(getattr(self, attribute)==getattr(other, attribute) for attribute in ['service_name', 'price', 'minutes_duration'])


class _ValidatedOpeningHoursList(list):
    """
    Lista che valida automaticamente ogni operazione di scrittura/mutazione
    tramite PolicyManager._validate_opening_hours.
    """

    def _validate_and_merge(self, incoming: list) -> list:
        """Valida `incoming` e restituisce la lista ordinata e unita all'esistente."""
        # Validiamo solo i nuovi elementi come lista standalone
        validated_new = PolicyManager._validate_opening_hours(incoming)
        # Poi rivalidamo il merge con l'esistente per controllare sovrapposizioni
        merged = PolicyManager._validate_opening_hours(list(self) + validated_new)
        return merged

    # --- append / extend / insert ---

    def append(self, item):
        merged = self._validate_and_merge([item])
        super().clear()
        super().extend(merged)

    def extend(self, items):
        merged = self._validate_and_merge(list(items))
        super().clear()
        super().extend(merged)

    def insert(self, index, item):
        # index ignorato: la lista è sempre riordinata dalla validazione
        merged = self._validate_and_merge([item])
        super().clear()
        super().extend(merged)

    # --- rimozione (nessuna rivalidazione necessaria) ---

    def remove(self, item):
        super().remove(item)

    def pop(self, index=-1):
        return super().pop(index)

    # --- sostituzione intera lista ---

    def __setitem__(self, index, value):
        tmp = list(self)
        tmp[index] = value          # applica lo slice/index sulla copia
        validated = PolicyManager._validate_opening_hours(tmp)
        super().clear()
        super().extend(validated)

    def __delitem__(self, index):
        super().__delitem__(index)

    # --- operatori che creano/modificano ---

    def __iadd__(self, other):      # self.opening_hours += [...]
        self.extend(other)
        return self

    def __imul__(self, other):      # self.opening_hours *= n  → quasi sempre non ha senso
        raise TypeError('ValidatedOpeningHoursList does not support *= operator')

class PolicyManager:
    def __init__(self, services, min_advance_booking_minutes=30, min_advance_cancelation_minutes=120, opening_hours = [('09:00', '13:00'), ('15:00','21:00')]):
        self.services = {service.service_name:service for service in services}
        self.min_advance_booking_minutes = min_advance_booking_minutes
        self.min_advance_cancelation_minutes = min_advance_cancelation_minutes
        self.opening_hours = [(datetime.datetime.strptime(h[0], "%H:%M").time(),  datetime.datetime.strptime(h[1], "%H:%M").time()) for h in opening_hours]
        self.__set_default_slot_duration__()


    def __set_default_slot_duration__(self):
        self.default_slot_duration = 5
        #self.default_slot_duration = math.gcd(*[service.duration for service in self.services.values()])
        return
        
    def add_service(self, service: Service):
        if service.service_name in self.services:
            return False
        self.services[service.service_name]=service
        self.__set_default_slot_duration__()
        return service

    def remove_service(self, service_name: str):
        if service_name not in self.services:
            return False
        old_service = self.services.pop(service_name)
        self.__set_default_slot_duration__()
        return old_service

    def update_service(self, service_name: str, price: float = None, minutes_duration: int = None, description: str = None):
        curr_service = self.services.get(service_name, None)
        if curr_service is None:
            return False
        old_serv = curr_service.copy()
        for attr_n, attr_v in {'price': price, 'minutes_duration': minutes_duration, 'description': description}.items():
            if attr_v is not None:
                setattr(curr_service, attr_n, attr_v)
        return (old_serv, curr_service)
        
    def __setattr__(self, attr, value):
        if attr == 'opening_hours':
            validated = PolicyManager._validate_opening_hours(value)
            value = _ValidatedOpeningHoursList(validated)
        return super().__setattr__(attr, value)
        
    @staticmethod
    def _validate_opening_hours(opening_hours: list[tuple[str|datetime.time, str|datetime.time]]) -> list[tuple[datetime.time, datetime.time]]:
        
        from utils.datetimes_utils import map_to_time

        # --- Structure Validation (must be a list of tuples)---
        if isinstance(opening_hours, tuple):
            opening_hours = [opening_hours]

        if not isinstance(opening_hours, list):
            raise TypeError(
                'opening_hours must be either a list of 2-element tuples, '
                'or a single 2-element tuple'
            )

        # --- Tuple length validation (each tuple must contain 2 elements: (open_time, closing_time) ---
        if any(len(t) != 2 for t in opening_hours):
            raise ValueError(
                'Each opening hours entry must contain exactly two elements: '
                'open time and close time'
            )

        # --- Mapping each value to datetime.time and checking open<close for each tuple ---
        parsed: list[tuple[datetime.time, datetime.time]] = []
        for i, (raw_open, raw_close) in enumerate(opening_hours):
            open_t  = map_to_time(raw_open)
            close_t = map_to_time(raw_close)
            if open_t >= close_t:
                raise ValueError(
                    f'opening_hours[{i}]: open time ({open_t}) '
                    f'must be strictly before close time ({close_t})'
                )
            parsed.append((open_t, close_t))

        # --- Sorting and validating values -> no overlaps among tuples ---
        parsed.sort(key=lambda t: t[0])
        merged = []
        for open_t, close_t in parsed:
            if merged and merged[-1][1] == open_t:
                # adiacenti: estendi la tupla precedente
                merged[-1] = (merged[-1][0], close_t)
            elif merged and merged[-1][1] > open_t:
                # sovrapposti: errore (invariato)
                raise ValueError(
                    f'Interval ({open_t}–{close_t}) overlaps with '
                    f'previous interval ending at {merged[-1][1]}'
                )
            else:
                merged.append((open_t, close_t))

        return merged