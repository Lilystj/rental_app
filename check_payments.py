#!/usr/bin/env python
import sys
sys.path.insert(0, 'c:\\Users\\91877\\rental_app')

from app import app, db
from models import Payment, User, Booking, Vehicle, UserSubscription, SubscriptionPlan

with app.app_context():
    print("\n" + "="*80)
    print("PAYMENT DATABASE STATUS")
    print("="*80 + "\n")
    
    # Check users
    users = User.query.all()
    print(f"TOTAL USERS: {len(users)}")
    for u in users:
        print(f"  ID: {u.id}, Username: {u.username}, Email: {u.email}")
    
    # Check all payments
    payments = Payment.query.all()
    print(f"\nTOTAL PAYMENTS: {len(payments)}")
    for p in payments:
        print(f"  ID: {p.id}, User ID: {p.user_id}, Amount: ₹{p.amount}, Status: {p.status}, Method: {p.payment_method}, TxnID: {p.transaction_id}, Booking: {p.booking_id}, Sub: {p.subscription_id}, Date: {p.created_date}")
    
    # Check payments per user
    print("\nPAYMENTS PER USER:")
    for u in users:
        user_payments = Payment.query.filter_by(user_id=u.id).all()
        print(f"  User {u.id} ({u.username}): {len(user_payments)} payments")
        for p in user_payments:
            print(f"    - ₹{p.amount} ({p.status}) on {p.created_date}")
    
    # Check bookings
    bookings = Booking.query.all()
    print(f"\nTOTAL BOOKINGS: {len(bookings)}")
    for b in bookings:
        v = Vehicle.query.get(b.vehicle_id)
        print(f"  ID: {b.id}, User: {b.user_id}, Vehicle: {v.name if v else 'Unknown'}, Status: {b.booking_status}")
    
    # Check subscriptions
    subscriptions = UserSubscription.query.all()
    print(f"\nTOTAL SUBSCRIPTIONS: {len(subscriptions)}")
    for s in subscriptions:
        plan = SubscriptionPlan.query.get(s.plan_id)
        print(f"  ID: {s.id}, User: {s.user_id}, Plan: {plan.name if plan else 'Unknown'}, Status: {s.status}")
    
    print("\n" + "="*80 + "\n")
