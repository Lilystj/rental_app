#!/usr/bin/env python3
import requests
import time

BASE_URL = "http://127.0.0.1:5000"
session = requests.Session()

print("=" * 50)
print("Testing WheelShare Admin Modules")
print("=" * 50)

# Preliminary sanity check: try registering a new user with age
print("\n0. Testing REGISTRATION WITH AGE...")
try:
    new_sess = requests.Session()
    uname = f"testuser{int(time.time())}"
    resp = new_sess.post(f"{BASE_URL}/register", data={
        "username": uname,
        "password": "password123",
        "email": f"{uname}@example.com",
        "age": "25"
    }, allow_redirects=False)
    if resp.status_code in (302, 301):
        print("✅ Registration request accepted")
    else:
        print(f"❌ Registration failed: {resp.status_code}")
except Exception as e:
    print(f"❌ Registration error: {e}")

# Test 1: Login
print("\n1. Testing LOGIN...")
try:
    response = session.post(f"{BASE_URL}/login", data={
        "username": "admin",
        "password": "admin123"
    })
    if response.status_code == 302 or "admin" in session.cookies.get_dict():
        print("✅ Login successful")
    else:
        print(f"❌ Login failed: {response.status_code}")
except Exception as e:
    print(f"❌ Login error: {e}")

# Test 2: Admin Dashboard
print("\n2. Testing ADMIN DASHBOARD (/admin)...")
try:
    response = session.get(f"{BASE_URL}/admin")
    if response.status_code == 200:
        print("✅ Dashboard page loads")
        if 'markNoPenalty' in response.text:
            print("✅ No-penalty script available")
        else:
            print("⚠️ No-penalty script missing")
    else:
        print(f"❌ Dashboard error: {response.status_code}")
except Exception as e:
    print(f"❌ Dashboard error: {e}")

# Test 2a: All Bookings Page
print("\n2a. Testing ALL BOOKINGS PAGE (/admin/bookings)...")
try:
    response = session.get(f"{BASE_URL}/admin/bookings")
    if response.status_code == 200:
        print("✅ All bookings page loads")
    else:
        print(f"❌ All bookings error: {response.status_code}")
except Exception as e:
    print(f"❌ All bookings error: {e}")

# Test 3: Vehicles Page
print("\n3. Testing VEHICLES PAGE (/admin/vehicles)...")
try:
    response = session.get(f"{BASE_URL}/admin/vehicles")
    if response.status_code == 200:
        print("✅ Vehicles page loads")
    else:
        print(f"❌ Vehicles page error: {response.status_code}")
except Exception as e:
    print(f"❌ Vehicles page error: {e}")

# Test 4: Vehicles API
print("\n4. Testing VEHICLES API (/admin/vehicles-list)...")
try:
    response = session.get(f"{BASE_URL}/admin/vehicles-list")
    if response.status_code == 200:
        data = response.json()
        vehicles = data.get('vehicles', [])
        print(f"✅ Vehicles API works - {len(vehicles)} vehicles")
        if vehicles and 'subscription_only' in vehicles[0]:
            print("✅ subscription_only flag present in vehicles API response")
        else:
            print("⚠️ subscription_only flag missing in vehicles API response")
    else:
        print(f"❌ Vehicles API error: {response.status_code}")
except Exception as e:
    print(f"❌ Vehicles API error: {e}")

# Test 5: Users Page
print("\n5. Testing USERS PAGE (/admin/users)...")
try:
    response = session.get(f"{BASE_URL}/admin/users")
    if response.status_code == 200:
        print("✅ Users page loads")
    else:
        print(f"❌ Users page error: {response.status_code}")
except Exception as e:
    print(f"❌ Users page error: {e}")

# Test 6: Users API
print("\n6. Testing USERS API (/admin/users-list)...")
try:
    response = session.get(f"{BASE_URL}/admin/users-list")
    if response.status_code == 200:
        data = response.json()
        count = len(data.get('users', []))
        print(f"✅ Users API works - {count} users")
        if count > 0 and 'age' in data['users'][0]:
            print("✅ Age field present in users API response")
        else:
            print("⚠️ Age field missing in users API response")
    else:
        print(f"❌ Users API error: {response.status_code}")
except Exception as e:
    print(f"❌ Users API error: {e}")

# Test 7: Verify subscription plans visible for regular user on /subscriptions
print("\n7. Testing USER VIEW PLANS (/subscriptions)...")
try:
    resp = session.get(f"{BASE_URL}/subscriptions")
    if resp.status_code == 200 and 'Basic' in resp.text:
        print("✅ Plan cards appear on subscriptions page")
    else:
        print(f"❌ Plans not visible or page error: {resp.status_code}")
except Exception as e:
    print(f"❌ Error loading subscriptions page: {e}")

# Test 7a: Penalties sidebar link and page UI
print("\n7a. Testing USER PENALTIES PAGE (/user/penalties) and sidebar link...")
try:
    resp = session.get(f"{BASE_URL}/user/penalties")
    if resp.status_code == 200 and 'Pending Penalties' in resp.text:
        print("✅ Penalties page loads and header present")
        if '⚠️ Penalties' in resp.text and '/user/penalties' in resp.text:
            print("✅ Sidebar contains penalties link")
        else:
            print("⚠️ Sidebar link for penalties missing in page")
    else:
        print(f"❌ Penalties page error: {resp.status_code}")
except Exception as e:
    print(f"❌ Penalties page error: {e}")

# Test 7: Feedback Page
print("\n7. Testing FEEDBACK PAGE (/admin/feedbacks)...")
try:
    response = session.get(f"{BASE_URL}/admin/feedbacks")
    if response.status_code == 200:
        print("✅ Feedback page loads")
    else:
        print(f"❌ Feedback page error: {response.status_code}")
except Exception as e:
    print(f"❌ Feedback page error: {e}")

# Test 8: Feedback API
print("\n8. Testing FEEDBACK API (/admin/feedbacks-list)...")
try:
    response = session.get(f"{BASE_URL}/admin/feedbacks-list")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Feedback API works - {len(data.get('feedbacks', []))} feedbacks")
    else:
        print(f"❌ Feedback API error: {response.status_code}")
except Exception as e:
    print(f"❌ Feedback API error: {e}")

# Test 9: Feedback Stats
print("\n9. Testing FEEDBACK STATS (/admin/feedback-stats)...")
try:
    response = session.get(f"{BASE_URL}/admin/feedback-stats")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Feedback stats work - Total: {data.get('total_feedbacks')}, Approved: {data.get('approved')}")
    else:
        print(f"❌ Feedback stats error: {response.status_code}")
except Exception as e:
    print(f"❌ Feedback stats error: {e}")

# Test 10: Reports Page
print("\n10. Testing REPORTS PAGE (/admin/reports)...")
try:
    response = session.get(f"{BASE_URL}/admin/reports")
    if response.status_code == 200:
        print("✅ Reports page loads")
    else:
        print(f"❌ Reports page error: {response.status_code}")
except Exception as e:
    print(f"❌ Reports page error: {e}")

# Test 11: Revenue Report
print("\n11. Testing REVENUE REPORT (/admin/report/revenue)...")
try:
    response = session.get(f"{BASE_URL}/admin/report/revenue")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Revenue report works - Total: ${data.get('total_revenue')}")
    else:
        print(f"❌ Revenue report error: {response.status_code}")
except Exception as e:
    print(f"❌ Revenue report error: {e}")

# Test 12: Booking Report
print("\n12. Testing BOOKING REPORT (/admin/report/booking)...")
try:
    response = session.get(f"{BASE_URL}/admin/report/booking")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Booking report works - Total: {data.get('total_bookings')}")
    else:
        print(f"❌ Booking report error: {response.status_code}")
except Exception as e:
    print(f"❌ Booking report error: {e}")

# Test 13: User Report
print("\n13. Testing USER REPORT (/admin/report/user)...")
try:
    response = session.get(f"{BASE_URL}/admin/report/user")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ User report works - Total: {data.get('total_users')}")
    else:
        print(f"❌ User report error: {response.status_code}")
except Exception as e:
    print(f"❌ User report error: {e}")

# Test 14: Vehicle Report
print("\n14. Testing VEHICLE REPORT (/admin/report/vehicle)...")
try:
    response = session.get(f"{BASE_URL}/admin/report/vehicle")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Vehicle report works - Total: {data.get('total_vehicles')}")
    else:
        print(f"❌ Vehicle report error: {response.status_code}")
except Exception as e:
    print(f"❌ Vehicle report error: {e}")

# Test 15: Subscriptions Admin Page
print("\n15. Testing SUBSCRIPTIONS ADMIN PAGE (/admin/subscriptions)...")
try:
    response = session.get(f"{BASE_URL}/admin/subscriptions")
    if response.status_code == 200:
        print("✅ Subscriptions admin page loads")
    else:
        print(f"❌ Subscriptions page error: {response.status_code}")
except Exception as e:
    print(f"❌ Subscriptions page error: {e}")

# Test 16: User Subscriptions Admin Page
print("\n16. Testing USER SUBSCRIPTIONS ADMIN PAGE (/admin/subscriptions/users)...")
try:
    response = session.get(f"{BASE_URL}/admin/subscriptions/users")
    if response.status_code == 200:
        print("✅ User subscriptions admin page loads")
    else:
        print(f"❌ User subscriptions page error: {response.status_code}")
except Exception as e:
    print(f"❌ User subscriptions page error: {e}")

print("\n" + "=" * 50)
print("Testing Complete!")
print("=" * 50)
