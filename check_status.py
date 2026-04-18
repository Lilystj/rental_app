#!/usr/bin/env python
import sys
sys.path.insert(0, 'c:\\Users\\91877\\rental_app')

from app import app, db
from models import Booking, Vehicle

with app.app_context():
    print("=== CHECKING DATABASE STATUS ===\n")
    
    # Check bookings
    bookings = Booking.query.all()
    print(f"Total Bookings: {len(bookings)}\n")
    for booking in bookings:
        vehicle = Vehicle.query.get(booking.vehicle_id)
        print(f"Booking #{booking.id}:")
        print(f"  User ID: {booking.user_id}")
        print(f"  Vehicle: {vehicle.name if vehicle else 'Unknown'}")
        print(f"  Booking Status: {booking.booking_status}")
        print(f"  Total Price: {booking.total_price}")
        print()
    
    # Check vehicles
    print("\n=== VEHICLES STATUS ===\n")
    vehicles = Vehicle.query.all()
    for vehicle in vehicles:
        print(f"Vehicle #{vehicle.id}: {vehicle.name}")
        print(f"  Status: {vehicle.availability_status}")
        print(f"  Price/Day: {vehicle.price_per_day}")
        print()
