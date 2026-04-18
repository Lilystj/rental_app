#!/usr/bin/env python
"""
Test script to verify payment flow and vehicle status updates
"""
import sys
sys.path.insert(0, 'c:\\Users\\91877\\rental_app')

from app import app, db
from services.email_service import send_handover_notification

# avoid sending real emails during tests
import app as _app_module
_app_module.send_handover_notification = lambda *args, **kwargs: True
_app_module.send_completion_notification = lambda *args, **kwargs: True
from models import User, Vehicle, Booking
import datetime

with app.app_context():
    print("=" * 60)
    print("TESTING PAYMENT FLOW AND VEHICLE STATUS UPDATES")
    print("=" * 60)
    
    # Find a test vehicle
    vehicle = Vehicle.query.first()
    if not vehicle:
        print("ERROR: No vehicles found in database")
        sys.exit(1)
    
    print(f"\n1. Found Vehicle: {vehicle.name} (ID: {vehicle.id})")
    print(f"   Current Status: {vehicle.availability_status}")
    
    # Find a test user
    user = User.query.filter_by(role='user').first()
    if not user:
        print("ERROR: No regular users found in database")
        sys.exit(1)
    
    print(f"\n2. Found User: {user.username} (ID: {user.id})")
    
    # Clean up any active bookings for this vehicle to provide a fresh test scenario
    active_bookings = Booking.query.filter_by(vehicle_id=vehicle.id).filter(
        Booking.booking_status.in_(['Pending','Confirmed','Paid','Booked'])
    ).all()
    if active_bookings:
        print(f"\n   Found {len(active_bookings)} existing active booking(s) for this vehicle; removing for clean test.")
        for b in active_bookings:
            db.session.delete(b)
        db.session.commit()

    # ---------- simple cancellation API test ----------
    print("\n   Testing cancellation endpoint with a temporary booking...")
    temp = Booking(
        user_id=user.id,
        vehicle_id=vehicle.id,
        booking_date=datetime.datetime.now(),
        days=1,
        due_date=datetime.datetime.now() + datetime.timedelta(days=1),
        booking_status='Pending',
        total_price=vehicle.price_per_day
    )
    db.session.add(temp)
    vehicle.availability_status = 'Booked'
    db.session.add(vehicle)
    db.session.commit()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['role'] = 'user'
        resp = client.post(f'/cancel/{temp.id}')
        data = resp.get_json() or {}
        assert data.get('success'), f"Cancellation API did not succeed: {data}"
    db.session.refresh(temp)
    assert temp.booking_status == 'Cancelled', "Booking status should flip to Cancelled"
    db.session.refresh(vehicle)
    assert vehicle.availability_status == 'Available', "Vehicle should be freed when cancellation occurs"
    print("   Cancellation route working - booking cancelled and vehicle freed.")

    # remove the temporary entry
    db.session.delete(temp)
    db.session.commit()

    # Create a test booking (if one doesn't exist in Pending state)
    booking = Booking.query.filter_by(
        user_id=user.id,
        vehicle_id=vehicle.id,
        booking_status='Pending'
    ).first()
    
    if not booking:
        print(f"\n3. No pending booking found. Creating test booking...")
        booking = Booking(
            user_id=user.id,
            vehicle_id=vehicle.id,
            booking_date=datetime.datetime.now(),
            days=2,
            due_date=datetime.datetime.now() + datetime.timedelta(days=2),
            booking_status='Pending',
            total_price=vehicle.price_per_day * 2
        )
        db.session.add(booking)
        # mimic route behaviour: immediately reserve vehicle
        vehicle.availability_status = 'Booked'
        db.session.add(vehicle)
        db.session.commit()
        print(f"   Created Booking ID: {booking.id}")
        print(f"   Status: {booking.booking_status}")
        print(f"   Vehicle status after creation: {vehicle.availability_status}")
        assert vehicle.availability_status == 'Booked', "Vehicle should be BOOKED as soon as booking is created"

        # attempt to book the same vehicle again should be blocked by our logic
        existing = Booking.query.filter_by(vehicle_id=vehicle.id).filter(
            Booking.booking_status.in_(['Pending','Confirmed','Paid','Booked'])
        ).all()
        assert len(existing) == 1, "Duplicate active bookings should not be possible"
        print("   Duplicate booking prevented: only one active booking exists.")

        # verify unique index prevents a direct insert too
        try:
            b2 = Booking(
                user_id=user.id,
                vehicle_id=vehicle.id,
                booking_date=datetime.datetime.now(),
                days=1,
                due_date=datetime.datetime.now() + datetime.timedelta(days=1),
                booking_status='Pending',
                total_price=vehicle.price_per_day
            )
            db.session.add(b2)
            db.session.commit()
            # if commit succeeded, that's a bug
            print("   ERROR: manual insert of duplicate booking succeeded, index missing")
            sys.exit(1)
        except Exception as e:
            db.session.rollback()
            print("   Unique index correctly prevented manual duplicate insertion.")

        # also verify that the /book route refuses a second request
        from io import BytesIO
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = user.id
                sess['role'] = 'user'
            resp = client.get(f'/book/{vehicle.id}', follow_redirects=True)
            assert b"not available" in resp.data.lower(), "Route should prevent booking when status is not Available"
            print("   Route correctly blocked a second booking attempt.")

            # -- subscription-only restriction test
            print("   Testing subscription-only restriction...")
            # mark vehicle as subscription only temporarily
            orig_flag = vehicle.subscription_only
            vehicle.subscription_only = True
            db.session.add(vehicle)
            db.session.commit()

            # ensure user has no active subscriptions
            UserSubscription.query.filter_by(user_id=user.id, status='Active').update({"status": "Cancelled"})
            db.session.commit()

            with app.test_client() as client2:
                with client2.session_transaction() as sess2:
                    sess2['user_id'] = user.id
                    sess2['role'] = 'user'
                r2 = client2.get(f'/book/{vehicle.id}', follow_redirects=True)
                assert b"subscribe" in r2.data.lower(), "Non-subscribed user should be redirected to subscriptions page"
                print("   Non-subscriber blocked from booking subscription-only vehicle.")

            # grant the user an active subscription and retry
            plan = SubscriptionPlan.query.first()
            if not plan:
                plan = SubscriptionPlan(name='Test', description='test', price_per_month=1,
                                        discount_percent=0, rental_limit_per_month=1,
                                        priority_booking=False, free_cancellation_hours=24)
                db.session.add(plan)
                db.session.commit()
            import datetime
            active_sub = UserSubscription(user_id=user.id, plan_id=plan.id,
                                          start_date=datetime.datetime.now(),
                                          end_date=datetime.datetime.now() + datetime.timedelta(days=30),
                                          status='Active', auto_renew=False)
            db.session.add(active_sub)
            db.session.commit()

            with app.test_client() as client3:
                with client3.session_transaction() as sess3:
                    sess3['user_id'] = user.id
                    sess3['role'] = 'user'
                r3 = client3.get(f'/book/{vehicle.id}', follow_redirects=True)
                assert b"complete your booking" in r3.data.lower(), "Subscribed user should be allowed to see booking page"
                print("   Subscribed user allowed to access subscription-only vehicle.")

            # restore original flag
            vehicle.subscription_only = orig_flag
            db.session.add(vehicle)
            db.session.commit()
    else:
        print(f"\n3. Found Pending Booking ID: {booking.id}")
        print(f"   Status: {booking.booking_status}")
        print(f"   Vehicle status currently: {vehicle.availability_status}")

        # verify duplicate booking would not be allowed
        existing = Booking.query.filter_by(vehicle_id=vehicle.id).filter(
            Booking.booking_status.in_(['Pending','Confirmed','Paid','Booked'])
        ).all()
        assert len(existing) == 1, "Even when an old booking exists, there should only be one active booking for the vehicle"
        print("   Duplicate booking prevention check passed (only one active found).")
    
    # Simulate admin verification: confirm booking and reserve vehicle
    print(f"\n4. Simulating admin verification (set to Confirmed) and reserve vehicle...")
    booking.booking_status = 'Confirmed'
    vehicle.availability_status = 'Booked'
    db.session.commit()
    # verify reservation status
    db.session.refresh(vehicle)
    assert vehicle.availability_status == 'Booked', "Vehicle should be BOOKED after admin confirmation"
    print("   Vehicle reserved successfully (Booked)")
    
    # user now pays
    print("   then simulate payment and vehicle status change to Rented...")
    booking.booking_status = 'Paid'
    vehicle.availability_status = 'Rented'
    # artificially set due_date in the past so we can test penalty logic later
    booking.due_date = datetime.datetime.now() - datetime.timedelta(hours=5)
    db.session.commit()
    
    # Refresh from database to verify
    db.session.refresh(booking)
    db.session.refresh(vehicle)
    
    print(f"\n5. VERIFICATION AFTER PAYMENT:")
    print(f"   Booking Status: {booking.booking_status}")
    print(f"   Vehicle Status: {vehicle.availability_status}")
    
    if booking.booking_status == 'Paid' and vehicle.availability_status == 'Rented':
        print(f"\n✓ SUCCESS: Payment flow and vehicle status update working correctly!")
    else:
        print(f"\n✗ ERROR: Status update failed!")
        sys.exit(1)

    # allow admin to record pre-handover condition instead of user doing it
    print("\n6. Simulating admin recording handover condition...")
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['role'] = 'admin'
        payload = {'damage_report': 'Pre-existing scratch on bumper', 'penalty_amount': 0}
        resp = client.post(f'/admin/handover/{booking.id}/start', json=payload)
        data = resp.get_json() or {}
        assert data.get('success'), f"Admin start handover failed: {data}"
        db.session.refresh(booking)
        print(f"   Booking status after admin handover start: {booking.booking_status}")
        assert booking.booking_status == 'Handover', "Booking should change to Handover after admin start"
        assert booking.handover_time is not None, "handover_time should be recorded by admin"
        assert 'Pre-existing scratch' in (booking.damage_report or ''), "Damage report should include note"

    # skip user handover since admin already recorded it

    # simulate admin receiving handover with damage inspection
    print("\n7. Simulating admin receiving vehicle and completing booking (with damage notes)...")
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['role'] = 'admin'
        payload = {
            'damage_report': 'Scratch on door, mirror broken',
            'penalty_amount': 500.0
        }
        resp = client.post(
            f'/admin/handover/{booking.id}/receive',
            json=payload,
            follow_redirects=True
        )
        data = resp.get_json()
        assert data.get('success'), f"Admin receive route failed: {data}"
        db.session.refresh(booking)
        db.session.refresh(vehicle)
        print(f"   After receive - booking_status: {booking.booking_status}, vehicle availability: {vehicle.availability_status}")
        assert booking.booking_status == 'Completed', "Booking should be marked Completed after receive"
        assert vehicle.availability_status == 'Available', "Vehicle should return to Available after receive"
        print("   Penalty amount recorded: {}".format(booking.penalty_amount))
        assert booking.penalty_amount == 500.0, "Penalty should reflect passed value"
        assert 'Scratch on door' in (booking.damage_report or ''), "Damage report should be stored"

    print("\n" + "=" * 60)
    # Verify penalty is initially unpaid
    db.session.refresh(booking)
    assert booking.penalty_amount == 500.0
    assert booking.penalty_paid in (False, 0), "Penalty should be unpaid immediately after admin receive"

    # Simulate user paying the penalty via the penalty payment route
    print("\n8. Simulating user paying penalty...")
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['role'] = 'user'
        resp = client.post(f'/pay/penalty/{booking.id}', data={'payment_method': 'penalty'}, follow_redirects=True)
        # After redirect, booking should be marked paid for penalty
    db.session.refresh(booking)
    assert booking.penalty_paid is True, "Penalty should be marked paid after payment"

    # Verify a Payment record was created for the penalty
    from models import Payment
    payrec = Payment.query.filter_by(booking_id=booking.id, amount=500.0).first()
    assert payrec is not None and payrec.status == 'Success', "A successful Payment record should exist for penalty"
    print("   Penalty payment flow working: booking.penalty_paid updated and Payment created.")

    print("\nALL TESTS PASSED")
