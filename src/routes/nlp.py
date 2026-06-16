from fastapi import FastAPI, APIRouter, Depends, UploadFile, status , Request
from fastapi.responses import JSONResponse
from routes.schemes.nlp import PushRequest,SearchRequest
from models.ProjectModel import ProjectModel
from models.ChunkModel import ChunkModel
from models.ChatModel import ChatModel
from models import ResponseSignal
from controllers import NLPController

import logging




logger = logging.getLogger("uvicorn.error")


nlp_router = APIRouter(
    prefix="/api/v1/nlp",
    tags=["api_v1","nlp"],
)


@nlp_router.post("/index/push/{project_id}")
async def index_projcet(request : Request , project_id: int, push_request:PushRequest):
    
    project_model = await ProjectModel.create_instance(
        db_client = request.app.db_client
    )

    chunk_model = await ChunkModel.create_instance(
        db_client=  request.app.db_client
    ) 


    project = await project_model.get_project_or_create_one(
        project_id = project_id
    )

    if not project:
        return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.PROJECT_NOT_FOUND_ERROR.value
            }
        )
    
    nlp_controller = NLPController(
        vectordb_client= request.app.vectordb_client,
        generation_client = request.app.generation_client,
        embedding_client = request.app.embedding_client,
        template_parser= request.app.template_parser,

    )

    has_records = True
    page_no = 1
    inserted_items_count = 0
    idx = 0

    # create collection if not exists 
    collection_name =nlp_controller.create_collection_name(project_id=project.project_id)

    _ = await request.app.vectordb_client.create_collection(
        collection_name=collection_name,
        embedding_size = request.app.embedding_client.embedding_size,
        do_reset = push_request.do_reset
    )

    # setup batching
    total_chunk_count = await chunk_model.get_total_chunks_count(project_id=project.project_id)
    logger.info(f"Starting vector indexing: {total_chunk_count} chunks for project {project.project_id}")



    while  has_records:
        page_chunks = await chunk_model.get_project_chunks(project_id=project.project_id, page_no=page_no) 
        if len(page_chunks):
            page_no+=1

        if not page_chunks or len(page_chunks)==0:
            has_records = False
            break

        chunks_ids= [c.chunk_id for c in page_chunks]
        idx += len(page_chunks)


        is_inserted =await nlp_controller.index_into_vector_db(
            project= project,
            chunks= page_chunks,
            chunks_ids = chunks_ids 
        )

        if not is_inserted:
            return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.INSERT_INTO_VECTORDB_ERROR.value
            }
        )


        logger.info(f"Indexed {inserted_items_count + len(page_chunks)}/{total_chunk_count} chunks")
        inserted_items_count += len(page_chunks)

    return JSONResponse(
        content={
        "signal": ResponseSignal.INSERT_INTO_VECTORDB_SUCCESS.value,
        "insertd_iteam_count" : inserted_items_count
      
        }
    )
    




@nlp_router.get("/index/info/{project_id}")

async def get_project_index_info (request: Request, project_id: int):
    
    project_model = await ProjectModel.create_instance(
        db_client = request.app.db_client
    )

    project = await project_model.get_project_or_create_one(
        project_id = project_id
    )

    nlp_controller = NLPController(
        vectordb_client= request.app.vectordb_client,
        generation_client = request.app.generation_client,
        embedding_client = request.app.embedding_client,
        template_parser= request.app.template_parser

    )

    collection_info =await nlp_controller.get_vectordb_collection_info(project=project)

    return JSONResponse(
        content={
        "signal": ResponseSignal.VECTORDB_COLLECTION_RETRIVED.value,
        "insertd_iteam_count" : collection_info
      
        }
    )



@nlp_router.post("/index/search/{project_id}")
async def search_index(request: Request , project_id: int, search_request : SearchRequest):
    
    project_model = await ProjectModel.create_instance(
        db_client = request.app.db_client
    )

    project = await project_model.get_project_or_create_one(
        project_id = project_id
    )

    nlp_controller = NLPController(
        vectordb_client= request.app.vectordb_client,
        generation_client = request.app.generation_client,
        embedding_client = request.app.embedding_client,
        template_parser = request.app.template_parser

    )

    results = await nlp_controller.search_vector_db_collection(
        project= project,
        text=search_request.text,
        limit= search_request.limit
    )

    if not results:
        return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.VECTORDB_SEARCH_ERROR.value
            }
        )
    
    return JSONResponse(
        content={
        "signal": ResponseSignal.VECTORDB_SEARCH_SUCCESS.value,
        "results" : [result.dict() for result in results]
      
        }
    )



@nlp_router.post("/index/answer/{project_id}")
async def answer_rag(request: Request , project_id: int, search_request : SearchRequest):
    
    project_model = await ProjectModel.create_instance(
        db_client = request.app.db_client
    )

    project = await project_model.get_project_or_create_one(
        project_id = project_id
    )

    nlp_controller = NLPController(
        vectordb_client= request.app.vectordb_client,
        generation_client = request.app.generation_client,
        embedding_client = request.app.embedding_client,
        template_parser = request.app.template_parser

    )

    # --- تحديد مصدر الذاكرة ---
    chat_history = []
    session_id = search_request.session_id

    if session_id:
        # الذاكرة من قاعدة البيانات (الوضع الجديد: واتساب + Streamlit)
        chat_model = await ChatModel.create_instance(
            db_client=request.app.db_client
        )
        db_messages = await chat_model.get_recent_messages(
            session_id=session_id,
            project_id=project_id,
            limit=6
        )
        chat_history = [{"role": msg.role, "content": msg.content} for msg in db_messages]
    else:
        # الذاكرة من الطلب (الوضع القديم: Streamlit بدون session_id - backward compatible)
        chat_history = [msg.dict() for msg in search_request.chat_history] if search_request.chat_history else []

    answer , full_prompt , ret_chat_history =await nlp_controller.answer_rag_question(
        project= project,
        query= search_request.text,
        limit= search_request.limit,
        chat_history=chat_history,
        language_instruction=search_request.language_instruction
    )

    if not answer :
         return JSONResponse(
            status_code= status.HTTP_400_BAD_REQUEST,
            content={
                "signal" : ResponseSignal.RAG_ANSWER_ERROR.value
            }
        )

    # --- حفظ الرسائل في قاعدة البيانات ---
    if session_id:
        # حفظ سؤال المستخدم
        await chat_model.add_message(
            session_id=session_id,
            project_id=project_id,
            role="user",
            content=search_request.text
        )
        # حفظ إجابة المساعد
        await chat_model.add_message(
            session_id=session_id,
            project_id=project_id,
            role="assistant",
            content=answer
        )
    
    return JSONResponse(
        content={
            "signal":ResponseSignal.RAG_ANSWER_SUCCESS.value,
            "answer": answer,
            "full_prompt": full_prompt,
            "chat_history": ret_chat_history,
            "session_id": session_id
        }
    )