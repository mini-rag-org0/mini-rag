from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, status
from fastapi.responses import PlainTextResponse, JSONResponse
from helpers.config import get_setting
from services.whatsapp_service import WhatsAppService
from models.ProjectModel import ProjectModel
from models.ChatModel import ChatModel
from controllers import NLPController
from langdetect import detect
import logging
import hmac

logger = logging.getLogger("uvicorn.error")

whatsapp_router = APIRouter(
    prefix="/api/v1/whatsapp",
    tags=["api_v1", "whatsapp"],
)

async def process_whatsapp_message(app, phone_number: str, message_text: str):
    """
    Background task to process incoming WhatsApp messages.
    """
    setting = get_setting()
    project_id_str = setting.WHATSAPP_DEFAULT_PROJECT_ID
    
    if not project_id_str:
        logger.error("WHATSAPP_DEFAULT_PROJECT_ID is not configured.")
        return
        
    try:
        project_id = int(project_id_str)
    except ValueError:
        logger.error(f"Invalid WHATSAPP_DEFAULT_PROJECT_ID: {project_id_str}")
        return

    # Initialize models
    project_model = await ProjectModel.create_instance(db_client=app.db_client)
    chat_model = await ChatModel.create_instance(db_client=app.db_client)
    
    project = await project_model.get_project_or_create_one(project_id=project_id)
    if not project:
        logger.error(f"Project ID {project_id} not found for WhatsApp processing.")
        return

    # Initialize WhatsApp Service
    whatsapp_service = WhatsAppService(
        api_token=setting.WHATSAPP_API_TOKEN,
        phone_number_id=setting.WHATSAPP_PHONE_NUMBER_ID
    )

    # Detect language for NLP instruction
    try:
        detected_lang = detect(message_text)
        language_instruction = "Please answer in Arabic." if detected_lang == 'ar' else "Please answer in English."
    except Exception:
        language_instruction = "Please answer in Arabic."

    # Fetch Chat History
    db_messages = await chat_model.get_recent_messages(
        session_id=phone_number,
        project_id=project_id,
        limit=6
    )
    chat_history = [{"role": msg.role, "content": msg.content} for msg in db_messages]

    # NLP Controller
    nlp_controller = NLPController(
        vectordb_client=app.vectordb_client,
        generation_client=app.generation_client,
        embedding_client=app.embedding_client,
        template_parser=app.template_parser
    )

    try:
        # Generate Answer
        answer, full_prompt, ret_chat_history = await nlp_controller.answer_rag_question(
            project=project,
            query=message_text,
            limit=5,
            chat_history=chat_history,
            language_instruction=language_instruction
        )
        
        if not answer:
            answer = "عذراً، لم أتمكن من العثور على إجابة في المستندات المتاحة."

        # Save to DB
        await chat_model.add_message(
            session_id=phone_number,
            project_id=project_id,
            role="user",
            content=message_text
        )
        await chat_model.add_message(
            session_id=phone_number,
            project_id=project_id,
            role="assistant",
            content=answer
        )

        # Send Reply via WhatsApp
        await whatsapp_service.send_message(to_phone_number=phone_number, text=answer)

    except Exception as e:
        logger.error(f"Error processing WhatsApp message: {str(e)}")
        error_message = "عذراً، حدث خطأ أثناء معالجة طلبك. الرجاء المحاولة لاحقاً."
        await whatsapp_service.send_message(to_phone_number=phone_number, text=error_message)


@whatsapp_router.get("/webhook")
async def verify_webhook(request: Request):
    """
    Meta Webhook Verification Endpoint.
    """
    setting = get_setting()
    verify_token = setting.WHATSAPP_VERIFY_TOKEN

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and hmac.compare_digest(token, verify_token):
            logger.info("WEBHOOK_VERIFIED")
            return PlainTextResponse(content=challenge, status_code=200)
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")
    
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing parameters")


@whatsapp_router.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive incoming messages from WhatsApp Cloud API.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"status": "error"}, status_code=400)

    # Validate WhatsApp Webhook payload structure
    if body.get("object") == "whatsapp_business_account":
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Check if it's an incoming message (not a status update)
                if "messages" in value:
                    for message in value["messages"]:
                        if message.get("type") == "text":
                            phone_number = message.get("from")
                            message_text = message.get("text", {}).get("body", "")
                            
                            logger.info(f"Received WhatsApp message from {phone_number}: {message_text}")
                            
                            # Add the heavy lifting to a background task
                            background_tasks.add_task(
                                process_whatsapp_message, 
                                app=request.app, 
                                phone_number=phone_number, 
                                message_text=message_text
                            )
                            
        # Always return 200 OK immediately to WhatsApp
        return JSONResponse(content={"status": "ok"}, status_code=200)
    
    return JSONResponse(content={"status": "not_whatsapp_event"}, status_code=404)
