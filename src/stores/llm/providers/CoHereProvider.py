from ..LLMInterface import LLMInterface
from ..LLMEnums import CohereEnum , DocumentType
import cohere
import logging
from typing import List, Union

class CoHereProvider(LLMInterface):

    def __init__(self, api_key: str,
                 default_input_max_characters: int=1000,
                 default_generation_max_output_tokens: int=1000,
                 default_generation_temprature: float=0.1):
        
        self.api_key = api_key

        self.default_input_max_characters = default_input_max_characters
        self.default_generation_max_output_tokens = default_generation_max_output_tokens
        self.default_generation_temprature = default_generation_temprature

        self.generation_model_id = None

        self.embedding_size = None
        self.embedding_model_id = None

        self.client = cohere.AsyncClient(api_key = self.api_key)

        self.logger= logging.getLogger(__name__)

        self.enums = CohereEnum

    def set_generation_model(self, model_id: str):
        self.generation_model_id = model_id

    def set_embedding_model(self, model_id: str, embedding_size:int):
        self.embedding_model_id = model_id
        self.embedding_size = embedding_size


    def process_text(self, text: str):
        processed = text.strip()
        if not processed:
                return "empty"
        return processed
                


    async def generate_text(self, prompt: str,chat_history:list = None , max_output_tokens: int=None,
                      temperature: float = None ):
        if chat_history is None:
            chat_history = []
        if not self.client:
            self.logger.error("CoHere client was not set")
            return None
        
        if not self.generation_model_id:
             self.logger.error("Generation model for CoHere was not set")
             return None
        
        max_output_tokens = max_output_tokens if max_output_tokens else self.default_generation_max_output_tokens
        temperature = temperature if temperature else self.default_generation_temprature 
        
        response = await self.client.chat(
            model =self.generation_model_id,
            chat_history = chat_history,
            message = self.process_text(prompt),
            temperature =temperature,
            max_tokens= max_output_tokens
        )


        if not response or not response.text:
            self.logger.error("Error while generating text with CoHere")
            return None

        return response.text


    async def embed_text(self, text: Union[str,List[str]], document_type: str = None):
        if not self.client:
            self.logger.error("CoHere client was not set")
            return None
        
        if isinstance(text,str):
            text = [text]
        
        if not self.embedding_model_id:
             self.logger.error("Embedding model for CoHere was not set")
             return None
        
        input_type = CohereEnum.DOCUMENT.value
        if document_type == DocumentType.QUERY.value:
            input_type = CohereEnum.QUERY.value

        response = await self.client.embed(
            model = self.embedding_model_id,
            texts = [self.process_text(t)for t in text ],
            input_type = input_type,
            embedding_types = ['float']
        )
        
        if response is None or getattr(response, 'embeddings', None) is None or not hasattr(response.embeddings, 'float'):
            self.logger.error("Error while embedding text with CoHere")
            return None
        
        return [f for f in response.embeddings.float ]


    def construct_prompt(self, prompt:str, role: str):
        return{
            "role" : role,
            "text": prompt
        }