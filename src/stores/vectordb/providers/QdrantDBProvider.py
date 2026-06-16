from qdrant_client import models, QdrantClient
from ..VectorDBInterface import VectorDBInterface
from ..VectorDBEnums import DistnaceMethodEnum
from typing import List
import logging
from models.db_schemes import RetrieveDocument

class QdrantDBProvider(VectorDBInterface):
    
    def __init__(self, db_client: str, default_vector_size: int = 786,
                    distance_method: str = None, index_threshold : int = 100):
        
        self.client = None
        self.db_client = db_client
        self.distance_method = None
        self.defaulf_vector_size = default_vector_size

        if distance_method == DistnaceMethodEnum.COSINE.value:
            self.distance_method = models.Distance.COSINE
        elif distance_method == DistnaceMethodEnum.DOT.value:
            self.distance_method = models.Distance.DOT


        self.logger = logging.getLogger("uvicorn")

    async def connect(self):
        self.client = QdrantClient(path = self.db_client)

    async def disconnect(self):
        self.client = None

    async def is_collection_existed(self, collection_name: str)->bool :
        return self.client.collection_exists(collection_name=collection_name)
    
    async def list_all_collection(self)->List :
        return self.client.get_collection()
    
    async def get_collection_info(self, collection_name: str)->dict:
        return self.client.get_collection(collection_name=collection_name)
    
    async def delete_collection(self, collection_name:str):
        if await self.is_collection_existed(collection_name):
            self.logger.info(f"Deleting collection: {collection_name}")
            return self.client.delete_collection(collection_name=collection_name)

    async def create_collection(self,collection_name: str,
                          embedding_size: int,
                          do_reset: bool = False):
        if do_reset:
            _ = await self.delete_collection(collection_name=collection_name)
        

        if not await self.is_collection_existed(collection_name):
            self.logger.info(f"Creating new qdrant collection: {collection_name}")
            _ = self.client.create_collection(
                collection_name=collection_name,
                vectors_config = models.VectorParams(
                    size =embedding_size,
                    distance = self.distance_method

                )
            )
            return True
        return False
    
    async def insert_one(self,  collection_name:str, text:str, vector:list,
                   metadata: dict = None,
                   record_id :str = None):
        
        if not await self.is_collection_existed(collection_name):
            self.logger.error(f"Can not insert new record to non-existed collection{collection_name}:")
            return False
        try:
            _ = self.client.upload_records(
                collection_name = collection_name,
                records =[

                    models.Record(
                        id= [record_id],
                        vector = vector,
                        payload = {
                            "text": text,
                            "metadata" : metadata
                        }
                    )
                ]
            )
        
        except Exception as e :
            self.logger.error(f"Error while inserting batch:{e}")
            return False
        
        return True
    
    async def insert_many(self,  collection_name:str, texts:str, vectors:list,
                   metadata: dict = None,
                   record_ids :str = None, batch_size: int = 50):
        
            if metadata is None:
                metadata = [None] * len(texts)

            if record_ids is None:
                record_ids = list(range(0,len(texts)))

            for i in range(0,len(texts),batch_size):
                batch_end = i + batch_size

                batch_text = texts[i:batch_end]
                batch_vector = vectors[i:batch_end]
                batch_metadate = metadata[i:batch_end]
                batch_of_record_ids = record_ids[i:batch_end]
                batch_record=[

                        models.Record(
                            id = batch_of_record_ids[x],
                            vector = batch_vector[x],
                            payload = {
                            "text": batch_text[x],
                            "metadata" : batch_metadate[x]
                    }
                )
            
        


                    for x in range(len(batch_text))
                ]

                try:
                    _ = self.client.upload_records(
                        collection_name = collection_name,
                        records =batch_record,
                    )
                except Exception as e :
                    self.logger.error(f"Error while inserting batch:{e}")
                    return False

            return True
    
    async def search_by_vector(self, collection_name:str, vector:list, limit: int = 5):
        
        results =  self.client.search(
            collection_name=collection_name,
            query_vector = vector,
            limit = limit
        )

        if not results or len(results)==0:
            return None
        
        return [

            RetrieveDocument(**{
                "score" : result.score,
                "text": result.payload["text"]

            })
            for result in results
        ]