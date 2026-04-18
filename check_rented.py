from app import app, db
from models import Vehicle, Booking

with app.app_context():
    rented = Vehicle.query.filter_by(availability_status='Rented').all()
    print('Rented vehicles:', [(v.id, v.name) for v in rented])
    for v in rented:
        bks = Booking.query.filter_by(vehicle_id=v.id).order_by(Booking.id.desc()).limit(5).all()
        for b in bks:
            print(' vehicle', v.id, 'booking', b.id, b.booking_status, b.due_date, b.received_time)
