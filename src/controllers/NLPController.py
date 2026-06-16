from .BaseController import BaseController
from models.db_schemes import Project ,DataChunk
from typing import List
from stores.llm.LLMEnums import DocumentType
import json

class NLPController(BaseController):

    def __init__(self, generation_client, embedding_client, 
                 vectordb_client, template_parser ):
        super().__init__()

        self.vectordb_client = vectordb_client
        self.generation_client = generation_client
        self.embedding_client = embedding_client
        self.template_parser = template_parser


    def create_collection_name(self, project_id: str):
        return f"collection_{self.vectordb_client.default_vector_size}_{project_id}".strip()
    
    async def reset_vector_db_collection(self, project: Project):
        collection_name = self.create_collection_name(project_id= project.project_id)
        return await self.vectordb_client.delete_collection(collection_name= collection_name)
    

    async def get_vectordb_collection_info(self, project:Project):
        collection_name = self.create_collection_name(project_id= project.project_id)
        collection_info = await self.vectordb_client.get_collection_info(collection_name = collection_name)

        return json.loads(
            json.dumps(collection_info,default= lambda x: x.__dict__)
        )

        
    
    async def index_into_vector_db(self, project: Project , chunks: List[DataChunk],
                             chunks_ids: List[int],
                             do_reset: bool = False):
        
        # step1 : get collection name
        collection_name = self.create_collection_name(project_id= project.project_id)

        # step2: manage items
        texts = [c.chunk_text for c in chunks ]
        metadata = [c.chunk_metadata for c in chunks]
        vectors = await self.embedding_client.embed_text(
            text = texts,
            document_type = DocumentType.DOCUMENT.value
        )

        # step3: create collection if not exists 
        _ = await self.vectordb_client.create_collection(
            collection_name = collection_name ,
            embedding_size = self.embedding_client.embedding_size ,
            do_reset = do_reset,
            
        )



        # step4: insert into vector db

        _ = await self.vectordb_client.insert_many(
            collection_name=collection_name,
            texts = texts,
            metadata = metadata,
            vectors = vectors,
            record_ids = chunks_ids
            
        )

        return True
    
    async def search_vector_db_collection(self, project:Project, text: str, limit: int = 10):

        #step1: get collection name 
        query_vector = None
        collection_name = self.create_collection_name(project_id= project.project_id)

        #step2: get text embedding vector
        vectors = await self.embedding_client.embed_text(
            text = text, 
            document_type = DocumentType.QUERY.value

        )

        if not vectors or len(vectors)==0:
            return False
        
        if isinstance(vectors,list) and len(vectors) > 0:
            query_vector =vectors[0]
        
        if not query_vector:
            return False
        #step3: do semantic search

        results = await self.vectordb_client.search_by_vector(
            collection_name=collection_name,
            vector =query_vector,
            limit = limit
        )

        if not results:
            return False

        return results
    

    async def answer_rag_question(self, project:Project, query: str, limit: int = 10, chat_history: list = None, language_instruction: str = None):

        if chat_history is None:
            chat_history = []

        answer , full_prompt , ret_chat_history = None, None, None

        # --- تحسين البحث عبر إعادة صياغة السؤال (Query Rewriting) ---
        search_query = query
        if len(chat_history) > 0:
            # 1. تحويل سجل المحادثة إلى نص مقروء
            chat_history_str = "\n".join([f"{'المستخدم' if msg['role'] == 'user' else 'المساعد'}: {msg['content']}" for msg in chat_history])
            
            # 2. إحضار القالب الخاص بإعادة صياغة السؤال
            condense_prompt = self.template_parser.get("rag", "condense_question_prompt", {
                "chat_history": chat_history_str,
                "query": query
            })
            
            # 3. نطلب من الموديل إعادة صياغة السؤال ليصبح مستقلاً
            rewritten_query = await self.generation_client.generate_text(
                prompt=condense_prompt,
                chat_history=[], # نرسل القالب كسؤال جديد بدون تاريخ إضافي
                max_output_tokens=100 # لا نحتاج لإجابة طويلة، فقط السؤال
            )
            
            if rewritten_query:
                search_query = rewritten_query.strip()
                print(f"\n[DEBUG] Original Query: {query}", flush=True)
                print(f"[DEBUG] Rewritten Query: {search_query}\n", flush=True)

         #step1: retrieve related document 
        retrieved_documents = await self.search_vector_db_collection(
            project=project,
            text= search_query,
            limit=limit,
            )
        
        if not retrieved_documents or len(retrieved_documents)==0:
            return answer , full_prompt , chat_history 
        
        #step2: construct LLM prompt

        dialect = self.app_settings.ARABIC_DIALECT or ""
        dialect_instruction = f"عند الرد بالعربية، يجب أن تكون إجابتك باللهجة {dialect}." if dialect else ""
        system_prompt = self.template_parser.get("rag", "system_prompt", {
            "dialect_instruction": dialect_instruction
        })

        
        documents_prompts = "\n".join([
            self.template_parser.get("rag", "document_prompt", {
                    "doc_num": idx + 1,
                    "chunk_text": self.generation_client.process_text(doc.text),
            })
            for idx, doc in enumerate(retrieved_documents)
        ])

        footer_prompt = self.template_parser.get("rag","footer_template",{
            "query": f"{language_instruction}\n{query}" if language_instruction else query
        })
        
        
        
        base_chat_history = [

            self.generation_client.construct_prompt(
                prompt = system_prompt,
                role = self.generation_client.enums.SYSTEM.value
            )
        ]

        for msg in chat_history:
            # We skip adding full_prompt and sources from previous messages,
            # and just pass the text content of previous user queries and assistant answers
            # This logic assumes Streamlit already passes {"role": "...", "content": "..."}
            base_chat_history.append(
                self.generation_client.construct_prompt(
                    prompt=msg['content'],
                    role=msg['role']
                )
            )

        full_prompt = "\n\n".join([ documents_prompts,  footer_prompt])

        # step4: Retrieve the Answer
        answer = await self.generation_client.generate_text(
            prompt =full_prompt,
            chat_history = base_chat_history
        )

        return answer , full_prompt , base_chat_history