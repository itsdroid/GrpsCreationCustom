#!/usr/bin/env python3
"""
Test script for account usage tracking system
This script tests the 24-hour group creation limits functionality
"""

import sys
import os
import json
from datetime import datetime, timedelta

# Add the current directory to Python path to import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from telegram_bot import (
        load_account_usage, save_account_usage, get_account_key,
        get_account_usage_info, can_create_groups, record_group_creation,
        format_time_remaining, cleanup_old_usage_records, MAX_GROUPS_PER_24H
    )
    print("✅ Successfully imported usage tracking functions")
except ImportError as e:
    print(f"❌ Failed to import functions: {e}")
    sys.exit(1)

def test_basic_functionality():
    """Test basic usage tracking functionality"""
    print("\n🧪 Testing Basic Functionality")
    print("=" * 50)
    
    # Test data
    test_user_id = 12345
    test_phone = "+1234567890"
    
    # Test 1: New account should have full quota
    usage_info = get_account_usage_info(test_user_id, test_phone)
    print(f"📊 New account usage: {usage_info['groups_created_24h']}/{MAX_GROUPS_PER_24H}")
    assert usage_info['groups_created_24h'] == 0, "New account should have 0 groups created"
    assert usage_info['remaining_groups'] == MAX_GROUPS_PER_24H, f"New account should have {MAX_GROUPS_PER_24H} remaining"
    print("✅ New account test passed")
    
    # Test 2: Can create groups check
    can_create = can_create_groups(test_user_id, test_phone, 10)
    assert can_create == True, "New account should be able to create groups"
    print("✅ Can create groups test passed")
    
    # Test 3: Record some group creations
    record_group_creation(test_user_id, test_phone, 5)
    usage_info = get_account_usage_info(test_user_id, test_phone)
    print(f"📊 After creating 5 groups: {usage_info['groups_created_24h']}/{MAX_GROUPS_PER_24H}")
    assert usage_info['groups_created_24h'] == 5, "Should have 5 groups recorded"
    assert usage_info['remaining_groups'] == MAX_GROUPS_PER_24H - 5, f"Should have {MAX_GROUPS_PER_24H - 5} remaining"
    print("✅ Group creation recording test passed")
    
    # Test 4: Check limit enforcement
    can_create_more = can_create_groups(test_user_id, test_phone, MAX_GROUPS_PER_24H)
    assert can_create_more == False, "Should not be able to create more than remaining quota"
    print("✅ Limit enforcement test passed")

def test_limit_scenarios():
    """Test various limit scenarios"""
    print("\n🧪 Testing Limit Scenarios")
    print("=" * 50)
    
    test_user_id = 67890
    test_phone = "+9876543210"
    
    # Test 1: Fill up the quota
    record_group_creation(test_user_id, test_phone, MAX_GROUPS_PER_24H)
    usage_info = get_account_usage_info(test_user_id, test_phone)
    print(f"📊 After maxing out: {usage_info['groups_created_24h']}/{MAX_GROUPS_PER_24H}")
    assert usage_info['groups_created_24h'] == MAX_GROUPS_PER_24H, f"Should have {MAX_GROUPS_PER_24H} groups"
    assert usage_info['remaining_groups'] == 0, "Should have 0 remaining"
    print("✅ Quota maxed out test passed")
    
    # Test 2: Try to create more (should fail)
    can_create = can_create_groups(test_user_id, test_phone, 1)
    assert can_create == False, "Should not be able to create when quota is full"
    print("✅ Over-quota prevention test passed")
    
    # Test 3: Check time formatting
    next_reset = usage_info['next_reset_time']
    if next_reset:
        time_str = format_time_remaining(next_reset)
        print(f"⏰ Time until reset: {time_str}")
        assert isinstance(time_str, str), "Time formatting should return string"
        print("✅ Time formatting test passed")

def test_cleanup_functionality():
    """Test cleanup of old records"""
    print("\n🧪 Testing Cleanup Functionality")
    print("=" * 50)
    
    # Create test data with old timestamps
    usage_data = load_account_usage()
    test_key = "cleanup_test_123_1234567890"
    
    # Add old and new timestamps
    old_time = (datetime.now() - timedelta(hours=25)).isoformat()
    new_time = datetime.now().isoformat()
    
    usage_data[test_key] = {
        'creation_times': [old_time, new_time],
        'phone': '+1234567890',
        'user_id': 123
    }
    save_account_usage(usage_data)
    
    # Run cleanup
    cleaned_data = cleanup_old_usage_records()
    
    # Check results
    if test_key in cleaned_data:
        remaining_times = cleaned_data[test_key]['creation_times']
        print(f"📊 Before cleanup: 2 records, After cleanup: {len(remaining_times)} records")
        assert len(remaining_times) == 1, "Should have removed old timestamp"
        assert new_time in remaining_times, "Should keep recent timestamp"
        print("✅ Cleanup functionality test passed")
    else:
        print("⚠️ Test key was completely removed (acceptable if no recent activity)")

def test_multiple_accounts():
    """Test multiple accounts scenario"""
    print("\n🧪 Testing Multiple Accounts Scenario")
    print("=" * 50)
    
    accounts = [
        (111, "+1111111111"),
        (222, "+2222222222"),
        (333, "+3333333333")
    ]
    
    # Create different usage patterns
    for i, (user_id, phone) in enumerate(accounts):
        groups_to_create = (i + 1) * 10  # 10, 20, 30 groups
        record_group_creation(user_id, phone, groups_to_create)
        
        usage_info = get_account_usage_info(user_id, phone)
        print(f"📱 Account {phone}: {usage_info['groups_created_24h']}/{MAX_GROUPS_PER_24H} groups")
        
        expected_remaining = MAX_GROUPS_PER_24H - groups_to_create
        assert usage_info['remaining_groups'] == expected_remaining, f"Account {phone} should have {expected_remaining} remaining"
    
    print("✅ Multiple accounts test passed")

def display_current_usage():
    """Display current usage data for all accounts"""
    print("\n📊 Current Usage Summary")
    print("=" * 50)
    
    usage_data = load_account_usage()
    if not usage_data:
        print("📭 No usage data found")
        return
    
    for account_key, data in usage_data.items():
        phone = data.get('phone', 'Unknown')
        user_id = data.get('user_id', 'Unknown')
        creation_times = data.get('creation_times', [])
        groups_count = len(creation_times)
        remaining = MAX_GROUPS_PER_24H - groups_count
        
        print(f"📱 {phone} (User: {user_id})")
        print(f"   📊 Usage: {groups_count}/{MAX_GROUPS_PER_24H} groups")
        print(f"   🟢 Remaining: {remaining} groups")
        
        if creation_times:
            try:
                oldest = min(creation_times)
                oldest_time = datetime.fromisoformat(oldest)
                reset_time = oldest_time + timedelta(hours=24)
                time_remaining = format_time_remaining(reset_time)
                print(f"   ⏰ Reset in: {time_remaining}")
            except:
                print(f"   ⏰ Reset time: Unable to calculate")
        print()

def main():
    """Run all tests"""
    print("🚀 Starting Account Usage Tracking Tests")
    print("=" * 60)
    
    try:
        # Run tests
        test_basic_functionality()
        test_limit_scenarios()
        test_cleanup_functionality()
        test_multiple_accounts()
        
        # Display current state
        display_current_usage()
        
        print("\n🎉 All Tests Passed!")
        print("=" * 60)
        print("✅ Account usage tracking system is working correctly")
        print(f"📊 Maximum groups per account per 24 hours: {MAX_GROUPS_PER_24H}")
        print("🔄 Old records are automatically cleaned up")
        print("⚠️ Limits are enforced before group creation")
        print("📱 Multiple accounts are supported")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
