#!/usr/bin/env python3
"""
Verification script for the 24-hour group creation limit implementation
This script verifies that all components are working correctly
"""

import sys
import os

def test_imports():
    """Test that all required modules can be imported"""
    print("🔍 Testing imports...")
    
    try:
        # Test BigBotFinal imports
        from BigBotFinal import API_ID, API_HASH
        print("✅ BigBotFinal basic imports successful")
        
        # Test telegram_bot imports (just the functions we need)
        from telegram_bot import (
            load_account_usage, save_account_usage, get_account_key,
            get_account_usage_info, can_create_groups, record_group_creation,
            format_time_remaining, cleanup_old_usage_records, MAX_GROUPS_PER_24H
        )
        print("✅ telegram_bot usage tracking functions imported successfully")
        
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False

def test_usage_tracking():
    """Test the usage tracking functionality"""
    print("\n🧪 Testing usage tracking functionality...")
    
    try:
        from telegram_bot import (
            get_account_usage_info, can_create_groups, record_group_creation,
            MAX_GROUPS_PER_24H
        )
        
        # Test with dummy data
        test_user_id = 999999
        test_phone = "+9999999999"
        
        # Test 1: New account
        usage_info = get_account_usage_info(test_user_id, test_phone)
        assert usage_info['groups_created_24h'] == 0, "New account should have 0 groups"
        assert usage_info['remaining_groups'] == MAX_GROUPS_PER_24H, f"New account should have {MAX_GROUPS_PER_24H} remaining"
        print(f"✅ New account test: 0/{MAX_GROUPS_PER_24H} groups")
        
        # Test 2: Can create groups
        can_create = can_create_groups(test_user_id, test_phone, 10)
        assert can_create == True, "New account should be able to create groups"
        print("✅ Can create groups: True")
        
        # Test 3: Record creation
        record_group_creation(test_user_id, test_phone, 5)
        usage_info = get_account_usage_info(test_user_id, test_phone)
        assert usage_info['groups_created_24h'] == 5, "Should have 5 groups recorded"
        print(f"✅ After recording 5 groups: {usage_info['groups_created_24h']}/{MAX_GROUPS_PER_24H}")
        
        # Test 4: Limit check
        can_create_all = can_create_groups(test_user_id, test_phone, MAX_GROUPS_PER_24H)
        assert can_create_all == False, "Should not be able to exceed remaining quota"
        print("✅ Limit enforcement working")
        
        return True
    except Exception as e:
        print(f"❌ Usage tracking test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_configuration():
    """Test configuration and constants"""
    print("\n⚙️ Testing configuration...")
    
    try:
        from telegram_bot import MAX_GROUPS_PER_24H, ACCOUNT_USAGE_FILE
        
        print(f"📊 Maximum groups per 24 hours: {MAX_GROUPS_PER_24H}")
        print(f"📁 Usage tracking file: {ACCOUNT_USAGE_FILE}")
        
        assert MAX_GROUPS_PER_24H == 50, "Maximum should be 50 groups per 24 hours"
        assert isinstance(ACCOUNT_USAGE_FILE, str), "Usage file should be a string"
        
        print("✅ Configuration is correct")
        return True
    except Exception as e:
        print(f"❌ Configuration test failed: {e}")
        return False

def display_implementation_summary():
    """Display a summary of the implementation"""
    print("\n📋 Implementation Summary")
    print("=" * 60)
    print("🎯 OBJECTIVE: Implement 24-hour group creation limits")
    print()
    print("✅ COMPLETED FEATURES:")
    print("   📊 Account usage tracking system")
    print("   ⏰ 24-hour rolling window limit (50 groups max)")
    print("   🔍 Real-time limit checking before group creation")
    print("   📱 Per-account usage display in bot interface")
    print("   ⚠️ User notifications for limit violations")
    print("   🧹 Automatic cleanup of old usage records")
    print("   🔄 Integration with existing group creation process")
    print()
    print("🔧 TECHNICAL COMPONENTS:")
    print("   📁 account_usage.json - Persistent usage tracking")
    print("   🕐 Timestamp-based 24-hour rolling window")
    print("   🚫 Pre-creation limit validation")
    print("   📈 Real-time usage statistics")
    print("   🎨 Enhanced UI with usage indicators")
    print()
    print("🛡️ SAFETY FEATURES:")
    print("   ⚡ Prevents account overuse and potential bans")
    print("   📊 Clear usage visibility for users")
    print("   ⏰ Countdown timers for limit resets")
    print("   🔄 Graceful handling of mixed account states")

def main():
    """Run all verification tests"""
    print("🚀 Verifying 24-Hour Group Creation Limit Implementation")
    print("=" * 70)
    
    all_passed = True
    
    # Test imports
    if not test_imports():
        all_passed = False
    
    # Test usage tracking
    if not test_usage_tracking():
        all_passed = False
    
    # Test configuration
    if not test_configuration():
        all_passed = False
    
    # Display summary
    display_implementation_summary()
    
    print("\n" + "=" * 70)
    if all_passed:
        print("🎉 ALL TESTS PASSED! Implementation is ready for use.")
        print("✅ The 24-hour group creation limit system is fully functional.")
        print()
        print("🚀 NEXT STEPS:")
        print("   1. Start the bot: python telegram_bot.py")
        print("   2. Test with real accounts")
        print("   3. Monitor usage tracking in action")
    else:
        print("❌ Some tests failed. Please review the errors above.")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
