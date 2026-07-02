from django.contrib import admin

from .models import (
    Merchant,
    MerchantDocument,
    Customer,
    CardDetails,
    CreditStatement,
    CreditTransaction,
    PaymentSession,
    Repayment,
    RegulatoryReport,
    MerchantLoan,
)


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display  = ['name', 'business_type', 'phone', 'location', 'is_active', 'risk_rating', 'kyc_approved', 'created_at']
    list_filter   = ['is_active', 'business_type', 'risk_rating', 'kyc_approved']
    search_fields = ['name', 'phone']


@admin.register(MerchantDocument)
class MerchantDocumentAdmin(admin.ModelAdmin):
    list_display  = ['merchant', 'document_type', 'status', 'uploaded_at']
    list_filter   = ['document_type', 'status']
    search_fields = ['merchant__name']


@admin.register(MerchantLoan)
class MerchantLoanAdmin(admin.ModelAdmin):
    list_display = ['merchant', 'requested_amount', 'approved_amount', 'balance_due', 'term_months', 'status', 'applied_at']
    list_filter = ['status', 'term_months']
    search_fields = ['merchant__name', 'merchant__phone']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display   = ['full_name', 'phone', 'bank', 'credit_limit', 'current_balance', 'available_credit', 'created_at']
    list_filter    = ['bank', 'is_active', 'default_funding_mode']
    search_fields  = ['full_name', 'phone']
    readonly_fields = ['available_credit', 'minimum_payment', 'days_until_due']


@admin.register(CardDetails)
class CardDetailsAdmin(admin.ModelAdmin):
    list_display   = ['customer', 'masked_number', 'expiry_display', 'status', 'bin_country', 'created_at']
    list_filter    = ['status', 'bin_country']
    readonly_fields = ['masked_number', 'expiry_display']


@admin.register(CreditStatement)
class CreditStatementAdmin(admin.ModelAdmin):
    list_display = ['customer', 'period_start', 'period_end', 'due_date', 'closing_balance', 'minimum_payment', 'status']
    list_filter  = ['status']


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display  = ['customer', 'transaction_type', 'funding_mode', 'amount', 'merchant', 'created_at']
    list_filter   = ['transaction_type', 'funding_mode']
    search_fields = ['customer__phone', 'merchant__name']


@admin.register(PaymentSession)
class PaymentSessionAdmin(admin.ModelAdmin):
    list_display   = ['merchant', 'amount', 'status', 'funding_mode', 'bank_used', 'created_at']
    list_filter    = ['status', 'funding_mode']
    readonly_fields = ['id', 'created_at', 'confirmed_at']


@admin.register(Repayment)
class RepaymentAdmin(admin.ModelAdmin):
    list_display = ['customer', 'amount', 'method', 'status', 'created_at']
    list_filter  = ['method', 'status']


@admin.register(RegulatoryReport)
class RegulatoryReportAdmin(admin.ModelAdmin):
    list_display  = ['report_type', 'customer_name', 'customer_phone', 'amount', 'status', 'created_at']
    list_filter   = ['report_type', 'status']
    search_fields = ['customer_phone', 'customer_name']
    readonly_fields = ['created_at']
