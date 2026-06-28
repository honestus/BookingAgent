from google import genai
from huggingface_hub import AsyncInferenceClient
from enum import Enum
from abc import abstractmethod


class ModelType(Enum):
    GEMINI = 'g'
    HUGGINGFACE = 'hf'


class LLMModel:
    def __init__(self, model_type: ModelType, api_key: str, model_name: str = None):
        """
        model must be either ModelType.GEMINI or ModelType.HUGGINGFACE
        api_key must be the corresponding api_key (i.e. gemini api_key or huggingface_hub api_key)
        model_name is the corresponding huggingface model, if None is provided: llama3.1-8b will be the used by default.
        Notice that model_name is ignored if model!='huggingface'
        """
        self._model_type = LLMModel.validate_model_type(model_type)
        self._api_key = api_key
        self._model_name = model_name
        self._any_change = True
        self._build()

    def update(self, model_type: ModelType = None, api_key: str = None, model_name: str = None):
        if all(e is None for e in (model_type, api_key, model_name)):
            return

        self._any_change = True
        if model_type is not None:
            self._model_type = LLMModel.validate_model_type(model_type)
        if api_key is not None:
            self._api_key = api_key
        if model_name is not None:
            self._model_name = model_name

        self._build()

    async def run(self, prompt: str) -> str:
        if self._any_change:
            self._build()
        model_resp = await self._get_llm_reply(prompt)
        response_text = self._llm_response_to_str(model_resp)
        return response_text

    def __new__(cls, model_type: ModelType, api_key: str, model_name: str = None):
        if cls is LLMModel:  # chiamato direttamente su LLMModel
            if model_type == ModelType.GEMINI:
                concrete_cls = GeminiModel
            elif model_type == ModelType.HUGGINGFACE:
                concrete_cls = HuggingfaceModel
            else:
                raise ValueError(f'Unknown model type: {model_type}')
            return super().__new__(concrete_cls)
        return super().__new__(cls)

    @abstractmethod
    def _build(self):
        pass

    @abstractmethod
    def _llm_response_to_str(self, response) -> str:
        pass

    @abstractmethod
    async def _get_llm_reply(self, message: str):
        pass

    def __setattr__(self, attribute, value):
        if attribute in ['model']:
            raise ValueError(f'Cannot set {attribute}. It is final. Instantiate a new LLMModel instead.')
        if attribute in ['_model_type', '_api_key', '_model_name']:
            self._any_change = True
        return super().__setattr__(attribute, value)

    @staticmethod
    def validate_model_type(model):
        if model not in set(ModelType):
            raise ValueError(f'Unknown model {model}. ')
        return model


class GeminiModel(LLMModel):
    _available_models = None

    def _build(self):
        if not self._any_change:
            return

        if GeminiModel._get_available_models() is None:
            GeminiModel._build_available_models(self._api_key)

        self._model_name = GeminiModel._validate_model_name(self._model_name)
        # genai.Client exposes BOTH sync (client.models.*) and async
        # (client.aio.models.*) interfaces from the same Client instance --
        # no separate async client class needed, unlike huggingface_hub.
        object.__setattr__(self, 'model', genai.Client(api_key=self._api_key))
        self._any_change = False

    def _llm_response_to_str(self, response):
        return response.text

    async def _get_llm_reply(self, message):
        # .aio.models.generate_content is the native async equivalent of
        # .models.generate_content -- awaiting it yields control back to
        # the event loop during the HTTP call instead of blocking it.
        return await self.model.aio.models.generate_content(
            model=self._model_name,
            contents=message,
        )

    @staticmethod
    def _validate_model_name(model_name):
        if not GeminiModel._get_available_models():
            raise UnboundLocalError('No available models. Ensure to initialize them by running _build_available_models with a proper api_key')

        if model_name is None:
            model_name = 'gemini-flash-latest'

        if model_name not in GeminiModel._get_available_models():
            raise ValueError('invalid model_name')
        return model_name

    @classmethod
    def _build_available_models(cls, api_key):
        client = genai.Client(api_key=api_key)
        cls._available_models = [
            m.name.split('models/')[-1]
            for m in client.models.list()
        ]

    @classmethod
    def _get_available_models(cls):
        return cls._available_models


class HuggingfaceModel(LLMModel):

    def _build(self):
        if not self._any_change:
            return

        self._model_name = HuggingfaceModel._validate_model_name(self._model_name)
        # AsyncInferenceClient is a SEPARATE class from the sync
        # InferenceClient (not a namespace on the same object, unlike
        # genai.Client.aio) -- same init signature and same exception
        # hierarchy as the sync client, per huggingface_hub's docs.
        object.__setattr__(self, 'model', AsyncInferenceClient(api_key=self._api_key, model=self._model_name))
        self._any_change = False

    def _llm_response_to_str(self, response):
        return response.choices[0].message.content

    async def _get_llm_reply(self, message):
        msg_dct = {"role": "user", "content": message}
        response = await self.model.chat.completions.create(
            messages=[msg_dct],
        )
        return response

    @staticmethod
    def _validate_model_name(model_name):
        if model_name is None:
            model_name = 'meta-llama/llama-3.1-8b-instruct'
        return model_name