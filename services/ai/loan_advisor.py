"""Advisory-only merchant loan analysis for administrators."""

import json

from .ai_service import AIService


class MerchantLoanAdvisor:
    SYSTEM_PROMPT = (
        "You are Qinance's AI Loan Advisor for microfinance administrators. "
        "You provide advisory explanations ONLY. You NEVER approve or reject loans. "
        "All financial decisions are made by backend business rules. Return valid JSON only."
    )

    PROMPT = """
Analyze the following merchant loan profile, loan summary, and repayment history.
Suggest a recommended loan amount and evaluate creditworthiness for an administrator
who may not know the merchant or its local area.

GUARDRAILS:
- The deterministic Python ceiling is E{python_ceiling}.
- The absolute Gemini cap is E{gemini_cap}.
- The merchant requested E{requested_amount}. Evaluate whether that exact
  request is justified by the profile. Do not recommend E500 merely because
  it is available; recommend less if the evidence supports less.
- suggested_loan_amount MUST stay within the policy range and must not exceed
  the requested amount unless a concrete opportunity justifies the buffer.
- Qinance does not currently use credit scores. Do not mention a credit score;
  describe E200–E500 for new merchants as the starter loan-limit policy.
- You may use the Gemini buffer above the Python ceiling only when a concrete
  opportunity (verified event, seasonality, or measurable growth) supports it.
  State that opportunity explicitly in reasons. Do not use the buffer by default.
- Never approve or reject the application.

LOCAL CONTEXT:
The merchant is located in {merchant_location}. Consider local events, holidays,
market days, and seasonal opportunities, but list any such assumptions explicitly.

MERCHANT PROFILE:
{profile}

LOAN SUMMARY:
{loan_summary}

REPAYMENT SUMMARY:
{repayment_summary}

Return exactly this JSON shape:
{{
  "explanation": "2-3 concise professional sentences",
  "risk_summary": "low|medium|high",
  "suggested_loan_amount": 500.0,
  "confidence": 95,
  "reasons": ["reason"],
  "strengths": ["strength"],
  "weaknesses": ["weakness"]
}}
"""

    def __init__(self):
        self.ai_service = AIService()

    @staticmethod
    def _remove_credit_score_language(value):
        if isinstance(value, str):
            return (value.replace('credit score', 'deterministic loan limit')
                         .replace('Credit score', 'Deterministic loan limit'))
        if isinstance(value, list):
            return [MerchantLoanAdvisor._remove_credit_score_language(item) for item in value]
        if isinstance(value, dict):
            return {key: MerchantLoanAdvisor._remove_credit_score_language(item) for key, item in value.items()}
        return value

    def advise(self, data):
        profile = {
            'name': data['merchant_name'],
            'business_type': data['merchant_type'],
            'location': data['context']['location'] or 'Unknown',
            'blacklisted': data['risk_rating'] == 'high',
            'has_active_loan': data['history']['active_loans'] > 0,
            'business_profile': data['business_profile'],
        }
        prompt = self.PROMPT.format(
            python_ceiling=data['python_ceiling'],
            gemini_cap=data['gemini_cap'],
            requested_amount=data['requested_amount'],
            merchant_location=profile['location'],
            profile=json.dumps(profile),
            loan_summary=json.dumps(data['history']),
            repayment_summary=json.dumps(data['repayment_summary']),
        )
        result = self.ai_service.generate_json(
            prompt,
            system_prompt=self.SYSTEM_PROMPT,
            feature='admin_loan_analysis',
            user_role='admin',
        )
        if not result['success']:
            ceiling = float(data['python_ceiling'])
            requested = float(data['requested_amount'])
            fallback_amount = min(ceiling, requested) if requested > 0 else ceiling
            return {
                'success': True,
                'advice': {
                    'explanation': (
                        f"AI service temporarily unavailable. Based on the deterministic "
                        f"loan ceiling of E{ceiling:.2f}, E{fallback_amount:.2f} is the system fallback."
                    ),
                    'risk_summary': 'medium',
                    'suggested_loan_amount': fallback_amount,
                    'confidence': 50,
                    'reasons': ['AI unavailable; deterministic ceiling used as fallback.'],
                    'strengths': [],
                    'weaknesses': [],
                    'buffer_used': False,
                    'buffer_amount': 0,
                },
                'tokens': 0,
                'latency_ms': 0,
                'fallback': True,
            }

        advice = self._remove_credit_score_language(result.get('data') or {})
        suggested = float(advice.get('suggested_loan_amount', 0) or 0)
        requested = float(data['requested_amount'])
        policy_min = float(data['deterministic_range']['min'])
        policy_max = float(data['gemini_cap'] if requested > data['python_ceiling'] else requested)
        advice['suggested_loan_amount'] = round(min(max(suggested, policy_min), policy_max), 2)
        advice['buffer_used'] = advice['suggested_loan_amount'] > float(data['python_ceiling'])
        advice['buffer_amount'] = round(
            max(0, advice['suggested_loan_amount'] - float(data['python_ceiling'])), 2
        )
        return {
            'success': True,
            'advice': advice,
            'tokens': result.get('tokens', 0),
            'latency_ms': result.get('latency_ms', 0),
        }
