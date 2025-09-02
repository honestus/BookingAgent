import math
import datetime
from datetime import timedelta

def validate_time(time):
    if not isinstance(time, datetime.datetime):
        return False
    return time.replace(second=0, microsecond=0)

def validate_hhmm(time_str):
    try:
        datetime.strptime(time_str, "%H:%M")
        return True
    except ValueError:
        return False


def map_datetime_to_next_slot_datetime(datetime):
    default_slot_duration=5
    datetime = validate_time(datetime)
    curr_mismatch = datetime.minute%default_slot_duration    
    if curr_mismatch:
        datetime = datetime + timedelta(minutes=default_slot_duration-curr_mismatch)
    return datetime

def get_timedelta_in_minutes(timedelta):
    return math.ceil(timedelta.seconds / 60)

def next_multiple_of_k(n, k):
    return math.ceil(n / k) * k

    
def map_to_time(input_time):
    if isinstance(input_time, datetime.time):
        return input_time
    if isinstance(input_time, datetime.datetime):
        return input_time.time()
    try:
        return datetime.time.fromisoformat(input_time) 
    except:
        raise TypeError("Wrong time input. It must be either a valid time/datetime object, or an isoformat time string")

def map_to_date(input_time):
    if isinstance(input_time, datetime.date):
        return input_time
    if isinstance(input_time, datetime.datetime):
        return input_time.date()
    try:
        return datetime.date.fromisoformat(input_time) 
    except:
        raise TypeError("Wrong date input. It must be either a valid date/datetime object, or an isoformat date string")
        
        
def minutes_between(previous_time: datetime, following_time: datetime) -> float:
    """
    Returns the difference in minutes between following_time and previous_time.
    Can be negative if following_time is earlier than previous_time.
    """
    delta = following_time - previous_time
    return math.ceil(delta.total_seconds() / 60)