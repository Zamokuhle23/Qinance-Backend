import json
from datetime import date, timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db.models import Q

from .models import Campaign, CampaignAnalytics, MerchantAISummary, AILog, SavedDeal, FavouriteMerchant
from payments.models import Merchant, Customer


def _can_manage_campaign(request, merchant):
    user = request.user
    return (
        user.is_authenticated and (
            user.is_staff or user.is_superuser or
            (getattr(user, 'role', '') == 'merchant' and user.phone == merchant.phone)
        )
    )


class CampaignListCreateView(APIView):
    """List campaigns (filterable) and create a new campaign."""

    def get(self, request):
        qs = Campaign.objects.filter(status='active')
        category = request.query_params.get('category')
        deal_type = request.query_params.get('deal_type')
        if category:
            qs = qs.filter(category__icontains=category)
        if deal_type:
            qs = qs.filter(deal_type=deal_type)
        return Response([self._serialize(c) for c in qs])

    def post(self, request):
        merchant_id = request.data.get('merchant_id')
        merchant = get_object_or_404(Merchant, id=merchant_id)
        if not _can_manage_campaign(request, merchant):
            return Response({'error': 'Only the merchant owner can manage this campaign.'}, status=403)
        start_date = request.data.get('start_date') or date.today()
        end_date = request.data.get('end_date') or (date.today() + timedelta(days=30))
        campaign = Campaign.objects.create(
            merchant=merchant,
            title=request.data.get('title', ''),
            description=request.data.get('description', ''),
            category=request.data.get('category', ''),
            deal_type=request.data.get('deal_type', 'discount'),
            goal=request.data.get('goal', 'increase_customers'),
            discount_percent=request.data.get('discount_percent'),
            cashback_percent=request.data.get('cashback_percent'),
            budget=request.data.get('budget', 0),
            start_date=start_date,
            end_date=end_date,
            max_redemptions=request.data.get('max_redemptions', 0),
            applicable_products=request.data.get('applicable_products', ''),
            status=request.data.get('status', 'active'),
        )
        return Response(self._serialize(campaign), status=status.HTTP_201_CREATED)

    def _serialize(self, c):
        return {
            'id': str(c.id), 'merchant_id': str(c.merchant_id),
            'merchant_name': c.merchant.name, 'title': c.title,
            'description': c.description, 'category': c.category,
            'deal_type': c.deal_type, 'goal': c.goal,
            'discount_percent': str(c.discount_percent) if c.discount_percent else None,
            'cashback_percent': str(c.cashback_percent) if c.cashback_percent else None,
            'budget': str(c.budget), 'start_date': str(c.start_date),
            'end_date': str(c.end_date), 'max_redemptions': c.max_redemptions,
            'redemptions': c.redemptions, 'status': c.status,
            'views': c.views, 'clicks': c.clicks,
            'merchant_location': c.merchant.location,
            'google_maps_link': c.merchant.google_maps_link,
        }


class CampaignDetailView(APIView):
    def get(self, request, campaign_id):
        c = get_object_or_404(Campaign, id=campaign_id)
        return Response({
            'id': str(c.id), 'merchant_id': str(c.merchant_id),
            'merchant_name': c.merchant.name, 'title': c.title,
            'description': c.description, 'category': c.category,
            'deal_type': c.deal_type, 'goal': c.goal,
            'discount_percent': str(c.discount_percent) if c.discount_percent else None,
            'cashback_percent': str(c.cashback_percent) if c.cashback_percent else None,
            'budget': str(c.budget), 'start_date': str(c.start_date),
            'end_date': str(c.end_date), 'max_redemptions': c.max_redemptions,
            'redemptions': c.redemptions, 'status': c.status,
            'views': c.views, 'clicks': c.clicks,
            'merchant_location': c.merchant.location,
            'google_maps_link': c.merchant.google_maps_link,
        })

    def patch(self, request, campaign_id):
        campaign = get_object_or_404(Campaign, id=campaign_id)
        if not _can_manage_campaign(request, campaign.merchant):
            return Response({'error': 'Only the merchant owner can edit this campaign.'}, status=403)
        allowed = {
            'title', 'description', 'category', 'deal_type', 'goal',
            'discount_percent', 'cashback_percent', 'budget', 'start_date',
            'end_date', 'max_redemptions', 'applicable_products', 'status',
        }
        for field in allowed:
            if field in request.data:
                setattr(campaign, field, request.data[field])
        campaign.save()
        return Response(CampaignListCreateView()._serialize(campaign))

    def delete(self, request, campaign_id):
        campaign = get_object_or_404(Campaign, id=campaign_id)
        if not _can_manage_campaign(request, campaign.merchant):
            return Response({'error': 'Only the merchant owner can delete this campaign.'}, status=403)
        campaign.delete()
        return Response({'ok': True})


class CampaignTrackView(APIView):
    """Track campaign views/clicks."""
    def post(self, request, campaign_id):
        c = get_object_or_404(Campaign, id=campaign_id)
        action = request.data.get('action', 'view')
        if action == 'click':
            c.clicks += 1
        else:
            c.views += 1
        c.save()
        return Response({'ok': True, 'views': c.views, 'clicks': c.clicks})


class MerchantCampaignsView(APIView):
    """List campaigns for a specific merchant."""
    def get(self, request, merchant_id):
        merchant = get_object_or_404(Merchant, id=merchant_id)
        if not _can_manage_campaign(request, merchant):
            return Response({'error': 'Only the merchant owner can view campaign management.'}, status=403)
        qs = Campaign.objects.filter(merchant_id=merchant_id)
        return Response([CampaignListCreateView()._serialize(c) for c in qs])


class MerchantAISummaryView(APIView):
    """Get cached AI merchant summary. Auto-generates deterministically when missing."""

    def _generate_summary(self, merchant_id):
        """Deterministic Python calculation — Gemini never computes scores."""
        merchant = get_object_or_404(Merchant, id=merchant_id)
        from campaigns.models import Campaign, CampaignAnalytics
        from django.db.models import Sum

        campaigns = Campaign.objects.filter(merchant=merchant)
        analytics = CampaignAnalytics.objects.filter(campaign__merchant=merchant)

        trust_factor = min(100, merchant.trust_score)
        risk_penalty = {'low': 0, 'medium': 15, 'high': 35}.get(merchant.risk_rating, 0)
        dispute_penalty = min(40, merchant.dispute_count * 10)
        campaign_count = campaigns.count()
        campaign_boost = min(20, campaign_count * 4)

        business_health = max(0, min(100, 50 + (trust_factor // 2) - risk_penalty - dispute_penalty + campaign_boost))
        growth_score = max(0, min(100, 40 + (analytics.aggregate(total=Sum('revenue'))['total'] or 0) // 1000 - dispute_penalty))
        marketing_score = max(0, min(100, 20 + campaign_boost + (campaign_count * 5)))
        visibility_score = max(0, min(100, int(campaigns.aggregate(total=Sum('views'))['total'] or 0) // 10))

        if business_health >= 80:
            health_label = 'Excellent'
        elif business_health >= 60:
            health_label = 'Good'
        elif business_health >= 40:
            health_label = 'Average'
        else:
            health_label = 'Needs Attention'

        if merchant.kyc_approved:
            repayment_behaviour = 'Good'
        elif merchant.is_active:
            repayment_behaviour = 'Fair'
        else:
            repayment_behaviour = 'Unknown'

        if campaign_count > 0:
            campaign_effectiveness = 'Good' if marketing_score >= 60 else 'Average'
        else:
            campaign_effectiveness = 'No Campaigns'

        segmentation = 'High Value' if business_health >= 70 else ('Standard' if business_health >= 45 else 'At Risk')

        return {
            'business_health': business_health,
            'health_label': health_label,
            'growth_score': growth_score,
            'marketing_score': marketing_score,
            'customer_satisfaction': max(0, min(100, 100 - dispute_penalty)),
            'repayment_behaviour': repayment_behaviour,
            'campaign_effectiveness': campaign_effectiveness,
            'revenue_trend': 'Growing' if analytics.aggregate(total=Sum('revenue'))['total'] else 'Flat',
            'recommended_actions': 'Increase campaign visibility to grow marketing score.' if marketing_score < 60 else 'Maintain current campaign performance.',
            'visibility_score': visibility_score,
            'segmentation': segmentation,
            'data': {
                'trust_score': merchant.trust_score,
                'risk_rating': merchant.risk_rating,
                'dispute_count': merchant.dispute_count,
                'campaign_count': campaign_count,
                'generated_by': 'python-deterministic',
            },
        }

    def get(self, request, merchant_id):
        summary, created = MerchantAISummary.objects.get_or_create(
            merchant_id=merchant_id,
            defaults=self._generate_summary(merchant_id),
        )
        if not created and summary.data.get('generated_by') != 'python-deterministic':
            generated = self._generate_summary(merchant_id)
            for key, value in generated.items():
                setattr(summary, key, value)
            summary.save()
        return Response({
            'merchant_id': str(summary.merchant_id),
            'business_health': summary.business_health,
            'health_label': summary.health_label,
            'growth_score': summary.growth_score,
            'marketing_score': summary.marketing_score,
            'customer_satisfaction': summary.customer_satisfaction,
            'repayment_behaviour': summary.repayment_behaviour,
            'campaign_effectiveness': summary.campaign_effectiveness,
            'revenue_trend': summary.revenue_trend,
            'recommended_actions': summary.recommended_actions,
            'visibility_score': summary.visibility_score,
            'segmentation': summary.segmentation,
            'data': summary.data,
            'updated_at': summary.updated_at,
        })


class AILogStatsView(APIView):
    """Admin: AI usage statistics."""
    def get(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({'error': 'Unauthorized.'}, status=403)
        from django.db.models import Count, Avg, Sum
        logs = AILog.objects.all()
        total = logs.count()
        success = logs.filter(success=True).count()
        return Response({
            'total_requests': total,
            'success_count': success,
            'failure_count': total - success,
            'success_rate': round((success / total) * 100, 1) if total else 0,
            'avg_latency_ms': logs.aggregate(avg=Avg('latency_ms'))['avg'] or 0,
            'total_tokens': logs.aggregate(total=Sum('tokens'))['total'] or 0,
            'feature_usage': list(logs.values('feature').annotate(count=Count('id')).order_by('-count')),
        })


class AIAskView(APIView):
    """Ask Qinance — universal endpoint for merchants, customers, and admins."""
    def post(self, request):
        message = request.data.get('message') or request.data.get('prompt')
        role = request.data.get('role', 'merchant')
        context = request.data.get('context') or {}

        if not message:
            return Response({'success': False, 'error': 'message is required.'}, status=400)

        from services.ai.orchestrator import AIOrchestrator
        orchestrator = AIOrchestrator()
        result = orchestrator.handle(message, role=role, context=context)
        return Response(result)


class AIRecentRequestsView(APIView):
    """Admin: recent AI requests."""
    def get(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({'error': 'Unauthorized.'}, status=403)
        logs = AILog.objects.order_by('-created_at')[:20]
        return Response([{
            'feature': log.feature,
            'user_role': log.user_role,
            'tokens': log.tokens,
            'latency_ms': log.latency_ms,
            'success': log.success,
            'cache_hit': log.cache_hit,
            'tool_used': log.tool_used,
            'intent': log.intent,
            'response_time': log.response_time,
            'created_at': log.created_at,
        } for log in logs])


class NearbyOffersView(APIView):
    """
    Pillar 2 — Nearby Offers.
    Returns active campaigns with merchant distance, cashback/discount, and a
    Google Maps directions link. Sorting by distance, cashback, discount, category, expiry.
    """

    def get(self, request):
        from django.utils import timezone
        today = timezone.localdate()
        category = request.query_params.get('category')
        sort = request.query_params.get('sort', 'distance')

        qs = Campaign.objects.filter(
            status='active',
            start_date__lte=today,
            end_date__gte=today,
        ).select_related('merchant')

        if category:
            qs = qs.filter(category__icontains=category)

        offers = []
        for c in qs:
            merchant = c.merchant
            # Distance is a placeholder — merchant.location is a text address.
            # In production, geocode merchant.location and compute haversine.
            distance_m = 0
            maps_url = f'https://www.google.com/maps/dir/?api=1&destination={merchant.location.replace(" ", "+")}' if merchant.location else None

            offers.append({
                'campaign_id': str(c.id),
                'merchant_id': str(merchant.id),
                'merchant_name': merchant.name,
                'merchant_location': merchant.location,
                'category': c.category,
                'deal_type': c.deal_type,
                'discount_percent': str(c.discount_percent) if c.discount_percent else None,
                'cashback_percent': str(c.cashback_percent) if c.cashback_percent else None,
                'distance_m': distance_m,
                'expires_on': c.end_date.isoformat(),
                'maps_url': maps_url,
            })

        # Sorting
        if sort == 'cashback':
            offers.sort(key=lambda o: float(o['cashback_percent'] or 0), reverse=True)
        elif sort == 'discount':
            offers.sort(key=lambda o: float(o['discount_percent'] or 0), reverse=True)
        elif sort == 'expiry':
            offers.sort(key=lambda o: o['expires_on'])
        elif sort == 'category':
            offers.sort(key=lambda o: o['category'])
        else:  # distance
            offers.sort(key=lambda o: o['distance_m'])

        return Response(offers)


class SavedDealView(APIView):
    """Customer saves a deal."""
    def post(self, request):
        customer_id = request.data.get('customer_id')
        campaign_id = request.data.get('campaign_id')
        customer = get_object_or_404(Customer, id=customer_id)
        campaign = get_object_or_404(Campaign, id=campaign_id)
        SavedDeal.objects.get_or_create(customer=customer, campaign=campaign)
        return Response({'ok': True})


class FavouriteMerchantView(APIView):
    """Customer follows a merchant."""
    def post(self, request):
        customer_id = request.data.get('customer_id')
        merchant_id = request.data.get('merchant_id')
        customer = get_object_or_404(Customer, id=customer_id)
        merchant = get_object_or_404(Merchant, id=merchant_id)
        FavouriteMerchant.objects.get_or_create(customer=customer, merchant=merchant)
        return Response({'ok': True})
