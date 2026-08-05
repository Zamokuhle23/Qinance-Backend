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