import httpx
import logging

logger = logging.getLogger("uvicorn")

class WhatsAppService:
    def __init__(self, api_token: str, phone_number_id: str):
        self.api_token = api_token
        self.phone_number_id = phone_number_id
        self.base_url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        self._client = httpx.AsyncClient(
            headers=self.headers,
            timeout=10.0,
        )

    async def send_message(self, to_phone_number: str, text: str) -> bool:
        """
        Send a text message using WhatsApp Cloud API.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone_number,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text
            }
        }

        try:
            response = await self._client.post(
                self.base_url,
                json=payload,
            )
                
            if response.status_code in (200, 201):
                logger.info(f"Successfully sent WhatsApp message to {to_phone_number}")
                return True
            else:
                logger.error(f"Failed to send WhatsApp message. Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Exception occurred while sending WhatsApp message: {str(e)}")
            return False

    async def close(self):
        """Close the persistent HTTP client."""
        await self._client.aclose()
