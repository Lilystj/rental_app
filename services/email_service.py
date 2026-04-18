"""
Email notification service for WheelShare
Handles sending emails for bookings, payments, subscriptions, etc.
"""
from flask_mail import Mail, Message
from flask import render_template_string, current_app
import logging

mail = Mail()

# Simple email templates
BOOKING_CONFIRMATION_TEMPLATE = """
<html>
    <body style="font-family: Arial, sans-serif; color: #333; background-color: #f5f5f5; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h2 style="color: #ff9800; border-bottom: 2px solid #ff9800; padding-bottom: 10px;">Booking Confirmation - WheelShare</h2>
            
            <p>Hi <strong>{{ user_name }}</strong>,</p>
            
            <p>Your booking has been confirmed! Here are your booking details:</p>
            
            <div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid #ff9800; margin: 20px 0;">
                <p><strong>Booking ID:</strong> #{{ booking_id }}</p>
                <p><strong>Vehicle:</strong> {{ vehicle_name }}</p>
                <p><strong>Duration:</strong> {{ days }} days</p>
                <p><strong>Total Amount:</strong> ₹{{ amount }}</p>
                <p><strong>Booking Date:</strong> {{ booking_date }}</p>
                <p><strong>Status:</strong> <span style="color: #ff9800; font-weight: bold;">{{ status }}</span></p>
            </div>
            
            <p>Thank you for choosing WheelShare. If you have any questions, please contact our support team.</p>
            
            <p style="color: #999; font-size: 12px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px;">
                This is an automated email. Please do not reply. Contact support@wheelshare.com for assistance.
            </p>
        </div>
    </body>
</html>
"""

PAYMENT_SUCCESS_TEMPLATE = """
<html>
    <body style="font-family: Arial, sans-serif; color: #333; background-color: #f5f5f5; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h2 style="color: #4caf50; border-bottom: 2px solid #4caf50; padding-bottom: 10px;">Payment Successful ✓</h2>
            
            <p>Hi <strong>{{ user_name }}</strong>,</p>
            
            <p>Your payment has been processed successfully!</p>
            
            <div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid #4caf50; margin: 20px 0;">
                <p><strong>Amount Paid:</strong> ₹{{ amount }}</p>
                <p><strong>Transaction ID:</strong> {{ transaction_id }}</p>
                <p><strong>Date:</strong> {{ payment_date }}</p>
                <p><strong>Status:</strong> <span style="color: #4caf50; font-weight: bold;">COMPLETED</span></p>
            </div>
            
            <p>Your receipt has been sent to your email. Keep it for your records.</p>
            
            <p style="color: #999; font-size: 12px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px;">
                Need help? Contact support@wheelshare.com
            </p>
        </div>
    </body>
</html>
"""

SUBSCRIPTION_ACTIVATED_TEMPLATE = """
<html>
    <body style="font-family: Arial, sans-serif; color: #333; background-color: #f5f5f5; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h2 style="color: #2196f3; border-bottom: 2px solid #2196f3; padding-bottom: 10px;">Subscription Activated ✓</h2>
            
            <p>Hi <strong>{{ user_name }}</strong>,</p>
            
            <p>Your <strong>{{ plan_name }}</strong> subscription is now active!</p>
            
            <div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid #2196f3; margin: 20px 0;">
                <p><strong>Plan:</strong> {{ plan_name }}</p>
                <p><strong>Valid Until:</strong> {{ end_date }}</p>
                <p><strong>Monthly Price:</strong> ₹{{ price }}</p>
                <p><strong>Discount:</strong> {{ discount }}% on all rentals</p>
            </div>
            
            <h3>Your Benefits:</h3>
            <ul>
                <li>{{ discount }}% discount on all bookings</li>
                <li>{{ free_cancellation_hours }} hours free cancellation</li>
                {% if priority_booking %}
                <li>Priority booking on new vehicles</li>
                {% endif %}
            </ul>
            
            <p>Start booking now and enjoy your benefits!</p>
            
            <p style="color: #999; font-size: 12px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px;">
                Contact support@wheelshare.com for subscription issues.
            </p>
        </div>
    </body>
</html>
"""


def send_booking_confirmation(user_email, user_name, booking_id, vehicle_name, days, amount, booking_date, status):
    """Send booking confirmation email"""
    try:
        html = render_template_string(BOOKING_CONFIRMATION_TEMPLATE,
            user_name=user_name,
            booking_id=booking_id,
            vehicle_name=vehicle_name,
            days=days,
            amount=amount,
            booking_date=booking_date,
            status=status
        )
        
        msg = Message(
            subject=f'Booking Confirmation #{booking_id} - WheelShare',
            recipients=[user_email],
            html=html
        )
        mail.send(msg)
        logging.info(f"Booking confirmation email sent to {user_email}")
        return True
    except Exception as e:
        logging.error(f"Error sending booking confirmation email: {e}")
        return False


def send_payment_success(user_email, user_name, amount, transaction_id, payment_date):
    """Send payment success email"""
    try:
        html = render_template_string(PAYMENT_SUCCESS_TEMPLATE,
            user_name=user_name,
            amount=amount,
            transaction_id=transaction_id,
            payment_date=payment_date
        )
        
        msg = Message(
            subject=f'Payment Successful - Transaction {transaction_id}',
            recipients=[user_email],
            html=html
        )
        mail.send(msg)
        logging.info(f"Payment success email sent to {user_email}")
        return True
    except Exception as e:
        logging.error(f"Error sending payment success email: {e}")
        return False


def send_subscription_activated(user_email, user_name, plan_name, end_date, price, discount, 
                                free_cancellation_hours, priority_booking=False):
    """Send subscription activation email"""
    try:
        html = render_template_string(SUBSCRIPTION_ACTIVATED_TEMPLATE,
            user_name=user_name,
            plan_name=plan_name,
            end_date=end_date,
            price=price,
            discount=discount,
            free_cancellation_hours=free_cancellation_hours,
            priority_booking=priority_booking
        )
        
        msg = Message(
            subject=f'{plan_name} Subscription Activated - WheelShare',
            recipients=[user_email],
            html=html
        )
        mail.send(msg)
        logging.info(f"Subscription activation email sent to {user_email}")
        return True
    except Exception as e:
        logging.error(f"Error sending subscription activation email: {e}")
        return False


def send_handover_notification(user_email, user_name, booking_id, vehicle_name):
    """Notify user when admin marks vehicle ready for handover."""
    try:
        subject = f"Your booking #{booking_id} is ready for handover"
        body = (
            f"Hi {user_name},\n\n" 
            f"The vehicle '{vehicle_name}' for booking #{booking_id} is now ready to be handed over to you. "
            "Please visit our office at your earliest convenience to collect it.\n\n"
            "Thank you for using WheelShare!\n"
        )
        msg = Message(subject=subject, recipients=[user_email], body=body)
        mail.send(msg)
        logging.info(f"Handover notification email sent to {user_email}")
        return True
    except Exception as e:
        logging.error(f"Error sending handover notification email: {e}")
        return False


def send_completion_notification(user_email, user_name, booking_id, vehicle_name):
    """Notify user when admin confirms vehicle has been received (booking completed)."""
    try:
        subject = f"Your booking #{booking_id} is now completed"
        body = (
            f"Hi {user_name},\n\n"
            f"Thank you for returning the vehicle '{vehicle_name}' for booking #{booking_id}. "
            "Your booking has been marked as completed. We hope you had a great experience!\n\n"
            "Please consider leaving feedback or booking again.\n"
            "Thank you for using WheelShare!\n"
        )
        msg = Message(subject=subject, recipients=[user_email], body=body)
        mail.send(msg)
        logging.info(f"Completion notification email sent to {user_email}")
        return True
    except Exception as e:
        logging.error(f"Error sending completion notification email: {e}")
        return False


def send_admin_notification(admin_email, subject, message):
    """Send notification to admin"""
    try:
        msg = Message(
            subject=subject,
            recipients=[admin_email],
            body=message
        )
        mail.send(msg)
        logging.info(f"Admin notification sent: {subject}")
        return True
    except Exception as e:
        logging.error(f"Error sending admin notification: {e}")
        return False


def send_plain_email(to_email, subject, body):
    """Send a simple plain-text email to a single recipient."""
    try:
        msg = Message(subject=subject, recipients=[to_email], body=body)
        mail.send(msg)
        logging.info(f"Plain email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logging.error(f"Error sending plain email to {to_email}: {e}")
        return False
