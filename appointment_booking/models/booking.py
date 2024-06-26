from datetime import timedelta , datetime

from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.db.models import Q, F
from appointment_booking.models.service import Service
from appointment_booking.models.company import Company
from appointment_booking.models.branch import Branch
from appointment_booking.models.staff_times import Staff_Times

from appointment_booking.models.helper.enums import Order_Status_Choices


class Booking(models.Model):
    """
    The `Booking` model represents a booking with specified attributes and constraints.

    Attributes:
        start_time (DateTimeField): The start time for the booking.
        end_time (DateTimeField): The end time for the booking.
        service_id (ForeignKey): Reference to the `Service` model.
        main_id (ForeignKey): Reference to the `MainModel`.
        branch_id (ForeignKey): Reference to the `Branch` model.
        is_canceled (BooleanField): Indicates if the booking is canceled.
        payment_status (CharField): Status of the payment.
    """

    start_time: models.DateTimeField = models.DateTimeField(
        verbose_name=_("Start Time"),
        help_text=_("The start time for the booking."),
        db_column="start_time",
    )

    end_time: models.DateTimeField = models.DateTimeField(
        verbose_name=_("End Time"),
        help_text=_("The end time for the booking."),
        db_column="end_time",
    )

    booked_by: models.ForeignKey[settings.AUTH_USER_MODEL] = models.ForeignKey(
        settings.AUTH_USER_MODEL ,
        on_delete = models.CASCADE ,
        #? Todo
        related_name="applied_bookings",  # Name for reverse lookup
        verbose_name = _("Booked By") ,
        help_text = _("The user who booked the booking.") ,
        db_column = "booked_by" ,
    )

    service_id: models.ForeignKey[Service] = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name="bookings",
        verbose_name=_("Service"),
        help_text=_("The service associated with this booking."),
        db_column="service_id",
    )

    main_id: models.ForeignKey[Company] = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="bookings",
        verbose_name=_("Main Entity"),
        help_text=_("The main entity associated with this booking."),
        db_column="main_id",
    )

    branch_id: models.ForeignKey[Branch] = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="bookings",
        verbose_name=_("Branch"),
        help_text=_("The branch associated with this booking."),
        db_column="branch_id",
    )

    is_canceled: models.BooleanField = models.BooleanField(
        default=False,
        verbose_name=_("Is Canceled"),
        help_text=_("Indicates if this booking is canceled."),
        db_column="is_canceled",
    )

    payment_status: models.CharField = models.CharField(
        max_length = 10 ,
        choices = Order_Status_Choices.choices ,
        default = Order_Status_Choices.PENDING ,
        verbose_name = _("Payment Status") ,
        help_text = _("Status of the payment.") ,
        db_column = "payment_status" ,
    )

    class Meta:
        db_table = "bookings"
        verbose_name = _("Booking")
        verbose_name_plural = _("Bookings")
        ordering = ["-start_time"]  # Order by most recent start time

        constraints = [
            models.CheckConstraint(
                check=Q(start_time__lt=F("end_time")),
                name="start_time_before_end_time",
                violation_error_message=_("Start time must be before end time."),
            ),
            models.CheckConstraint(
                check = models.Q(payment_status__in = [choice.value for choice in Order_Status_Choices]) ,
                name = "valid_payment_status_booking" ,
                violation_error_message = _("Payment status must be one of the following: {choices}.").format(
                    choices = Order_Status_Choices.get_available_choices()) ,
            ) ,
        ]

    def clean(self) -> None:
        """
        Custom validation logic to ensure data integrity.
        """
        if self.start_time >= self.end_time:
            raise ValidationError({
                "start_time": _("Start time must be before end time."),
            })

        # 20-second delay to the current time
        future_time = timezone.now() - timedelta(seconds = 20)

        if self.start_time < future_time:
            raise ValidationError({
                "start_time": _("Start time must be at least 20 seconds in the future.") ,
            })

        # Check for overlaps with other bookings for the same service
        overlapping_bookings = Booking.objects.filter(
            service_id = self.service_id ,
            start_time__lt = self.end_time ,
            end_time__gt = self.start_time ,
        ).exclude(id = self.id)

        if overlapping_bookings.exists():
            raise ValidationError({
                "start_time": _("This booking overlaps with an existing booking for the same service.")
            })

        # Find the staff assigned to the service
        assigned_staff = self.service_id.assigned_staffs  # Assuming each service has a related staff

        if not assigned_staff:
            raise ValidationError({
                "service_id": _("No staff is assigned to this service.") ,
            })

        # Retrieve working hours from StaffTimes
        staff_times = Staff_Times.objects.filter(user = assigned_staff).first()

        if not staff_times:
            raise ValidationError({
                "service_id": _("Working hours for the assigned staff are not defined.") ,
            })

        # Validate that the booking times are within the staff's working hours
        day_of_week = self.start_time.strftime('%A').lower()  # Get day of the week in lowercase
        if day_of_week not in staff_times.working_hours:
            raise ValidationError({
                "start_time": _("Working hours for this day are not defined.") ,
            })

        day_times = staff_times.working_hours[day_of_week]
        # Convert start and end times from JSON to time
        start_str = day_times.get("start" , "")
        end_str = day_times.get("end" , "")
        if not start_str or not end_str:
            raise ValidationError({
                "start_time": _("Working hours are incomplete for this day.") ,
            })

        start_time = datetime.strptime(start_str , "%I:%M%p").time()
        end_time = datetime.strptime(end_str , "%I:%M%p").time()
        booking_start_time = self.start_time.time()
        booking_end_time = self.end_time.time()

        if not (start_time <= booking_start_time <= end_time) or not (start_time <= booking_end_time <= end_time):
            raise ValidationError({
                    "start_time": _("Booking times must be within the working hours of the assigned staff.") ,
            })



        if self.payment_status not in dict(Order_Status_Choices.choices):
            raise ValidationError(
                _(f'The status of the booking must be: {Order_Status_Choices.get_available_choices()} .') ,
                code = 'invalid'
            )

        super().clean()

    def __str__(self) -> str:
        """
        String representation of the `Booking` model.
        """
        return f"Booking for {self.service_id} from {self.start_time} to {self.end_time}"
