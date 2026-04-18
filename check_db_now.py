#!/usr/bin/env python
import sys
sys.path.insert(0, 'c:\\Users\\91877\\rental_app')

from app import app, db
from models import Booking, Vehicle

with app.app_context():
    print("\n" + "="*60)
    print("CURRENT DATABASE STATUS")
    print("="*60 + "\n")
    
    # Check vehicles
    vehicles = Vehicle.query.all()
    print("VEHICLES:")
    for v in vehicles:
        print(f"  {v.id}. {v.name}: {v.availability_status}")
    
    print("\nBOOKINGS:")
    bookings = Booking.query.all()
    for b in bookings:
        v = Vehicle.query.get(b.vehicle_id)
        print(f"  #{b.id}: {v.name if v else 'Unknown'} - User {b.user_id} - Status: {b.booking_status}")
    
    print("\n" + "="*60 + "\n")
