import math
import datetime as dt
from datetime import timedelta
from zoneinfo import ZoneInfo

__BUSINESS_TZ__ = ZoneInfo("Europe/Rome")
def set_business_timezone(tz: ZoneInfo):
    global __BUSINESS_TZ__
    __BUSINESS_TZ__ = tz

def get_business_timezone():
    return __BUSINESS_TZ__

def get_global_timezone():
    return dt.UTC

def validate_datetime(ts: dt.datetime):
    if not isinstance(ts, dt.datetime):
        return False
    return ts

def to_default_tz(ts: dt.datetime, replace_tz_only: bool = False):
    if replace_tz_only:
        ts.replace(tzinfo=get_business_timezone())
    return ts.astimezone(tz=get_business_timezone())

def map_datetime_to_default(ts: dt.datetime, ignore_seconds: bool = False, map_to_default_tz: bool = True):
    ts = validate_datetime(ts)
    if not ts:
        raise TypeError('Wrong ts in input. It is not a datetime object')
    if ignore_seconds:
        ts = ts.replace(second=0, microsecond=0)
    if map_to_default_tz:
        ts = to_default_tz(ts, replace_tz_only=False)
    return ts

def validate_hhmm(time_str):
    try:
        dt.datetime.strptime(time_str, "%H:%M")
        return time_str
    except ValueError:
        return False

def get_timedelta_as_total_minutes(td: timedelta):
    abs_td = abs(td)
    total_minutes = math.ceil(abs_td.total_seconds() / 60)
    return total_minutes if abs_td==td else -total_minutes

def next_multiple_of_k(n, k):
    return math.ceil(n / k) * k

    
def map_to_time(input_time):
    if isinstance(input_time, dt.time):
        return input_time
    if isinstance(input_time, dt.datetime):
        return input_time.time()
    try:
        return dt.time.fromisoformat(input_time) 
    except:
        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                return dt.datetime.strptime(input_time, fmt).time()
            except:
                raise TypeError("Wrong time input. It must be either a valid time/datetime object, or an isoformat time string")

def map_to_date(input_time):
    if isinstance(input_time, dt.date):
        return input_time
    if isinstance(input_time, dt.datetime):
        return input_time.date()
    try:
        return dt.date.fromisoformat(input_time) 
    except:
        raise TypeError("Wrong date input. It must be either a valid date/datetime object, or an isoformat date string")
        
        
def minutes_between(previous_time: dt.datetime, following_time: dt.datetime) -> float:
    """
    Returns the difference in minutes between following_time and previous_time.
    Can be negative if following_time is earlier than previous_time.
    """
    delta = following_time - previous_time
    return get_timedelta_as_total_minutes(delta)
    