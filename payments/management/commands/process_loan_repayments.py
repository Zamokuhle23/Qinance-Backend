from django.core.management.base import BaseCommand
from django.utils import timezone
from payments.models import MerchantLoan, PublicHoliday

class Command(BaseCommand):
    help = 'Process automatic merchant loan repayments based on their chosen schedule and frequency.'

    def handle(self, *args, **options):
        self.stdout.write("Checking for due loan repayments...")
        
        today = timezone.now().date()
        current_time = timezone.now().time()
        
        # Check if today is a working day
        is_holiday = PublicHoliday.objects.filter(holiday_date=today).exists()
        if today.weekday() >= 5 or is_holiday:
            self.stdout.write(self.style.WARNING(f"Today ({today}) is not a working day. Skipping auto-repayments."))
            return

        # Query active loans that are due
        # In a real system, we'd check the last_repayment_date and frequency
        # For the hackathon, we simulate daily check for simplicity
        active_loans = MerchantLoan.objects.filter(status='active', start_date__lte=today, due_date__gte=today)
        
        processed_count = 0
        for loan in active_loans:
            # Check schedule time (optional but good for realism)
            if loan.repayment_schedule_time and loan.repayment_schedule_time > current_time:
                continue
            
            success, msg = loan.process_automatic_repayment()
            if success:
                self.stdout.write(self.style.SUCCESS(f"Processed: {loan.merchant.name} - {msg}"))
                processed_count += 1
            else:
                self.stdout.write(self.style.ERROR(f"Failed: {loan.merchant.name} - {msg}"))

        self.stdout.write(f"Done. Processed {processed_count} repayments.")
