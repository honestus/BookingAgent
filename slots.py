import math, warnings, copy, threading, datetime
from datetime import timedelta
from booking_errors import AlreadyBookedError
class Slot:
    def __init__(self, start_time: datetime):
        self.start_time = start_time  # datetime or "HH:MM" string
        self.is_booked = False
        self._lock = threading.Lock()  # Only locks THIS slot
        
    def __repr__(self):
        status = "Booked" if self.is_booked else "Free"
        return f"<Slot {self.start_time} - {status}>"

    def reset(self):
        self.is_booked = False
        return self
        
    def copy(self):
        return copy.copy(self)
    
    """
    def to_dict(self):
        return {
            "start_time": self.start_time.isoformat(),  # store as ISO string
            "is_booked": self.is_booked,
            "service_type": self.service_type,
            "booking_user": self.booking_user,
            "booking_id": self.booking_id
        }

    @classmethod
    def from_dict(cls, data):
        slot = cls(datetime.fromisoformat(data["start_time"]))
        slot.is_booked = data["is_booked"]
        slot.service_type = data.get("service_type")
        slot.booking_user = data.get("booking_user")
        slot.booking_id = data.get("booking_id")
        return slot
"""



class Segment:
    def __init__(self, start_time, end_time, slot_duration=5, force_past_slots=True):
        if start_time>= end_time:
            raise ValueError('End time must be after start time')
        object.__setattr__(self, 'start_time', start_time)
        object.__setattr__(self, 'end_time', end_time)
        object.__setattr__(self, 'slot_duration', slot_duration)
        object.__setattr__(self, 'slots', [])

        self.__generate_slots__(force_past_slots=force_past_slots)
        self._update_time_index_map()
    
    def _update_time_index_map(self):
        time_index_map = {slot.start_time:i for i,slot in enumerate(self.slots)}
        object.__setattr__(self, 'time_index_map', time_index_map)

    def get_slot(self, start_time, return_index=False):
        """Get a slot by datetime (O(1) lookup)."""
        idx = self.time_index_map.get(start_time)
        if idx is not None:
            return self.slots[idx] if not return_index else idx
        return None

    def join(self, other_segment):
        if other_segment.slot_duration!=self.slot_duration:
            raise ValueError('Cannot join segments with different slot durations')
        # Only allow joining if segments are exactly adjacent (no gap, no overlap)
        if self.end_time == other_segment.start_time:
            # Merge forward
            object.__setattr__(self, 'end_time', other_segment.end_time)
            object.__setattr__(self, 'slots', self.slots + other_segment.slots)
        elif other_segment.end_time == self.start_time:
            # Merge backward
            object.__setattr__(self, 'start_time', other_segment.start_time)
            object.__setattr__(self, 'slots', other_segment.slots + self.slots)
        else:
            raise ValueError("Segments are not exactly adjacent. Cannot join.")

        self._update_time_index_map()
        return self

    def get_slots_slice(self, start_time, end_time):
        if start_time>self.end_time or end_time<self.start_time:
            return []
        # Find start index
        if start_time in self.time_index_map:
            start_idx = self.time_index_map[start_time]
        else:
            start_idx = bisect_left(self.slots, start_time, key=lambda s: s.start_time)
        # Find end index (exclusive)
        end_idx = bisect_left(self.slots, end_time, key=lambda s: s.start_time)
        
        return self.slots[start_idx:end_idx]

    def get_subsegment(self, start_time, end_time):
        if start_time < self.start_time or end_time > self.end_time:
            raise ValueError('cannot get a subsegment with hours out of the current segment')
        if start_time > self.end_time or end_time < self.start_time:
            return None
        if start_time==self.start_time and end_time==self.end_time:
            return self.copy()
        
        subsegment = self.copy()
        object.__setattr__(subsegment, 'start_time', start_time)
        object.__setattr__(subsegment, 'end_time', end_time)
        object.__setattr__(subsegment, 'slots', self.get_slots_slice(start_time=start_time, end_time=end_time))
        subsegment._update_time_index_map()
        return subsegment

    def minutes_mismatch_from_default(self, curr_datetime, default_minutes_grid_range=15):
        """
        Returns the mismatch of minutes from start of the day, by following default_minutes_grid_range.
        I.E. curr_time = self.start_time + default_minutes_grid_range * k + minutes_mismatch. Returns minutes mismatch
        """
        minutes_from_start = ((curr_datetime - curr_datetime.replace(hour=self.start_time.hour, minute=self.start_time.minute)).seconds / 60)
        return int(minutes_from_start % default_minutes_grid_range)
        
    def copy(self):
        return copy.copy(self)
        
    def __repr__(self):
        return f"<Segment - from {self.start_time} to {self.end_time}. Contains {len(self.slots)} slots)>"

    def __setattr__(self, attribute, value):
        raise ValueError(f'Cannot set attribute {attribute}. It is final')

    def __generate_slots__(self, force_past_slots=True):
        """ If force_past_slots is False, only generates slots from max(start_time, current_time + minimum_advance_booking_minutes) on. 
        If start_time < curr_time, sets self.start_time = curr_time. 
        """
        if getattr(self, '_is_generated', False) is True:
            return

        object.__setattr__(self, '_is_generated', True)
        object.__setattr__(self, 'slots', [])

        if not force_past_slots:
            curr_time = datetime.now()
            if self.end_time < curr_time:
                raise ValueError('Cannot generate on past timeframes')

            curr_time = curr_time.replace(
                                        second=0, microsecond=0, minute=next_multiple_of_k(curr_time.minute, k=5)) \
                    + timedelta(minutes=global_policy_manager.min_advance_booking_minutes)

            if self.start_time<=curr_time:
                starting_time_minutes = self.slot_duration*math.ceil( (curr_time.hour*60 + curr_time.minute) / self.slot_duration) ##returns the minute count(i.e. how many minutes from midnight) to start the generation from
                starting_hour = starting_time_minutes // 60
                starting_minute = starting_time_minutes % 60
                print(starting_hour, starting_minute)
                object.__setattr__(self, 'start_time', curr_time.replace(hour=starting_hour, minute=starting_minute))
            if self.end_time <= self.start_time:
                warnings.warn('End time is too close or already past. Nothing to generate')
                object.__setattr__(self, 'start_time', self.end_time)
                return []
        
        current = self.start_time
        while current + timedelta(minutes=self.slot_duration) <= self.end_time:
            self.slots.append(Slot(current))
            current += timedelta(minutes=self.slot_duration)


from bisect import bisect_left, bisect_right


class SlotsManager():
    def __init__(self, slot_minutes_duration=5):
        self.slot_minutes_duration = slot_minutes_duration
        self.segments = []
        #self._update_time_index_map()


    def add_segment(self, start_time, end_time, force_past_slots=True):
        new_segment = Segment(start_time=start_time, end_time=end_time, slot_duration=self.slot_minutes_duration, force_past_slots=force_past_slots)
        
        idx = bisect_left(self.segments, new_segment.start_time, key=lambda x: x.start_time)
        # Check left neighbor no-overlaps
        if idx > 0:
            previous_segment = self.segments[idx-1]
            if new_segment.start_time < previous_segment.end_time:
                raise ValueError(f"Overlaps with segment {previous_segment}")
            if new_segment.start_time == previous_segment.end_time:
                warnings.warn(f'The new segment is adiacent with previously generated segment {previous_segment}. Will now join them as a unique segment')
                new_segment = new_segment.join(previous_segment)
                del self.segments[idx-1]
                idx -= 1      
        
        # Check right neighbor no-overlaps
        if idx < len(self.segments):
            following_segment = self.segments[idx]
            if new_segment.end_time > following_segment.start_time:
                raise ValueError(f"Overlaps with segment {following_segment}")
            if new_segment.end_time == following_segment.start_time:
                warnings.warn(f'The new segment is adiacent with previously generated segment {following_segment}. Will now join them as a unique segment')
                new_segment = new_segment.join(following_segment)
                del self.segments[idx]

        self.segments.insert(idx, new_segment)
        return True


    def remove_segment(self, start_time, end_time, raise_error_if_any_booking=True):
        if end_time<=self.segments[0].start_time or start_time >= self.segments[-1].end_time:
            return
        
        first_segment_involved_idx, last_segment_involved_idx = self._get_segments_involved(start_time=start_time, end_time=end_time, return_index=True)
        segments_involved = self.segments[first_segment_involved_idx:last_segment_involved_idx]
        final_segments = self.segments[:first_segment_involved_idx]
        for segment in segments_involved:
            if segment.start_time>end_time or segment.end_time<start_time:
                continue
            if raise_error_if_any_booking:
                slots_to_remove = segment.get_slots_slice(start_time=start_time, end_time=end_time)
                if any(s.is_booked for s in slots_to_remove):
                    raise ValueError(f'Cannot remove segments: they contain bookings.')
                    final_segments.append(segment) ## so far not working as we are raising error. But it may be a solution to keep whole segment whenever it contains reservations, and deleting only involved segments with no reservations...
                    warnings.warn(f'Cannot remove segment {segment}: contains bookings.')
                    continue
            left_segment = segment.get_subsegment(start_time=segment.start_time, end_time=start_time)
            right_segment = segment.get_subsegment(start_time=end_time, end_time=segment.end_time)
            if left_segment:
                final_segments.append(left_segment)
            if right_segment:
                final_segments.append(right_segment)
        final_segments.extend(self.segments[last_segment_involved_idx:])

        self.segments = final_segments
        return
   

    def find_segment_by_start_time(self, start_time):
        idx = bisect_left(self.segments, start_time, key=lambda s: s.start_time)
        # idx points to the first segment whose start_time >= target_start_time
        if idx < len(self.segments):
            curr_segment = self.segments[idx]
            if curr_segment.start_time == start_time:
                return curr_segment
        return None  # not found
    

    def find_segment_containing(self, start_time, end_time, return_index=False):
        if start_time > end_time:
            return None
        # Find the index of the first segment whose start_time is > start_time
        idx = bisect_right(self.segments, start_time, key=lambda s: s.start_time) - 1
        if idx >= 0:
            matching_segment = self.segments[idx]
            if matching_segment.start_time <= start_time and end_time <= matching_segment.end_time:
                return matching_segment if not return_index else idx
        return None

    def _get_segments_involved(self, start_time, end_time, return_index=False):
        first_segment_involved_idx = bisect_right(self.segments, start_time, key=lambda x:x.end_time)
        last_segment_involved_idx = bisect_left(self.segments, end_time, key=lambda x:x.start_time)
        if return_index:
            return first_segment_involved_idx, last_segment_involved_idx
        return self.segments[first_segment_involved_idx:last_segment_involved_idx]
        
    def is_available_timeframe(self, start_time, end_time, as_int_error=False):
        segment = self.find_segment_containing(start_time, end_time)
        if not segment:
            return -1 if as_int_error else False
        needed_slots = segment.get_slots_slice(start_time=start_time, end_time=end_time)
        return not any(slot.is_booked for slot in needed_slots)


    def get_slots(self, start_time, end_time, same_segment_only = False):
        slots_found=[]
        if same_segment_only:
            matched_segment = self.find_segment_containing(start_time=start_time, end_time=end_time)
            if not matched_segment:    
                return []
            slots_found = matched_segment.get_slots_slice(start_time=start_time, end_time=end_time)
        else:
            for segment in self._get_segments_involved(start_time=start_time, end_time=end_time):
                segment_filtered_slot_list = segment.get_slots_slice(start_time=start_time, end_time=end_time)
                slots_found.extend(segment_filtered_slot_list)
               
        return slots_found

    def get_available_booking_slots(self, minutes_duration, min_start_time, max_start_time=None, minutes_grid_span=15, split_by_segment=True):
        """
        Returns all the slots groups which total duration is >= duration.
        I.e. returns the slots that have free time >= duration after its start_time
        start_time: filtering start_time - will only look for slots after it.
        end_time: filtering end_time - will only look for slots before it.
        """
        from datetimes_utils import validate_time
        min_start_time = validate_time(min_start_time)
        if max_start_time is None:
            max_start_time = min_start_time + timedelta(days=7)
        else:
            max_start_time = validate_time(max_start_time)
        if not min_start_time or not max_start_time:
            raise TypeError('start_time must be a datetime object containing date, hours and minutes')
        if minutes_grid_span%self.slot_minutes_duration or minutes_grid_span<=0:
            raise ValueError(f'Grid span must be a multiple of {self.slot_minutes_duration}')
        if max_start_time<min_start_time:
            return ([], [])

        n_slots_needed = math.ceil(minutes_duration / self.slot_minutes_duration)
        available_default_slots, available_special_slots = [], []

        segments_involved = self._get_segments_involved(start_time=min_start_time, end_time=max_start_time+timedelta(minutes=minutes_duration))
        for segment in segments_involved:
            slot_list = segment.get_slots_slice(start_time=min_start_time, end_time=max_start_time + timedelta(minutes=minutes_duration))
            segment_available_default_slots, segment_available_special_slots = [], []
            last_slot_booked = False
            for i in range(len(slot_list) - n_slots_needed + 1):
                current_slot = slot_list[i]
                current_slot_group = slot_list[i : i + n_slots_needed]
                if not any(s.is_booked for s in current_slot_group): ###if the whole slots'group needed is available
                    if not segment.minutes_mismatch_from_default(curr_datetime=current_slot.start_time, default_minutes_grid_range=minutes_grid_span):
                        segment_available_default_slots.append(current_slot) # Append current slot object to the default availabilities 
                    elif last_slot_booked:
                        segment_available_special_slots.append(current_slot)  ##appending current slot as special slot if it starts right after a previous reservation finished
                last_slot_booked=current_slot.is_booked 

            available_default_slots.append(segment_available_default_slots)
            available_special_slots.append(segment_available_special_slots)
        if not split_by_segment:
            available_default_slots = [e for l in available_default_slots for e in l]
            available_special_slots = [e for l in available_special_slots for e in l]
            return available_default_slots, available_special_slots  
        else:
            return list(zip(*[available_default_slots,available_special_slots]))

    def lock_slots(self, sorted_slots):
        """
        Lock the given slots in a consistent order to avoid deadlocks.
        """
        for slot in sorted_slots:
            slot._lock.acquire()
        return sorted_slots  # return locked slots so caller knows what’s locked

    def unlock_slots(self, sorted_slots):
        """
        Release locks for the given slots.
        """
        for slot in sorted_slots:
            slot._lock.release()

    def reserve_slots(self, slots):
        if any(slot.is_booked for slot in slots):
            raise AlreadyBookedError('Already booked')
        for slot in slots:
            slot.is_booked=True
        return True
    

    def free_slots(self, slots):
        for slot in slots:
            slot.reset()
        return True
