#!/usr/bin/env python
"""
Script to backfill missing Payment records for existing bookings
"""
import sys
sys.path.insert(0, 'c:\\Users\\91877\\rental_app')

from app import app, db
from models import Payment, Booking, Vehicle, User
import uuid
from datetime import datetime

with app.app_context():
    print("\n" + "="*80)
    print("BACKFILLING MISSING PAYMENT RECORDS")
    print("="*80 + "\n")
    
    # Get all bookings that are Paid or Completed but don't have payment records
    bookings = Booking.query.filter(Booking.booking_status.in_(['Paid', 'Completed', 'Booked'])).all()
    
    print(f"Found {len(bookings)} bookings to check...\n")
    
    created_count = 0
    skipped_count = 0
    
    for booking in bookings:
        # Check if payment already exists for this booking
        existing_payment = Payment.query.filter_by(booking_id=booking.id).first()
        
        if existing_payment:
            print(f"✓ Booking {booking.id}: Payment already exists (ID: {existing_payment.id})")
            skipped_count += 1
            continue
        
        # Create payment record
        try:
            payment = Payment(
                user_id=booking.user_id,
                booking_id=booking.id,
                amount=booking.total_price or 0.0,
                payment_method='backfilled',
                transaction_id=f"BACKFILL-{booking.id}-{uuid.uuid4().hex[:8]}",
                status='Success',
                description=f"Backfilled payment for booking #{booking.id}",
                created_date=booking.booking_date or datetime.now()
            )
            db.session.add(payment)
            db.session.flush()
            
            print(f"✓ Booking {booking.id}: Created Payment record (Amount: ₹{booking.total_price}, Status: {booking.booking_status})")
            created_count += 1
        except Exception as e:
            print(f"✗ Booking {booking.id}: Error creating payment - {str(e)}")
            db.session.rollback()
            continue
    
    # Commit all changes
    try:
        db.session.commit()
        print(f"\n" + "="*80)
        print(f"SUMMARY: Created {created_count} payment records, Skipped {skipped_count} existing")
        print("="*80 + "\n")
    except Exception as e:
        print(f"\nError committing changes: {str(e)}")
        db.session.rollback()
