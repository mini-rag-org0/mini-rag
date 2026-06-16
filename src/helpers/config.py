from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
from functools import lru_cache

class Setting(BaseSettings):

    APP_NAME : str
    APP_VERSION : str

    
    FILE_ALLOWED_TYPE: list
    FILE_MAX_SIZE: int
    FILE_DEFAULT_CHUNK_SIZE : int

    #MONGO_URL:  str
    #MONGODB_DATABASE : str

    POSTGRES_USERNAME: str
    POSTGRES_PASSWORD : str
    POSTGRES_HOST : str
    POSTGRES_PORT : int
    POSTGRES_MAIN_DATABASE : str 

    GENERATION_BACEND : str
    EMBEDDING_BACKEND : str  


    OPENAI_API_KEY : str = None
    OPENAI_API_URL : str = None
    COHERE_API_KEY : str = None

    GENERATION_MODEL_ID_LITERAL: List[str] = None
    GENERATION_MODEL_ID : str = None
    EMBEDDING_MODEL_ID : str = None
    EMBEDDING_SIZE : int = None
    INPUT_DEFAULT_MAX_CHARACTERS : int = None
    GENERATION_DEFAULT_MAX_TOKENS : int = None
    GENERATION_DEFAULT_TEMPERATURE: float = None

    VECTOR_DB_BACKEND_LITERAL: List[str] = None
    VECTOR_DB_BACKEND :str 
    VECTOR_DB_PATH :str
    VECTOR_DB_DISTANCE_METHOD :str = None
    VECTOR_DB_PGVEC_INDEX_THRESHOLD : int = 100

    DEFAULT_LANG : str = "en"
    PRIMARY_LANG : str = "en"

    ARABIC_DIALECT : str = None  # e.g. "كويتية", "أردنية", "مصرية"


    # WhatsApp Cloud API Settings
    WHATSAPP_VERIFY_TOKEN: str = None
    WHATSAPP_API_TOKEN: str = None
    WHATSAPP_PHONE_NUMBER_ID: str = None
    WHATSAPP_DEFAULT_PROJECT_ID: str = None

    model_config = SettingsConfigDict(env_file=".env")


@lru_cache()
def get_setting():
    return Setting()

