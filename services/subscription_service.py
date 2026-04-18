"""
Subscription management routes and utilities for WheelShare
"""

def get_active_subscription(user_id):
    """Get user's active subscription"""
    from extensions import db
    from models import UserSubscription, SubscriptionPlan
    
    subscription = UserSubscription.query.filter_by(
        user_id=user_id,
        status='Active'
    ).first()
    
    if subscription:
        plan = db.session.get(SubscriptionPlan, subscription.plan_id)
        return {
            'subscription': subscription,
            'plan': plan
        }
    
    return None


def calculate_subscription_price(plan_price, billing_cycle_months=1):
    """Calculate subscription price with applicable taxes"""
    base_price = plan_price * billing_cycle_months
    # GST 18% in India
    gst = base_price * 0.18
    total = base_price + gst
    
    return {
        'base_price': base_price,
        'gst': gst,
        'total_price': total
    }


def check_subscription_expiry(subscription):
    """Check if subscription has expired"""
    from datetime import datetime
    
    if subscription and subscription.end_date:
        return datetime.now() > subscription.end_date
    
    return False


def renew_subscription(subscription, plan):
    """Renew a subscription for another billing cycle"""
    from extensions import db
    from datetime import datetime, timedelta
    
    # Add one month to end date
    new_end_date = subscription.end_date + timedelta(days=30)
    
    subscription.end_date = new_end_date
    subscription.status = 'Active'
    
    db.session.commit()
    
    return subscription
