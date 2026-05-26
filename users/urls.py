from django.urls import path

from .views import (
    RegisterView,
    LoginView,
    SetPinView,
    PinLoginView,
    VerifyPhoneOTPView,
    ResendOTPView,
    UploadKYCDocumentView,
    ApproveKYCView,
    RejectKYCView,
    SubmitCreditApplicationView,
    PendingCreditApplicationsView,
    ApproveCreditApplicationView,
    RejectCreditApplicationView,
    FraudFlagsView,
    UserConsentsView,
    DataErasureView,
    KYCExpiryView,
    ForgotPasswordView,
    ResetPasswordView,
    ForgotPinView,
    ResetPinView,
    RegisterFCMTokenView,
)

urlpatterns = [

    # Auth
    path('register/', RegisterView.as_view()),
    path('login/', LoginView.as_view()),
    path('set-pin/', SetPinView.as_view()),
    path('pin-login/', PinLoginView.as_view()),

    # Phone / OTP verification
    path('verify-phone/', VerifyPhoneOTPView.as_view()),
    path('resend-otp/', ResendOTPView.as_view()),

    # Password & PIN reset (email OTP)
    path('forgot-password/', ForgotPasswordView.as_view()),
    path('reset-password/', ResetPasswordView.as_view()),
    path('forgot-pin/', ForgotPinView.as_view()),
    path('reset-pin/', ResetPinView.as_view()),

    # FCM push token
    path('register-fcm/', RegisterFCMTokenView.as_view()),

    # KYC
    path('kyc/upload/', UploadKYCDocumentView.as_view()),
    path('kyc/<uuid:user_id>/approve/', ApproveKYCView.as_view()),
    path('kyc/<uuid:user_id>/reject/', RejectKYCView.as_view()),
    path('kyc/expiry/', KYCExpiryView.as_view()),

    # Credit applications
    path('credit/apply/', SubmitCreditApplicationView.as_view()),
    path('admin/credit/pending/', PendingCreditApplicationsView.as_view()),
    path('admin/credit/<uuid:application_id>/approve/', ApproveCreditApplicationView.as_view()),
    path('admin/credit/<uuid:application_id>/reject/', RejectCreditApplicationView.as_view()),

    # Fraud flags
    path('admin/fraud-flags/', FraudFlagsView.as_view()),

    # PDPA
    path('my-consents/', UserConsentsView.as_view()),
    path('erase-my-data/', DataErasureView.as_view()),
]
