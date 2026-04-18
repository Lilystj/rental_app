"""
WheelShare Services Module
Contains reusable services for email, payments, subscriptions and other functionality
"""

from .email_service import (
    mail,
    send_booking_confirmation,
    send_payment_success,
    send_subscription_activated,
    send_admin_notification
)

from .payment_service import (
    RazorpayPaymentService,
    calculate_booking_amount,
    process_payment_callback
)

from .subscription_service import (
    get_active_subscription,
    calculate_subscription_price,
    check_subscription_expiry,
    renew_subscription
)

__all__ = [
    'mail',
    'send_booking_confirmation',
    'send_payment_success',
    'send_subscription_activated',
    'send_admin_notification',
    'RazorpayPaymentService',
    'calculate_booking_amount',
    'process_payment_callback',
    'get_active_subscription',
    'calculate_subscription_price',
    'check_subscription_expiry',
    'renew_subscription'
]
