"""Ask Qinance orchestrator — Qinance-Backend.

Understands intent, executes registered tools (Python calculates), and asks
Gemini to explain the results. Gemini never sees raw database data.
"""

import json
import logging

from .ai_service import AIService
from .tools import registry
from .tools import merchant_tools  # noqa: F401
from .tools import customer_tools  # noqa: F401

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are Ask Qinance, the AI assistant for the Qinance marketplace. "
    "About Qinance: Qinance is a fintech platform in Eswatini that integrates digital payments and business financing. "
    "Customers use it to pay merchants (via QR/NFC/Sound), track wallets, and discover deals. "
    "Merchants use it to accept payments, run discount/cashback campaigns, and apply for business loans (working capital). "
    "Agents are authorized field workers who collect repayments from merchants. "
    "To become a merchant, users create an account on the Merchant Portal and upload KYC docs. "
    "You provide advisory explanations ONLY. You never make decisions. "
    "Numbers presented to you are calculated deterministically by our backend - "
    "never change them, only explain them. Be concise and honest. "
    "CRITICAL UX RULE: If a search tool was executed (like 'search_merchants' or 'search_deals'), do NOT list the merchants, descriptions, categories, or map links in your text reply. The frontend already renders them as beautiful interactive cards. Simply provide a friendly, very short, 1-2 sentence introduction (e.g., 'Here is what I found for you:') and let the cards do the rest!"
)


def _extract_intent(message):
    msg = (message or '').lower()
    if any(w in msg for w in ['find', 'search', 'cheapest', 'nearby', 'deals', 'deal', 'buy', 'shop', 'special', 'promotion', 'discount', 'cashback', 'restaurant', 'barber', 'pharmacy', 'supermarket', 'pizza', 'grocer', 'merchant', 'merchants', 'show', 'list', 'where', 'who', 'salon', 'haircut', 'clothes']):
        return 'shopping'
    if any(w in msg for w in ['loan', 'borrow', 'credit', 'working capital', 'finance']):
        return 'loan_recommendation'
    if any(w in msg for w in ['briefing', 'today summary', 'daily summary', 'today\'s summary', 'assistant']):
        return 'daily_briefing'
    if any(w in msg for w in ['campaign', 'advertise', 'marketing', 'what campaign', 'run this weekend', 'promote', 'promotion']):
        return 'campaign_advice'
    if any(w in msg for w in ['revenue', 'sales', 'performance', 'grow', 'growth']):
        return 'performance'
    if any(w in msg for w in ['competitor', 'competition', 'market']):
        return 'competition'
    if any(w in msg for w in ['customer', 'follow']):
        return 'customer_guidance'
    return 'general'


class AIOrchestrator:
    """Routes Ask Qinance messages to the right tools + Gemini explanation."""

    def __init__(self):
        self.ai_service = AIService()

    def handle(self, message, role='merchant', context=None):
        context = context or {}
        intent = _extract_intent(message)
        available = registry.list_tools_for_role(role)
        available_names = [t['name'] for t in available]

        tool_name = None
        args = {}
        merchant_id = context.get('merchant_id')

        if intent == 'shopping':
            # Customer natural language shopping routes to backend search tools.
            msg_lower = message.lower()
            if any(w in msg_lower for w in ['deal', 'discount', 'special', 'promotion', 'cashback', 'off']) and 'search_deals' in available_names:
                tool_name = 'search_deals'
                args = {'query': message[:100]}
            elif 'search_merchants' in available_names:
                tool_name = 'search_merchants'
                args = {'query': message[:100]}
            elif 'nearby_merchants' in available_names:
                tool_name = 'nearby_merchants'
                args = {'location': context.get('location', '')}
        elif intent == 'loan_recommendation':
            if merchant_id and 'ai_loan_recommendation' in available_names:
                tool_name = 'ai_loan_recommendation'
                args = {
                    'merchant_id': merchant_id,
                    'risk_score': context.get('risk_score', 'low'),
                    'loan_range_lower': context.get('loan_range_lower', 0),
                    'loan_range_upper': context.get('loan_range_upper', 0),
                }
        elif intent == 'daily_briefing':
            if merchant_id and 'daily_briefing' in available_names:
                tool_name = 'daily_briefing'
                args = {'merchant_id': merchant_id}
        elif intent == 'campaign_advice':
            msg_lower = message.lower()
            if any(w in msg_lower for w in ['simulate', 'what if', 'would happen', 'predict']) and 'simulate_campaign' in available_names:
                tool_name = 'simulate_campaign'
                # Extract value (e.g. 10) and type
                import re
                val_match = re.search(r'(\d+)', message)
                val = float(val_match.group(1)) if val_match else 10.0
                dtype = 'cashback' if 'cashback' in msg_lower else 'discount'
                args = {'merchant_id': merchant_id, 'value': val, 'deal_type': dtype}
            elif any(w in msg_lower for w in ['create', 'run', 'start', 'launch', 'plan']) and 'create_campaign_plan' in available_names:
                tool_name = 'create_campaign_plan'
                # Extract title and value
                import re
                val_match = re.search(r'(\d+)', message)
                val = float(val_match.group(1)) if val_match else 10.0
                dtype = 'cashback' if 'cashback' in msg_lower else 'discount'
                args = {
                    'merchant_id': merchant_id, 
                    'title': f'{int(val)}% {dtype.capitalize()} Campaign',
                    'description': f'AI-planned {int(val)}% {dtype} for our customers.',
                    'value': val,
                    'deal_type': dtype
                }
            elif 'confirm' in msg_lower and 'confirm_campaign_creation' in available_names:
                # Check for manual location pinning: "... at location -26.49,31.36"
                import re
                loc_match = re.search(r'location ([\d\.-]+),([\d\.-]+)', message)
                if loc_match and 'set_merchant_location' in available_names:
                    lat, lon = loc_match.groups()
                    # Silently update merchant's permanent location first
                    registry.execute_tool('set_merchant_location', role, {'merchant_id': merchant_id, 'lat': lat, 'lon': lon})
                
                # Now proceed with campaign creation
                tool_name = 'confirm_campaign_creation'
                # Extract parameters from the confirmation message or context
                val_match = re.search(r'(\d+)%', message)
                val = float(val_match.group(1)) if val_match else context.get('pending_value', 10.0)
                dtype = 'cashback' if 'cashback' in msg_lower else 'discount'
                
                args = {
                    'merchant_id': merchant_id,
                    'title': context.get('pending_title', 'New Campaign'),
                    'description': context.get('pending_description', 'Created via Ask Qinance'),
                    'value': val,
                    'deal_type': dtype
                }
            elif merchant_id and 'promotion_recommendation' in available_names:
                tool_name = 'promotion_recommendation'
                args = {'merchant_id': merchant_id}
            elif merchant_id and 'recommend_campaign' in available_names:
                tool_name = 'recommend_campaign'
                args = {'merchant_id': merchant_id}
            elif 'campaign_summary' in available_names:
                tool_name = 'campaign_summary'
                args = {'merchant_id': merchant_id}
        elif intent == 'performance' and 'merchant_performance' in available_names:
            tool_name = 'merchant_performance'
            args = {'merchant_id': merchant_id}
        elif 'campaign_roi' in available_names:
            campaign_id = context.get('campaign_id')
            if campaign_id:
                tool_name = 'campaign_roi'
                args = {'campaign_id': campaign_id}

        tool_result = registry.execute_tool(tool_name, role, args) if tool_name else None
        tool_block = self._format_tool_result(tool_result)

        prompt = (
            f'User message: {message}\n\n'
            f'Extracted intent: {intent}\n\n'
            f'Tool data available (deterministic, calculated by Python):\n'
            f'{tool_block}'
        )

        result = self.ai_service.generate(
            prompt,
            system_prompt=_SYSTEM_PROMPT,
            feature=f'ask_{intent}',
            user_role=role,
            tool_used=tool_name,
            intent=intent,
        )

        if not result['success']:
            return {
                'success': False,
                'reply': 'AI advice is temporarily unavailable. Please try again later.',
                'error': result.get('error'),
                'intent': intent,
                'tool_used': tool_name,
            }

        reply = result['text']
        if tool_result is not None and not tool_result.get('ok', True):
            reply += "\n\n(Note: the requested data tool could not be executed.)"

        return {
            'success': True,
            'reply': reply,
            'intent': intent,
            'tool_used': tool_name,
            'tool_data': tool_result.get('data') if tool_result else None,
            'tokens': result.get('tokens', 0),
            'latency_ms': result.get('latency_ms', 0),
        }

    @staticmethod
    def _format_tool_result(tool_result):
        if tool_result is None:
            return 'No tool data. Answer from general knowledge only.'
        if not tool_result.get('ok', True):
            return 'Tool error: ' + tool_result.get('error', 'unknown')
        return json.dumps(tool_result.get('data', {}), default=str)