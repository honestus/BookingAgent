from google import genai
from huggingface_hub import InferenceClient
import datetime, time, warnings, ast
from globals_shared import *
from datetimes_utils import map_datetime_to_next_slot_datetime
from collections import defaultdict


prompt = "You are a barber reservation assistant.\
The services offered by the barber are: \n{services}.\n\
The default opening hours are: {opening_hours}.\n\
Current user reservations: {user_reservations}.\n \
Your role is to help understanding the client request and map it to one of the following Python methods: \n\
{exposed_methods} \n" + \
f"If the request is strictly associated with a method: validate the needed parameters by using dynamic approach in python. For all the parameters to validate, only validate those that are explicitly in the user request; otherwise use their default value, or ask the user to include them if they have no default.\n\
To validate datetime objects, use datetime.datetime to refer to datetime class. For all the relative datetimes (e.g. tomorrow, next week, yesterday) validate using datetime.datetime.now() as baseline, and using timedelta.\n\
When user asks for a whole day or date, use start_time=datetime.datetime.combine(whole_date, datetime.time.min), end_time=datetime.datetime.combine(whole_date, datetime.time.max) .\n\
Include the corresponding method as {METHOD_ATTRIBUTE}, the validated parameters as {PARAMS_ATTRIBUTE}; for any missing parameters, include them as {MISSING_PARAMS_ATTRIBUTE}.\n\
Generate a friendly reply message to answer the user request in the same language of the user, it must only include friendly natural language words, and must never include any reference to code. Include the most important details of the user request, i.e. which parameters are used to satisfy his request. Comunicate them in very user friendly language.\n\
When you are successfully validating method and parameters (no missing parameters), the reply message must only confirm you are actually working on the request. \n' \
If the request is not associated with any method: set {METHOD_ATTRIBUTE} as '', {PARAMS_ATTRIBUTE} as dict(), {MISSING_PARAMS_ATTRIBUTE} as [], and try answering with the other info you have, for example price/service/duration.\n\
You must always validate the corresponding Python 'method' and 'params' to the user request, don't ever answer based on previous conversation.\n"

end_of_prompt = f"Dont generate any code. Return the output very short as:  __start__{{ {METHOD_ATTRIBUTE!r}: method_name, {PARAMS_ATTRIBUTE!r}: {{param_name: param_value}}, 'reply_to_user': reply_message, {MISSING_PARAMS_ATTRIBUTE!r}: [param_name], 'user_language': lang}}__end__"


output_to_user_prompt = f"You are a barber reservation assistant. Your role is System. \n\
    Your task is to transform the system output into a friendly, precise and concise message for the human user.\n\
       Keep the output simple and concise, by giving the details useful for the user. \
       When user is asking for availabilities, ask him if he wants to proceed with the booking. \
       If the operation is a final operation (e.g. new booking, new service, cancelation/update), give him positive/negative confirmation with details.\n\
      Think and generate the message in the same language of the user, grammatically and formally correct, and be kind. Only focus into mapping the system operation and system output into the user natural language idiom, by giving him details. You are actually talking to the user. \n" + \
      "User language: {user_language}.\n\
      {previous_conversation}\n\
      Current system operation performed: {system_request}\n\
      Current system output: Success: {system_output_bool};\n system response: {system_output};\n {extra}"
      
      
      


NO_PREVIOUS_CONVERSATION_STATUS = 'I'
ERROR_STATUS = 'E'
INVALID_REQUEST_STATUS = 'FIR'
MISSING_PARAMETERS_STATUS = 'FMP'
WAITING_FOR_CONFIRMATION_STATUS = 'WC'
WAITING_FOR_BOOKING_AGENT_STATUS = 'WA'
WAITING_FOR_USER_REPLY_STATUS = 'WU'
VALID_ACTION_PERFORMED_STATUS = 'S'


class _StringifyKeys(ast.NodeTransformer):
    def visit_Dict(self, node):
        # Transform each key into a string literal
        new_keys = []
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                # already a string
                new_keys.append(key)
            else:
                # wrap as string
                new_keys.append(ast.Constant(value=ast.unparse(key)))
        node.keys = new_keys
        return self.generic_visit(node)

def _safe_eval_dict(expr: str) -> dict:
    """
    Returns a dict from expr str that defines the dict -> all of the keys will be mapped to string, i.e. kept as string if already string, otherwise '{key}'
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except Exception as e:
        raise ValueError(f"Invalid expression: {e}")

    # Rewrite dict keys to strings
    tree = _StringifyKeys().visit(tree)
    ast.fix_missing_locations(tree)
    #return(tree)
    dct = eval(compile(tree, "<ast>", "eval"))
    if not isinstance(dct, dict):
        raise ValueError("Expression does not evaluate to a dict")
    return dct
    
    

def validate_llm_model(model):
    if model in ['gemini','huggingface']:
        return model
    raise ValueError(f'Unknown model {model}')


class NLUAgent:
    def __init__(self, business_agent, gemini_key, huggingface_key):
        self.business_agent = business_agent
        
        #self.prompt = prompt
        #self.prompt_output_requested_str = end_of_prompt
        self.past_conversation_messages = defaultdict(list)
        self.last_llm_validated_response = {}

        self.__init_llm__(model='gemini', api_key=gemini_key)
        self.__init_llm__(model='huggingface', api_key=huggingface_key)
        
        self.status = NO_PREVIOUS_CONVERSATION_STATUS            

    def act(self, user_message, user):
        """
        if self.status in [WAITING_FOR_BOOKING_AGENT_STATUS, WAITING_FOR_CONFIRMATION_STATUS, WAITING_FOR_USER_REPLY_STATUS]:
            return
        """
        user_message = self.preprocess_user_message(user_message)
        
        llm_prompt = self.__build_prompt_message__(user=user, user_message=user_message)
        print('Sending to llm:', llm_prompt)
        #return
        try:
            llm_response = self.query_llm(message=llm_prompt, model='gemini', map_to_dct=True)
        except:
            warnings.warn('gemini fail... now huggingface')
            try:
                llm_response = self.query_llm(message=llm_prompt, model='huggingface', map_to_dct=True)
            except Exception as e:
                raise e
        finally:
            self.past_conversation_messages[user].append(('User', user_message))

        llm_response = self.__validate_llm_request_params__(llm_response)
        print('LLM Response user:', llm_response.get(PARAMS_ATTRIBUTE).get('user'))

        llm_response[USER_ATTRIBUTE] = user if user!='system' else ''
        if llm_response.get(PARAMS_ATTRIBUTE, {}).get('user') in ['admin','system']: ##should never happen as far as we don't expose 'user' param in methods written in the prompt
            llm_response[PARAMS_ATTRIBUTE].pop('user') ##removing 'user' from request parameters in order to avoid any potential not-allowed operations
        print('LLM Response:', llm_response)
        self.update_status(llm_response=llm_response)
        print('Status:', self.status)
        message_to_user = llm_response.get(REPLY_ATTRIBUTE)
        validated_method_response = {k:v for k,v in llm_response.items() if k in [METHOD_ATTRIBUTE, PARAMS_ATTRIBUTE, ]}
        print('Msg to user:' , message_to_user)
        self.send_message_to_user(message_to_user)
        self.past_conversation_messages[user].append(('System', message_to_user))
        self.last_llm_validated_response[user]=validated_method_response

        if self.status == WAITING_FOR_BOOKING_AGENT_STATUS:
            print('Making action with params...', llm_response)
            b_a_request, b_a_success, b_a_response_msg, b_a_extra_msg = self.send_request_to_business_agent(request=llm_response)
            if b_a_success:
                self.update_status(status=VALID_ACTION_PERFORMED_STATUS)
                self.last_llm_validated_response.pop(user)

            user_prev_conversation = self.get_user_past_conversation(user)
            last_user_msg_index = -([role for role,msg in user_prev_conversation][::-1].index('User')) 
            prev_conversation_to_include = user_prev_conversation[last_user_msg_index-1:]
            prompt_to_user_friendly = output_to_user_prompt.format(previous_conversation=self.render_user_past_conversation_messages(prev_conversation_to_include),
                                                                 system_request=b_a_request, system_output_bool=b_a_success,
                                                                 system_output=b_a_response_msg, extra=b_a_extra_msg,
                                                                 user_language=llm_response.get('user_language','')
                                                                 )
            print('Sending to chat assistant...', prompt_to_user_friendly)
            try:
                friendly_response = self.query_llm(message=prompt_to_user_friendly, model='huggingface', map_to_dct=False)
            except:
                warnings.warn('huggingface fail... now gemini')
                friendly_response = self.query_llm(message=prompt_to_user_friendly, model='gemini', map_to_dct=False)

            self.past_conversation_messages[user].append(('System', friendly_response))
            print('Msg to user:' , friendly_response)
            self.send_message_to_user(friendly_response)
        if self.status != VALID_ACTION_PERFORMED_STATUS:
            self.update_status(WAITING_FOR_USER_REPLY_STATUS)

        
        
        return

    def __build_prompt_message__(self, user, user_message):
        all_available_services = self.send_request_to_business_agent({METHOD_ATTRIBUTE:'get_available_services',
                                                                     USER_ATTRIBUTE: 'system'}, 
                                                                     force_role=True)[2]
        formatted_available_services = ';\n'.join(map(str, all_available_services))
        default_opening_hours =  self.send_request_to_business_agent({METHOD_ATTRIBUTE: 'get_default_opening_hours',
                                                                     USER_ATTRIBUTE: 'system'}, 
                                                                     force_role=True)[2]
        formatted_opening_hours = [f'from {s.isoformat()} to {e.isoformat()}' for s, e in default_opening_hours]
        formatted_opening_hours = ", ".join(formatted_opening_hours)
        user_reservations = self.send_request_to_business_agent({METHOD_ATTRIBUTE: 'get_user_reservations', 
                                                                 PARAMS_ATTRIBUTE: {'user':user},
                                                             USER_ATTRIBUTE:'system'}, 
                                                               force_role=True)[2]
        formatted_user_reservations = ';\n'.join(map(str,user_reservations))
        available_methods = self.business_agent.get_exposed_methods_params(user, as_string=True)
        formatted_available_methods = '- ' + ';\n- '.join(available_methods)

        last_request_method = self.last_llm_validated_response.get(user)
        
        return prompt.format(services=formatted_available_services, 
                                  opening_hours=formatted_opening_hours, 
                                  user_reservations=formatted_user_reservations, 
                                  exposed_methods=formatted_available_methods) + \
            self.render_user_past_conversation_messages(self.get_user_past_conversation(user)) + \
            (f"\n --- Previous user request': {last_request_method} --- \n" if last_request_method else '') + \
            f"\n --- User:  {user_message} ---\n"+ \
            end_of_prompt

    def update_status(self, status=None, llm_response=None):
        if status is not None:
            self.status = status
            return
        if not llm_response.get(METHOD_ATTRIBUTE):
            self.status = INVALID_REQUEST_STATUS
            return
        if llm_response.get(MISSING_PARAMS_ATTRIBUTE):
            self.status = MISSING_PARAMETERS_STATUS
            return
        if llm_response.get(METHOD_ATTRIBUTE) and llm_response.get(PARAMS_ATTRIBUTE) and not llm_response.get(MISSING_PARAMS_ATTRIBUTE):
            self.status = WAITING_FOR_BOOKING_AGENT_STATUS
            return
        self.status = ERROR_STATUS
        
    def query_llm(self, message, model, map_to_dct=False):
        llm_response = self.__get_llm_response__(message=message, model=model)
        llm_response = self.__llm_response_to_str__(response=llm_response, model=model)
        if not map_to_dct:
            return llm_response
        try:
            llm_response = llm_response.strip().split('__start__')[-1].split('__end__')[0]
            llm_response_dict = _safe_eval_dict(llm_response)
            return llm_response_dict
        except NameError as e:
            raise e

    def send_request_to_business_agent(self, request, force_role=False):
        request = self.__validate_business_agent_request_params__(request)
        return self.business_agent.make_action(request, force_role=force_role)

    def send_message_to_user(self, message):
        print(message)

    def get_user_past_conversation(self, user):
        return self.past_conversation_messages.get(user, '')
        
    def render_user_past_conversation_messages(self, user_past_conversation):
        if user_past_conversation:
            past_conversation_rended_list = [f'{sender} : {message}\n' for sender, message in user_past_conversation]
            return 'Past conversation: ---\n' + ' '.join(past_conversation_rended_list) + '\n ---'
        return ''

    def preprocess_user_message(self, message):
        return message

    def __init_llm__(self, model, api_key):
        if getattr(self, 'llm', None) is None:
            self.llm = {}
        model = validate_llm_model(model)
        self.llm[model] = InferenceClient(api_key=api_key) if model=='huggingface' else genai.Client(api_key=api_key)

    def __llm_response_to_str__(self, response, model):
        model = validate_llm_model(model)
        if model=='gemini':
            return response.text
        return response.choices[0].message.content
    def __get_llm_response__(self, message, model):
        model = validate_llm_model(model)
        if model=='gemini':
            return self.llm['gemini'].models.generate_content(
                model="gemini-2.5-flash",
                contents=message )
        
        return self.llm['huggingface'].chat.completions.create(
                  model="meta-llama/llama-3.1-8b-instruct",
                  messages=[
                    {
                      "role": "user",
                      "content": message
                    }
                  ]
            )

            
    def __validate_llm_request_params__(self, request):
        print('LLM raw request:', request)
        request = {k.lower():v for k,v in request.items()}
        if METHOD_ATTRIBUTE in request:
            request[METHOD_ATTRIBUTE] = request[METHOD_ATTRIBUTE].lower() if request[METHOD_ATTRIBUTE] else ''
        if PARAMS_ATTRIBUTE in request:
            if not request[PARAMS_ATTRIBUTE]:
                request[PARAMS_ATTRIBUTE] = {}
            request[PARAMS_ATTRIBUTE] = {k.lower():v for k,v in request[PARAMS_ATTRIBUTE].items()}
            for time_param in ['start_time', 'end_time', 'old_start_time', 'new_start_time']:
                if time_param in request[PARAMS_ATTRIBUTE]:
                    request[PARAMS_ATTRIBUTE][time_param] = map_datetime_to_next_slot_datetime(request[PARAMS_ATTRIBUTE][time_param])
            for service_param in ['service_name', 'new_service_name', 'old_service_name']:
                if service_param in request[PARAMS_ATTRIBUTE]:
                    request[PARAMS_ATTRIBUTE][service_param] = request[PARAMS_ATTRIBUTE][service_param].lower()
            for duration_param in ['minutes_duration', 'new_minutes_duration', 'old_minutes_duration']:
                if duration_param in request[PARAMS_ATTRIBUTE]:
                    request[PARAMS_ATTRIBUTE][duration_param] = int(request[PARAMS_ATTRIBUTE][duration_param])
        return request


    def __validate_business_agent_request_params__(self, request):
        if METHOD_ATTRIBUTE not in request.keys():
            raise TypeError(f'request must include {METHOD_ATTRIBUTE}')
        if USER_ATTRIBUTE not in request.keys():
            raise TypeError(f'request must include {USER_ATTRIBUTE}')
        if PARAMS_ATTRIBUTE not in request.keys():
            request[PARAMS_ATTRIBUTE] = {}
        return request