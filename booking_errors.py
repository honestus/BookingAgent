class AlreadyBookedError(Exception):
    """Exception raised for booking on already booked slots.
    """
    def __init__(self, message='Slot is Already booked'):
        self.message = message
        super().__init__(self.message)

class ClosingTimeError(Exception):
    """Exception raised for booking during closing time (no slots)
    """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)
        
class NotPreviouslyBookedError(Exception):
    """Exception raised for deleting/updating a non-previously booked slot(s).
    """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class PolicyError(Exception):
    """Exception raised for any action that violates policy (e.g. canceling a booking later than min_advance_minutes, reserving for an invalid service name)
    """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class PastTimeError(Exception):
    """Exception raised for any action that tries to modify bookings related to past times.
    """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)
        
class InvalidTimeError(Exception):
    """Exception raised for any action on irregular datetimes, e.g. booking at 11:43.
    """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)