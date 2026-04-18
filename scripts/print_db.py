import os, sys

# Make imports work when run from the scripts folder
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app

with app.app_context():
    from models import User, Vehicle, Booking

    users = User.query.all()
    vehicles = Vehicle.query.all()
    bookings = Booking.query.all()

    print('--- Users ---')
    if users:
        for u in users:
            print(f'id={u.id} username={u.username} role={u.role}')
    else:
        print('No users')

    print('\n--- Vehicles ---')
    if vehicles:
        for v in vehicles:
            print(f'id={v.id} category={v.category} brand={v.brand} price={v.price_per_day} status={v.availability_status} image={v.image}')
    else:
        print('No vehicles')

    print('\n--- Bookings ---')
    if bookings:
        for b in bookings:
            print(f'id={b.id} user_id={b.user_id} vehicle_id={b.vehicle_id} days={b.days} total={b.total_price}')
    else:
        print('No bookings')
