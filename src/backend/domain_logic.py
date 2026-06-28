from __future__ import annotations
from backend.reservations import ReservationStatus
import datetime as dt
from datetime import timedelta

def is_reservation_active(reservation: Reservation):
	if reservation.status==ReservationStatus.DELETED_STATUS:
		return False
	if reservation.is_confirmed:
		return True
	return not reservation.is_confirmation_expired()
    
    
def is_past_reservation(reservation: Reservation, now: dt.datetime = None):
    if now is None:
        now = dt.datetime.now(tz=reservation.start_time.tzinfo)
    return reservation.start_time < now
    
    
def is_reservation_confirmed_nopending(res: Reservation):
    return is_reservation_active(res) and res.status==ReservationStatus.CONFIRMED_STATUS
    
def is_reservation_pending_confirmation(res: Reservation):
    return is_reservation_active(res) and res.status==ReservationStatus.PENDING_CONFIRMATION_STATUS
    
def is_reservation_pending_cancelation(res: Reservation):
    return is_reservation_active(res) and res.status==ReservationStatus.PENDING_CANCELATION_STATUS
    
def is_reservation_pending_update(res: Reservation):
    return is_reservation_active(res) and res.status==ReservationStatus.PENDING_UPDATE_STATUS and (r.get_associated_update_reservation() is not None)
            
def is_reservation_confirmation_expired(res: Reservation):
    return (res.status==ReservationStatus.PENDING_CONFIRMATION_STATUS and res.is_confirmation_expired())
    
def check_reserve_time_constraints(start_time, end_time, min_advance_minutes, now: dt.datetime=None):
    
    if now is None:
        now = dt.datetime.now(tz=start_time.tzinfo)
    
    if end_time<=start_time:
        return False, "end_time cannot be <= start_time"
    too_close_to_book = (start_time - now) < timedelta(minutes=min_advance_minutes)        
    if too_close_to_book:
        err_msg = ("Cannot book. Start time too close. " + f"Booking must be done at least {min_advance_minutes} minutes before" if min_advance_minutes>=0 else '') if start_time>=now else "Cannot reserve on past time"
        return False, err_msg
    return True, ""
    
def check_delete_time_constraints(reservation_start_time, min_advance_minutes, now: dt.datetime=None):
    if now is None:
        now = dt.datetime.now(tz=reservation_start_time.tzinfo)
    too_close_to_cancel = (reservation_start_time - now) < timedelta(minutes=min_advance_minutes)        
    if too_close_to_cancel:
        return False, "Too late to cancel. " + f"Cancelation must be done at least {min_advance_minutes} minutes before" if min_advance_minutes>=0 else ''
    return True, ""
        
def check_update_time_constraints(old_start_time, old_end_time, new_start_time, new_end_time, min_advance_cancel_minutes, min_advance_reserve_minutes, now: dt.datetime=None):
    if now is None:
        now = dt.datetime.now(tz=old_start_time.tzinfo)
        
    valid_cancel_time, cancel_err_msg = check_delete_time_constraints(reservation_start_time=old_start_time, min_advance_minutes=min_advance_cancel_minutes, now=now)    
    if not valid_cancel_time:
        # Can only change end time or service, not start time
        if new_start_time != old_start_time:
            return False, f"Too late to update booking start_time. {cancel_err_msg}"
        
    # Treat like a new booking: must respect min_reserve_minutes
    valid_reserv_output = check_reserve_time_constraints(start_time=new_start_time, end_time=new_end_time, min_advance_minutes=min_advance_reserve_minutes, now=now)
    return valid_reserv_output