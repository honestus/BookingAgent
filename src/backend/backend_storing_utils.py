import datetime as dt
import json

def store_business_core(business_manager: "BusinessCore", filename_path: str):
    from backend.business_core import BusinessCoreWithConfirmation
    
    with open(filename_path, 'w') as f:
        json_dct = {}
        json_dct['segments'] = [(s.start_time.isoformat(), s.end_time.isoformat(), s.slot_duration) for s in business_manager.calendar.segments]
        json_dct['reservations'] = [r.to_dict() for r in business_manager.reservation_manager.reservations_id_mappings.values()]
        """
        if isinstance(business_manager, BusinessManagerWithConfirmation):
            json_dct['inner_updates_reservations'] = dict()
            for reservation_id in business_manager.__unconfirmed_updates_timestamps__:
                json_dct['inner_updates_reservations'][reservation_id] = business_manager.reservation_manager.get_reservation(reservation_id).get_associated_update_reservation().to_dict()
        """
        policy_manager_dct = {k: v for k,v in business_manager.policy_manager.__dict__.items() if k not in ['services', 'opening_hours', 'default_slot_duration']}
        policy_manager_dct['services'] = [s.to_dict() for s in business_manager.policy_manager.services.values()]
        policy_manager_dct['opening_hours'] = [(h[0].strftime('%H:%M'), h[1].strftime('%H:%M')) for h in business_manager.policy_manager.opening_hours]
        json_dct['policy'] = policy_manager_dct

        json_dct['default_grid_minutes'] = business_manager.default_grid_minutes
        
        if isinstance(business_manager, BusinessCoreWithConfirmation):
            for attr in ['max_confirmation_minutes', '__unconfirmed_updates_timestamps__', '__unconfirmed_services_timestamps__']:
                json_dct[attr] = getattr(business_manager, attr)
        return json.dump(json_dct, f, default=str)

async def load_business_core(json_filepath: str) -> "BusinessCore":
    from backend.business_calendar import BusinessCalendar
    from backend.reservations import ReservationManager
    from backend.policy import Service, PolicyManager
    from backend.business_core import BusinessCore, BusinessCoreWithConfirmation
    
    with open(json_filepath, 'r') as f:
        data_dct = json.load(f)

    segments_times = data_dct.pop('segments')
    calendar = BusinessCalendar(slot_minutes_duration=segments_times[0][-1])
    for start_time, end_time, slots_duration in segments_times: 
        calendar.add_new_segment(start_time=dt.datetime.fromisoformat(start_time), end_time=dt.datetime.fromisoformat(end_time), force_past_slots=True)

    res_manager = ReservationManager()
    reservations = data_dct.pop('reservations')
    for res_dct in reservations:
        reservation = _dict_to_reservation(res_dct)
        await res_manager.insert_reservation(reservation)
        if reservation.is_confirmed or not reservation.is_confirmation_expired():
            await calendar.reserve_slots(calendar.get_slots(start_time=reservation.start_time, end_time=reservation.end_time, same_segment_only=True))
    
    policy_manager_dct = data_dct.pop('policy')
    policy_manager_dct['services'] = [Service(**serv_dct) for serv_dct in policy_manager_dct['services']]
    policy_manager = PolicyManager(**policy_manager_dct)

    default_grid_minutes = data_dct.pop('default_grid_minutes', None)

    if 'max_confirmation_minutes' in data_dct:
        other_attrs = {k:data_dct[k] for k in ['max_confirmation_minutes', '__unconfirmed_updates_timestamps__', '__unconfirmed_services_timestamps__']}
        business_manager = BusinessCoreWithConfirmation(calendar=calendar, reservation_manager=res_manager, policy_manager=policy_manager)
        for attr in other_attrs:
            setattr(business_manager, attr, other_attrs[attr])
    else:    
        business_manager = BusinessCore(calendar=calendar, reservation_manager=res_manager, policy_manager=policy_manager)
    
    if default_grid_minutes:
        business_manager.default_grid_minutes = default_grid_minutes
    return business_manager

def _dict_to_reservation(reservation_dict: dict):
    from backend.reservations import Reservation

    res_default_keys = ['reservation_id', 'user', 'start_time', 'end_time', 'service_name']
    
    res = Reservation(**{k:reservation_dict.pop(k) if k not in ['start_time', 'end_time'] else dt.datetime.fromisoformat(reservation_dict.pop(k)) for k in res_default_keys})
    for k,v in reservation_dict.items():
        if k in ['timestamp', 'status_change_timestamp', '_expires_at']:
            if isinstance(v, str):
                v = dt.datetime.fromisoformat(v)
        if k=='__update_reservation__':
            v = _dict_to_reservation(v)
        object.__setattr__(res, k, v)
    return res