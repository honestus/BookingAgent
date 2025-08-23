import datetime
class Service():
    def __init__(self, name, price, minutes_duration, minutes_grid_span=None):
        self.price=price
        self.minutes_duration=self.__validate_duration__(minutes_duration)
        self.minutes_grid_span=minutes_grid_span or 15
        object.__setattr__(self, 'name', name)


    def set_price(self,price):
        self.price = price

    def set_duration(self,minutes_duration):
        self.minutes_duration = self.__validate_duration__(duration)

    def set_minutes_grid_span(self,minutes_grid_span):
        self.minutes_grid_span = minutes_grid_span

    def __validate_duration__(self, minutes_duration):
        if minutes_duration%5:
            raise ValueError('minutes_duration must be a multiple of 5')
        return minutes_duration
        
    def __setattr__(self, attribute, value):
        if attribute=='name':
            raise TypeError('Cannot update service name. It is uneditable!')
        elif attribute=='minutes_duration':
            value = self.__validate_duration__(value)
        super().__setattr__(attribute, value)

    def __repr__(self):
        return f"Service: {self.name}. Price: {self.price}. Duration: {self.minutes_duration}"


class PolicyManager():
    def __init__(self, services, min_advance_booking_minutes=30, min_advance_cancelation_minutes=120, opening_hours = [('09:00', '13:00'), ('15:00','21:00')]):
        self.services = {service.name:service for service in services}
        self.set_default_slot_duration()
        self.min_advance_booking_minutes = min_advance_booking_minutes
        self.min_advance_cancelation_minutes = min_advance_cancelation_minutes
        self.opening_hours = [(datetime.datetime.strptime(h[0], "%H:%M").time(),  datetime.datetime.strptime(h[1], "%H:%M").time()) for h in opening_hours]


    def set_default_slot_duration(self):
        #self.default_slot_duration = math.gcd(*[service.duration for service in self.services.values()])
        self.default_slot_duration = 5
        
    def add_service(self,service):
        if service.name in self.services:
            raise ValueError('Cannot add a service that already exists')
        self.services[service.name]=service
        self.set_default_slot_duration()

    def remove_service(self,service_name):
        if service_name not in self.services:
            return False
        self.services = {s:self.services[s] for s in self.services if s!=service_name}
        self.set_default_slot_duration()

    def update_service(self,service_name, service_price=None, service_duration=None, minutes_grid_span=None):
        if service_name not in self.services:
            raise ValueError('Service not in current services')
        if all(el is None for el in [service_price, service_duration, minutes_grid_span]):
            raise ValueError('Nothing to update.')
        curr_service = self.services[service_name]
        if service_price is not None:
            curr_service.price=service_price
        if minutes_grid_span is not None:
            curr_service.minutes_grid_span=minutes_grid_span
        if service_duration is not None:
            curr_service.minutes_duration=service_duration
            self.calculate_default_slot_duration()