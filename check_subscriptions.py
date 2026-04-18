from app import app, db
from models import SubscriptionPlan

with app.app_context():
    plans = SubscriptionPlan.query.all()
    print('plans count', len(plans))
    for p in plans:
        print(p.id, p.name, p.price_per_month)
