import json
from channels.generic.websocket import AsyncWebsocketConsumer


class PaymentSessionConsumer(AsyncWebsocketConsumer):
    """
    Each merchant screen connects to a WebSocket for their session.
    When the customer confirms payment, the server sends a 'payment_confirmed'
    event to this group, and the merchant screen updates instantly.
    """

    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f'session_{self.session_id}'

        # Join the session group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()
        await self.send(text_data=json.dumps({
            'type': 'connected',
            'message': f'Listening for payment on session {self.session_id}',
        }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Receive message from WebSocket client (not used in our flow)
    async def receive(self, text_data):
        pass

    # Called when confirm_payment view fires group_send
    async def payment_confirmed(self, event):
        await self.send(text_data=json.dumps({
            'type': 'payment_confirmed',
            'session_id': event['session_id'],
            'amount': event['amount'],
            'funding_mode': event.get('funding_mode', ''),
            'bank_used': event.get('bank_used', ''),
            'customer_phone': event.get('customer_phone', ''),
        }))
