#!/usr/bin/env python
import sys
sys.path.insert(0, 'c:\\Users\\91877\\rental_app')

from app import app, db
from models import Booking, Vehicle

with app.app_context():
    print("=== FIXING DATABASE STATUS ===\n")
    
    # Fix booking statuses: "Booked" → "Paid", and update vehicle statuses
    booked_bookings = Booking.query.filter_by(booking_status='Booked').all()
    
    if booked_bookings:
        print(f"Found {len(booked_bookings)} booking(s) with 'Booked' status\n")
        for booking in booked_bookings:
            vehicle = Vehicle.query.get(booking.vehicle_id)
            print(f"Updating Booking #{booking.id}:")
            print(f"  Status: Booked -> Paid")
            booking.booking_status = 'Paid'
            
            if vehicle:
                print(f"  Vehicle '{vehicle.name}': {vehicle.availability_status} -> Rented")
                vehicle.availability_status = 'Rented'
            print()
    
    # Fix any bookings left in Handover state (admin forgot to receive)
    handover_bookings = Booking.query.filter_by(booking_status='Handover').all()
    if handover_bookings:
        print(f"Found {len(handover_bookings)} booking(s) still in Handover state\n")
        for booking in handover_bookings:
            vehicle = Vehicle.query.get(booking.vehicle_id)
            print(f"Completing Booking #{booking.id}: Handover -> Completed")
            booking.booking_status = 'Completed'
            if vehicle and vehicle.availability_status == 'Rented':
                print(f"  Vehicle '{vehicle.name}': Rented -> Available")
                vehicle.availability_status = 'Available'
            print()
    else:
        print("No 'Booked' bookings found")
    
    # Fix vehicle statuses: "Booked" → "Rented"
    booked_vehicles = Vehicle.query.filter_by(availability_status='Booked').all()
    
    if booked_vehicles:
        print(f"\nFound {len(booked_vehicles)} vehicle(s) with 'Booked' status\n")
        for vehicle in booked_vehicles:
            print(f"Updating Vehicle #{vehicle.id} '{vehicle.name}':")
            print(f"  Status: Booked -> Rented")
            vehicle.availability_status = 'Rented'
            print()
    else:
        print("\nNo 'Booked' vehicles found")
    
    # Commit changes
    db.session.commit()
    print("\n✓ Database updated successfully!")
    
    # Show final status
    print("\n=== FINAL STATUS ===\n")
    bookings = Booking.query.all()
    for booking in bookings:
        vehicle = Vehicle.query.get(booking.vehicle_id)
        if booking.booking_status in ['Paid', 'Booked']:
            print(f"Active Booking #{booking.id}: {vehicle.name if vehicle else 'Unknown'}, Status: {booking.booking_status}")
    
    vehicles = Vehicle.query.all()
    for vehicle in vehicles:
        if vehicle.availability_status != 'Available':
            print(f"Unavailable Vehicle: {vehicle.name}, Status: {vehicle.availability_status}")
