from flask import Flask, render_template, redirect, request, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import datetime
import time
from config import Config
from dotenv import load_dotenv
from sqlalchemy import inspect, text

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config.from_object(Config)

# Initialize DB from centralized extensions to avoid circular imports
from extensions import db
from sqlalchemy import event, text
db.init_app(app)

# automatically add user_class column if the database is out of date
from sqlalchemy import inspect
with app.app_context():
    insp = inspect(db.engine)
    if insp.has_table('user'):
        cols = [c['name'] for c in insp.get_columns('user')]
        if 'user_class' not in cols:
            # sqlite supports simple ALTER; defaulting to 'middle'
            db.session.execute(text(
                "ALTER TABLE user ADD COLUMN user_class VARCHAR(20) DEFAULT 'middle'"
            ))
            db.session.commit()
        # also add subscription_only flag to vehicle table if missing
        if insp.has_table('vehicle'):
            vcols = [c['name'] for c in insp.get_columns('vehicle')]
            if 'subscription_only' not in vcols:
                db.session.execute(text(
                    "ALTER TABLE vehicle ADD COLUMN subscription_only BOOLEAN DEFAULT 0 NOT NULL"
                ))
                db.session.commit()

# Initialize Flask-Mail for email notifications
from services.email_service import mail
mail.init_app(app)

from sqlalchemy.exc import IntegrityError

# Development helper: optionally bypass authentication checks for quick local testing.
# Enable by setting environment variable `DEV_BYPASS_AUTH=1` (only use in local/dev).
@app.before_request
def _dev_bypass_auth():
    try:
        if os.getenv('DEV_BYPASS_AUTH', '') != '1':
            return

        # don't interfere with static asset requests
        if request.path.startswith('/static'):
            return

        # set session role and user_id to an admin account for convenience
        if 'role' not in session:
            session['role'] = 'admin'

        if 'user_id' not in session:
            # import here to avoid circular imports at module import time
            from werkzeug.security import generate_password_hash
            from models import User
            admin = User.query.filter_by(role='admin').first()
            if admin:
                session['user_id'] = admin.id
            else:
                # create a default admin if missing (provide email to satisfy schema)
                admin = User(username='admin', password=generate_password_hash('admin123'), email='admin@wheelshare.local', role='admin')
                db.session.add(admin)
                db.session.commit()
                session['user_id'] = admin.id
    except Exception:
        # never raise in request path — if something goes wrong, let normal auth handle it
        pass

# Import models after db is available
with app.app_context():
    from models import User, Vehicle, Booking, MaintenanceLog, Feedback, Report, PromoCode, SubscriptionPlan, UserSubscription, Payment
    # create database tables if they don't exist
    db.create_all()
    # if the 'age' column was added later, ensure existing table has it
    try:
        # sqlite allows adding column with ALTER TABLE; use text() to avoid SA coercion errors
        from sqlalchemy import text
        db.session.execute(text('ALTER TABLE user ADD COLUMN age INTEGER'))
        db.session.commit()
    except Exception:
        # ignore if column already exists or operation fails
        db.session.rollback()
    # ensure at least some subscription plans exist for users
    if SubscriptionPlan.query.count() == 0:
        default_plans = [
            SubscriptionPlan(
                name='Basic',
                description='Perfect for occasional users',
                price_per_month=499,
                discount_percent=10,
                rental_limit_per_month=5,
                priority_booking=False,
                free_cancellation_hours=6,
                is_active=True
            ),
            SubscriptionPlan(
                name='Premium',
                description='Best value for regular users',
                price_per_month=999,
                discount_percent=20,
                rental_limit_per_month=15,
                priority_booking=True,
                free_cancellation_hours=24,
                is_active=True
            ),
            SubscriptionPlan(
                name='Elite',
                description='For power users',
                price_per_month=1999,
                discount_percent=30,
                rental_limit_per_month=None,
                priority_booking=True,
                free_cancellation_hours=48,
                is_active=True
            )
        ]
        db.session.add_all(default_plans)
        db.session.commit()

    # create a unique index preventing more than one active reservation per vehicle
    # (SQLite supports partial indexes; other DBs will ignore the WHERE clause)
    try:
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_booking "
            "ON booking(vehicle_id) WHERE booking_status IN ('Pending','Confirmed','Paid','Booked')"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # ensure new handover/received columns exist (may be nil in older DBs)
    for stmt in [
        "ALTER TABLE booking ADD COLUMN handover_time DATETIME",
        "ALTER TABLE booking ADD COLUMN received_time DATETIME",
        "ALTER TABLE booking ADD COLUMN penalty_amount FLOAT DEFAULT 0.0",
        "ALTER TABLE booking ADD COLUMN damage_report VARCHAR(500)",
        "ALTER TABLE booking ADD COLUMN penalty_paid INTEGER DEFAULT 0"
    ]:
        try:
            db.session.execute(text(stmt))
            db.session.commit()
        except Exception:
            db.session.rollback()  # ignore if column already exists

    # repair mismatched vehicle states when app starts
    try:
        mismatched = []
        paid_bookings = Booking.query.filter_by(booking_status='Paid').all()
        for b in paid_bookings:
            v = db.session.get(Vehicle, b.vehicle_id)
            if v and v.availability_status != 'Rented':
                v.availability_status = 'Rented'
                mismatched.append(v.id)
        if mismatched:
            db.session.commit()
    except Exception:
        db.session.rollback()

# Initialize Razorpay payment service
from services.payment_service import RazorpayPaymentService
razorpay_service = RazorpayPaymentService(
    key_id=app.config.get('RAZORPAY_KEY_ID'),
    key_secret=app.config.get('RAZORPAY_KEY_SECRET')
)

# keep vehicle status in sync whenever a booking's status is modified
@event.listens_for(Booking, 'after_update')
def _sync_vehicle_with_booking(mapper, connection, target):
    """Ensure vehicle moves to Rented when booking is marked Paid (existing)

    This handles updates where the status changes on an existing row.
    """
    if target.booking_status == 'Paid':
        connection.execute(
            text("UPDATE vehicle SET availability_status='Rented' WHERE id=:vid"),
            {'vid': target.vehicle_id}
        )

# also listen for inserts so we catch new bookings created with Paid status
@event.listens_for(Booking, 'after_insert')
def _sync_vehicle_on_insert(mapper, connection, target):
    if target.booking_status == 'Paid':
        connection.execute(
            text("UPDATE vehicle SET availability_status='Rented' WHERE id=:vid"),
            {'vid': target.vehicle_id}
        )


def check_expired_bookings():
    """Check for bookings that have passed their due date and mark them as completed"""
    try:
        now = datetime.datetime.now()
        # Get all active bookings which might expire.
        # We deliberately exclude 'Completed' because once a booking is completed
        # the vehicle status should already have been updated; re-running the
        # logic would toggle it back to Available if somebody fixed the vehicle
        # status manually (see bug report). Only Paid/Booked bookings are relevant.
        active_bookings = Booking.query.filter(
            Booking.booking_status.in_(['Paid', 'Booked'])
        ).all()
        for booking in active_bookings:
            # Calculate due_date if not set
            if booking.due_date is None and booking.booking_date and booking.days:
                booking.due_date = booking.booking_date + datetime.timedelta(days=booking.days)
            # Check if expired
            if booking.due_date and booking.due_date < now:
                if booking.booking_status != 'Completed':
                    booking.booking_status = 'Completed'
                # Make vehicle available again
                vehicle = db.session.get(Vehicle, booking.vehicle_id)
                if vehicle and vehicle.availability_status in ['Rented', 'Booked']:
                    vehicle.availability_status = 'Available'
        db.session.commit()
    except Exception as e:
        # If there's a database error (like missing column), skip the check
        # This allows the app to run while we fix the database
        print(f"Warning: Could not check expired bookings: {e}")
        db.session.rollback()

# Role-based access control decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to access this page.", "warning")
            return redirect(url_for('login_select'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to access this page.", "warning")
            return redirect(url_for('login_select'))
        if session.get("role") != "admin":
            flash("Access denied. Admin privileges required.", "danger")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def user_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to access this page.", "warning")
            return redirect(url_for('login_select'))
        if session.get("role") != "user":
            flash("Access denied. User privileges required.", "danger")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# Home Page Redirect
@app.route('/')
def home():
    # Render a landing page with user/admin login panels (dark theme)
    if "user_id" in session:
        if session["role"] == "admin":
            return redirect("/admin")
        else:
            return redirect("/vehicles")
    return render_template("home.html")


# Register
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == "POST":
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        age_input = request.form.get('age', '').strip()

        # Validation
        if not username or not password or not email:
            flash("Username, password, and email are required!", "danger")
            return render_template("register.html")

        # validate age if provided
        age = None
        if age_input:
            try:
                age = int(age_input)
                if age <= 0:
                    raise ValueError()
            except ValueError:
                flash("Please enter a valid age.", "danger")
                return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters long!", "danger")
            return render_template("register.html")

        # Check for existing username
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("Username already exists!", "danger")
            return render_template("register.html")

        # Check for existing email
        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            flash("Email already registered!", "danger")
            return render_template("register.html")

        hashed_password = generate_password_hash(password)
        user = User(username=username, password=hashed_password, email=email,
                   phone=phone, address=address, age=age, role="user")
        db.session.add(user)
        db.session.commit()
        flash("Registration successful!", "success")
        return redirect("/user/login")

    return render_template("register.html")


# Login Selection Page
@app.route('/login-select')
def login_select():
    """Show login type selection page"""
    if "user_id" in session:
        if session["role"] == "admin":
            return redirect("/admin")
        else:
            return redirect("/vehicles")
    return render_template("login_select.html")


# User Login
@app.route('/user/login', methods=['GET','POST'])
def user_login():
    if request.method == "POST":
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash("Username and password are required!", "danger")
            return render_template("user_login.html")

        user = User.query.filter_by(username=username, role='user').first()
        if user and check_password_hash(user.password, password):
            if user.status == 'Blocked':
                flash("Your account has been blocked. Please contact administrator.", "danger")
                return render_template("user_login.html")

            session["user_id"] = user.id
            session["role"] = user.role
            session["username"] = user.username
            flash(f"Welcome back, {user.username}!", "success")
            return redirect('/dashboard')
        else:
            flash("Invalid credentials", "danger")

    return render_template("user_login.html")


# Admin Login
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == "POST":
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash("Username and password are required!", "danger")
            return render_template("admin_login.html")

        user = User.query.filter_by(username=username, role='admin').first()
        if user and check_password_hash(user.password, password):
            if user.status == 'Blocked':
                flash("Your account has been blocked.", "danger")
                return render_template("admin_login.html")

            session["user_id"] = user.id
            session["role"] = user.role
            session["username"] = user.username
            flash(f"Welcome, Admin {user.username}!", "success")
            return redirect('/admin')
        else:
            flash("Invalid admin credentials", "danger")

    return render_template("admin_login.html")


# Legacy login route - redirect to login selection
@app.route('/login', methods=['GET','POST'])
def login():
    """Legacy login route - redirect to login selection page"""
    if request.method == "POST":
        # If form is posted, try to determine if it's user or admin login
        # For backward compatibility, default to user login
        return redirect('/user/login')
    return redirect('/login-select')


# Logout
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out successfully!", "info")
    return redirect("/login-select")


# Admin Dashboard
@app.route('/admin')
@admin_required
def admin_dashboard():
    vehicles = Vehicle.query.all()
    return render_template("admin_dashboard.html", vehicles=vehicles)


@app.route('/admin/vehicles')
@admin_required
def admin_vehicles():
    """Vehicle Management page"""
    return render_template("admin_vehicles.html")


# --- Subscription Plan Management ---
@app.route('/admin/subscriptions', methods=['GET','POST'])
@admin_required
def admin_subscriptions():
    """Admin interface to create/edit/delete subscription plans"""
    if request.method == 'POST':
        # gather form data
        plan_id = request.form.get('id')
        name = request.form.get('name')
        description = request.form.get('description')
        price = request.form.get('price')
        discount = request.form.get('discount') or 0
        limit = request.form.get('limit') or None
        priority = bool(request.form.get('priority'))
        free_cancel = request.form.get('free_cancel')
        is_active = bool(request.form.get('is_active'))

        if plan_id:
            plan = db.session.get(SubscriptionPlan, int(plan_id))
            if not plan:
                flash("Plan not found", "danger")
                return redirect('/admin/subscriptions')
        else:
            plan = SubscriptionPlan()

        plan.name = name
        plan.description = description
        plan.price_per_month = float(price)
        plan.discount_percent = float(discount)
        plan.rental_limit_per_month = int(limit) if limit not in (None, '', '0') else None
        plan.priority_booking = priority
        plan.free_cancellation_hours = int(free_cancel)
        plan.is_active = is_active

        db.session.add(plan)
        db.session.commit()
        flash('Subscription plan saved successfully.', 'success')
        return redirect('/admin/subscriptions')

    plans = SubscriptionPlan.query.all()
    return render_template('admin_subscriptions.html', plans=plans)


@app.route('/admin/subscriptions/edit/<int:plan_id>')
@admin_required
def edit_admin_subscription(plan_id):
    plan = db.session.get(SubscriptionPlan, plan_id)
    plans = SubscriptionPlan.query.all()
    return render_template('admin_subscriptions.html', plans=plans, edit_plan=plan)


@app.route('/admin/subscriptions/delete/<int:plan_id>')
@admin_required
def delete_admin_subscription(plan_id):
    plan = db.session.get(SubscriptionPlan, plan_id)
    if plan:
        db.session.delete(plan)
        db.session.commit()
        flash('Subscription plan deleted.', 'success')
    return redirect('/admin/subscriptions')


# -- user subscription management — allow admin to assign / cancel plans for users --
@app.route('/admin/subscriptions/users', methods=['GET','POST'])
@admin_required
def admin_user_subscriptions():
    """List users and let admin assign or cancel subscriptions."""
    if request.method == 'POST':
        action = request.form.get('action')
        user_id = request.form.get('user_id')
        if not user_id:
            flash('User ID missing', 'danger')
            return redirect('/admin/subscriptions/users')
        user_id = int(user_id)

        if action == 'assign':
            plan_id = request.form.get('plan_id')
            if not plan_id:
                flash('Please select a plan', 'warning')
            else:
                plan_id = int(plan_id)
                # cancel existing active if any
                old = UserSubscription.query.filter_by(user_id=user_id, status='Active').first()
                if old:
                    old.status = 'Cancelled'
                start = datetime.datetime.now()
                end = start + datetime.timedelta(days=30)
                new = UserSubscription(
                    user_id=user_id,
                    plan_id=plan_id,
                    start_date=start,
                    end_date=end,
                    status='Active',
                    auto_renew=True
                )
                db.session.add(new)
                db.session.commit()
                flash('Subscription assigned/updated for user.', 'success')
        elif action == 'cancel':
            sub = UserSubscription.query.filter_by(user_id=user_id, status='Active').first()
            if sub:
                sub.status = 'Cancelled'
                db.session.commit()
                flash('Subscription cancelled for user.', 'success')
            else:
                flash('No active subscription to cancel.', 'info')
        return redirect('/admin/subscriptions/users')

    users = User.query.all()
    plans = SubscriptionPlan.query.filter_by(is_active=True).all()
    return render_template('admin_user_subscriptions.html', users=users, plans=plans)


@app.route('/admin/users')
@admin_required
def admin_users():
    """User Management page"""
    return render_template("admin_users.html")


# --- Feedback Management API Endpoints ---
@app.route('/admin/feedbacks')
@admin_required
def admin_feedbacks_page():
    """Feedback management page"""
    return render_template("admin_feedbacks.html")


@app.route('/admin/feedbacks-list')
@admin_required
@admin_required
def admin_feedbacks_list():
    """API endpoint for feedback list with filters"""
    status = request.args.get('status', '')
    rating = request.args.get('rating', '')
    
    query = Feedback.query.order_by(Feedback.submitted_date.desc())
    
    if status:
        query = query.filter_by(status=status)
    
    if rating:
        query = query.filter_by(rating=int(rating))
    
    feedbacks = query.all()
    feedbacks_data = []
    
    for f in feedbacks:
        user = db.session.get(User, f.user_id)
        vehicle = db.session.get(Vehicle, f.vehicle_id) if f.vehicle_id else None
        feedbacks_data.append({
            'id': f.id,
            'username': user.username if user else 'Unknown',
            'message': f.message,
            'rating': f.rating,
            'submitted_date': f.submitted_date.strftime('%Y-%m-%d %H:%M') if f.submitted_date else '',
            'status': f.status,
            'vehicle_name': vehicle.name if vehicle else 'N/A',
            'admin_reply': f.admin_reply or ''
        })
    
    return jsonify({'feedbacks': feedbacks_data})


@app.route('/admin/feedback/<int:feedback_id>/approve', methods=['POST'])
@admin_required
@admin_required
def approve_feedback(feedback_id):
    """Approve feedback"""
    feedback = db.session.get(Feedback, feedback_id)
    if not feedback:
        return jsonify({"error": "Feedback not found"}), 404

    feedback.status = 'Approved'
    db.session.commit()

    return jsonify({"success": True})


@app.route('/admin/feedback/<int:feedback_id>/hide', methods=['POST'])
@admin_required
def hide_feedback(feedback_id):
    """Hide feedback"""
    feedback = db.session.get(Feedback, feedback_id)
    if not feedback:
        return jsonify({"error": "Feedback not found"}), 404

    feedback.status = 'Hidden'
    db.session.commit()

    return jsonify({"success": True})


@app.route('/admin/feedback/<int:feedback_id>/delete', methods=['POST'])
@admin_required
def delete_feedback(feedback_id):
    """Delete feedback"""
    feedback = db.session.get(Feedback, feedback_id)
    if not feedback:
        return jsonify({"error": "Feedback not found"}), 404

    db.session.delete(feedback)
    db.session.commit()

    return jsonify({"success": True})


@app.route('/admin/feedback-stats')
@admin_required
@admin_required
def admin_feedback_stats():
    """API endpoint for feedback statistics"""
    total_feedbacks = Feedback.query.count()
    approved = Feedback.query.filter_by(status='Approved').count()
    pending = Feedback.query.filter_by(status='Pending').count()
    hidden = Feedback.query.filter_by(status='Hidden').count()
    
    # Calculate average rating
    avg_rating = 0
    all_feedbacks = Feedback.query.filter_by(status='Approved').all()
    if all_feedbacks:
        avg_rating = sum([f.rating for f in all_feedbacks]) / len(all_feedbacks)
    
    return jsonify({
        'total_feedbacks': total_feedbacks,
        'approved': approved,
        'pending': pending,
        'hidden': hidden,
        'average_rating': round(avg_rating, 1)
    })


# --- Reports Module API Endpoints ---
@app.route('/admin/reports')
@admin_required
def admin_reports_page():
    """Reports page"""
    return render_template("admin_reports.html")


@app.route('/admin/report/revenue')
@admin_required
@admin_required
def report_revenue():
    """Revenue report data"""
    # Monthly revenue data (last 12 months)
    bookings = Booking.query.all()
    monthly_revenue = {}
    
    for booking in bookings:
        month = booking.booking_date.strftime('%Y-%m') if booking.booking_date else 'N/A'
        if month not in monthly_revenue:
            monthly_revenue[month] = 0
        monthly_revenue[month] += booking.total_price or 0
    
    # Vehicle-wise earnings
    vehicle_earnings = {}
    for booking in bookings:
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        if vehicle:
            vname = vehicle.name or vehicle.brand
            if vname not in vehicle_earnings:
                vehicle_earnings[vname] = 0
            vehicle_earnings[vname] += booking.total_price or 0
    
    total_revenue = sum([b.total_price or 0 for b in bookings])
    
    return jsonify({
        'total_revenue': total_revenue,
        'monthly_revenue': monthly_revenue,
        'vehicle_earnings': vehicle_earnings
    })


@app.route('/admin/report/booking')
@admin_required
@admin_required
def report_booking():
    """Booking report data"""
    total_bookings = Booking.query.count()
    completed = Booking.query.filter_by(booking_status='Completed').count()
    cancelled = Booking.query.filter_by(booking_status='Cancelled').count()
    confirmed = Booking.query.filter_by(booking_status='Confirmed').count()
    
    # Most rented vehicle
    vehicles_rented = {}
    bookings = Booking.query.all()
    for booking in bookings:
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        if vehicle:
            vname = vehicle.name or vehicle.brand
            if vname not in vehicles_rented:
                vehicles_rented[vname] = 0
            vehicles_rented[vname] += 1
    
    most_rented = max(vehicles_rented, key=vehicles_rented.get) if vehicles_rented else 'N/A'
    
    return jsonify({
        'total_bookings': total_bookings,
        'completed': completed,
        'cancelled': cancelled,
        'confirmed': confirmed,
        'most_rented_vehicle': most_rented
    })


@app.route('/admin/report/user')
def report_user():
    """User report data"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    total_users = User.query.filter_by(role='user').count()
    active_users = User.query.filter_by(role='user', status='Active').count()
    blocked_users = User.query.filter_by(role='user', status='Blocked').count()
    
    return jsonify({
        'total_users': total_users,
        'active_users': active_users,
        'blocked_users': blocked_users
    })


@app.route('/admin/report/vehicle')
def report_vehicle():
    """Vehicle utilization report"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    total_vehicles = Vehicle.query.count()
    available = Vehicle.query.filter_by(availability_status='Available').count()
    booked = Vehicle.query.filter_by(availability_status='Booked').count()
    maintenance = Vehicle.query.filter_by(availability_status='Maintenance').count()
    
    # Vehicle utilization (times rented)
    vehicle_usage = {}
    bookings = Booking.query.all()
    for booking in bookings:
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        if vehicle:
            vname = vehicle.name or vehicle.brand
            if vname not in vehicle_usage:
                vehicle_usage[vname] = 0
            vehicle_usage[vname] += 1
    
    most_used = max(vehicle_usage, key=vehicle_usage.get) if vehicle_usage else 'N/A'
    least_used = min(vehicle_usage, key=vehicle_usage.get) if vehicle_usage else 'N/A'
    
    return jsonify({
        'total_vehicles': total_vehicles,
        'available': available,
        'booked': booked,
        'maintenance': maintenance,
        'most_used_vehicle': most_used,
        'least_used_vehicle': least_used
    })


@app.route('/admin/report/user-specific')
@admin_required
def report_user_specific():
    """User-specific booking and revenue report"""
    user_id = request.args.get('user_id', type=int)
    
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    # Get all bookings for this user
    bookings = Booking.query.filter_by(user_id=user_id).all()
    
    # Calculate statistics
    total_bookings = len(bookings)
    completed_bookings = sum(1 for b in bookings if b.booking_status == 'Completed')
    cancelled_bookings = sum(1 for b in bookings if b.booking_status == 'Cancelled')
    confirmed_bookings = sum(1 for b in bookings if b.booking_status == 'Confirmed')
    total_spent = sum(b.total_price or 0 for b in bookings)
    
    # Monthly booking data
    monthly_bookings = {}
    monthly_revenue = {}
    for booking in bookings:
        month = booking.booking_date.strftime('%Y-%m') if booking.booking_date else 'N/A'
        
        if month not in monthly_bookings:
            monthly_bookings[month] = 0
            monthly_revenue[month] = 0
        
        monthly_bookings[month] += 1
        monthly_revenue[month] += booking.total_price or 0
    
    # Vehicle preferences
    vehicle_bookings = {}
    for booking in bookings:
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        if vehicle:
            vname = vehicle.name or vehicle.brand
            if vname not in vehicle_bookings:
                vehicle_bookings[vname] = 0
            vehicle_bookings[vname] += 1
    
    return jsonify({
        'user_name': user.username,
        'user_email': user.email,
        'total_bookings': total_bookings,
        'completed_bookings': completed_bookings,
        'cancelled_bookings': cancelled_bookings,
        'confirmed_bookings': confirmed_bookings,
        'total_spent': total_spent,
        'monthly_bookings': monthly_bookings,
        'monthly_revenue': monthly_revenue,
        'vehicle_bookings': vehicle_bookings
    })


@app.route('/admin/report/users-list')
@admin_required
def report_users_list():
    """Get list of all users for user-specific reports"""
    users = User.query.filter_by(role='user').all()
    return jsonify({
        'users': [
            {
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'bookings_count': Booking.query.filter_by(user_id=u.id).count()
            }
            for u in users
        ]
    })


@app.route('/admin/stats')
def admin_stats():
    """API endpoint for admin dashboard stats"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    user_count = User.query.count()
    booking_count = Booking.query.count()
    # Calculate total revenue as sum of all bookings' total_price
    total_revenue = sum([b.total_price or 0 for b in Booking.query.all()])
    
    return jsonify({
        'total_users': user_count,
        'total_bookings': booking_count,
        'total_revenue': total_revenue
    })


@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    """Render page showing all bookings to the admin."""
    # the page itself will fetch data from the real‑time API `/api/admin/bookings-list`
    return render_template("admin_bookings.html")


@app.route('/admin/booking/<int:booking_id>/approve', methods=['POST'])
def approve_booking(booking_id):
    """Approve a booking request"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    booking = Booking.query.get_or_404(booking_id)
    
    # mark booking as confirmed (proof verified)
    booking.booking_status = 'Confirmed'
    
    # reserve the vehicle (booked) so others cannot take it, but do not mark rented
    vehicle = db.session.query(Vehicle).filter_by(id=booking.vehicle_id).first()
    if vehicle:
        vehicle.availability_status = 'Booked'
        db.session.add(vehicle)
    
    db.session.add(booking)
    db.session.flush()
    db.session.commit()
    
    return jsonify({"success": True, "message": "Booking approved successfully"})

@app.route('/cancel/<int:booking_id>', methods=['POST'])
@login_required

def cancel_booking(booking_id):
    """Allow a user to cancel their own booking.

    Only bookings that have not already been completed or cancelled can be
    cancelled. When a booking is cancelled we also release the vehicle if it
    was reserved.
    """
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 403

    # disallow cancelling once it's finished, already cancelled or vehicle already handed over
    if booking.booking_status in ['Completed', 'Cancelled', 'Handover']:
        return jsonify({'error': 'Cannot cancel booking in current state'}), 400

    # simple policy: mark cancelled and free the vehicle if necessary
    booking.booking_status = 'Cancelled'

    vehicle = db.session.get(Vehicle, booking.vehicle_id)
    if vehicle and vehicle.availability_status in ['Booked', 'Rented']:
        # if vehicle was held exclusively for this booking, make it available
        vehicle.availability_status = 'Available'
        db.session.add(vehicle)

    db.session.add(booking)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Booking cancelled'})


@app.route('/handover/<int:booking_id>', methods=['POST'])
@login_required
def handover_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != session.get('user_id'):
        return jsonify({'error':'Unauthorized'}), 403
    if booking.booking_status not in ['Paid','Booked']:
        return jsonify({'error':'Cannot handover in current state'}), 400
    booking.handover_time = datetime.datetime.now()
    booking.booking_status = 'Handover'
    db.session.add(booking)
    db.session.commit()
    return jsonify({'success':True})

@app.route('/admin/handover/<int:booking_id>/start', methods=['POST'])
@login_required
def admin_start_handover(booking_id):
    if session.get('role') != 'admin':
        return jsonify({"error": "Unauthorized"}), 401
    booking = Booking.query.get_or_404(booking_id)
    if booking.booking_status not in ['Paid', 'Booked']:
        return jsonify({'error': 'Cannot hand over in current state'}), 400
    data = request.get_json(silent=True) or {}
    report = data.get('damage_report')
    if report:
        # prefix to indicate this was the initial condition
        booking.damage_report = 'handover: ' + report[:450]
    booking.handover_time = datetime.datetime.now()
    booking.booking_status = 'Handover'
    db.session.add(booking)
    db.session.commit()

    # notify the user that the vehicle is ready to be handed over
    try:
        user = db.session.get(User, booking.user_id)
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        if user and vehicle:
            send_handover_notification(
                user_email=user.email,
                user_name=user.username,
                booking_id=booking.id,
                vehicle_name=vehicle.name
            )
    except Exception:
        pass  # don't break the API if email fails

    return jsonify({'success': True})


@app.route('/admin/handover/<int:booking_id>/receive', methods=['POST'])
def admin_receive_handover(booking_id):
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    booking = Booking.query.get_or_404(booking_id)
    if booking.booking_status != 'Handover':
        return jsonify({'error':'Not awaiting handover'}), 400

    data = request.get_json(silent=True) or {}
    # record optional damage report supplied by admin
    report = data.get('damage_report')
    if report is not None:
        booking.damage_report = report[:500]

    # support explicit "no_penalty" flag from admin UI to skip auto-calculation
    no_penalty = bool(data.get('no_penalty', False))

    booking.received_time = datetime.datetime.now()

    if no_penalty:
        # admin explicitly chose no penalty
        booking.penalty_amount = 0.0
        booking.penalty_paid = True
    else:
        # admin may have supplied an explicit penalty_amount; respect it if provided
        penalty_raw = data.get('penalty_amount', None)
        if penalty_raw is not None and str(penalty_raw).strip() != '':
            try:
                penal = float(penalty_raw)
            except (TypeError, ValueError):
                penal = booking.penalty_amount or 0.0
            booking.penalty_amount = max(0.0, penal)
            if booking.penalty_amount > 0:
                booking.penalty_paid = False
        else:
            # no explicit penalty provided: compute default based on overdue hours
            booking.penalty_amount = booking.penalty_amount or 0.0
            if booking.penalty_amount == 0:
                rate = float(app.config.get('PENALTY_RATE_PER_HOUR', 100))
                delta = booking.received_time - booking.due_date if booking.due_date else datetime.timedelta(0)
                hours = delta.total_seconds() / 3600.0
                booking.penalty_amount = max(0.0, hours * rate)
                if booking.penalty_amount > 0:
                    booking.penalty_paid = False

    booking.booking_status = 'Completed'
    vehicle = db.session.get(Vehicle, booking.vehicle_id)
    if vehicle:
        vehicle.availability_status = 'Available'
        db.session.add(vehicle)
    db.session.add(booking)
    db.session.commit()

    # send completion email to user
    try:
        user = db.session.get(User, booking.user_id)
        if user and vehicle:
            send_completion_notification(
                user_email=user.email,
                user_name=user.username,
                booking_id=booking.id,
                vehicle_name=vehicle.name
            )
    except Exception:
        pass

    return jsonify({'success':True})


@app.route('/admin/penalties')
@admin_required
def admin_penalties_page():
    """Admin penalty management page"""
    return render_template("admin_penalties.html")


@app.route('/api/admin/penalties-list')
@admin_required
def get_admin_penalties():
    """Get all penalties for admin management"""
    # Get all bookings with penalties or damage reports
    bookings = db.session.query(Booking).filter(
        (Booking.penalty_amount > 0) | (Booking.damage_report.isnot(None))
    ).all()
    
    penalties = []
    for booking in bookings:
        user = db.session.get(User, booking.user_id)
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        
        penalties.append({
            'booking_id': booking.id,
            'user_name': user.username if user else 'Unknown',
            'user_email': user.email if user else 'Unknown',
            'vehicle_name': (vehicle.name or vehicle.brand) if vehicle else 'Unknown',
            'booking_date': booking.booking_date.strftime('%Y-%m-%d') if booking.booking_date else 'N/A',
            'due_date': booking.due_date.strftime('%Y-%m-%d') if booking.due_date else 'N/A',
            'penalty_amount': booking.penalty_amount,
            'penalty_paid': booking.penalty_paid,
            'damage_report': booking.damage_report or 'None',
            'booking_status': booking.booking_status
        })
    
    return jsonify({'penalties': penalties})


@app.route('/api/admin/update-penalty', methods=['POST'])
@admin_required
def update_penalty():
    """Update penalty amount for a booking"""
    data = request.get_json(silent=True) or {}
    booking_id = data.get('booking_id')
    penalty_amount = data.get('penalty_amount', 0)
    damage_report = data.get('damage_report')
    
    if not booking_id:
        return jsonify({'error': 'booking_id required'}), 400
    
    booking = Booking.query.get(booking_id)
    if not booking:
        return jsonify({'error': 'Booking not found'}), 404
    
    try:
        penalty_amount = float(penalty_amount)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid penalty amount'}), 400
    
    booking.penalty_amount = max(0.0, penalty_amount)
    # Update booking status based on penalty amount
    if booking.penalty_amount > 0:
        booking.penalty_paid = False
        booking.booking_status = 'Completed'  # Mark as completed even with pending penalty
    else:
        booking.booking_status = 'Completed'  # Mark as completed when no penalty
    
    if damage_report:
        booking.damage_report = damage_report[:500]
    
    db.session.add(booking)
    db.session.commit()
    
    # Send notification to user about penalty
    try:
        user = db.session.get(User, booking.user_id)
        if user and penalty_amount > 0:
                    send_plain_email(
                f'Reason: {damage_report or "Vehicle damage"}\n\n'
                f'Please visit your account to pay the penalty.\n\nThank you!'
            )
    except Exception as e:
        print(f"Email notification failed: {e}")
    
    return jsonify({'success': True, 'penalty_amount': booking.penalty_amount})


@app.route('/admin/penalty/create', methods=['POST'])
@admin_required
def create_penalty():
    """Create a penalty for a completed booking"""
    data = request.get_json()
    booking_id = data.get('booking_id')
    amount = data.get('amount')
    reason = data.get('reason')

    if not booking_id or not amount or not reason:
        return jsonify({'success': False, 'message': 'All fields are required'}), 400

    booking = Booking.query.get(booking_id)
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404

    if booking.booking_status != 'Completed':
        return jsonify({'success': False, 'message': 'Only completed bookings can have penalties'}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            return jsonify({'success': False, 'message': 'Penalty amount must be greater than 0'}), 400
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid penalty amount'}), 400

    # Update booking with penalty
    booking.penalty_amount = amount
    booking.penalty_paid = False
    booking.damage_report = reason[:500]  # Store reason in damage_report field

    db.session.add(booking)
    db.session.commit()

    # Send notification to user
    try:
        user = db.session.get(User, booking.user_id)
        if user:
            send_plain_email(
                user.email,
                'Penalty Issued for Your Booking',
                f'Dear {user.username},\n\n'
                f'A penalty of ₹{amount} has been issued for your booking #{booking_id}.\n\n'
                f'Reason: {reason}\n\n'
                f'Please visit your account to pay the penalty.\n\nThank you!'
            )
    except Exception as e:
        print(f"Email notification failed: {e}")

    return jsonify({'success': True, 'message': 'Penalty created successfully'})


@app.route('/admin/penalty/no-penalty', methods=['POST'])
@admin_required
def mark_no_penalty():
    """Mark a completed booking as having no penalty"""
    data = request.get_json()
    booking_id = data.get('booking_id')

    if not booking_id:
        return jsonify({'success': False, 'message': 'Booking ID is required'}), 400

    booking = Booking.query.get(booking_id)
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404

    if booking.booking_status != 'Completed':
        return jsonify({'success': False, 'message': 'Only completed bookings can be marked'}), 400

    # Mark as no penalty
    booking.penalty_amount = 0.0
    booking.penalty_paid = True
    booking.damage_report = 'No penalty issued'

    db.session.add(booking)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Booking marked as no penalty'})


@app.route('/user/penalties')
@login_required
def user_penalties_page():
    """User view their pending penalties"""
    user_id = session.get('user_id')
    
    # Get all bookings with unpaid penalties for this user
    bookings_with_penalties = db.session.query(Booking).filter(
        Booking.user_id == user_id,
        Booking.penalty_amount > 0
    ).all()
    
    penalties = []
    for booking in bookings_with_penalties:
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        penalties.append({
            'booking_id': booking.id,
            'vehicle_name': (vehicle.name or vehicle.brand) if vehicle else 'Unknown',
            'booking_date': booking.booking_date.strftime('%Y-%m-%d') if booking.booking_date else 'N/A',
            'due_date': booking.due_date.strftime('%Y-%m-%d') if booking.due_date else 'N/A',
            'penalty_amount': booking.penalty_amount,
            'penalty_paid': booking.penalty_paid,
            'damage_report': booking.damage_report or 'No details provided'
        })
    
    return render_template('user_penalties.html', penalties=penalties)


@app.route('/api/user/pending-penalties')
@login_required
def get_user_penalties():
    """Get user's pending penalties via API"""
    user_id = session.get('user_id')
    
    bookings_with_penalties = db.session.query(Booking).filter(
        Booking.user_id == user_id,
        Booking.penalty_amount > 0,
        Booking.penalty_paid == False
    ).all()
    
    total_penalty = sum(b.penalty_amount for b in bookings_with_penalties)
    
    penalties = []
    for booking in bookings_with_penalties:
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        penalties.append({
            'booking_id': booking.id,
            'vehicle_name': (vehicle.name or vehicle.brand) if vehicle else 'Unknown',
            'penalty_amount': booking.penalty_amount,
            'damage_report': booking.damage_report or 'No details',
            'paid': booking.penalty_paid
        })
    
    return jsonify({
        'total_pending': total_penalty,
        'penalty_count': len(penalties),
        'penalties': penalties
    })


@app.route('/admin/revenue-data')
def admin_revenue_data():
    """API endpoint for monthly revenue chart data"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    import calendar
    from datetime import datetime, timedelta
    
    # Get current year
    current_year = datetime.now().year
    
    # Initialize monthly data for all 12 months
    monthly_revenue = {}
    for month in range(1, 13):
        monthly_revenue[month] = 0
    
    # Calculate revenue from completed bookings
    completed_bookings = Booking.query.filter_by(booking_status='Completed').all()
    
    for booking in completed_bookings:
        if booking.booking_date:
            booking_month = booking.booking_date.month
            booking_year = booking.booking_date.year
            # Only count bookings from current year
            if booking_year == current_year:
                monthly_revenue[booking_month] += booking.total_price or 0
    
    # Prepare data for chart
    month_names = [calendar.month_abbr[i] for i in range(1, 13)]
    revenue_values = [monthly_revenue[i] for i in range(1, 13)]
    
    monthly_data = {
        'labels': month_names,
        'data': revenue_values
    }
    
    return jsonify(monthly_data)


# --- Vehicle Management API Endpoints ---
@app.route('/admin/vehicles-list')
def admin_vehicles_list():
    """API endpoint for vehicle management list with search and filters"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    search = request.args.get('search', '').lower()
    category = request.args.get('category', '')
    availability = request.args.get('availability', '')
    
    query = Vehicle.query
    
    if search:
        query = query.filter(
            (Vehicle.name.ilike(f'%{search}%')) | 
            (Vehicle.brand.ilike(f'%{search}%')) |
            (Vehicle.vehicle_number.ilike(f'%{search}%')) |
            (Vehicle.location.ilike(f'%{search}%'))
        )
    
    if category:
        query = query.filter_by(category=category)
    
    if availability:
        query = query.filter_by(availability_status=availability)
    
    vehicles = query.all()
    vehicles_data = []
    
    for v in vehicles:
        vehicles_data.append({
            'id': v.id,
            'name': v.name or '',
            'brand': v.brand or '',
            'location': v.location or '',
            'category': v.category or '',
            'vehicle_number': v.vehicle_number or '',
            'availability_status': v.availability_status or 'Available',
            'price_per_day': v.price_per_day or 0,
            'fuel_type': v.fuel_type or '',
            'seating_capacity': v.seating_capacity or 0,
            'transmission': v.transmission or '',
            'image': v.image or 'placeholder.png',
            'subscription_only': v.subscription_only
        })
    
    return jsonify({'vehicles': vehicles_data})


@app.route('/admin/vehicle-stats')
def admin_vehicle_stats():
    """API endpoint for vehicle statistics"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    total_vehicles = Vehicle.query.count()
    available = Vehicle.query.filter_by(availability_status='Available').count()
    booked = Vehicle.query.filter_by(availability_status='Booked').count()
    rented = Vehicle.query.filter_by(availability_status='Rented').count()
    maintenance = Vehicle.query.filter_by(availability_status='Maintenance').count()
    
    return jsonify({
        'total_vehicles': total_vehicles,
        'available_vehicles': available,
        'booked_vehicles': booked,
        'rented_vehicles': rented,
        'maintenance_vehicles': maintenance
    })


# --- User Management API Endpoints ---
@app.route('/admin/users-list')
def admin_users_list():
    """API endpoint for user management list with search and filters"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    search = request.args.get('search', '').lower()
    status = request.args.get('status', '')
    
    query = User.query.filter_by(role='user')  # Only regular users, not admins
    
    if search:
        query = query.filter(
            (User.username.ilike(f'%{search}%')) | 
            (User.email.ilike(f'%{search}%')) |
            (User.phone.ilike(f'%{search}%'))
        )
    
    if status:
        query = query.filter_by(status=status)
    
    users = query.all()
    users_data = []
    
    for u in users:
        users_data.append({
            'id': u.id,
            'username': u.username or '',
            'email': u.email or '',
            'phone': u.phone or '',
            'age': u.age,
            'address': u.address or '',
            'status': u.status or 'Active',
            'registration_date': u.registration_date.strftime('%Y-%m-%d') if u.registration_date else '',
            'total_bookings': Booking.query.filter_by(user_id=u.id).count()
        })
    
    return jsonify({'users': users_data})


@app.route('/admin/user-stats')
def admin_user_stats():
    """API endpoint for user statistics"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    total_users = User.query.filter_by(role='user').count()
    active_users = User.query.filter_by(role='user', status='Active').count()
    blocked_users = User.query.filter_by(role='user', status='Blocked').count()
    
    return jsonify({
        'total_users': total_users,
        'active_users': active_users,
        'blocked_users': blocked_users
    })


@app.route('/admin/user/<int:user_id>/toggle-status', methods=['POST'])
def toggle_user_status(user_id):
    """Toggle user status (Active/Blocked)"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    user.status = 'Blocked' if user.status == 'Active' else 'Active'
    db.session.commit()
    
    return jsonify({"success": True, "new_status": user.status})


@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
def delete_user(user_id):
    """Delete user and their bookings"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    # Delete bookings first
    Booking.query.filter_by(user_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()
    
    return jsonify({"success": True})


@app.route('/admin/vehicle/<int:vehicle_id>/edit', methods=['GET','POST'])
def edit_vehicle(vehicle_id):
    if "role" not in session or session["role"] != "admin":
        return redirect('/login')

    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        flash('Vehicle not found', 'danger')
        return redirect('/admin')

    if request.method == 'POST':
        vehicle.name = request.form.get('name')
        vehicle.category = request.form.get('category')
        vehicle.brand = request.form.get('brand')
        vehicle.location = request.form.get('location')
        vehicle.vehicle_number = request.form.get('vehicle_number')
        vehicle.price_per_day = float(request.form.get('price_per_day', 0)) if request.form.get('price_per_day') else 0
        vehicle.price_per_hour = float(request.form.get('price_per_hour', 0)) if request.form.get('price_per_hour') else 0
        vehicle.fuel_type = request.form.get('fuel_type')
        vehicle.seating_capacity = int(request.form.get('seating_capacity', 0)) if request.form.get('seating_capacity') else 0
        vehicle.transmission = request.form.get('transmission')
        vehicle.availability_status = request.form.get('availability_status', 'Available')
        vehicle.subscription_only = bool(request.form.get('subscription_only'))
        
        file = request.files.get('image')
        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            vehicle.image = filename

        db.session.commit()
        flash('Vehicle updated', 'success')
        return redirect('/admin/vehicles')

    return render_template('edit_vehicle.html', vehicle=vehicle)


@app.route('/admin/vehicle/<int:vehicle_id>/delete', methods=['POST'])
def delete_vehicle(vehicle_id):
    if "role" not in session or session["role"] != "admin":
        return redirect('/login')

    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        flash('Vehicle not found', 'danger')
        return redirect('/admin')

    # optionally remove image file
    try:
        if vehicle.image:
            path = os.path.join(app.config['UPLOAD_FOLDER'], vehicle.image)
            if os.path.exists(path):
                os.remove(path)
    except Exception:
        pass

    db.session.delete(vehicle)
    db.session.commit()
    flash('Vehicle deleted', 'success')
    return redirect('/admin')


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/contact', methods=['GET','POST'])
def contact():
    # for now, just render the contact page; handling form submission can be added
    return render_template('contact.html')


@app.route('/feedback', methods=['GET','POST'])
@login_required
def user_feedback():
    """User feedback page - GET shows form, POST submits feedback"""
    if request.method == 'POST':
        user_id = session.get('user_id')
        message = request.form.get('message', '').strip()
        rating = request.form.get('rating', 5)
        booking_id = request.form.get('booking_id', None)
        vehicle_id = request.form.get('vehicle_id', None)
        
        if not message:
            flash("Please provide feedback message", "danger")
            return redirect('/feedback')
        
        try:
            rating = int(rating)
            if rating < 1 or rating > 5:
                rating = 5
        except:
            rating = 5
        
        try:
            booking_id = int(booking_id) if booking_id else None
        except:
            booking_id = None
        
        try:
            vehicle_id = int(vehicle_id) if vehicle_id else None
        except:
            vehicle_id = None
        
        # Create feedback
        feedback = Feedback(
            user_id=user_id,
            booking_id=booking_id,
            vehicle_id=vehicle_id,
            message=message,
            rating=rating,
            status='Pending'
        )
        
        db.session.add(feedback)
        db.session.commit()
        
        flash("Thank you for your feedback! It will be reviewed by our team.", "success")
        return redirect('/feedback')
    
    # GET request - show feedback form
    user_id = session.get('user_id')
    # Get user's bookings for feedback selection
    user_bookings = Booking.query.filter_by(user_id=user_id).all()
    
    return render_template('feedback.html', bookings=user_bookings)


# Add Vehicle
@app.route('/add-vehicle', methods=['GET','POST'])
def add_vehicle():
    if "role" in session and session["role"] == "admin":
        if request.method == "POST":
            name = request.form.get('name', '')
            category = request.form.get('category', '')
            brand = request.form.get('brand', '')
            location = request.form.get('location', '')
            vehicle_number = request.form.get('vehicle_number', '')
            price_per_day = request.form.get('price_per_day', 0)
            price_per_hour = request.form.get('price_per_hour', 0)
            fuel_type = request.form.get('fuel_type', '')
            seating_capacity = request.form.get('seating_capacity', 0)
            transmission = request.form.get('transmission', '')

            file = request.files.get('image')
            filename = 'placeholder.png'
            if file and file.filename:
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            vehicle = Vehicle(
                name=name,
                category=category,
                brand=brand,
                location=location,
                vehicle_number=vehicle_number,
                availability_status='Available',
                price_per_day=float(price_per_day) if price_per_day else 0,
                price_per_hour=float(price_per_hour) if price_per_hour else 0,
                fuel_type=fuel_type,
                seating_capacity=int(seating_capacity) if seating_capacity else 0,
                transmission=transmission,
                image=filename,
                subscription_only=bool(request.form.get('subscription_only'))
            )
            db.session.add(vehicle)
            db.session.commit()
            flash("Vehicle Added!", "success")
            return redirect("/admin")

        return render_template("add_vehicle.html")
    return redirect("/login")


# View Vehicles (User)
@app.route('/vehicles')
def view_vehicles():
    # pass user_class to the template so the client script can default the
    # cost filter according to the user's category.
    user_class = None
    has_subscription = False
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
        if user:
            user_class = getattr(user, 'user_class', None)
            # determine if there's an active subscription
            active = UserSubscription.query.filter_by(user_id=user.id, status='Active').first()
            has_subscription = bool(active)
    # the UI uses the public API for data; the vehicles list below is only used
    # for initial server-side rendering in case JS is disabled.
    vehicles = Vehicle.query.all()
    return render_template("view_vehicles.html", vehicles=vehicles, user_class=user_class, has_subscription=has_subscription)


# Booking Vehicle
@app.route('/book/<int:id>', methods=['GET','POST'])
@login_required
def book_vehicle(id):
    vehicle = Vehicle.query.get_or_404(id)

    # run expiration check before evaluating availability - a booking may have
    # completed since the page was rendered
    check_expired_bookings()
    vehicle = db.session.get(Vehicle, id)  # re-fetch in case the status changed

    # If the vehicle is marked subscription-only, ensure user has an active subscription
    if vehicle.subscription_only:
        from services.subscription_service import get_active_subscription
        active = get_active_subscription(session.get('user_id')) if 'user_id' in session else None
        if not active:
            flash("This vehicle is only available to subscribed users. Please subscribe first.", "warning")
            return redirect("/subscriptions")

    # Check if vehicle is available
    if vehicle.availability_status != 'Available':
        flash("Vehicle is not available for booking.", "danger")
        return redirect("/vehicles")

    # also make sure there aren't any active bookings lingering (race
    # conditions could make the status stale). this extra check guards
    # against simultaneous requests and also catches any data inconsistencies.
    active = Booking.query.filter_by(vehicle_id=id).filter(
        Booking.booking_status.in_(['Pending', 'Confirmed', 'Paid', 'Booked'])
    ).first()
    if active:
        # vehicle should have been marked as booked already, but be safe
        flash("This vehicle is already reserved. Please choose another.", "danger")
        return redirect("/vehicles")

    if request.method == "POST":
        try:
            days = int(request.form.get('days', 0))
            if days <= 0 or days > 30:  # Max 30 days booking
                flash("Please enter a valid number of days (1-30).", "danger")
                return render_template("book_vehicle.html", vehicle=vehicle)

            # Handle proof document upload
            proof_type = request.form.get('proof_type', 'License')
            licence_proof = request.files.get('licence_proof')
            
            if not licence_proof:
                flash("Please upload a proof of identity (License/Aadhar/Passport).", "danger")
                return render_template("book_vehicle.html", vehicle=vehicle)
            
            # Validate file
            allowed_extensions = {'pdf', 'jpg', 'jpeg', 'png'}
            if '.' not in licence_proof.filename:
                flash("Invalid file type. Please upload PDF, JPG, or PNG.", "danger")
                return render_template("book_vehicle.html", vehicle=vehicle)
            
            file_ext = licence_proof.filename.rsplit('.', 1)[1].lower()
            if file_ext not in allowed_extensions:
                flash("Invalid file type. Please upload PDF, JPG, or PNG.", "danger")
                return render_template("book_vehicle.html", vehicle=vehicle)
            
            # Check file size (5MB max)
            licence_proof.seek(0, 2)  # Seek to end
            file_size = licence_proof.tell()
            if file_size > 5 * 1024 * 1024:  # 5MB
                flash("File size exceeds 5MB. Please upload a smaller file.", "danger")
                return render_template("book_vehicle.html", vehicle=vehicle)
            
            # Save file
            import os
            from werkzeug.utils import secure_filename
            
            if not os.path.exists('static/uploads'):
                os.makedirs('static/uploads')
            
            filename = secure_filename(f"proof_{session['user_id']}_{int(time.time())}_{licence_proof.filename}")
            licence_proof.seek(0)
            licence_proof.save(os.path.join('static/uploads', filename))

            total = days * vehicle.price_per_day

            booking_date = datetime.datetime.now()
            due_date = booking_date + datetime.timedelta(days=days)

            booking = Booking(
                user_id=session["user_id"], 
                vehicle_id=id, 
                days=days, 
                total_price=total, 
                booking_date=booking_date,
                due_date=due_date,
                licence_proof=filename,
                proof_type=proof_type
            )
            db.session.add(booking)

            # reserve the vehicle immediately to prevent other users from
            # booking it while the request is pending admin verification
            vehicle.availability_status = 'Booked'
            db.session.add(vehicle)
            db.session.flush()

            # booking created but payment is not allowed until admin verifies proof
            # status remains Pending until admin approves or rejects
            db.session.commit()

            # booking created but payment is not allowed until admin verifies proof
            flash("Booking request submitted – awaiting admin verification.", "info")
            return redirect('/bookings')
        except IntegrityError:
            # uniqueness violation – another active booking snuck in concurrently
            db.session.rollback()
            flash("Sorry, another user has just reserved this vehicle. Please choose a different one.", "danger")
            return redirect('/vehicles')
        except (ValueError, TypeError) as e:
            flash("Invalid input data.", "danger")
            return render_template("book_vehicle.html", vehicle=vehicle)

    return render_template("book_vehicle.html", vehicle=vehicle)


# Payment for booking
@app.route('/pay/<int:booking_id>', methods=['GET', 'POST'])
@login_required
def pay_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)

    # Check if booking belongs to current user
    if booking.user_id != session["user_id"]:
        flash("Unauthorized access", "danger")
        return redirect('/bookings')

    # If booking hasn't been approved by admin yet, disallow payment
    if booking.booking_status == 'Pending':
        flash("Your booking is still awaiting admin verification. You cannot pay until it is approved.", "warning")
        return redirect('/bookings')

    # Check if already paid/completed
    if booking.booking_status == 'Completed':
        flash("This booking is already paid", "info")
        return redirect('/bookings')

    vehicle = db.session.get(Vehicle, booking.vehicle_id)

    if request.method == "POST":
        # Handle coupon application separately: detect apply_coupon button
        if request.form.get('apply_coupon'):
            code = (request.form.get('coupon_code') or '').strip().upper()
            message = None
            discounted = None
            if not code:
                flash('Please enter a coupon code', 'warning')
                return render_template('payment.html', booking=booking, vehicle=vehicle)

            promo = PromoCode.query.filter_by(code=code, active=True).first()
            if not promo:
                flash('Invalid or expired coupon', 'danger')
                return render_template('payment.html', booking=booking, vehicle=vehicle)

            # Check expiry
            if promo.expires_at and promo.expires_at < datetime.datetime.now():
                flash('Coupon has expired', 'danger')
                return render_template('payment.html', booking=booking, vehicle=vehicle)

            original = booking.total_price or 0.0
            discounted_amount = promo.apply_discount(original)
            # Pass discount info back to template (do not persist until payment success)
            flash(f'Coupon applied: {promo.discount_percent}% off', 'success')
            return render_template('payment.html', booking=booking, vehicle=vehicle, promo=promo, discounted_amount=discounted_amount)

        # Simulate payment processing with multiple methods
        payment_method = request.form.get('payment_method')

        if not payment_method:
            flash("Please select a payment method", "danger")
            return render_template("payment.html", booking=booking, vehicle=vehicle)

        # Card payment
        if payment_method == 'card':
            card_number = request.form.get('card_number') or ''
            expiry_date = request.form.get('expiry_date') or ''
            cvv = request.form.get('cvv') or ''

            # Basic validation
            if not all([card_number, expiry_date, cvv]):
                flash("Please fill all card details", "danger")
                return render_template("payment.html", booking=booking, vehicle=vehicle)

            if len(card_number.replace(' ', '')) < 13 or not card_number.replace(' ', '').isdigit():
                flash("Invalid card number", "danger")
                return render_template("payment.html", booking=booking, vehicle=vehicle)

            if len(cvv) < 3 or not cvv.isdigit():
                flash("Invalid CVV", "danger")
                return render_template("payment.html", booking=booking, vehicle=vehicle)

        # UPI payment (accept any non-empty UPI ID for testing)
        elif payment_method == 'upi':
            upi_id = request.form.get('upi_id', '').strip()
            if not upi_id:
                flash("Please enter UPI ID", "danger")
                return render_template("payment.html", booking=booking, vehicle=vehicle)

        # Netbanking payment (accept selection)
        elif payment_method == 'netbanking':
            bank = request.form.get('bank', '').strip()
            if not bank:
                flash("Please select a bank", "danger")
                return render_template("payment.html", booking=booking, vehicle=vehicle)

        # Account password payment (dummy/testing)
        elif payment_method == 'account':
            account_password = request.form.get('account_password', '')
            if not account_password:
                flash("Please enter your account password to proceed", "danger")
                return render_template("payment.html", booking=booking, vehicle=vehicle)

            # Verify against user's stored password hash
            user = db.session.get(User, session.get('user_id'))
            if not user or not check_password_hash(user.password, account_password):
                flash("Invalid account password", "danger")
                return render_template("payment.html", booking=booking, vehicle=vehicle)

        else:
            flash("Unsupported payment method", "danger")
            return render_template("payment.html", booking=booking, vehicle=vehicle)

        # If all validations pass for the chosen method, mark booking as confirmed (paid)
        # but first ensure vehicle is still available to avoid double-booking
        # Ensure booking_date/due_date exist
        if not booking.booking_date:
            booking.booking_date = datetime.datetime.now()
        if not booking.due_date and booking.days:
            booking.due_date = booking.booking_date + datetime.timedelta(days=booking.days)

        # Fetch fresh vehicle from DB using explicit session method
        vehicle = db.session.query(Vehicle).filter_by(id=booking.vehicle_id).first()
        if not vehicle:
            flash("Vehicle not found. Booking could not be completed.", "danger")
            return redirect('/bookings')
        
        # Check if vehicle is still available or reserved for this booking
        if vehicle.availability_status not in ['Available', 'Booked']:
            # Vehicle no longer available — cancel this booking and free if necessary
            booking.booking_status = 'Cancelled'
            if vehicle.availability_status in ['Booked', 'Rented']:
                vehicle.availability_status = 'Available'
                db.session.add(vehicle)
            db.session.commit()
            flash("Sorry, the vehicle is no longer available. Booking canceled.", "danger")
            return redirect('/bookings')

        # Before marking paid, check if a coupon was submitted and apply it to the booking total
        coupon_code = (request.form.get('coupon_code') or '').strip().upper()
        if coupon_code:
            promo = PromoCode.query.filter_by(code=coupon_code, active=True).first()
            if promo and (not promo.expires_at or promo.expires_at >= datetime.datetime.now()):
                try:
                    booking.total_price = float(promo.apply_discount(booking.total_price or 0.0))
                except Exception:
                    pass

        # MARK BOOKING AS PAID AND VEHICLE AS RENTED
        booking.booking_status = 'Paid'
        vehicle.availability_status = 'Rented'
        
        # Create payment record for form-based payment
        import uuid
        payment_record = Payment(
            user_id=session.get('user_id'),
            booking_id=booking_id,
            amount=booking.total_price,
            payment_method='form_payment',
            transaction_id=f"FORM-{booking_id}-{uuid.uuid4().hex[:8]}",
            status='Success',
            description=f"Payment for booking #{booking_id}"
        )
        db.session.add(payment_record)
        
        # Ensure both objects are in the session
        db.session.add(booking)
        db.session.add(vehicle)
        
        # Commit all changes to database with flush to ensure persistence
        db.session.flush()  # Write to DB immediately
        db.session.commit()  # Confirm transaction
        
        # Force refresh to confirm changes were saved
        db.session.refresh(booking)
        db.session.refresh(vehicle)

        flash("Payment successful! Your booking is now paid and confirmed.", "success")
        return redirect('/bookings')

    return render_template("payment.html", booking=booking, vehicle=vehicle)


# View User Bookings
@app.route('/bookings')
@login_required
def bookings():
    check_expired_bookings()
    user = db.session.get(User, session["user_id"])
    books = Booking.query.filter_by(user_id=session["user_id"]).all()

    # prepare a lightweight JSON-friendly version for client-side fallback
    books_json = []
    for book in books:
        vehicle = db.session.get(Vehicle, book.vehicle_id)
        books_json.append({
            'id': book.id,
            'vehicle': vehicle.name if vehicle else 'Unknown Vehicle',
            'image': vehicle.image if vehicle else 'placeholder.png',
            'days': book.days,
            'total_price': book.total_price,
            'booking_date': book.booking_date.isoformat() if book.booking_date else None,
            'due_date': book.due_date.isoformat() if book.due_date else None,
            'status': book.booking_status,
            'handover_time': book.handover_time.isoformat() if book.handover_time else None,
            'received_time': book.received_time.isoformat() if book.received_time else None,
            'penalty_amount': book.penalty_amount,
            'penalty_paid': bool(book.penalty_paid)
        })

    # Add full vehicle object back to each booking for any server-side rendering
    for book in books:
        book.vehicle = db.session.get(Vehicle, book.vehicle_id)

    return render_template("bookings.html", bookings=books, bookings_json=books_json, user=user, user_id=session["user_id"])


@app.route('/dashboard')
@login_required
def dashboard():
    check_expired_bookings()
    user = db.session.get(User, session['user_id'])
    bookings_list = Booking.query.filter_by(user_id=user.id).all()
    # determine if user has an active subscription
    has_subscription = bool(UserSubscription.query.filter_by(user_id=user.id, status='Active').first())
    return render_template('user_dashboard.html', user=user, bookings=bookings_list, has_subscription=has_subscription)


# Penalty payment route (separate from regular booking payment)
@app.route('/pay/penalty/<int:booking_id>', methods=['GET', 'POST'])
@login_required
def pay_penalty(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    is_admin = session.get('role') == 'admin'

    # ownership check - allow if user is the one who booked or if admin
    if not is_admin and booking.user_id != session.get('user_id'):
        flash('Unauthorized access', 'danger')
        return redirect('/bookings')

    if not booking.penalty_amount or booking.penalty_amount <= 0:
        flash('No penalty due for this booking', 'info')
        return redirect('/admin/penalties' if is_admin else '/bookings')

    # If penalty is already paid, show receipt (for both user and admin)
    if booking.penalty_paid:
        payment = Payment.query.filter_by(booking_id=booking_id, description='Penalty payment').first()
        return render_template('pay_penalty.html', booking=booking, payment=payment, is_receipt=True)

    if request.method == 'POST':
        # admin users should not perform payments
        if is_admin:
            flash('Admins cannot make payments through this page.', 'warning')
            return redirect('/admin/penalties')
        
        method = request.form.get('payment_method') or 'penalty'
        try:
            payment = Payment(
                user_id=session.get('user_id'),
                booking_id=booking.id,
                amount=float(booking.penalty_amount),
                payment_method=method,
                transaction_id=f'penalty-{int(time.time())}',
                status='Success',
                description='Penalty payment'
            )
            db.session.add(payment)
            booking.penalty_paid = True
            db.session.add(booking)
            db.session.commit()
            flash('Penalty payment successful. Thank you.', 'success')
            return redirect('/admin/penalties' if is_admin else '/bookings')
        except Exception as e:
            db.session.rollback()
            flash('Payment failed: ' + str(e), 'danger')
            return render_template('pay_penalty.html', booking=booking)

    return render_template('pay_penalty.html', booking=booking)


# Payment History Page
@app.route('/payment-history')
@login_required
def payment_history():
    """View user payment history"""
    user = db.session.get(User, session["user_id"])
    if not user:
        flash("User not found", "danger")
        return redirect("/dashboard")
    
    # Get all payments for the user
    payments = Payment.query.filter_by(user_id=session["user_id"]).order_by(Payment.created_date.desc()).all()
    
    # Get booking info for each payment if it's a booking payment
    payment_details = []
    for payment in payments:
        detail = {
            'payment': payment,
            'booking': None,
            'vehicle': None,
            'subscription': None
        }
        
        # Get booking details if this is a booking payment
        if payment.booking_id:
            detail['booking'] = db.session.get(Booking, payment.booking_id)
            if detail['booking']:
                detail['vehicle'] = db.session.get(Vehicle, detail['booking'].vehicle_id)
        
        # Get subscription details if this is a subscription payment
        if payment.subscription_id:
            detail['subscription'] = db.session.get(UserSubscription, payment.subscription_id)
        
        payment_details.append(detail)
    
    # Calculate summary stats
    total_paid = sum(p.amount for p in payments if p.status == 'Success')
    total_bookings_paid = len([p for p in payments if p.booking_id and p.status == 'Success'])
    
    return render_template("payment_history.html", 
                         user=user, 
                         payment_details=payment_details, 
                         total_paid=total_paid,
                         total_bookings_paid=total_bookings_paid)


# Public API endpoint for vehicles (for user pages)
@app.route('/api/vehicles')
def api_vehicles():
    """Public API endpoint to get all vehicles for user pages

    We also run a quick expiration check so that vehicle availability is always
    up to date when the page or javascript polls this endpoint. This makes the
    UI reflect a booking completion or cancellation without manual refresh.
    """
    check_expired_bookings()

    vehicles = Vehicle.query.all()
    
    vehicles_data = []
    for vehicle in vehicles:
        vehicles_data.append({
            'id': vehicle.id,
            'name': vehicle.name,
            'brand': vehicle.brand,
            'category': vehicle.category,
            'fuel_type': vehicle.fuel_type,
            'seating_capacity': vehicle.seating_capacity,
            'transmission': vehicle.transmission,
            'price_per_day': vehicle.price_per_day,
            'image': vehicle.image,
            'availability_status': vehicle.availability_status,
            'vehicle_number': vehicle.vehicle_number,
            'subscription_only': vehicle.subscription_only
        })
    
    return jsonify({'vehicles': vehicles_data})


# Public API endpoint for user bookings (for user dashboard and bookings page)
@app.route('/api/bookings')
def api_user_bookings():
    """Public API endpoint to get current user's bookings"""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    # make sure vehicle status is up to date before returning any booking info
    check_expired_bookings()
    
    bookings = Booking.query.filter_by(user_id=session["user_id"]).all()
    
    bookings_data = []
    for booking in bookings:
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        bookings_data.append({
            'id': booking.id,
            'vehicle_id': booking.vehicle_id,
            'vehicle': vehicle.name if vehicle else 'Unknown Vehicle',
            'image': vehicle.image if vehicle else 'placeholder.png',
            'days': booking.days,
            'total_price': booking.total_price,
            'booking_date': booking.booking_date.isoformat() if booking.booking_date else None,
            'due_date': booking.due_date.isoformat() if booking.due_date else None,
            'status': booking.booking_status,
            'handover_time': booking.handover_time.isoformat() if booking.handover_time else None,
            'received_time': booking.received_time.isoformat() if booking.received_time else None,
            'penalty_amount': booking.penalty_amount,
            'penalty_paid': bool(booking.penalty_paid),
            'damage_report': booking.damage_report,
            'proof_type': booking.proof_type,
            'licence_proof': booking.licence_proof
        })
    
    return jsonify({'bookings': bookings_data})


# Public API endpoint for available vehicles (for user dashboard)
@app.route('/api/vehicles/available')
def api_available_vehicles():
    """Public API endpoint to get available vehicles for user dashboard

    Supports optional query parameters `min_price` and `max_price` as well as an
    automatic restriction based on the logged‑in user's `user_class`.
    """
    # update any expired bookings before deciding which vehicles are available
    check_expired_bookings()
    query = Vehicle.query.filter_by(availability_status='Available')

    # numeric filters from query string
    try:
        min_price = float(request.args.get('min_price'))
    except (TypeError, ValueError):
        min_price = None
    try:
        max_price = float(request.args.get('max_price'))
    except (TypeError, ValueError):
        max_price = None

    if min_price is not None:
        query = query.filter(Vehicle.price_per_day >= min_price)
    if max_price is not None:
        query = query.filter(Vehicle.price_per_day <= max_price)

    # automatically narrow results by user class if available
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
        if user and getattr(user, 'user_class', None):
            if user.user_class == 'low':
                query = query.filter(Vehicle.price_per_day <= 500)          # adjust thresholds as needed
            elif user.user_class == 'high':
                query = query.filter(Vehicle.price_per_day >= 2000)
            # middle class sees everything (no extra filter)

    vehicles = query.all()
    
    vehicles_data = []
    for vehicle in vehicles:
        vehicles_data.append({
            'id': vehicle.id,
            'name': vehicle.name,
            'brand': vehicle.brand,
            'category': vehicle.category,
            'fuel_type': vehicle.fuel_type,
            'seating_capacity': vehicle.seating_capacity,
            'price_per_day': vehicle.price_per_day,
            'image': vehicle.image,
            'availability_status': vehicle.availability_status,
            'location': vehicle.location,
            'subscription_only': vehicle.subscription_only
        })
    
    return jsonify({'vehicles': vehicles_data})


# API endpoint for vehicle counts (for dashboard stats)
@app.route('/api/vehicle-counts')
def api_vehicle_counts():
    """Get counts of vehicles by status"""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    check_expired_bookings()
    total_vehicles = Vehicle.query.count()
    available_count = Vehicle.query.filter_by(availability_status='Available').count()
    rented_count = Vehicle.query.filter_by(availability_status='Rented').count()
    maintenance_count = Vehicle.query.filter_by(availability_status='Maintenance').count()
    
    return jsonify({
        'total_vehicles': total_vehicles,
        'available_vehicles': available_count,
        'rented_vehicles': rented_count,
        'maintenance_vehicles': maintenance_count
    })


# === REAL-TIME UPDATE ENDPOINTS FOR DYNAMIC CHANGES ===

@app.route('/api/admin/dashboard-stats')
def admin_dashboard_stats():
    """Real-time admin dashboard statistics"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    total_users = User.query.filter_by(role='user').count()
    total_vehicles = Vehicle.query.count()
    total_bookings = Booking.query.count()
    completed_bookings = Booking.query.filter_by(booking_status='Completed').count()
    total_revenue = sum(b.total_price for b in Booking.query.filter_by(booking_status='Completed').all()) or 0
    
    return jsonify({
        'total_users': total_users,
        'total_vehicles': total_vehicles,
        'total_bookings': total_bookings,
        'completed_bookings': completed_bookings,
        'total_revenue': round(total_revenue, 2)
    })


@app.route('/api/admin/bookings-list')
def admin_bookings_list():
    """Real-time bookings list for admin.

    Query parameters:
      - status: filter by booking_status value
      - limit: number of records to return; 'all' or <=0 for no limit
    """
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    status_filter = request.args.get('status', '')
    limit_param = request.args.get('limit', '10')
    
    query = Booking.query
    if status_filter:
        query = query.filter_by(booking_status=status_filter)
    
    # ordering should happen before limiting
    query = query.order_by(Booking.booking_date.desc())
    if limit_param.lower() != 'all':
        try:
            n = int(limit_param)
            if n > 0:
                query = query.limit(n)
        except ValueError:
            pass
    # if limit is 'all' or invalid, return all matching results
    bookings = query.all()
    
    bookings_data = []
    for booking in bookings:
        user = db.session.get(User, booking.user_id)
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        
        bookings_data.append({
            'id': booking.id,
            'user': user.username if user else 'Unknown',
            'renter': user.username if user else 'Unknown',  # keep legacy key
            'vehicle': vehicle.name if vehicle else 'Unknown',
            'brand': vehicle.brand if vehicle else None,
            'days': booking.days,
            'total_price': booking.total_price,
            'status': booking.booking_status,
            'booking_date': booking.booking_date.strftime('%d %b %Y') if booking.booking_date else 'N/A',
            'due_date': booking.due_date.strftime('%d %b %Y') if booking.due_date else 'N/A',
            'penalty_amount': booking.penalty_amount,
            'penalty_paid': booking.penalty_paid
        })
    
    return jsonify({'bookings': bookings_data})


@app.route('/api/admin/vehicle-update/<int:vehicle_id>', methods=['POST'])
def admin_vehicle_update(vehicle_id):
    """Update vehicle status and details"""
    if "role" not in session or session["role"] != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return jsonify({"error": "Vehicle not found"}), 404
    
    data = request.get_json()
    
    if 'availability_status' in data:
        vehicle.availability_status = data['availability_status']
    
    if 'price_per_day' in data:
        vehicle.price_per_day = float(data['price_per_day'])
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Vehicle updated successfully',
        'vehicle': {
            'id': vehicle.id,
            'name': vehicle.name,
            'availability_status': vehicle.availability_status,
            'price_per_day': vehicle.price_per_day
        }
    })


@app.route('/api/user/bookings-stats')
def user_bookings_stats():
    """Real-time user bookings statistics"""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    user_id = session["user_id"]
    bookings = Booking.query.filter_by(user_id=user_id).all()
    
    active_bookings = len([b for b in bookings if b.booking_status in ['Paid', 'Booked']])
    completed_bookings = len([b for b in bookings if b.booking_status == 'Completed'])
    total_spent = sum(b.total_price for b in bookings)
    
    return jsonify({
        'active_bookings': active_bookings,
        'completed_bookings': completed_bookings,
        'total_bookings': len(bookings),
        'total_spent': round(total_spent, 2)
    })


# API endpoint to get user profile info
@app.route('/api/user-profile')
@login_required
def api_user_profile():
    """Get current user profile information"""
    user = db.session.get(User, session["user_id"])
    if user:
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'phone': user.phone or '',
                'address': user.address or '',
                'role': user.role,
                'status': user.status,
                'registration_date': user.registration_date.isoformat() if user.registration_date else None
            }
        })
    return jsonify({'success': False, 'error': 'User not found'}), 404


# User Profile Page
@app.route('/profile')
@login_required
def user_profile():
    """View user profile"""
    user = db.session.get(User, session["user_id"])
    if not user:
        flash("User not found", "danger")
        return redirect("/dashboard")
    
    # Get booking stats
    bookings = Booking.query.filter_by(user_id=session["user_id"]).all()
    total_spent = sum(b.total_price for b in bookings if b.booking_status == 'Completed')
    
    return render_template("profile.html", user=user, bookings_count=len(bookings), total_spent=total_spent)


# Edit User Profile
@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """Edit user profile"""
    user = db.session.get(User, session["user_id"])
    if not user:
        flash("User not found", "danger")
        return redirect("/dashboard")
    
    if request.method == "POST":
        # Get form data
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        age_input = request.form.get('age', '').strip()
        
        # Validation
        if not email:
            flash("Email is required", "danger")
            return render_template("edit_profile.html", user=user)
        
        # validate age if provided
        age = None
        if age_input:
            try:
                age = int(age_input)
                if age <= 0:
                    raise ValueError()
            except ValueError:
                flash("Please enter a valid age.", "danger")
                return render_template("edit_profile.html", user=user)
        
        # Check if email is already used by another user
        existing_email = User.query.filter(User.email == email, User.id != user.id).first()
        if existing_email:
            flash("Email already registered!", "danger")
            return render_template("edit_profile.html", user=user)
        
        # Update user details
        user.email = email
        user.phone = phone
        user.address = address
        user.age = age
        db.session.commit()
        
        flash("Profile updated successfully!", "success")
        return redirect("/profile")
    
    return render_template("edit_profile.html", user=user)


# ==================== SUBSCRIPTION ROUTES ====================

@app.route('/subscriptions')
@login_required
def view_subscriptions():
    """View available subscription plans (user-facing).
    Admins are redirected to the admin panel where they can manage
    both plan definitions and individual user subscriptions.
    """
    # if admin, send them to admin pages instead
    if session.get('role') == 'admin':
        return redirect(url_for('admin_user_subscriptions'))

    plans = SubscriptionPlan.query.all()
    
    # Get user's active subscription if any
    user_id = session.get('user_id')
    active_subscription = UserSubscription.query.filter_by(
        user_id=user_id,
        status='Active'
    ).first()
    
    return render_template('subscriptions.html', plans=plans, active_subscription=active_subscription, role=session.get('role'))


@app.route('/api/subscriptions/plans', methods=['GET'])
def get_subscription_plans():
    """Get all subscription plans (API endpoint)"""
    try:
        plans = SubscriptionPlan.query.all()
        
        plans_data = []
        for plan in plans:
            plans_data.append({
                'id': plan.id,
                'name': plan.name,
                'description': plan.description,
                'price_per_month': plan.price_per_month,
                'discount_percent': plan.discount_percent,
                'rental_limit_per_month': plan.rental_limit_per_month,
                'priority_booking': plan.priority_booking,
                'free_cancellation_hours': plan.free_cancellation_hours
            })
        
        return jsonify({'success': True, 'plans': plans_data}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/subscriptions/user', methods=['GET'])
@login_required
def get_user_subscription():
    """Get current user's subscription"""
    try:
        user_id = session.get('user_id')
        
        subscription = UserSubscription.query.filter_by(user_id=user_id).first()
        
        if not subscription:
            return jsonify({'success': True, 'subscription': None}), 200
        
        plan = db.session.get(SubscriptionPlan, subscription.plan_id)
        
        return jsonify({
            'success': True,
            'subscription': {
                'id': subscription.id,
                'plan_name': plan.name if plan else None,
                'start_date': subscription.start_date.strftime('%Y-%m-%d') if subscription.start_date else None,
                'end_date': subscription.end_date.strftime('%Y-%m-%d') if subscription.end_date else None,
                'status': subscription.status,
                'auto_renew': subscription.auto_renew,
                'remaining_days': (subscription.end_date - datetime.datetime.now()).days if subscription.end_date else 0
            }
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/subscription/activate', methods=['POST'])
@login_required
def activate_subscription():
    """Activate a subscription plan for user"""
    try:
        data = request.get_json()
        plan_id = data.get('plan_id')
        
        if not plan_id:
            return jsonify({'error': 'Plan ID is required'}), 400
        
        plan = db.session.get(SubscriptionPlan, plan_id)
        if not plan:
            return jsonify({'error': 'Plan not found'}), 404
        
        user_id = session.get('user_id')
        user = db.session.get(User, user_id)
        
        # Check if user already has active subscription
        existing = UserSubscription.query.filter_by(
            user_id=user_id,
            status='Active'
        ).first()
        
        if existing:
            return jsonify({'error': 'You already have an active subscription'}), 400
        
        # Create payment order for subscription
        from services.payment_service import calculate_booking_amount
        
        amount_calc = calculate_booking_amount(
            plan.price_per_month * 100,  # Convert to paise first
            1,
            0
        )
        
        order = razorpay_service.create_order(
            amount=int(plan.price_per_month * 100),
            currency='INR',
            receipt=f"subscription_{plan_id}_{int(time.time())}",
            notes={
                'plan_id': plan_id,
                'user_id': user_id,
                'type': 'subscription'
            }
        )
        
        if not order:
            return jsonify({'error': 'Failed to create payment order'}), 500
        
        return jsonify({
            'success': True,
            'order_id': order['id'],
            'amount': plan.price_per_month,
            'amount_paise': order['amount'],
            'key_id': app.config.get('RAZORPAY_KEY_ID'),
            'plan_id': plan_id,
            'plan_name': plan.name
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/pay-subscription/<int:plan_id>', methods=['GET', 'POST'])
@login_required
def pay_subscription(plan_id):
    """Handle subscription payment with multiple payment methods including password"""
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    
    # Check if user already has an active subscription
    user_id = session.get('user_id')
    active_subscription = UserSubscription.query.filter_by(
        user_id=user_id,
        status='Active'
    ).filter(UserSubscription.end_date > datetime.datetime.now()).first()
    
    if active_subscription:
        # User has an active subscription, redirect back with message
        flash(f"You already have an active subscription to the '{active_subscription.plan.name}' plan. It is valid until {active_subscription.end_date.strftime('%B %d, %Y')}.", "warning")
        return redirect(url_for('view_subscriptions'))
    
    if request.method == "POST":
        payment_method = request.form.get('payment_method')
        
        if not payment_method:
            flash("Please select a payment method", "danger")
            return render_template("subscription_payment.html", plan=plan)
        
        # Card payment
        if payment_method == 'card':
            card_number = request.form.get('card_number') or ''
            expiry_date = request.form.get('expiry_date') or ''
            cvv = request.form.get('cvv') or ''
            
            # Basic validation
            if not all([card_number, expiry_date, cvv]):
                flash("Please fill all card details", "danger")
                return render_template("subscription_payment.html", plan=plan)
            
            if len(card_number.replace(' ', '')) < 13 or not card_number.replace(' ', '').isdigit():
                flash("Invalid card number", "danger")
                return render_template("subscription_payment.html", plan=plan)
            
            if len(cvv) < 3 or not cvv.isdigit():
                flash("Invalid CVV", "danger")
                return render_template("subscription_payment.html", plan=plan)
        
        # UPI payment
        elif payment_method == 'upi':
            upi_id = request.form.get('upi_id', '').strip()
            if not upi_id:
                flash("Please enter UPI ID", "danger")
                return render_template("subscription_payment.html", plan=plan)
        
        # Net Banking payment
        elif payment_method == 'netbanking':
            bank = request.form.get('bank', '').strip()
            if not bank:
                flash("Please select a bank", "danger")
                return render_template("subscription_payment.html", plan=plan)
        
        # Account password payment
        elif payment_method == 'account':
            account_password = request.form.get('account_password', '')
            if not account_password:
                flash("Please enter your account password to proceed", "danger")
                return render_template("subscription_payment.html", plan=plan)
            
            # Verify against user's stored password hash
            user = db.session.get(User, session.get('user_id'))
            if not user or not check_password_hash(user.password, account_password):
                flash("Invalid account password", "danger")
                return render_template("subscription_payment.html", plan=plan)
        
        else:
            flash("Unsupported payment method", "danger")
            return render_template("subscription_payment.html", plan=plan)
        
        # Check if user already has an active subscription
        user_id = session.get('user_id')
        old_subscription = UserSubscription.query.filter_by(
            user_id=user_id,
            status='Active'
        ).first()
        
        if old_subscription:
            old_subscription.status = 'Cancelled'
        
        # Create new subscription
        start_date = datetime.datetime.now()
        end_date = start_date + datetime.timedelta(days=30)  # 1 month subscription
        
        new_subscription = UserSubscription(
            user_id=user_id,
            plan_id=plan_id,
            start_date=start_date,
            end_date=end_date,
            status='Active',
            auto_renew=True
        )
        
        # Create payment record
        import uuid
        payment_record = Payment(
            user_id=user_id,
            subscription_id=new_subscription.id,
            amount=plan.price_per_month,
            payment_method='form_payment',
            transaction_id=f"SUB-{plan_id}-{uuid.uuid4().hex[:8]}",
            status='Success',
            description=f"Subscription to {plan.name}"
        )
        
        db.session.add(new_subscription)
        db.session.add(payment_record)
        db.session.flush()
        db.session.commit()
        
        # Refresh objects
        db.session.refresh(new_subscription)
        
        # Send email notification
        user = db.session.get(User, user_id)
        if user:
            send_subscription_activated(
                user_email=user.email,
                user_name=user.username,
                plan_name=plan.name,
                end_date=end_date.strftime('%Y-%m-%d'),
                price=plan.price_per_month,
                discount=plan.discount_percent,
                free_cancellation_hours=plan.free_cancellation_hours,
                priority_booking=plan.priority_booking
            )
        
        flash("Payment successful! Your subscription is now active.", "success")
        return redirect('/subscriptions')
    
    return render_template("subscription_payment.html", plan=plan)


@app.route('/api/subscription/verify-payment', methods=['POST'])
@login_required
def verify_subscription_payment():
    """Verify subscription payment and activate plan"""
    try:
        data = request.get_json()
        
        payment_id = data.get('razorpay_payment_id')
        order_id = data.get('razorpay_order_id')
        signature = data.get('razorpay_signature')
        plan_id = data.get('plan_id')
        
        if not all([payment_id, order_id, signature, plan_id]):
            return jsonify({'error': 'Missing payment information'}), 400
        
        # Verify payment signature
        is_valid = razorpay_service.verify_payment_signature(payment_id, order_id, signature)
        
        if not is_valid:
            return jsonify({'error': 'Invalid payment signature'}), 403
        
        # Fetch payment details
        payment = razorpay_service.fetch_payment(payment_id)
        
        if not payment or payment['status'] != 'captured':
            return jsonify({'error': 'Payment not captured'}), 400
        
        user_id = session.get('user_id')
        plan = db.session.get(SubscriptionPlan, plan_id)
        
        if not plan:
            return jsonify({'error': 'Plan not found'}), 404
        
        # Cancel existing subscription if any
        old_subscription = UserSubscription.query.filter_by(
            user_id=user_id,
            status='Active'
        ).first()
        
        if old_subscription:
            old_subscription.status = 'Cancelled'
        
        # Create new subscription
        start_date = datetime.datetime.now()
        end_date = start_date + datetime.timedelta(days=30)  # 1 month subscription
        
        new_subscription = UserSubscription(
            user_id=user_id,
            plan_id=plan_id,
            start_date=start_date,
            end_date=end_date,
            status='Active',
            auto_renew=True
        )
        
        # Create payment record
        new_payment = Payment(
            user_id=user_id,
            subscription_id=new_subscription.id,
            amount=plan.price_per_month,
            payment_method='online',
            transaction_id=payment_id,
            payment_id=order_id,
            status='Success',
            description=f'Subscription to {plan.name}'
        )
        
        db.session.add(new_subscription)
        db.session.add(new_payment)
        db.session.commit()
        
        # Get user for email
        user = db.session.get(User, user_id)
        if user:
            send_subscription_activated(
                user_email=user.email,
                user_name=user.username,
                plan_name=plan.name,
                end_date=end_date.strftime('%Y-%m-%d'),
                price=plan.price_per_month,
                discount=plan.discount_percent,
                free_cancellation_hours=plan.free_cancellation_hours,
                priority_booking=plan.priority_booking
            )
        
        return jsonify({
            'success': True,
            'message': 'Subscription activated successfully',
            'subscription_id': new_subscription.id
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/subscription/cancel', methods=['POST'])
@login_required
def cancel_subscription():
    """Cancel user's active subscription"""
    try:
        user_id = session.get('user_id')
        
        subscription = UserSubscription.query.filter_by(
            user_id=user_id,
            status='Active'
        ).first()
        
        if not subscription:
            return jsonify({'error': 'No active subscription found'}), 404
        
        subscription.status = 'Cancelled'
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Subscription cancelled successfully'
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== END SUBSCRIPTION ROUTES ====================



@app.route('/api/payment/create-order', methods=['POST'])
@login_required
def create_payment_order():
    """Create a Razorpay payment order for a booking"""
    try:
        data = request.get_json()
        booking_id = data.get('booking_id')
        
        if not booking_id:
            return jsonify({'error': 'Booking ID is required'}), 400
        
        booking = Booking.query.get_or_404(booking_id)
        
        # Check if booking belongs to current user
        if booking.user_id != session.get('user_id'):
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Check if already paid
        if booking.booking_status in ['Paid', 'Completed']:
            return jsonify({'error': 'Booking already paid'}), 400
        
        # Calculate amount with subscription discount if user has active subscription
        user = db.session.get(User, session.get('user_id'))
        discount = 0
        
        if user:
            active_subscription = UserSubscription.query.filter_by(
                user_id=user.id,
                status='Active'
            ).first()
            
            if active_subscription:
                plan = db.session.get(SubscriptionPlan, active_subscription.plan_id)
                if plan:
                    discount = plan.discount_percent
        
        # Calculate final amount
        amount_calc = calculate_booking_amount(
            booking.vehicle.price_per_day if booking.vehicle else 0,
            booking.days or 1,
            discount
        )
        
        # Create Razorpay order
        order = razorpay_service.create_order(
            amount=amount_calc['total_paise'],
            currency='INR',
            receipt=f"booking_{booking_id}_{int(time.time())}",
            notes={
                'booking_id': booking_id,
                'user_id': user.id if user else None,
                'vehicle_id': booking.vehicle_id
            }
        )
        
        if not order:
            return jsonify({'error': 'Failed to create payment order'}), 500
        
        # Store order ID in booking for reference
        booking.razorpay_order_id = order['id']
        db.session.commit()
        
        return jsonify({
            'success': True,
            'order_id': order['id'],
            'amount': amount_calc['total'] / 100,  # Convert to rupees
            'amount_paise': order['amount'],
            'currency': order['currency'],
            'key_id': app.config.get('RAZORPAY_KEY_ID')
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/payment/verify', methods=['POST'])
@login_required
def verify_payment():
    """Verify and process Razorpay payment"""
    try:
        data = request.get_json()
        
        payment_id = data.get('razorpay_payment_id')
        order_id = data.get('razorpay_order_id')
        signature = data.get('razorpay_signature')
        booking_id = data.get('booking_id')
        
        if not all([payment_id, order_id, signature, booking_id]):
            return jsonify({'error': 'Missing payment information'}), 400
        
        booking = Booking.query.get_or_404(booking_id)
        
        # Verify it belongs to current user
        if booking.user_id != session.get('user_id'):
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Verify payment signature
        is_valid = razorpay_service.verify_payment_signature(payment_id, order_id, signature)
        
        if not is_valid:
            return jsonify({'error': 'Invalid payment signature'}), 403
        
        # Fetch payment details from Razorpay
        payment = razorpay_service.fetch_payment(payment_id)
        
        if not payment or payment['status'] != 'captured':
            return jsonify({'error': 'Payment not captured'}), 400
        
        # Create payment record in database
        new_payment = Payment(
            user_id=booking.user_id,
            booking_id=booking_id,
            amount=payment['amount'] / 100,  # Convert from paise
            payment_method=payment.get('method', 'online'),
            transaction_id=payment_id,
            payment_id=order_id,
            status='Success',
            description=f"Payment for booking #{booking_id}"
        )
        
        # Update booking status
        booking.booking_status = 'Paid'
        booking.razorpay_payment_id = payment_id
        
        # Mark vehicle as rented
        vehicle = db.session.get(Vehicle, booking.vehicle_id)
        if vehicle:
            vehicle.availability_status = 'Rented'
        
        # Set booking dates if not set
        if not booking.booking_date:
            booking.booking_date = datetime.datetime.now()
        if not booking.due_date and booking.days:
            booking.due_date = booking.booking_date + datetime.timedelta(days=booking.days)
        
        db.session.add(new_payment)
        db.session.commit()
        
        # Send booking confirmation email
        user = db.session.get(User, booking.user_id)
        if user:
            send_booking_confirmation(
                user_email=user.email,
                user_name=user.username,
                booking_id=booking_id,
                vehicle_name=booking.vehicle.name if booking.vehicle else 'Vehicle',
                days=booking.days,
                amount=new_payment.amount,
                booking_date=booking.booking_date.strftime('%Y-%m-%d'),
                status='Confirmed'
            )
        
        return jsonify({
            'success': True,
            'message': 'Payment verified and booking confirmed',
            'booking_id': booking_id
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/payment/callback', methods=['POST'])
def payment_callback():
    """Handle Razorpay webhook callback"""
    try:
        data = request.get_json()
        event = data.get('event')
        
        if event == 'payment.failed':
            payment_data = data.get('payload', {}).get('payment', {}).get('entity', {})
            order_id = payment_data.get('order_id')
            
            # Find and update booking
            booking = Booking.query.filter_by(razorpay_order_id=order_id).first()
            if booking:
                booking.booking_status = 'Failed'
                db.session.commit()
        
        elif event == 'payment.authorized':
            payment_data = data.get('payload', {}).get('payment', {}).get('entity', {})
            payment_id = payment_data.get('id')
            
            # Capture payment automatically
            amount = payment_data.get('amount')
            razorpay_service.capture_payment(payment_id, amount)
        
        return jsonify({'status': 'ok'}), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/payment/history', methods=['GET'])
@login_required
def get_payment_history():
    """Get user's payment history"""
    try:
        user_id = session.get('user_id')
        print(f"DEBUG: Getting payment history for user_id: {user_id}")
        
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'User not logged in',
                'payments': []
            }), 401
        
        # Get all payments for the user
        payments = Payment.query.filter_by(user_id=user_id).order_by(
            Payment.created_date.desc()
        ).all()
        
        print(f"DEBUG: Found {len(payments)} payments for user {user_id}")
        
        payments_data = []
        for payment in payments:
            item_name = 'N/A'
            
            # Get item name based on payment type
            if payment.booking_id:
                booking = db.session.get(Booking, payment.booking_id)
                if booking:
                    vehicle = db.session.get(Vehicle, booking.vehicle_id)
                    if vehicle:
                        item_name = vehicle.name
            elif payment.subscription_id:
                subscription = db.session.get(UserSubscription, payment.subscription_id)
                if subscription:
                    plan = db.session.get(SubscriptionPlan, subscription.plan_id)
                    if plan:
                        item_name = f"{plan.name} Subscription"
            
            payment_dict = {
                'id': payment.id,
                'amount': payment.amount,
                'status': payment.status,
                'payment_method': payment.payment_method,
                'transaction_id': payment.transaction_id,
                'created_date': payment.created_date.strftime('%Y-%m-%d %H:%M:%S') if payment.created_date else None,
                'vehicle_name': item_name,
                'booking_id': payment.booking_id
            }
            payments_data.append(payment_dict)
            print(f"DEBUG: Added payment: {payment_dict}")
        
        result = {
            'success': True,
            'payments': payments_data,
            'total_count': len(payments_data)
        }
        print(f"DEBUG: Returning result: {result}")
        return jsonify(result), 200
    
    except Exception as e:
        print(f"ERROR in get_payment_history: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'payments': []
        }), 500


# ==================== END RAZORPAY PAYMENT ROUTES ====================

# Import at top level for payment emails
from services import send_booking_confirmation, send_subscription_activated, calculate_booking_amount
from services.email_service import send_handover_notification, send_completion_notification, send_plain_email

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)

