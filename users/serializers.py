from django.contrib.auth import authenticate

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

    phone = serializers.CharField()

    password = serializers.CharField(
        write_only=True
    )

    def validate(self, attrs):

        phone = attrs.get("phone")
        password = attrs.get("password")

        user = authenticate(
            username=phone,
            password=password
        )

        if not user:
            raise serializers.ValidationError(
                "Invalid credentials"
            )

        refresh = RefreshToken.for_user(user)

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user_id": str(user.id),
            "full_name": user.full_name,
            "kyc_status": user.kyc_status,
            "credit_status": user.credit_status,
            "phone_verified": user.is_phone_verified,
            "has_pin": bool(user.pin),
        }


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