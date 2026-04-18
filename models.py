from extensions import db

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.String(200))
    age = db.Column(db.Integer, nullable=True)  # optional age of the user
    # new field to categorize user (low/middle/high) for filtering purposes
    user_class = db.Column(db.String(20), default='middle')
    role = db.Column(db.String(10), nullable=False, default='user')  # admin, user
    status = db.Column(db.String(20), default='Active', nullable=False)  # Active, Blocked
    registration_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)

    # Relationships
    bookings = db.relationship('Booking', backref='user', lazy=True)
    feedbacks = db.relationship('Feedback', backref='user', lazy=True)


class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # Vehicle name/model
    category = db.Column(db.String(20), nullable=False)  # Car, Bike
    brand = db.Column(db.String(50), nullable=False)
    vehicle_number = db.Column(db.String(20), unique=True, nullable=False)
    availability_status = db.Column(db.String(20), default='Available', nullable=False)  # Available, Rented, Maintenance
    price_per_day = db.Column(db.Float, nullable=False)
    price_per_hour = db.Column(db.Float, nullable=False)
    fuel_type = db.Column(db.String(20), nullable=False)  # Petrol, Diesel, Electric
    seating_capacity = db.Column(db.Integer, nullable=False)
    transmission = db.Column(db.String(20), nullable=False)  # Manual, Automatic
    image = db.Column(db.String(200))
    document = db.Column(db.String(200))  # RC or document file
    location = db.Column(db.String(100))  # city/area where vehicle is available
    subscription_only = db.Column(db.Boolean, default=False, nullable=False)  # only subscribers can book
    created_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)

    # Relationships
    bookings = db.relationship('Booking', backref='vehicle', lazy=True)
    maintenance_logs = db.relationship('MaintenanceLog', backref='vehicle', lazy=True)
    feedbacks = db.relationship('Feedback', backref='vehicle', lazy=True)


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    days = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    booking_status = db.Column(db.String(20), default='Pending', nullable=False)  # Pending (awaiting verification), Confirmed (proof verified), Paid, Booked, Completed, Cancelled
    booking_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)
    handover_time = db.Column(db.DateTime, nullable=True)  # when user handed vehicle back
    received_time = db.Column(db.DateTime, nullable=True)  # when admin confirmed reception
    penalty_amount = db.Column(db.Float, default=0.0, nullable=False)
    penalty_paid = db.Column(db.Boolean, default=False, nullable=False)
    damage_report = db.Column(db.String(500), nullable=True)  # notes about any damage found at handover/receive
    licence_proof = db.Column(db.String(200), nullable=True)  # License/Aadhar ID proof file
    proof_type = db.Column(db.String(20), default='License', nullable=False)  # License, Aadhar, Passport

    # Relationships
    feedbacks = db.relationship('Feedback', backref='booking', lazy=True)


class MaintenanceLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    service_date = db.Column(db.DateTime, nullable=False)
    next_service_due = db.Column(db.DateTime)
    issues_reported = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pending')  # Completed, Pending
    created_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=True)
    message = db.Column(db.String(500), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5 stars
    status = db.Column(db.String(20), default='Pending', nullable=False)  # Pending, Approved, Hidden
    submitted_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    admin_reply = db.Column(db.String(500))


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_type = db.Column(db.String(50), nullable=False)  # revenue, booking, user, vehicle, feedback
    report_data = db.Column(db.Text, nullable=False)  # JSON data
    generated_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    generated_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Admin user ID

    # Relationships
    generated_by_user = db.relationship('User', backref='reports')


class PromoCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    discount_percent = db.Column(db.Float, default=0.0, nullable=False)  # percentage discount e.g. 10.0
    max_discount_amount = db.Column(db.Float, nullable=True)  # optional cap on discount amount
    expires_at = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    def apply_discount(self, amount):
        """Return discounted amount after applying this promo to given amount."""
        if not self.active:
            return amount
        discount = (self.discount_percent / 100.0) * amount
        if self.max_discount_amount:
            discount = min(discount, self.max_discount_amount)
        return max(0.0, amount - discount)


class SubscriptionPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # Basic, Premium, Gold
    description = db.Column(db.String(500), nullable=False)
    price_per_month = db.Column(db.Float, nullable=False)
    discount_percent = db.Column(db.Float, default=0.0)  # Discount on rentals
    rental_limit_per_month = db.Column(db.Integer, nullable=True)  # None = unlimited
    priority_booking = db.Column(db.Boolean, default=False)  # Early access to new vehicles
    free_cancellation_hours = db.Column(db.Integer, default=24)  # Free cancellation within X hours
    created_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Relationships
    user_subscriptions = db.relationship('UserSubscription', backref='plan', lazy=True)


class UserSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plan.id'), nullable=False)
    start_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='Active', nullable=False)  # Active, Expired, Cancelled
    auto_renew = db.Column(db.Boolean, default=True)
    created_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)

    # Relationships
    user = db.relationship('User', backref='subscriptions')


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=True)  # NULL for subscription payments
    subscription_id = db.Column(db.Integer, db.ForeignKey('user_subscription.id'), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50), nullable=False)  # razorpay, card, upi, wallet
    transaction_id = db.Column(db.String(100), unique=True, nullable=False)  # Razorpay order ID
    payment_id = db.Column(db.String(100), nullable=True)  # Razorpay payment ID
    status = db.Column(db.String(20), default='Pending', nullable=False)  # Pending, Success, Failed, Refunded
    description = db.Column(db.String(200), nullable=True)
    created_date = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    completed_date = db.Column(db.DateTime, nullable=True)

    # Relationships
    user = db.relationship('User', backref='payments')
    booking = db.relationship('Booking', backref='payments')
    subscription = db.relationship('UserSubscription', backref='payments')
