import os
import yaml
import datetime


import sys
sys.path.append("C:/Users/onest/Documents/data_analysis/booking_agent/src")

from dotenv import load_dotenv

DAY_NAME_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

from pathlib import Path
import yaml

_SRC_DIR = Path(__file__).resolve().parent

CONFIG_DIR = _SRC_DIR / "config"


def get_users_messages_data_dir():
    app_config_filepath = CONFIG_DIR / 'app_config.yaml'
    app_config = load_yaml(app_config_filepath)
    return _SRC_DIR / app_config["storage"]["users_msgs_dir"]


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def parse_time(time_str: str):
    hour, minute = map(int, time_str.split(":"))
    return hour, minute


def generate_calendar_segments(calendar_config, opening_hours):
    """
    Genera Segment a partire dalla config.
    """
    
    from utils.datetimes_utils import map_datetime_to_default
    from backend.business_calendar import Segment

    slot_duration = calendar_config.get("slot_minutes_duration", 5)

    generation_mode = calendar_config["generation_mode"]

    if generation_mode == "rolling_days":
        future_days = calendar_config["future_days"]

        start_date = datetime.date.today()
        end_date = start_date + datetime.timedelta(days=future_days)

    else:
        start_date = datetime.date.fromisoformat(calendar_config["start_date"])
        end_date = datetime.date.fromisoformat(calendar_config["end_date"])

    current_date = start_date

    segments = []

    while current_date <= end_date:

        """
        weekday_idx = current_date.weekday()

        weekday_name = list(DAY_NAME_TO_INDEX.keys())[weekday_idx]

        daily_ranges = opening_hours.get(weekday_name, [])
        """
        daily_ranges = opening_hours

        for start_str, end_str in daily_ranges:

            start_h, start_m = parse_time(start_str)
            end_h, end_m = parse_time(end_str)

            start_dt = datetime.datetime.combine(
                current_date,
                datetime.time(start_h, start_m)
            )

            end_dt = datetime.datetime.combine(
                current_date,
                datetime.time(end_h, end_m)
            )

            segment = Segment(
                start_time=map_datetime_to_default(start_dt, ignore_seconds=True, map_to_default_tz=True),
                end_time=map_datetime_to_default(end_dt, ignore_seconds=True, map_to_default_tz=True),
                slot_duration=slot_duration,
                force_past_slots=True
            )

            segments.append(segment)

        current_date += datetime.timedelta(days=1)

    return segments

_provider_to_model_type = {
    "gemini": ModelType.GEMINI,
    "huggingface": ModelType.HUGGINGFACE,
}

def build_llm_model(llm_config):
    from llm_agent import ModelType, LLMModel
    
    def _resolve_api_key_value(api_keys_config, api_key):
        return api_keys_config.get(api_key, api_key)

    api_keys = llm_config.get('api_keys', {})
    models = llm_config.get('models', {})

    default_model = llm_config.get("default_model", "gemini_fast")

    model_cfg = models[default_model]
    provider = model_cfg["provider"]
    model_name = model_cfg.get("model_name")
    if 'api_key' in model_cfg:
        api_key = _resolve_api_key_value(api_keys, model_cfg['api_key'])
    else:
        api_key = api_keys.get(model_cfg['provider'], '')
    
    if not api_key:
        raise ValueError(
            f"Missing api_key from llm_config file"
        )

    

    model_type = _provider_to_model_type[provider]

    return LLMModel(
        model_type=model_type,
        api_key=api_key,
        model_name=model_name
    )


def get_backend_system_path() -> Path:
    app_config_filepath = CONFIG_DIR / 'app_config.yaml'
    app_config = load_yaml(app_config_filepath)
    return _SRC_DIR / app_config["storage"]["backend_system_path"]




def _generate_new_business_core_from_config(business_config: dict):
    from backend.policy import PolicyManager, Service
    from backend.reservations import ReservationManager
    from backend.business_core import BusinessCoreWithConfirmation
    from backend.business_calendar import BusinessCalendar
    # =========================
    # SERVICES
    # =========================
    services = []

    for service_cfg in business_config["services"]:
        service = Service(
            service_name=service_cfg["name"],
            price=service_cfg["price"],
            minutes_duration=service_cfg["duration_minutes"],
            description=service_cfg.get("description", "")
        )

        services.append(service)

    # =========================
    # POLICY MANAGER
    # =========================
    policies_cfg = business_config["policies"]
    policy_manager = PolicyManager(services=services,
        min_advance_booking_minutes=policies_cfg["min_advance_booking_minutes"],
        min_advance_cancelation_minutes=policies_cfg["min_advance_cancelation_minutes"],
        opening_hours=[tuple(x) for x in business_config["opening_hours"]]
    )

    # =========================
    # CALENDAR
    # =========================

    calendar = BusinessCalendar(slot_minutes_duration=business_config["calendar"].get("slot_minutes_duration", 5))

    segments = generate_calendar_segments(business_config["calendar"], business_config["opening_hours"])
    for segment in segments:
        calendar.add_segment(segment)

    # =========================
    # RESERVATION MANAGER
    # =========================
    reservation_manager = ReservationManager()
    # =========================
    # BUSINESS CORE
    # =========================
    business_manager = BusinessCoreWithConfirmation(
        reservation_manager=reservation_manager,
        calendar=calendar,
        policy_manager=policy_manager,
        default_grid_minutes=business_config["booking"].get("default_grid_minutes", 15),
        max_confirmation_minutes=policies_cfg.get("max_confirmation_minutes", 15)
    )
    return business_manager

async def initialize_application_orchestrator(load_if_existing: bool = True):
    from backend.backend_storing_utils import load_business_core
    from backend.booking_service import BookingService
    from application.orchestrator import ApplicationOrchestrator
    from application.request_handler import _map_execute_output_to_response
    from application.storing_manager import AppStoringManager
    from application.authenticator import UsersToRoleDB 
    import warnings, asyncio
    
    load_dotenv()

    llm_config = load_yaml(CONFIG_DIR / "llm_config.yaml")
    app_config = load_yaml(CONFIG_DIR / "app_config.yaml")
    
    
    if load_if_existing:
        backend_manager_fp = get_backend_system_path()
        requests_fp = backend_manager_fp.parent / "requests.jsonl"
        storage_manager = AppStoringManager(backend_manager_filepath=backend_manager_fp, requests_filepath=requests_fp)
        try:
            business_manager = await storage_manager.load_manager()
            
        except Exception:
            warnings.warn(f'No backend data found on disk (path: {get_backend_system_path()}). Building a new system from scratch')
            business_config = load_yaml(CONFIG_DIR / "business_config.yaml")
            business_manager = _generate_new_business_core_from_config(business_config)
          
        finally:
            requests_to_execute = await storage_manager.load_requests()
            
    else:
        requests_to_execute = []
        business_config = load_yaml(CONFIG_DIR / "business_config.yaml")
        business_manager = _generate_new_business_core_from_config(business_config)
        
        existing_backend_manager_fp = get_backend_system_path()
        i=0
        while ( (new_backend_fp:=existing_backend_manager_fp.parent / f'backend_{i}/{existing_backend_manager_fp.name}').exists()):
            i+=1
        backend_manager_fp = new_backend_fp
        requests_fp = backend_manager_fp.parent / "requests.jsonl"
        storage_manager = AppStoringManager(backend_manager_filepath=backend_manager_fp, requests_filepath=requests_fp)

    booking_service = BookingService(core=business_manager)

    # =========================
    # USERS DB
    # =========================

    users_db = UsersToRoleDB.from_disk(
        app_config["storage"]["users_db_path"]
    )

    # =========================
    # LLM
    # =========================
    llm_model = build_llm_model(llm_config)
    # =========================
    # ORCHESTRATOR
    # =========================
    
    orchestrator = ApplicationOrchestrator(
        backend_manager=booking_service,
        users_db=users_db,
        llm_model=llm_model,
        storage_manager=storage_manager,
        
    )
    
    all_successes = True
    for req in requests_to_execute:
        try:
            outp = await orchestrator.request_handler._execute_request(req, replay_mode=True)
        except Exception as e:
            outp = e
        resp = _map_execute_output_to_response(outp)
        if not resp.success:
            all_successes=False
            warnings.warn(f'Request {req._id} was not executed successfully!!! \t Error: {resp.error_code}; error_details: {resp.error_msg}')
    if bool(requests_to_execute) and all_successes:
        asyncio.create_task(orchestrator.checkpoint())
        

    return orchestrator