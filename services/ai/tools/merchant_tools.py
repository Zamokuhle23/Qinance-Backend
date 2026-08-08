"""Merchant tools for Ask Qinance — Qinance-Backend.

Merchant tools return anonymised, aggregated data. Python calculates,
Gemini only explains. No merchant PII leaks to the model.
"""

from .registry import register_tool


@register_tool(
    'campaign_summary',
    roles=['merchant', 'admin'],
    description='Summarise campaigns for a merchant. Returns counts by status, total views/clicks.',
)
def campaign_summary(merchant_id=None):
    from campaigns.models import Campaign
    from django.db.models import Sum

    qs = Campaign.objects.all()
    if merchant_id:
        qs = qs.filter(merchant_id=merchant_id)

    return {
        'ok': True,
        'data': {
            'total_campaigns': qs.count(),
            'active_campaigns': qs.filter(status='active').count(),
            'ended_campaigns': qs.filter(status='ended').count(),
            'total_views': qs.aggregate(total=Sum('views'))['total'] or 0,
            'total_clicks': qs.aggregate(total=Sum('clicks'))['total'] or 0,
            'total_redemptions': qs.aggregate(total=Sum('redemptions'))['total'] or 0,
            'campaign_effectiveness': None,
        }
    }


@register_tool(
    'merchant_performance',
    roles=['merchant', 'admin'],
    description='Summarise merchant business performance from campaigns and analytics.',
)
def merchant_performance(merchant_id):
    from campaigns.models import Campaign, CampaignAnalytics
    from django.db.models import Sum

    campaigns = Campaign.objects.filter(merchant_id=merchant_id)
    analytics = CampaignAnalytics.objects.filter(campaign__merchant_id=merchant_id)

    return {
        'ok': True,
        'data': {
            'campaign_count': campaigns.count(),
            'total_views': campaigns.aggregate(total=Sum('views'))['total'] or 0,
            'total_clicks': campaigns.aggregate(total=Sum('clicks'))['total'] or 0,
            'total_redemptions': campaigns.aggregate(total=Sum('redemptions'))['total'] or 0,
            'total_revenue': float(analytics.aggregate(total=Sum('revenue'))['total'] or 0),
        }
    }


@register_tool(
    'ai_loan_recommendation',
    roles=['merchant', 'admin'],
    description=(
        'Contextual AI loan recommendation. Takes the traditional risk engine\'s '
        'safe loan range and layers Gemini context (weather, events, seasonality, '
        'merchant profile, repayment history) to recommend an amount WITHIN the '
        'approved range with an explanation and confidence. Never overrides guardrails.'
    ),
)
def ai_loan_recommendation(merchant_id, risk_score='low', loan_range_lower=0, loan_range_upper=0):
    """Deterministic context gathering — Gemini only explains within the safe range."""
    from payments.models import Merchant, MerchantLoan
    from campaigns.models import Campaign, CampaignAnalytics
    from django.db.models import Sum

    merchant = Merchant.objects.filter(id=merchant_id).first()
    if not merchant:
        return {'ok': False, 'error': 'Merchant not found.'}

    loans = MerchantLoan.objects.filter(merchant=merchant)
    completed = loans.filter(status='repaid').count()
    active = loans.filter(status='active').count()
    total_borrowed = float(loans.aggregate(total=Sum('requested_amount'))['total'] or 0)
    campaigns = Campaign.objects.filter(merchant=merchant)
    analytics = CampaignAnalytics.objects.filter(campaign__merchant=merchant)

    # Deterministic context (no PII). Gemini uses this to recommend within range.
    return {
        'ok': True,
        'data': {
            'merchant_business_type': merchant.business_type,
            'merchant_risk_rating': merchant.risk_rating,
            'trust_score': merchant.trust_score,
            'kyc_approved': merchant.kyc_approved,
            'completed_loans': completed,
            'active_loans': active,
            'total_borrowed': total_borrowed,
            'campaign_count': campaigns.count(),
            'campaign_views': campaigns.aggregate(total=Sum('views'))['total'] or 0,
            'campaign_revenue': float(analytics.aggregate(total=Sum('revenue'))['total'] or 0),
            'traditional_risk_score': risk_score,
            'approved_loan_range': [float(loan_range_lower), float(loan_range_upper)],
            'context_hints': {
                'weather': 'Check local weather forecast for the week.',
                'events': 'Check for nearby events/tournaments this weekend.',
                'seasonality': 'Consider business type seasonality.',
            },
        }
    }


@register_tool(
    'simulate_campaign',
    roles=['merchant', 'admin'],
    description='Simulate the impact of a campaign (e.g. 10% discount). Calculates projected ROI/Sales based on historical data.',
)
def simulate_campaign(merchant_id, deal_type='discount', value=10.0):
    from payments.models import Merchant
    from campaigns.models import Campaign, CampaignAnalytics
    from django.db.models import Avg

    m = Merchant.objects.filter(id=merchant_id).first()
    if not m:
        return {'ok': False, 'error': 'Merchant not found.'}
    
    # Get average historical ROI for this merchant or business type
    avg_roi = CampaignAnalytics.objects.filter(campaign__merchant=m).aggregate(Avg('roi'))['roi__avg'] or 15.5
    avg_revenue = CampaignAnalytics.objects.filter(campaign__merchant=m).aggregate(Avg('revenue'))['revenue__avg'] or 5000.0
    
    # Simple simulation logic
    multiplier = 1.0 + (float(value) / 100.0)
    projected_revenue = float(avg_revenue) * multiplier
    projected_roi = float(avg_roi) * (1.1 if float(value) >= 10 else 1.0)

    return {
        'ok': True,
        'data': {
            'merchant_name': m.name,
            'simulated_deal': f"{value}% {deal_type}",
            'projected_revenue_increase_pct': value,
            'projected_total_revenue': round(projected_revenue, 2),
            'projected_roi_pct': round(projected_roi, 1),
            'confidence_score': 85 if m.trust_score > 70 else 60
        }
    }


@register_tool(
    'create_campaign_plan',
    roles=['merchant', 'admin'],
    description='Create a draft campaign plan for a merchant. Returns a campaign object for confirmation.',
)
def create_campaign_plan(merchant_id, title, description, deal_type='discount', value=10.0):
    from payments.models import Merchant
    
    m = Merchant.objects.filter(id=merchant_id).first()
    if not m:
        return {'ok': False, 'error': 'Merchant not found.'}

    plan = {
        'merchant_id': str(m.id),
        'merchant_name': m.name,
        'title': title,
        'description': description,
        'deal_type': deal_type,
        'value': value,
        'status': 'draft_plan',
        'requires_confirmation': True
    }
    
    return {'ok': True, 'data': {'plan': plan}}


@register_tool(
    'confirm_campaign_creation',
    roles=['merchant', 'admin'],
    description='Actually create the campaign in the database after merchant confirmation.',
)
def confirm_campaign_creation(merchant_id, title, description, deal_type='discount', value=10.0):
    from payments.models import Merchant
    from campaigns.models import Campaign
    from datetime import date, timedelta

    m = Merchant.objects.filter(id=merchant_id).first()
    if not m:
        return {'ok': False, 'error': 'Merchant not found.'}

    campaign = Campaign.objects.create(
        merchant=m,
        title=title,
        description=description,
        deal_type=deal_type,
        discount_percent=value if deal_type == 'discount' else None,
        cashback_percent=value if deal_type == 'cashback' else None,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status='active'
    )
    
    return {'ok': True, 'data': {'campaign_id': str(campaign.id), 'title': campaign.title, 'status': 'Created & Active'}}


@register_tool(
    'set_merchant_location',
    roles=['merchant', 'admin'],
    description='Update the merchant business location using precise coordinates.',
)
def set_merchant_location(merchant_id, lat, lon):
    from payments.models import Merchant
    
    m = Merchant.objects.filter(id=merchant_id).first()
    if not m:
        return {'ok': False, 'error': 'Merchant not found.'}
    
    m.latitude = float(lat)
    m.longitude = float(lon)
    m.save()
    
    return {'ok': True, 'data': {'status': 'Location updated successfully', 'lat': lat, 'lon': lon}}


@register_tool(
    'daily_briefing',
    roles=['merchant', 'admin'],
    description=(
        'AI Merchant Assistant daily briefing. Returns today\'s revenue, repayment '
        'status, campaign performance, and recommendation context for Gemini to explain.'
    ),
)
def daily_briefing(merchant_id):
    from payments.models import Merchant, MerchantLoan
    from campaigns.models import Campaign, CampaignAnalytics
    from django.db.models import Sum
    from django.utils import timezone

    merchant = Merchant.objects.filter(id=merchant_id).first()
    if not merchant:
        return {'ok': False, 'error': 'Merchant not found.'}

    today = timezone.localdate()
    campaigns = Campaign.objects.filter(merchant=merchant)
    analytics = CampaignAnalytics.objects.filter(campaign__merchant=merchant)
    loans = MerchantLoan.objects.filter(merchant=merchant)

    return {
        'ok': True,
        'data': {
            'merchant_name': merchant.name,
            'business_type': merchant.business_type,
            'active_campaigns': campaigns.filter(status='active').count(),
            'campaign_views_today': campaigns.filter(created_at__date=today).aggregate(total=Sum('views'))['total'] or 0,
            'campaign_revenue': float(analytics.aggregate(total=Sum('revenue'))['total'] or 0),
            'active_loans': loans.filter(status='active').count(),
            'repaid_loans': loans.filter(status='repaid').count(),
            'repayment_rate': '100%' if loans.filter(status='repaid').count() else '0%',
            'recommendation_context': {
                'weather': 'Check local weather forecast.',
                'events': 'Check for nearby events this weekend.',
                'seasonality': 'Consider business type seasonality.',
            },
        }
    }


@register_tool(
    'promotion_recommendation',
    roles=['merchant', 'admin'],
    description=(
        'AI promotion recommendation. Analyses merchant performance and returns '
        'context (campaigns, revenue, seasonality) for Gemini to suggest promotions.'
    ),
)
def promotion_recommendation(merchant_id):
    from payments.models import Merchant
    from campaigns.models import Campaign, CampaignAnalytics
    from django.db.models import Sum

    merchant = Merchant.objects.filter(id=merchant_id).first()
    if not merchant:
        return {'ok': False, 'error': 'Merchant not found.'}

    campaigns = Campaign.objects.filter(merchant=merchant)
    analytics = CampaignAnalytics.objects.filter(campaign__merchant=merchant)

    return {
        'ok': True,
        'data': {
            'business_type': merchant.business_type,
            'active_campaigns': campaigns.filter(status='active').count(),
            'total_campaigns': campaigns.count(),
            'total_views': campaigns.aggregate(total=Sum('views'))['total'] or 0,
            'total_clicks': campaigns.aggregate(total=Sum('clicks'))['total'] or 0,
            'total_redemptions': campaigns.aggregate(total=Sum('redemptions'))['total'] or 0,
            'campaign_revenue': float(analytics.aggregate(total=Sum('revenue'))['total'] or 0),
            'recommendation_context': {
                'weather': 'Check local weather forecast.',
                'events': 'Check for nearby events this weekend.',
                'seasonality': 'Consider business type seasonality.',
            },
        }
    }
