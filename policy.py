import datetime
class Service():
    def __init__(self, name, price, minutes_duration, description=''):
        self.price = price
        self.minutes_duration = self.__validate_duration__(minutes_duration)
        self.description = description
        object.__setattr__(self, 'name', name)


    def set_price(self,price):
        self.price = price

    def set_duration(self,minutes_duration):
        self.minutes_duration = self.__validate_duration__(minutes_duration)

    def set_description(self, description):
        self.description = description

    def __validate_duration__(self, minutes_duration):
        if minutes_duration%5:
            raise ValueError('minutes_duration must be a multiple of 5')
        return minutes_duration

    def to_dict(self):
        return self.__dict__
        
    
    def __setattr__(self, attribute, value):
        if attribute=='name':
            raise TypeError('Cannot update service name. It is uneditable!')
        elif attribute=='minutes_duration':
            value = self.__validate_duration__(value)
        super().__setattr__(attribute, value)

    def __repr__(self):
        return f"Service: {self.name}. Price: {self.price}. Duration: {self.minutes_duration} mins." + (f"\n{self.description}" if self.description else '')


class PolicyManager():
    def __init__(self, services, min_advance_booking_minutes=30, min_advance_cancelation_minutes=120, opening_hours = [('09:00', '13:00'), ('15:00','21:00')]):
        self.services = {service.name:service for service in services}
        self.min_advance_booking_minutes = min_advance_booking_minutes
        self.min_advance_cancelation_minutes = min_advance_cancelation_minutes
        self.opening_hours = [(datetime.datetime.strptime(h[0], "%H:%M").time(),  datetime.datetime.strptime(h[1], "%H:%M").time()) for h in opening_hours]
        self.__set_default_slot_duration__()


    def __set_default_slot_duration__(self):
        self.default_slot_duration = 5
        #self.default_slot_duration = math.gcd(*[service.duration for service in self.services.values()])
        return
        
    def add_service(self, service: Service):
        if service.name in self.services:
            raise ValueError('Cannot add a service that already exists')
        self.services[service.name]=service
        self.__set_default_slot_duration__()

    def remove_service(self,service_name: str):
        if service_name not in self.services:
            return False
        self.services = {s:self.services[s] for s in self.services if s!=service_name}
        self.__set_default_slot_duration__()

    def update_service(self, service_name: str, price: float = None, minutes_duration: int = None, description: str = None):
        if service_name not in self.services:
            raise ValueError('Service not in current services')
        if all(el is None for el in [price, minutes_duration, description]):
            return #raise ValueError('Nothing to update.')
        curr_service = self.services[service_name]
        if price is not None:
            curr_service.set_price(price)
        if minutes_duration is not None:
            curr_service.set_duration(minutes_duration)
            self.__set_default_slot_duration__()
        if description is not None:
            curr_service.set_description(description)
        