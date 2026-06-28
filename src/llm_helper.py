from __future__ import annotations
from shared.globals_shared import *
import datetime as dt
from typing import Any


_end_of_prompt = f"Dont generate any code. Return the output very short as:  __start__{{{REQUEST_ATTRIBUTE!r}: [{{{METHOD_ATTRIBUTE!r}: method_name, {PARAMS_ATTRIBUTE!r}: {{param_name: param_value}}, {MISSING_PARAMS_ATTRIBUTE!r}: [param_name]}},] , {REPLY_ATTRIBUTE}: reply_message, {USER_LANGUAGE_ATTRIBUTE}: lang }}__end__"


_choose_action_prompt = "You are a barber reservation assistant.\
Your role is to help understanding the client request and map it to one of the following Python methods: \n\
{exposed_methods} \n" + \
f"If the user request clearly corresponds to one of the available methods, select that method and validate its parameters using a dynamic Python-like approach.\n\
Only include parameters whose values can be directly and unambiguously inferred from the user's message.\n\
For datetime values, use datetime.datetime to refer to the datetime class.\n\
For relative datetime expressions (e.g. 'tomorrow', 'next week', 'yesterday', 'in 2 hours'), use datetime.datetime.now() as the temporal baseline and datetime.timedelta for computations.\n\
Never infer the 'user' parameter from chat roles, usernames, speaker labels, metadata, or conversation structure.\
Infer 'user' only if the person is explicitly mentioned inside CURRENT_MSG content. Use the chat role only for reply purposes, only if chat role is a friendly nickname.\n\
If a parameter value is not explicitly provided in the user's message: include its name inside {MISSING_PARAMS_ATTRIBUTE}, and explicitly ask the user to be more specific about such parameter, in the reply message.\n\
Return the selected method inside {METHOD_ATTRIBUTE}, the validated parameters inside {PARAMS_ATTRIBUTE}, and all required missing parameters inside {MISSING_PARAMS_ATTRIBUTE}.\n\
If the user asks for multiple atomic operations at once, validate each of them separately and add them in the 'requests' list on the response.\n\
Generate a friendly reply message to answer the user request in the same language of the user, it must only include friendly natural language words, and must never include any reference to code. Include the most important details of the user request, i.e. which parameters are used to satisfy his request. Comunicate them in very user friendly language.\n\
When you are successfully validating method and parameters (no missing parameters), the reply message must only confirm you are actually working on the request. \n\
You must always validate the corresponding Python 'method' and 'params' to the user request, don't ever answer based on previous conversation.\n\
If the request is not associated with any method: set {REQUEST_ATTRIBUTE} as list(), and try answering with the other info you have, for example price/service/duration.\n" +\
"The services offered by the barber are:\n {services}.\n\
The default opening hours are: {opening_hours}.\n\
Current user reservations: {user_reservations}.\n"



_prompt_to_reply_to_user = "You are a barber reservation assistant. Your role is System. \n\
    Your task is to transform the system output into a friendly, precise and concise message for the human user.\n\
       Keep the output simple and concise, by giving the details useful for the user. \
       When user is asking for availabilities, ask him if he wants to proceed with the booking. \n\
       Give the user feedback based on the success/fail of the operation, and report the details of the output, in a friendly human readable message. \n\
       If the operation needs further confirmation, explicitly tell the user the details of the data (e.g. service name, service info, reservation start_time. NO IDS) and tell him if we wants to proceed; dont report ids, or any domain related info not useful to the user.\n\
       Whenever an operation fails because it is out of working hours, kindly remind the user such opening hours: {opening_hours} .\n\
      If an operation fails because the requested service does not exist, remind him that current available services are: {services} .\n\
      Think and generate the message in the same language of the user, grammatically and formally correct, and be kind. Only focus into mapping the system operation and system output into the user natural language idiom, by giving him details. You are actually talking to the user. \n\
      {previous_conversation}\n\
      Current system operations performed: \n\
      [OPERATIONS]{operation_request_success_result_str}[OPERATIONS_END]\n\
      Generate the reply in the current language: {user_language}. "
 


def _build_conversation_str_(conversation: str, current_message: str):
    final_str = ''
    if conversation:
       final_str+=f"[CONVERSATION_START]:\n{conversation}\n[CONVERSATION_END]\n"
    if current_message:
        final_str+=f"[CURRENT_MSG_START]:\n{current_message}\n[CURRENT_MSG_END]\n" 
    return final_str

def build_backend_request_prompt(username: str, user_message: str, past_conversation_messages: list[tuple[str, str]], allowed_methods: list[str], services: list["Service"], opening_hours: list[tuple[dt.datetime, dt.datetime]], reservations: dict[str, list["Reservation"]]):
    formatted_available_services = ';\n'.join(map(str, services))
    
    formatted_opening_hours = [f'from {start.isoformat()} to {end.isoformat()}' for start, end in opening_hours]
    formatted_opening_hours = ", ".join(formatted_opening_hours)
    
    formatted_reservations = [
        f'{res_type.capitalize()}:\n' + '\n'.join(f'  {res}' for res in res_objects)
            for res_type, res_objects in reservations.items()
                if res_objects
    ]
    formatted_reservations = '\n'.join(formatted_reservations) or '[]'

    formatted_allowed_methods = '- ' + ';\n- '.join(allowed_methods)
    formatted_convers = render_user_past_conversation_messages(past_conversation_messages)
    formatted_current_msg = render_user_past_conversation_messages([(username, user_message)])

    #last_request_method = self.last_structured_requests[-1] if self.last_structured_requests else None
    last_request_method = ''
    prompt = _choose_action_prompt.format(services=formatted_available_services, 
                                opening_hours=formatted_opening_hours, 
                                user_reservations=formatted_reservations, 
                                exposed_methods=formatted_allowed_methods,
                                
    ) \
    + _build_conversation_str_(conversation = formatted_convers,
                                current_message = formatted_current_msg,) \
    + _end_of_prompt
        
    return prompt.strip()
    
    
def build_user_reply_prompt(services: list["Service"], opening_hours: list[tuple[dt.datetime, dt.datetime]], actions_performed_and_outputs_info: list[str], past_conversation_messages: list[tuple[str, str]], user_nickname: str=None, user_language: str=None):
    from application.request_response import ResponseErrorCode
    
    formatted_convers = render_user_past_conversation_messages(past_conversation_messages)
    formatted_available_services = ';\n'.join(map(str, services))
    formatted_opening_hours = [f'from {start.isoformat()} to {end.isoformat()}' for start, end in opening_hours]
    formatted_opening_hours = ", ".join(formatted_opening_hours)
    
    err_str = 'error_info'
    res_str = 'result'
    
    operations_results_str = ('\n' if actions_performed_and_outputs_info else '') + '\n'.join(actions_performed_and_outputs_info)
    
    if user_language is None:
        user_language='derive language from user messages'
    
    return _prompt_to_reply_to_user.format(user_language=user_language, 
        operation_request_success_result_str = operations_results_str,
        previous_conversation=formatted_convers, opening_hours=formatted_opening_hours, 
        services=formatted_available_services,
    )


_expected_types = {USER_ATTRIBUTE: str, USER_LANGUAGE_ATTRIBUTE: str, REPLY_ATTRIBUTE: str, REQUEST_ATTRIBUTE: list, f'{REQUEST_ATTRIBUTE}.*.{METHOD_ATTRIBUTE}': str, f'{REQUEST_ATTRIBUTE}.*.{PARAMS_ATTRIBUTE}': dict }
_expected_keys = [USER_LANGUAGE_ATTRIBUTE, REPLY_ATTRIBUTE, REQUEST_ATTRIBUTE, ]
_default_values = {USER_LANGUAGE_ATTRIBUTE:None, REPLY_ATTRIBUTE:'', REQUEST_ATTRIBUTE:[], }


def model_reply_to_dict(model_reply: str, ) -> dict[str, Any]:
    from utils.parsing_utils import parse_to_dict, cast_data

    try:
        model_reply = model_reply.strip().split('__start__')[-1].split('__end__')[0].strip()
        reply_dict = parse_to_dict(model_reply)
        
        casted_response_dict = cast_data(data=reply_dict, schema=_expected_types, strict=True, drop_unexpected=False, map_keys_to_str=True, map_keys_to_lower=True)
        """
        if expected_types_by_method is not None:
            for single_request in casted_response_dict.get(REQUEST_ATTRIBUTE, []):
                curr_method = single_request.get(METHOD_ATTRIBUTE, None)
                curr_params = single_request.get(PARAMS_ATTRIBUTE, None)
                if curr_method is None or curr_params is None:
                    continue
                #get method expected params -> run cast_data
                method_expected_types = expected_types_by_method.get(curr_method, {})
                are_all_expected_params_lower = all(k.lower()==k for k in method_expected_types)
                single_request[PARAMS_ATTRIBUTE] = cast_data(data=curr_params, schema=method_expected_types, map_keys_to_lower=are_all_expected_params_lower, strict=True, drop_unexpected=True)
        """
        response_dict_with_default_keys = _default_values | casted_response_dict
        
        return response_dict_with_default_keys
    except:
        raise 
        
def handle_message(model: "LLMModel", user_message: str, allowed_methods: list[str], services: list["Service"], opening_hours: list[tuple[dt.datetime, dt.datetime]], user_reservations: list["Reservation"] = []):
    prompt = build_backend_request_prompt(user_message=preprocess_user_message(user_message), allowed_methods=allowed_methods, services=services, opening_hours=opening_hours, reservations=user_reservations)
    model_reply = model.run(prompt)
    response_dict = model_reply_to_dict(model_reply)
    
    return response_dict






        
def render_user_past_conversation_messages(user_past_conversation: list[tuple[str, str]]):
    if user_past_conversation:
        past_conversation_rended_list = [f'{sender.capitalize()} : {message}' for sender, message in user_past_conversation]
        return '\n'.join(past_conversation_rended_list)
    return ''

def preprocess_user_message(message):
    return message
    
    



