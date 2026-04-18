"""
Payment service for WheelShare
Handles Razorpay payment processing and order management
"""
import razorpay
import logging
from datetime import datetime
import hashlib
import hmac

class RazorpayPaymentService:
    def __init__(self, key_id, key_secret):
        """Initialize Razorpay client"""
        self.client = razorpay.Client(auth=(key_id, key_secret))
        self.key_id = key_id
        self.key_secret = key_secret
    
    def create_order(self, amount, currency='INR', receipt=None, notes=None):
        """
        Create a payment order with Razorpay
        
        Args:
            amount: Amount in paise (e.g., 50000 for ₹500)
            currency: Currency code (default: INR)
            receipt: Receipt ID (optional)
            notes: Additional notes dictionary (optional)
        
        Returns:
            Order object or None if error
        """
        try:
            order_data = {
                'amount': amount,
                'currency': currency,
            }
            
            if receipt:
                order_data['receipt'] = receipt
            
            if notes:
                order_data['notes'] = notes
            
            order = self.client.order.create(data=order_data)
            logging.info(f"Order created: {order['id']}")
            return order
        except Exception as e:
            logging.error(f"Error creating order: {e}")
            return None
    
    def verify_payment_signature(self, payment_id, order_id, signature):
        """
        Verify payment signature from Razorpay webhook/callback
        
        Args:
            payment_id: Razorpay payment ID
            order_id: Razorpay order ID
            signature: Payment signature from Razorpay
        
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            data = f"{order_id}|{payment_id}"
            generated_signature = hmac.new(
                self.key_secret.encode(),
                data.encode(),
                hashlib.sha256
            ).hexdigest()
            
            is_valid = generated_signature == signature
            if is_valid:
                logging.info(f"Payment signature verified for order {order_id}")
            else:
                logging.warning(f"Invalid payment signature for order {order_id}")
            
            return is_valid
        except Exception as e:
            logging.error(f"Error verifying signature: {e}")
            return False
    
    def fetch_payment(self, payment_id):
        """
        Fetch payment details from Razorpay
        
        Args:
            payment_id: Razorpay payment ID
        
        Returns:
            Payment object or None if error
        """
        try:
            payment = self.client.payment.fetch(payment_id)
            return payment
        except Exception as e:
            logging.error(f"Error fetching payment: {e}")
            return None
    
    def fetch_order(self, order_id):
        """
        Fetch order details from Razorpay
        
        Args:
            order_id: Razorpay order ID
        
        Returns:
            Order object or None if error
        """
        try:
            order = self.client.order.fetch(order_id)
            return order
        except Exception as e:
            logging.error(f"Error fetching order: {e}")
            return None
    
    def refund_payment(self, payment_id, amount=None):
        """
        Refund a payment
        
        Args:
            payment_id: Razorpay payment ID
            amount: Amount to refund in paise (optional, full refund if not specified)
        
        Returns:
            Refund object or None if error
        """
        try:
            refund_data = {}
            if amount:
                refund_data['amount'] = amount
            
            refund = self.client.payment.refund(payment_id, refund_data)
            logging.info(f"Refund initiated for payment {payment_id}")
            return refund
        except Exception as e:
            logging.error(f"Error refunding payment: {e}")
            return None
    
    def capture_payment(self, payment_id, amount):
        """
        Capture authorized payment
        
        Args:
            payment_id: Razorpay payment ID
            amount: Amount to capture in paise
        
        Returns:
            Payment object or None if error
        """
        try:
            payment = self.client.payment.capture(payment_id, amount)
            logging.info(f"Payment captured: {payment_id}")
            return payment
        except Exception as e:
            logging.error(f"Error capturing payment: {e}")
            return None


def calculate_booking_amount(vehicle_price_per_day, days, subscription_discount=0):
    """
    Calculate total booking amount with discount
    
    Args:
        vehicle_price_per_day: Vehicle rental price per day
        days: Number of days to rent
        subscription_discount: Discount percentage (0-100)
    
    Returns:
        Dictionary with cost breakdown
    """
    subtotal = vehicle_price_per_day * days
    discount_amount = (subtotal * subscription_discount) / 100
    total = subtotal - discount_amount
    
    return {
        'subtotal': int(subtotal),
        'discount_amount': int(discount_amount),
        'discount_percent': subscription_discount,
        'total': int(total),
        'total_paise': int(total * 100)  # Convert to paise for Razorpay
    }


def process_payment_callback(payment_data, payment_service):
    """
    Process payment callback from Razorpay
    
    Args:
        payment_data: Dictionary with payment_id, order_id, signature
        payment_service: RazorpayPaymentService instance
    
    Returns:
        Tuple (success: bool, payment_info: dict)
    """
    try:
        # Verify signature
        is_valid = payment_service.verify_payment_signature(
            payment_data['payment_id'],
            payment_data['order_id'],
            payment_data['signature']
        )
        
        if not is_valid:
            return False, {'error': 'Invalid payment signature'}
        
        # Fetch payment details
        payment = payment_service.fetch_payment(payment_data['payment_id'])
        
        if not payment:
            return False, {'error': 'Payment not found'}
        
        # Check payment status
        if payment['status'] != 'captured':
            return False, {'error': f'Payment status: {payment["status"]}'}
        
        return True, {
            'payment_id': payment['id'],
            'order_id': payment['order_id'],
            'amount': payment['amount'] / 100,  # Convert from paise to rupees
            'status': payment['status'],
            'method': payment.get('method', 'unknown'),
            'created_at': datetime.fromtimestamp(payment['created_at']),
            'vpa': payment.get('vpa', None),  # For UPI payments
            'card_id': payment.get('card_id', None)  # For card payments
        }
    
    except Exception as e:
        logging.error(f"Error processing payment callback: {e}")
        return False, {'error': str(e)}
