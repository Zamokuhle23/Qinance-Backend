from django.conf import settings

from payments.compliance import (
    run_registration_compliance,
    run_affordability_check,
    log_consent,
    request_data_erasure,
    check_kyc_document_expiry,
)
from django.core.mail import send_mail

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

from .models import (
    User,
    OTPVerification,
    CreditApplication,
    FraudFlag,
    FCMDevice,
)

from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    SetPinSerializer,
    PinLoginSerializer,
    KYCDocumentSerializer,
    CreditApplicationSerializer,
    RejectSerializer,
)

from .permissions import (
    IsKYCOfficer,
    IsCreditOfficer,
    IsFraudAnalyst,
)

from .utils import (
    register_device,
    create_audit_log,
)


# ── Email helper ──────────────────────────────────────────────────────────────

def send_otp_email(user, otp_code):
    """
    Send OTP to user's email address.
    Uses Gmail SMTP when EMAIL_BACKEND is set in .env.
    Silently skips if user has no email.
    """
    if not user.email:
        return

    send_mail(
        subject='Your Qinance verification code',
        message=(
            f'Hi {user.full_name},\n\n'
            f'Your Qinance verification code is:\n\n'
            f'        {otp_code}\n\n'
            f'This code expires in 5 minutes.\n\n'
            f'If you did not request this, please ignore this email.\n\n'
            f'— The Qinance Team'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


# ── Registration ──────────────────────────────────────────────────────────────

class RegisterView(APIView):

    def post(self, request):

        serializer = RegisterSerializer(data=request.data)

        if serializer.is_valid():

            # Sanctions screening before creating account
            full_name = serializer.validated_data.get('full_name', '')
            allowed, reason = run_registration_compliance(full_name)
            if not allowed:
                return Response({'error': reason}, status=400)

            user = serializer.save()

            otp = OTPVerification.generate_otp(
                user,
                'phone_verification'
            )

            send_otp_email(user, otp.code)

            create_audit_log(
                user=user,
                action='otp_verified',
                request=request,
            )

            # Log PDPA consents accepted at registration
            log_consent(user, 'terms_and_conditions', request)
            log_consent(user, 'privacy_policy', request)

            response_data = {
                "message": (
                    "Registration successful. Check your email for the OTP."
                    if user.email
                    else "Registration successful."
                ),
                "user_id": str(user.id),
            }

            # Only expose OTP in response when DEBUG=True (local dev)
            if settings.DEBUG:
                response_data["otp_code"] = otp.code

            return Response(response_data)

        return Response(serializer.errors, status=400)


# ── Resend OTP ────────────────────────────────────────────────────────────────

class ResendOTPView(APIView):

    def post(self, request):

        phone = request.data.get('phone')

        if not phone:
            return Response(
                {"error": "phone is required"},
                status=400
            )

        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        if user.is_phone_verified:
            return Response(
                {"error": "Phone is already verified"},
                status=400
            )

        otp = OTPVerification.generate_otp(user, 'phone_verification')

        send_otp_email(user, otp.code)

        response_data = {
            "message": (
                "OTP resent. Check your email."
                if user.email
                else "OTP resent."
            )
        }

        if settings.DEBUG:
            response_data["otp_code"] = otp.code

        return Response(response_data)


# ── Phone OTP Verification ────────────────────────────────────────────────────

class VerifyPhoneOTPView(APIView):

    def post(self, request):

        phone = request.data.get('phone')
        code = request.data.get('code')

        if not phone or not code:
            return Response(
                {"error": "phone and code are required"},
                status=400
            )

        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        success, message = OTPVerification.verify(
            user, code, 'phone_verification'
        )

        if not success:
            return Response({"error": message}, status=400)

        user.is_phone_verified = True
        user.save()

        create_audit_log(
            user=user,
            action='otp_verified',
            request=request,
        )

        return Response({"message": "Phone verified successfully"})


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginView(APIView):

    def post(self, request):

        serializer = LoginSerializer(data=request.data)

        if serializer.is_valid():

            phone = request.data.get('phone')
            user = User.objects.get(phone=phone)

            register_device(request, user)

            create_audit_log(
                user=user,
                action='login',
                request=request,
            )

            return Response(serializer.validated_data)

        return Response(serializer.errors, status=400)


# ── PIN ───────────────────────────────────────────────────────────────────────

class SetPinView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        serializer = SetPinSerializer(data=request.data)

        if serializer.is_valid():

            request.user.set_pin(serializer.validated_data['pin'])
            request.user.save()

            return Response({"message": "PIN set successfully"})

        return Response(serializer.errors, status=400)


class PinLoginView(APIView):

    def post(self, request):

        serializer = PinLoginSerializer(data=request.data)

        if serializer.is_valid():

            phone = request.data.get('phone')
            user = User.objects.get(phone=phone)

            register_device(request, user)

            create_audit_log(
                user=user,
                action='pin_login',
                request=request,
            )

            return Response(serializer.validated_data)

        return Response(serializer.errors, status=400)


# ── KYC ───────────────────────────────────────────────────────────────────────

class UploadKYCDocumentView(APIView):

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):

        serializer = KYCDocumentSerializer(data=request.data)

        if serializer.is_valid():

            serializer.save(user=request.user)

            request.user.kyc_status = 'under_review'
            request.user.save()

            create_audit_log(
                user=request.user,
                action='kyc_upload',
                request=request,
            )

            return Response({"message": "Document uploaded successfully"})

        return Response(serializer.errors, status=400)


class ApproveKYCView(APIView):

    permission_classes = [IsKYCOfficer]

    def post(self, request, user_id):

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        user.kyc_status = 'approved'
        user.save()

        if user.email:
            send_mail(
                subject='Your Qinance KYC has been approved',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Great news! Your identity verification has been approved.\n'
                    f'You can now apply for your Qinance credit card.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='kyc_approved',
            request=request,
            metadata={"approved_user": str(user.id)}
        )

        return Response({"message": "KYC approved"})


class RejectKYCView(APIView):

    permission_classes = [IsKYCOfficer]

    def post(self, request, user_id):

        serializer = RejectSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        user.kyc_status = 'rejected'
        user.rejection_reason = serializer.validated_data['reason']
        user.save()

        if user.email:
            send_mail(
                subject='Qinance — Action required on your application',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Unfortunately we could not verify your identity.\n\n'
                    f'Reason: {serializer.validated_data["reason"]}\n\n'
                    f'Please re-upload your documents or contact us at '
                    f'support@qinance.sz.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='kyc_rejected',
            request=request,
            metadata={"rejected_user": str(user.id)}
        )

        return Response({"message": "KYC rejected"})


# ── Credit Application ────────────────────────────────────────────────────────

class SubmitCreditApplicationView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        if request.user.kyc_status != 'approved':
            return Response(
                {"error": "KYC must be approved before applying for credit"},
                status=400
            )

        if hasattr(request.user, 'credit_application'):
            return Response(
                {"error": "A credit application already exists"},
                status=400
            )

        serializer = CreditApplicationSerializer(data=request.data)

        if serializer.is_valid():

            serializer.save(user=request.user, status='pending')

            request.user.credit_status = 'pending'
            request.user.save()

            return Response({
                "message": "Credit application submitted successfully"
            })

        return Response(serializer.errors, status=400)


class PendingCreditApplicationsView(APIView):

    permission_classes = [IsCreditOfficer]

    def get(self, request):

        applications = CreditApplication.objects.filter(
            status='pending'
        ).select_related('user').order_by('-created_at')

        data = [
            {
                "application_id": str(app.id),
                "user_id": str(app.user.id),
                "full_name": app.user.full_name,
                "phone": app.user.phone,
                "employer_name": app.employer_name,
                "monthly_income": str(app.monthly_income),
                "requested_limit": str(app.requested_limit),
                "created_at": app.created_at.isoformat(),
            }
            for app in applications
        ]

        return Response(data)


class ApproveCreditApplicationView(APIView):

    permission_classes = [IsCreditOfficer]

    def post(self, request, application_id):

        try:
            application = CreditApplication.objects.select_related('user').get(
                id=application_id
            )
        except CreditApplication.DoesNotExist:
            return Response({"error": "Application not found"}, status=404)

        approved_limit = request.data.get('approved_limit')

        if not approved_limit:
            return Response(
                {"error": "approved_limit is required"},
                status=400
            )

        try:
            approved_limit = float(approved_limit)
        except (ValueError, TypeError):
            return Response(
                {"error": "approved_limit must be a number"},
                status=400
            )

        # Run affordability check
        approved_limit_decimal = float(approved_limit)
        existing = float(application.user.credit_application.approved_limit or 0)                    if hasattr(application.user, 'credit_application') else 0

        aff_ok, recommended, aff_reason = run_affordability_check(
            monthly_income=application.monthly_income,
            requested_limit=approved_limit_decimal,
            existing_balance=existing,
        )

        # If affordability fails entirely, block approval
        if not aff_ok:
            return Response({'error': f'Affordability check failed: {aff_reason}'}, status=400)

        # Use recommended limit if lower than requested
        final_limit = float(recommended)

        application.status = 'approved'
        application.approved_limit = final_limit
        application.reviewed_by = request.user
        application.save()

        user = application.user
        user.credit_status = 'approved'
        user.save()

        # Log credit bureau consent
        log_consent(user, 'credit_bureau_check', request)

        # Add affordability note to response if limit was adjusted
        affordability_note = aff_reason if final_limit < approved_limit_decimal else None

        if user.email:
            send_mail(
                subject='🎉 Your Qinance credit card is approved!',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Congratulations! Your Qinance credit card has been approved.\n\n'
                    f'Approved credit limit: E{approved_limit:,.2f}\n\n'
                    f'Sign in to your Qinance account to view your virtual card '
                    f'and start shopping.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='credit_approved',
            request=request,
            metadata={
                "application_id": str(application.id),
                "approved_user": str(user.id),
                "approved_limit": approved_limit,
            }
        )

        resp = {"message": "Credit application approved", "approved_limit": final_limit}
        if affordability_note:
            resp["note"] = affordability_note
        return Response(resp)


class RejectCreditApplicationView(APIView):

    permission_classes = [IsCreditOfficer]

    def post(self, request, application_id):

        serializer = RejectSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        try:
            application = CreditApplication.objects.select_related('user').get(
                id=application_id
            )
        except CreditApplication.DoesNotExist:
            return Response({"error": "Application not found"}, status=404)

        application.status = 'rejected'
        application.rejection_reason = serializer.validated_data['reason']
        application.reviewed_by = request.user
        application.save()

        user = application.user
        user.credit_status = 'rejected'
        user.rejection_reason = serializer.validated_data['reason']
        user.save()

        if user.email:
            send_mail(
                subject='Qinance — Credit application update',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Unfortunately your credit card application was not approved '
                    f'at this time.\n\n'
                    f'Reason: {serializer.validated_data["reason"]}\n\n'
                    f'You may reapply after 90 days or contact us at '
                    f'support@qinance.sz.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )

        create_audit_log(
            user=request.user,
            action='credit_rejected',
            request=request,
            metadata={
                "application_id": str(application.id),
                "rejected_user": str(user.id),
            }
        )

        return Response({"message": "Credit application rejected"})


# ── Fraud Flags ───────────────────────────────────────────────────────────────

class FraudFlagsView(APIView):

    permission_classes = [IsFraudAnalyst]

    def get(self, request):

        flags = FraudFlag.objects.filter(
            status='open'
        ).select_related('user').order_by('-created_at')

        data = [
            {
                "flag_id": str(flag.id),
                "user_id": str(flag.user.id),
                "full_name": flag.user.full_name,
                "phone": flag.user.phone,
                "risk_level": flag.user.risk_level,
                "flag_type": flag.flag_type,
                "description": flag.description,
                "status": flag.status,
                "created_at": flag.created_at.isoformat(),
            }
            for flag in flags
        ]

        return Response(data)

    def post(self, request):
        """Resolve a fraud flag"""

        flag_id = request.data.get('flag_id')

        if not flag_id:
            return Response({"error": "flag_id is required"}, status=400)

        try:
            flag = FraudFlag.objects.get(id=flag_id)
        except FraudFlag.DoesNotExist:
            return Response({"error": "Flag not found"}, status=404)

        flag.status = 'resolved'
        flag.save()

        return Response({"message": "Flag resolved"})


# ── PDPA — Data erasure & consents ────────────────────────────────────────────

class UserConsentsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        consents = request.user.consents.all()
        data = [
            {
                'consent_type': c.consent_type,
                'accepted': c.accepted,
                'ip_address': c.ip_address,
                'created_at': c.created_at.isoformat(),
            }
            for c in consents
        ]
        return Response(data)


class DataErasureView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        success, message = request_data_erasure(request.user, request)
        return Response({'message': message})


# ── KYC document expiry ───────────────────────────────────────────────────────

class KYCExpiryView(APIView):
    permission_classes = [IsKYCOfficer]

    def get(self, request):
        """List all users with expired or expiring KYC documents."""
        from users.models import User as AuthUser
        results = []
        for u in AuthUser.objects.filter(kyc_status='approved'):
            issues = check_kyc_document_expiry(u)
            if issues:
                results.append({
                    'user_id': str(u.id),
                    'full_name': u.full_name,
                    'phone': u.phone,
                    'issues': [
                        {'document_type': t, 'status': s, 'message': m}
                        for t, s, m in issues
                    ],
                })
        return Response(results)


# ── Forgot / Reset Password ───────────────────────────────────────────────────

class ForgotPasswordView(APIView):
    def post(self, request):
        phone = request.data.get('phone')
        if not phone:
            return Response({'error': 'phone is required'}, status=400)
        try:
            user = User.objects.get(phone=phone)
            otp = OTPVerification.generate_otp(user, 'password_reset')
            send_otp_email(user, otp.code)
            response_data = {'message': 'A reset code has been sent to your email.'}
            if settings.DEBUG:
                response_data['otp_code'] = otp.code
        except User.DoesNotExist:
            # Same message either way — don't reveal if account exists
            response_data = {'message': 'A reset code has been sent to your email.'}
        return Response(response_data)


class ResetPasswordView(APIView):
    def post(self, request):
        phone       = request.data.get('phone')
        code        = request.data.get('code')
        new_password = request.data.get('new_password')
        if not all([phone, code, new_password]):
            return Response({'error': 'phone, code, and new_password are required'}, status=400)
        if len(new_password) < 8:
            return Response({'error': 'Password must be at least 8 characters'}, status=400)
        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response({'error': 'Invalid request'}, status=400)
        success, message = OTPVerification.verify(user, code, 'password_reset')
        if not success:
            return Response({'error': message}, status=400)
        user.set_password(new_password)
        user.save()
        if user.email:
            send_mail(
                subject='Qinance — Your password has been reset',
                message=(
                    f'Hi {user.full_name},\n\n'
                    f'Your Qinance account password was successfully reset.\n\n'
                    f'If you did not make this change, contact us immediately at support@qinance.sz.\n\n'
                    f'— The Qinance Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        return Response({'message': 'Password reset successfully.'})


# ── Forgot / Reset PIN ────────────────────────────────────────────────────────

class ForgotPinView(APIView):
    def post(self, request):
        phone = request.data.get('phone')
        if not phone:
            return Response({'error': 'phone is required'}, status=400)
        try:
            user = User.objects.get(phone=phone)
            otp = OTPVerification.generate_otp(user, 'pin_reset')
            send_otp_email(user, otp.code)
            response_data = {'message': 'A PIN reset code has been sent to your email.'}
            if settings.DEBUG:
                response_data['otp_code'] = otp.code
        except User.DoesNotExist:
            response_data = {'message': 'A PIN reset code has been sent to your email.'}
        return Response(response_data)


class ResetPinView(APIView):
    def post(self, request):
        phone   = request.data.get('phone')
        code    = request.data.get('code')
        new_pin = request.data.get('new_pin')
        if not all([phone, code, new_pin]):
            return Response({'error': 'phone, code, and new_pin are required'}, status=400)
        if not str(new_pin).isdigit() or len(str(new_pin)) != 4:
            return Response({'error': 'PIN must be exactly 4 digits'}, status=400)
        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response({'error': 'Invalid request'}, status=400)
        success, message = OTPVerification.verify(user, code, 'pin_reset')
        if not success:
            return Response({'error': message}, status=400)
        user.set_pin(str(new_pin))
        user.save()
        return Response({'message': 'PIN reset successfully.'})


# ── FCM Device Token ──────────────────────────────────────────────────────────

class RegisterFCMTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token     = request.data.get('token')
        device_id = request.data.get('device_id', '')
        if not token:
            return Response({'error': 'token is required'}, status=400)
        FCMDevice.objects.update_or_create(
            token=token,
            defaults={'user': request.user, 'device_id': device_id},
        )
        return Response({'message': 'FCM token registered'})
