"""Customer shopping tools for Ask Qinance — Qinance-Backend.

Natural language shopping: search merchants, deals, nearby, details.
Backend queries PostgreSQL; Gemini only formats the answer.
"""

from .registry import register_tool


@register_tool(
    'search_merchants',
    roles=['customer', 'admin'],
    description='Search merchants by name/business type/category. Returns ranked merchant list.',
)
def search_merchants(query='', category='', limit=10):
    from payments.models import Merchant
    from campaigns.models import Campaign, CampaignAnalytics
    from django.db.models import Q, Sum

    qs = Merchant.objects.filter(is_active=True, kyc_approved=True)
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(business_type__icontains=query) | Q(location__icontains=query))
    if category:
        qs = qs.filter(business_type__icontains=category)

    results = []
    for m in qs[:limit]:
        campaigns = Campaign.objects.filter(merchant=m, status='active')
        analytics = CampaignAnalytics.objects.filter(campaign__merchant=m)
        revenue = analytics.aggregate(total=Sum('revenue'))['total'] or 0
        # Deterministic ranking: trust + activity + revenue.
        score = min(100, m.trust_score + campaigns.count() * 10 + int(revenue) // 500)
        results.append({
            'merchant_id': str(m.id),
            'name': m.name,
            'business_type': m.business_type,
            'location': m.location,
            'trust_score': m.trust_score,
            'active_campaigns': campaigns.count(),
            'revenue': float(revenue),
            'ranking_score': score,
        })
    results.sort(key=lambda x: x['ranking_score'], reverse=True)
    return {'ok': True, 'data': {'merchants': results}}


@register_tool(
    'search_deals',
    roles=['customer', 'admin'],
    description='Search active deals/campaigns by keyword or deal type.',
)
def search_deals(query='', deal_type='', limit=10):
    from campaigns.models import Campaign

    qs = Campaign.objects.filter(status='active')
    if query:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(merchant__name__icontains=query))
    if deal_type:
        qs = qs.filter(deal_type=deal_type)

    return {'ok': True, 'data': {'deals': [{
        'id': str(c.id),
        'title': c.title,
        'deal_type': c.deal_type,
        'discount_percent': float(c.discount_percent) if c.discount_percent else None,
        'cashback_percent': float(c.cashback_percent) if c.cashback_percent else None,
        'merchant_name': c.merchant.name,
        'merchant_id': str(c.merchant_id),
        'category': c.category,
    } for c in qs[:limit]]}}


@register_tool(
    'merchant_details',
    roles=['customer', 'agent', 'admin'],
    description='Get anonymised merchant details: trust, campaigns, revenue trend.',
)
def merchant_details(merchant_id):
    from payments.models import Merchant
    from campaigns.models import Campaign, CampaignAnalytics
    from django.db.models import Sum

    m = Merchant.objects.filter(id=merchant_id).first()
    if not m:
        return {'ok': False, 'error': f'Merchant {merchant_id} not found'}

    campaigns = Campaign.objects.filter(merchant=m)
    analytics = CampaignAnalytics.objects.filter(campaign__merchant=m)
    revenue = analytics.aggregate(total=Sum('revenue'))['total'] or 0

    return {'ok': True, 'data': {
        'merchant_id': str(m.id),
        'name': m.name,
        'business_type': m.business_type,
        'trust_score': m.trust_score,
        'trust_level': m.trust_level,
        'risk_rating': m.risk_rating,
        'dispute_count': m.dispute_count,
        'transaction_count': m.transaction_count,
        'active_campaigns': campaigns.filter(status='active').count(),
        'total_campaigns': campaigns.count(),
        'revenue': float(revenue),
    }}


@register_tool(
    'nearby_merchants',
    roles=['customer', 'admin'],
    description='List nearby active merchants (location-based).',
)
def nearby_merchants(location='', limit=10):
    from payments.models import Merchant

    qs = Merchant.objects.filter(is_active=True, kyc_approved=True)
    if location:
        qs = qs.filter(location__icontains=location)
    return {'ok': True, 'data': {'merchants': [
        {'merchant_id': str(m.id), 'name': m.name, 'business_type': m.business_type, 'location': m.location}
        for m in qs[:limit]
    ]}}


@register_tool(
    'campaign_roi',
    roles=['merchant', 'admin'],
    description='Calculate real campaign ROI from CampaignAnalytics (Python, deterministic).',
)
def campaign_roi(campaign_id):
    from campaigns.models import Campaign, CampaignAnalytics

    campaign = Campaign.objects.filter(id=campaign_id).first()
    if not campaign:
        return {'ok': False, 'error': f'Campaign {campaign_id} not found'}

    analytics = CampaignAnalytics.objects.filter(campaign=campaign).first()
    if not analytics:
        # Auto-compute from campaign data.
        roi = (campaign.redemptions * 20 - float(campaign.budget)) / float(campaign.budget) * 100 if campaign.budget else 0
        return {'ok': True, 'data': {
            'campaign_id': str(campaign.id),
            'title': campaign.title,
            'budget': float(campaign.budget),
            'redemptions': campaign.redemptions,
            'views': campaign.views,
            'clicks': campaign.clicks,
            'estimated_roi_pct': round(max(roi, 0), 1),
            'calculated_by': 'python-deterministic',
        }}

    spend = float(campaign.budget or 0) + float(analytics.revenue or 0) * 0.2
    roi = (float(analytics.revenue) - spend) / spend * 100 if spend else 0
    return {'ok': True, 'data': {
        'campaign_id': str(campaign.id),
        'title': campaign.title,
        'budget': float(campaign.budget),
        'store_visits': analytics.store_visits,
        'payments': analytics.payments,
        'revenue': float(analytics.revenue),
        'avg_basket_size': float(analytics.avg_basket_size),
        'repeat_customers': analytics.repeat_customers,
        'roi_pct': round(roi, 1),
        'calculated_by': 'python-deterministic',
    }}


@register_tool(
    'recommend_campaign',
    roles=['merchant', 'admin'],
    description='Recommend a campaign type based on merchant history and business health.',
)
def recommend_campaign(merchant_id=''):
    from payments.models import Merchant
    from campaigns.models import Campaign, CampaignAnalytics

    m = Merchant.objects.filter(id=merchant_id).first()
    if not m:
        return {'ok': False, 'error': f'Merchant {merchant_id} not found'}

    campaigns = Campaign.objects.filter(merchant=m)
    total_campaigns = campaigns.count()
    total_views = sum(c.views for c in campaigns)
    total_clicks = sum(c.clicks for c in campaigns)
    best_type = None
    best_roi = -9999

    for c in campaigns:
        a = CampaignAnalytics.objects.filter(campaign=c).first()
        if a:
            spend = float(c.budget or 0) + float(a.revenue or 0) * 0.2
            roi = (float(a.revenue) - spend) / spend * 100 if spend else 0
            if roi > best_roi:
                best_roi = roi
                best_type = c.deal_type

    if best_type is None:
        best_type = 'cashback' if m.trust_score >= 60 else 'discount'

    engagement = (total_clicks / total_views * 100) if total_views else 0
    recommendation = {
        'merchant_id': str(m.id),
        'merchant_name': m.name,
        'total_campaigns': total_campaigns,
        'total_views': total_views,
        'total_clicks': total_clicks,
        'avg_engagement_pct': round(engagement, 1),
        'recommended_deal_type': best_type,
        'best_historical_roi_pct': round(best_roi, 1) if best_roi > -9999 else None,
        'reason': f'Based on {total_campaigns} historical campaign(s) and merchant trust score of {m.trust_score}.',
        'calculated_by': 'python-deterministic',
    }
    return {'ok': True, 'data': recommendation}