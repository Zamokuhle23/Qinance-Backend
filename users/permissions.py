from rest_framework.permissions import BasePermission


class IsKYCOfficer(BasePermission):

    def has_permission(self, request, view):

        return (
            request.user.is_authenticated and
            request.user.role in [
                'kyc_officer',
                'super_admin'
            ]
        )


class IsCreditOfficer(BasePermission):

    def has_permission(self, request, view):

        return (
            request.user.is_authenticated and
            request.user.role in [
                'credit_officer',
                'super_admin'
            ]
        )


class IsFraudAnalyst(BasePermission):

    def has_permission(self, request, view):

        return (
            request.user.is_authenticated and
            request.user.role in [
                'fraud_analyst',
                'super_admin'
            ]
        )