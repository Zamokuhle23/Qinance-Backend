from django.contrib.auth import authenticate
from django.db.models import Q

from rest_framework import serializers

from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    User,
    KYCDocument,
    OTPVerification,
    CreditApplication,
    FraudFlag,
)


class RegisterSerializer(serializers.ModelSerializer):

    password = serializers.CharField(
        write_only=True
    )

    class Meta:
        model = User

        fields = [
            'phone',
            'email',
            'full_name',
            'national_id',
            'password',
        ]

    def create(self, validated_data):

        password = validated_data.pop('password')

        user = User.objects.create_user(
            password=password,
            **validated_data
        )

        return user


class LoginSerializer(serializers.Serializer):

    identifier = serializers.CharField(required=False)
    phone = serializers.CharField(required=False)

    password = serializers.CharField(
        write_only=True
    )

    def validate(self, attrs):

        identifier = attrs.get("identifier") or attrs.get("phone")
        password = attrs.get("password")

        if not identifier:
            raise serializers.ValidationError("Username or email is required")

        try:
            account = User.objects.get(
                Q(email__iexact=identifier) | Q(phone__iexact=identifier)
            )
        except User.DoesNotExist:
            raise serializers.ValidationError("Invalid credentials")

        user = authenticate(
            username=account.phone,
            password=password
        )

        if not user:
            raise serializers.ValidationError(
                "Invalid credentials"
            )

        return build_auth_payload(user)


def resolve_account(user):
    """Return the app role and linked payment profile for one identity."""
    from payments.models import Customer, Merchant

    if user.role == 'merchant':
        merchant = Merchant.objects.filter(phone=user.phone).first()
        return 'merchant', merchant
    if user.role == 'customer':
        customer = Customer.objects.filter(phone=user.phone).first()
        return 'customer', customer

    # Keep legacy accounts working while admin-created accounts use explicit roles.
    merchant = Merchant.objects.filter(phone=user.phone).first()
    if merchant:
        return 'merchant', merchant
    customer = Customer.objects.filter(phone=user.phone).first()
    if customer:
        return 'customer', customer
    return 'admin', None


def build_auth_payload(user):
    refresh = RefreshToken.for_user(user)
    account_type, profile = resolve_account(user)
    data = {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'user_id': str(user.id),
        'full_name': user.full_name,
        'phone': user.phone,
        'email': user.email,
        'role': account_type,
        'kyc_status': user.kyc_status,
        'credit_status': user.credit_status,
        'phone_verified': user.is_phone_verified,
        'has_pin': bool(user.pin),
    }
    if account_type == 'merchant' and profile:
        data.update(merchant_id=str(profile.id), merchant_name=profile.name)
    elif account_type == 'customer' and profile:
        data.update(customer_id=str(profile.id))
    return data


class SetPinSerializer(serializers.Serializer):

    pin = serializers.CharField(
        min_length=4,
        max_length=6
    )

    def validate_pin(self, value):

        if not value.isdigit():
            raise serializers.ValidationError(
                "PIN must contain only digits"
            )

        return value


class PinLoginSerializer(serializers.Serializer):

    phone = serializers.CharField()

    pin = serializers.CharField()

    def validate(self, attrs):

        phone = attrs.get("phone")
        pin = attrs.get("pin")

        try:
            user = User.objects.get(
                phone=phone
            )

        except User.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid credentials"
            )

        if not user.check_pin(pin):
            raise serializers.ValidationError(
                "Invalid PIN"
            )

        refresh = RefreshToken.for_user(user)

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user_id": str(user.id),
            "full_name": user.full_name,
        }


class KYCDocumentSerializer(serializers.ModelSerializer):

    class Meta:
        model = KYCDocument

        fields = '__all__'

        read_only_fields = [
            'status',
            'rejection_reason',
            'uploaded_at',
            'user',
        ]


class CreditApplicationSerializer(serializers.ModelSerializer):

    class Meta:
        model = CreditApplication

        fields = '__all__'

        read_only_fields = [
            'status',
            'approved_limit',
            'reviewed_by',
            'rejection_reason',
            'user',
        ]


class OTPVerifySerializer(serializers.Serializer):

    code = serializers.CharField()


class RejectSerializer(serializers.Serializer):

    reason = serializers.CharField()
