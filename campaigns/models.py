import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone


class Campaign(models.Model):
    """Merchant-created promotion that automatically becomes a Deal."""

    DEAL_TYPES = [
        ('discount', 'Discount'),
        ('cashback', 'Cashback'),
        ('bogo', 'Buy One Get One'),
        ('free_item', 'Free Item'),
        ('bundle', 'Bundle Offer'),
        ('limited_time', 'Limited Time Offer'),
        ('new_customer', 'New Customer Offer'),
        ('loyalty', 'Loyalty Offer'),
    ]
    GOALS = [
        ('increase_customers', 'Increase Customers'),
        ('increase_sales', 'Increase Sales'),
        ('reduce_inventory', 'Reduce Inventory'),
        ('weekend', 'Weekend Promotion'),
        ('holiday', 'Holiday Promotion'),
        ('new_product', 'New Product Launch'),
        ('loyalty', 'Customer Loyalty'),
    ]
    STATUS = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('ended', 'Ended'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey('payments.Merchant', on_delete=models.CASCADE, related_name='campaigns')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    deal_type = models.CharField(max_length=20, choices=DEAL_TYPES, default='discount')
    goal = models.CharField(max_length=30, choices=GOALS, default='increase_customers')
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    cashback_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    start_date = models.DateField()
    end_date = models.DateField()
    max_redemptions = models.PositiveIntegerField(default=0)
    redemptions = models.PositiveIntegerField(default=0)
    applicable_products = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='draft')
    views = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.merchant.name} — {self.title}'


class CampaignAnalytics(models.Model):
    """Post-campaign performance metrics."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.OneToOneField(Campaign, on_delete=models.CASCADE, related_name='analytics')
    store_visits = models.PositiveIntegerField(default=0)
    payments = models.PositiveIntegerField(default=0)
    revenue = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    avg_basket_size = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    repeat_customers = models.PositiveIntegerField(default=0)
    roi = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    top_products = models.TextField(blank=True)
    ai_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MerchantAISummary(models.Model):
    """Cached AI-generated merchant profile."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.OneToOneField('payments.Merchant', on_delete=models.CASCADE, related_name='ai_summary')
    business_health = models.IntegerField(default=0)
    health_label = models.CharField(max_length=30, blank=True)
    growth_score = models.IntegerField(default=0)
    marketing_score = models.IntegerField(default=0)
    customer_satisfaction = models.IntegerField(default=0)
    repayment_behaviour = models.CharField(max_length=30, blank=True)
    campaign_effectiveness = models.CharField(max_length=30, blank=True)
    revenue_trend = models.CharField(max_length=30, blank=True)
    recommended_actions = models.TextField(blank=True)
    visibility_score = models.IntegerField(default=0)
    segmentation = models.CharField(max_length=30, blank=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class AIRecommendation(models.Model):
    """History of AI recommendations."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='ai_recommendations')
    feature = models.CharField(max_length=50)
    recommendation = models.TextField()
    reasoning = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class AILog(models.Model):
    """AI request metadata log. Never stores prompts or sensitive data."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feature = models.CharField(max_length=100)
    user_role = models.CharField(max_length=30, blank=True, default='')
    model = models.CharField(max_length=100, blank=True, default='')
    provider = models.CharField(max_length=30, blank=True, default='')
    tokens = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['feature', 'created_at'])]


class SavedDeal(models.Model):
    """Customer saved deals / wishlist."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('payments.Customer', on_delete=models.CASCADE, related_name='saved_deals')
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='saved_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('customer', 'campaign')


class FavouriteMerchant(models.Model):
    """Customer follows a merchant."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('payments.Customer', on_delete=models.CASCADE, related_name='favourite_merchants')
    merchant = models.ForeignKey('payments.Merchant', on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('customer', 'merchant')