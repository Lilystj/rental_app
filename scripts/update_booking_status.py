#!/usr/bin/env python
"""
Script to update all pending/confirmed bookings to completed status
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app
from extensions import db
from models import Booking, Vehicle
import datetime

with app.app_context():
    try:
        # Get all bookings with status 'Confirmed' (these are the active ones that need to be marked as completed)
        confirmed_bookings = Booking.query.filter_by(booking_status='Confirmed').all()
        
        print(f"Found {len(confirmed_bookings)} confirmed bookings to update...")
        
        for booking in confirmed_bookings:
            print(f"Updating booking #{booking.id}: {booking.booking_status} -> Completed")
            booking.booking_status = 'Completed'
            
            # Also update vehicle status back to Available
            vehicle = db.session.get(Vehicle, booking.vehicle_id)
            if vehicle:
                vehicle.availability_status = 'Available'
                print(f"  - Vehicle {vehicle.name}: Rented -> Available")
        
        db.session.commit()
        print(f"\n✓ Successfully updated {len(confirmed_bookings)} bookings to Completed status!")
        
    except Exception as e:
        print(f"✗ Error updating bookings: {e}")
        db.session.rollback()
        sys.exit(1)
