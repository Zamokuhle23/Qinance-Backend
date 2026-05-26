from django.urls import path
from . import views

urlpatterns = [
    # Merchants
    path('merchants/', views.MerchantListView.as_view()),
    path('merchants/<uuid:pk>/', views.MerchantDetailView.as_view()),
    path('merchants/<uuid:merchant_id>/latest-session/', views.MerchantLatestSessionView.as_view()),

    # Customers
    path('customers/', views.CustomerListView.as_view()),
    path('customers/<str:phone>/', views.CustomerDetailView.as_view()),

    # Card
    path('card/<str:phone>/', views.CardDetailView.as_view()),
    path('card/freeze/', views.FreezeCardView.as_view()),

    # Statements
    path('statements/<str:phone>/', views.StatementListView.as_view()),
    path('statements/<str:phone>/current/', views.CurrentStatementView.as_view()),

    # Repayments
    path('repayments/', views.MakeRepaymentView.as_view()),

    # Payment Sessions
    path('sessions/create/', views.CreateSessionView.as_view()),
    path('sessions/confirm/', views.ConfirmPaymentView.as_view()),
    path('sessions/<uuid:session_id>/', views.SessionDetailView.as_view()),

    # Regulatory reports
    path('regulatory-reports/', views.RegulatoryReportListView.as_view()),
    path('regulatory-reports/<uuid:report_id>/submit/', views.SubmitReportView.as_view()),

    # Admin / Dashboard
    path('transactions/', views.TransactionListView.as_view()),
    path('credit-transactions/', views.CreditTransactionListView.as_view()),
    path('dashboard/stats/', views.DashboardStatsView.as_view()),

    # Agent Banking
    path('agent/merchants/', views.NearbyAgentMerchantsView.as_view()),
    path('agent/initiate/', views.InitiateAgentTransactionView.as_view()),
    path('agent/confirm/', views.ConfirmAgentTransactionView.as_view()),
    path('agent/cancel/', views.CancelAgentTransactionView.as_view()),
    path('agent/history/', views.AgentTransactionHistoryView.as_view()),
    path('agent/merchant-profile/<uuid:merchant_id>/', views.MerchantAgentProfileView.as_view()),

    # MTN MoMo
    path('momo/register/', views.RegisterMoMoView.as_view()),
    path('momo/status/<str:reference_id>/', views.MoMoStatusView.as_view()),

    # Agent Session — QR / NFC channels
    path('agent/session/create/', views.CreateAgentSessionView.as_view()),
    path('agent/session/<uuid:session_id>/', views.GetAgentSessionView.as_view()),
    path('agent/session/<uuid:session_id>/confirm/', views.ConfirmAgentSessionView.as_view()),

    # Agent Banking — Sound / Contactless channel
    path('agent/sound-initiate/', views.SoundAgentInitiateView.as_view()),

    # Contactless / Sound Payment
    path('sound/sync-secret/', views.SyncDeviceSecretView.as_view()),
    path('sound/process/', views.ProcessSoundPaymentView.as_view()),
    path('sound/status/<uuid:settlement_id>/', views.SoundPaymentStatusView.as_view()),
    path('sound/settlements/', views.SoundSettlementListView.as_view()),
    path('sound/merchant-trust/<uuid:merchant_id>/', views.MerchantTrustView.as_view()),
]
