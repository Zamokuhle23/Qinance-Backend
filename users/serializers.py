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

    account_type = serializers.ChoiceField(
        choices=['customer', 'merchant'],
        write_only=True
    )

    business_type = serializers.CharField(write_only=True, required=False, allow_blank=True)
    location = serializers.CharField(write_only=True, required=False, allow_blank=True)
    bank = serializers.CharField(write_only=True, required=False, allow_blank=True)

    password = serializers.CharField(
        write_only=True
    )

    class Meta:
        model = User

        fields = [
            'account_type',
            'phone',
            'email',
            'full_name',
            'national_id',
            'password',
            'business_type',
            'location',
            'bank',
        ]

        extra_kwargs = {
            'email': {'required': True, 'allow_blank': False},
            'national_id': {'required': True, 'allow_blank': False},
        }

    def create(self, validated_data):

        from payments.models import Customer, Merchant

        password = validated_data.pop('password')
        account_type = validated_data.pop('account_type')
        business_type = validated_data.pop('business_type', '')
        location = validated_data.pop('location', '')
        bank = validated_data.pop('bank', '')

        user = User.objects.create_user(
            password=password,
            role=account_type,
            kyc_status='pending',
            **validated_data
        )

        if account_type == 'merchant':
            Merchant.objects.create(
                phone=user.phone,
                name=user.full_name,
                business_type=business_type,
                location=location,
                is_active=False,
                kyc_approved=False,
            )
        else:
            Customer.objects.create(
                phone=user.phone,
                full_name=user.full_name,
                national_id=user.national_id,
                bank=bank,
                is_active=False,
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

        if not (user.is_staff or user.is_superuser) and user.kyc_status != 'approved':
            raise serializers.ValidationError(
                "Your application is still awaiting administrator approval"
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

        if not (user.is_staff or user.is_superuser) and user.kyc_status != 'approved':
            raise serializers.ValidationError(
                "Your application is still awaiting administrator approval"
            )

        refresh = RefreshToken.for_user(user)

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user_id": str(user.id),
            "full_name": user.full_name,
        }


class KYCDocumentSerializer(serializers.ModelSerializer):

    def validate_file(self, value):
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('Identity images must be smaller than 10 MB.')
        content_type = getattr(value, 'content_type', '')
        if content_type not in ('image/jpeg', 'image/png', 'image/webp'):
            raise serializers.ValidationError('Only JPEG, PNG, or WebP identity images are accepted.')
        return value

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
