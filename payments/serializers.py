from rest_framework import serializers
from .models import Merchant, MerchantLoan, Customer, CardDetails, CreditStatement, CreditTransaction, PaymentSession, DebitMandate


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = '__all__'


class MerchantLoanSerializer(serializers.ModelSerializer):
    monthly_payment = serializers.ReadOnlyField()

    class Meta:
        model = MerchantLoan
        fields = '__all__'
        read_only_fields = [
            'merchant', 'approved_amount', 'balance_due', 'status',
            'approved_at', 'due_date', 'applied_at',
        ]


class CardDetailsSerializer(serializers.ModelSerializer):
    masked_number = serializers.ReadOnlyField()
    expiry_display = serializers.ReadOnlyField()

    class Meta:
        model = CardDetails
        exclude = ['card_number', 'cvv']  # never expose full card number in list


class CardDetailsFullSerializer(serializers.ModelSerializer):
    masked_number = serializers.ReadOnlyField()
    expiry_display = serializers.ReadOnlyField()

    class Meta:
        model = CardDetails
        exclude = ['card_number', 'cvv']  # never expose full card number or CVV


class CustomerSerializer(serializers.ModelSerializer):
    available_credit = serializers.ReadOnlyField()
    minimum_payment  = serializers.ReadOnlyField()
    is_overdue       = serializers.ReadOnlyField()
    card             = CardDetailsSerializer(read_only=True)
    momo_number      = serializers.SerializerMethodField()

    def get_momo_number(self, obj):
        try:
            acc = obj.momo_account
            return acc.msisdn if acc.status == 'active' else None
        except Exception:
            return None

    class Meta:
        model  = Customer
        fields = '__all__'


class CreditStatementSerializer(serializers.ModelSerializer):
    days_until_due = serializers.ReadOnlyField()

    class Meta:
        model = CreditStatement
        fields = '__all__'


class CreditTransactionSerializer(serializers.ModelSerializer):
    merchant_name = serializers.CharField(source='merchant.name', read_only=True)

    class Meta:
        model = CreditTransaction
        fields = '__all__'


class PaymentSessionSerializer(serializers.ModelSerializer):
    merchant_name     = serializers.CharField(source='merchant.name',     read_only=True)
    merchant_sound_id = serializers.IntegerField(source='merchant.sound_id', read_only=True)
    customer_phone    = serializers.CharField(source='customer.phone',    read_only=True)

    class Meta:
        model = PaymentSession
        fields = '__all__'


class DebitMandateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DebitMandate
        fields = '__all__'


# ── Input serializers ──────────────────────────────────────────────────────────

class CreateSessionSerializer(serializers.Serializer):
    merchant_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    settlement_destination = serializers.ChoiceField(choices=['wallet', 'linked'], required=False)
    settlement_account_id = serializers.UUIDField(required=False, allow_null=True)


BANK_CHOICES = ['fnb', 'standard', 'nedbank', 'eswatini_bank', 'momo']


class ConfirmPaymentSerializer(serializers.Serializer):
    FUNDING_CHOICES = ['wallet', 'credit', 'linked', 'bank', 'jit', 'momo']

    session_id     = serializers.UUIDField()
    customer_phone = serializers.CharField(max_length=20)
    funding_mode   = serializers.ChoiceField(choices=FUNDING_CHOICES)
    payment_source_account_id = serializers.UUIDField(required=False, allow_null=True)

    bank           = serializers.ChoiceField(choices=BANK_CHOICES, required=False, allow_blank=True)
    jit_bank       = serializers.ChoiceField(choices=BANK_CHOICES, required=False, allow_blank=True)
    momo_number    = serializers.CharField(max_length=20, required=False, allow_blank=True)


class RepaymentSerializer(serializers.Serializer):
    customer_phone = serializers.CharField(max_length=20)
    amount         = serializers.DecimalField(max_digits=10, decimal_places=2)
    bank           = serializers.ChoiceField(choices=BANK_CHOICES)
    pay_full       = serializers.BooleanField(default=False)
    momo_number    = serializers.CharField(max_length=20, required=False, allow_blank=True)


class FreezeCardSerializer(serializers.Serializer):
    customer_phone = serializers.CharField(max_length=20)
    action = serializers.ChoiceField(choices=['freeze', 'unfreeze'])
