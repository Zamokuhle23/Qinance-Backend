from django.urls import path
from . import views

urlpatterns = [
    path('campaigns/', views.CampaignListCreateView.as_view()),
    path('campaigns/<uuid:campaign_id>/', views.CampaignDetailView.as_view()),
    path('campaigns/<uuid:campaign_id>/track/', views.CampaignTrackView.as_view()),
    path('merchants/<uuid:merchant_id>/campaigns/', views.MerchantCampaignsView.as_view()),
    path('merchants/<uuid:merchant_id>/ai-summary/', views.MerchantAISummaryView.as_view()),
    path('ai/stats/', views.AILogStatsView.as_view()),
    path('ai/ask/', views.AIAskView.as_view()),
    path('ai/recent/', views.AIRecentRequestsView.as_view()),
    path('deals/save/', views.SavedDealView.as_view()),
    path('merchants/favourite/', views.FavouriteMerchantView.as_view()),
]