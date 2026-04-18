#!/usr/bin/env python
"""Verify all templates and functions are correctly in place"""

# Check admin_dashboard functions
with open('templates/admin_dashboard.html', 'r', encoding='utf-8', errors='ignore') as f:
    admin_content = f.read()

functions_to_check = [
    'markNoPenalty',
    'openHandoverPenaltyModal', 
    'openAddPenaltyModal',
    'loadBookings',
    'loadStats',
    'approveBooking',
    'startHandover',
    'receiveHandover'
]

print('=== ADMIN DASHBOARD FUNCTIONS ===')
for func in functions_to_check:
    if f'function {func}(' in admin_content:
        print(f'✓ {func}() exists')
    else:
        print(f'✗ {func}() MISSING')

# Check modals
print('\n=== MODALS ===')
modals_to_check = ['addPenaltyModal', 'handoverModal']
for modal in modals_to_check:
    if f'id="{modal}"' in admin_content:
        print(f'✓ {modal} exists')
    else:
        print(f'✗ {modal} MISSING')

# Check user dashboard
print('\n=== USER DASHBOARD ===')
with open('templates/user_dashboard.html', 'r', encoding='utf-8', errors='ignore') as f:
    user_content = f.read()

if 'Penalty Management' in user_content:
    print('✓ Penalty sidebar link exists')
else:
    print('✗ Penalty sidebar link MISSING')

if 'markNoPenalty' in user_content:
    print('✓ markNoPenalty function in user dashboard')
else:
    print('✗ markNoPenalty function MISSING from user dashboard')

# Check user penalties template
print('\n=== USER PENALTIES PAGE ===')
with open('templates/user_penalties.html', 'r', encoding='utf-8', errors='ignore') as f:
    penalties_content = f.read()

if 'extend base.html' in penalties_content or 'base.html' in penalties_content:
    print('✓ User penalties extends base.html')
else:
    print('✗ User penalties NOT extending base.html')

if 'table' in penalties_content:
    print('✓ Penalties table exists')
else:
    print('✗ Penalties table MISSING')

print('\n=== ALL CHECKS COMPLETE ===')
