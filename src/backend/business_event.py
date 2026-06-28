from enum import Enum    
        
class ReservationEventType(Enum):
    # lifecycle (final operations)
    CREATED = "reservation_created"
    DELETED = "reservation_deleted"
    REPLACED = "reservation_replaced"

    # pending operations flow (request, confirm, cancel)
    # add flow
    PENDING_ADD_CREATED = 'reservation_booking_requested'
    PENDING_ADD_CANCELED = "pending_reservation_booking_canceled"
    CONFIRMED = "pending_reservation_booking_confirmed"
    # cancelation flow
    PENDING_DELETE_CREATED = "reservation_deletion_requested"
    PENDING_DELETE_CANCELED = "pending_reservation_deletion_canceled"
    PENDING_DELETE_CONFIRMED = "pending_reservation_deletion_confirmed"
    # update flow
    PENDING_UPDATE_CREATED = "reservation_update_requested"
    PENDING_UPDATE_CANCELED = "pending_reservation_update_canceled"
    PENDING_UPDATE_CONFIRMED = "pending_reservation_update_confirmed"
    
    #noop (e.g. get_reservation, get availabilities)
    NOOP = "reservation_noop"



class ServiceEventType(Enum):
    #final operations
    CREATED = "service_created"
    UPDATED = "service_updated"
    DELETED = "service_deleted"
    
    
    # pending operations flow (request, confirm, cancel)
    # add flow
    PENDING_ADD_CREATED = 'service_insertion_requested'
    PENDING_ADD_CANCELED = "pending_service_insertion_canceled"
    CONFIRMED = "pending_service_insertion_confirmed"
    # cancelation flow
    PENDING_DELETE_CREATED = "service_deletion_requested"
    PENDING_DELETE_CANCELED = "pending_service_deletion_canceled"
    PENDING_DELETE_CONFIRMED = "pending_service_deletion_confirmed"
    # update flow
    PENDING_UPDATE_CREATED = "service_update_requested"
    PENDING_UPDATE_CANCELED = "pending_service_update_canceled"
    PENDING_UPDATE_CONFIRMED = "pending_service_update_confirmed"
    
    #noop
    NOOP = "service_noop"

class SystemEventType(Enum):
    OPENING_HOURS_UPDATED = "opening_hours_updated"
    CALENDAR_UPDATED = "calendar_updated"
    CONFIG_UPDATED = "config_updated"
    NOOP = "system_noop"
    
    
from dataclasses import dataclass
    
@dataclass    
class BusinessEvent:
    
    class EventData:
        def __init__(self, old: "Reservation|Service" = None, new: "Reservation|Service" = None):
            self.old = old
            self.new = new
    
    def __init__(self, event_type: ReservationEventType|ServiceEventType|SystemEventType, actor: "UserRole", data: EventData, message: str = None, timestamp: "datetime.datetime" = None):
        import datetime
        self.event_type = event_type
        self.actor = actor
        self.data = data
        self.timestamp = timestamp or datetime.datetime.now()
        self.message = message
        

def updates_backend_data(event: BusinessEvent) -> bool:
    return event in [
        ReservationEventType.CREATED, 
        ReservationEventType.PENDING_ADD_CREATED, 
        ReservationEventType.PENDING_ADD_CANCELED, 
        ReservationEventType.CONFIRMED, 
        ReservationEventType.DELETED,
        ReservationEventType.PENDING_DELETE_CREATED,
        ReservationEventType.PENDING_DELETE_CANCELED,
        ReservationEventType.PENDING_DELETE_CONFIRMED,
        ReservationEventType.REPLACED,
        ReservationEventType.PENDING_UPDATE_CREATED,
        ReservationEventType.PENDING_UPDATE_CANCELED,
        ReservationEventType.PENDING_UPDATE_CONFIRMED,
        ServiceEventType.CREATED,
        ServiceEventType.PENDING_ADD_CREATED,
        ServiceEventType.PENDING_ADD_CANCELED,
        ServiceEventType.CONFIRMED,
        ServiceEventType.DELETED,
        ServiceEventType.PENDING_DELETE_CREATED,
        ServiceEventType.PENDING_DELETE_CANCELED,
        ServiceEventType.PENDING_DELETE_CONFIRMED,
        ServiceEventType.UPDATED,
        ServiceEventType.PENDING_UPDATE_CREATED,
        ServiceEventType.PENDING_UPDATE_CANCELED,
        ServiceEventType.PENDING_UPDATE_CONFIRMED,
        SystemEventType.OPENING_HOURS_UPDATED,
        SystemEventType.CALENDAR_UPDATED,
        SystemEventType.CONFIG_UPDATED
    ]