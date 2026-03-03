
import logging
import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, date
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to path
sys.path.append("c:/Users/Neo/AI_Programming/NeoStock2")

# Mock imports that might fail if dependencies are missing or if we just want unit tests
# But here we import real classes if possible, mocking external dependencies
try:
    from core.history_manager import HistoryDataManager
    from ledger.models import MarketData
except ImportError:
    # If import fails (e.g. models syntax error), we can't test
    print("Import failed, likely due to syntax error in source code.")
    sys.exit(1)

# Setup logging
logging.basicConfig(level=logging.INFO)

class TestHistoryManager(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_market = MagicMock()
        
        self.manager = HistoryDataManager(self.mock_db, self.mock_market)

    def test_chunking_logic(self):
        """Test if fetch_and_store_history splits date range correctly"""
        symbol = "2330"
        start_date = "2023-01-01"
        end_date = "2023-03-01" # 60 days approx
        
        # Mock get_kbars to return empty df (so we just test loop logic)
        self.mock_market.get_kbars.return_value = pd.DataFrame()
        
        self.manager.fetch_and_store_history(symbol, start_date, end_date)
        
        # Expect multiple calls
        calls = self.mock_market.get_kbars.call_args_list
        print(f"Calls: {len(calls)}")
        
        # With 30 day chunks:
        # 1. Jan 1 - Jan 31
        # 2. Feb 1 - Mar 1 (depends on logic)
        
        self.assertTrue(len(calls) >= 2)
        
        # Check calling args
        args1, kwargs1 = calls[0]
        self.assertEqual(kwargs1['start'], '2023-01-01')
        
        args2, kwargs2 = calls[1]
        # Should be later than Jan 1
        self.assertNotEqual(kwargs2['start'], '2023-01-01')

    def test_storage_logic(self):
        """Test if data is stored to DB correctly"""
        # We need to patch 'insert' inside history_manager, or mock the db session execute
        
        symbol = "2330"
        start = "2023-01-01"
        end = "2023-01-02"
        
        # Mock DataFrame
        df = pd.DataFrame({
            'open': [100.0],
            'high': [101.0],
            'low': [99.0],
            'close': [100.5],
            'volume': [1000],
            'amount': [100000.0]
        }, index=pd.to_datetime(['2023-01-01 09:00:00']))
        
        self.mock_market.get_kbars.return_value = df
        
        # Mock DB session
        mock_session = MagicMock()
        self.mock_db.get_session.return_value = mock_session
        
        # We also need to mock sqlalchemy insert, but it's imported in the module.
        # We can simulate success by checking if session.execute was called.
        
        with patch('core.history_manager.insert') as mock_insert:
            # Configure mock insert to return a dummy statement object
            mock_stmt = MagicMock()
            mock_insert.return_value = mock_stmt
            mock_stmt.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            
            count = self.manager.fetch_and_store_history(symbol, start, end)
            
            # Verify insert called
            self.assertTrue(mock_insert.called)
            # Verify commit
            self.assertTrue(mock_session.commit.called)
            self.assertEqual(count, 1)

    def test_smart_fetch_no_data(self):
        """Test smart fetch when no data exists (should fetch N months)"""
        symbol = "2330"

        # Mock get_history_status to return no data
        with patch.object(self.manager, 'get_history_status') as mock_status:
            mock_status.return_value = {
                "symbol": symbol,
                "count": 0,
                "start_date": None,
                "end_date": None,
                "timeframe": "1min"
            }
        
            # Mock fetch_and_store_history
            with patch.object(self.manager, 'fetch_and_store_history') as mock_fetch:
                self.manager.fetch_history_smart(symbol, months=3)
                
                # Should call fetch_and_store_history
                mock_fetch.assert_called_once()
                
                # Check args: start date should be approx 90 days ago
                args, _ = mock_fetch.call_args
                start_arg = args[1]
                end_arg = args[2]
                
                today = date.today()
                # 3 months is approx 90 days
                expected_start_limit = (today - timedelta(days=95)).isoformat()
                
                self.assertEqual(end_arg, today.isoformat())
                # Allow some flexibility in calculation
                self.assertTrue(start_arg >= expected_start_limit)

    def test_smart_fetch_incremental(self):
        """Test smart fetch when data exists (should fetch from last end + 1)"""
        symbol = "2330"
        last_end = "2023-01-01"
        
        # Mock get_history_status to return existing data
        with patch.object(self.manager, 'get_history_status') as mock_status:
            mock_status.return_value = {
                "symbol": symbol,
                "count": 100,
                "start_date": "2022-01-01",
                "end_date": last_end,
                "timeframe": "1min"
            }
            
            # Mock fetch_and_store_history
            with patch.object(self.manager, 'fetch_and_store_history') as mock_fetch:
                self.manager.fetch_history_smart(symbol)
                
                # Should fetch from 2023-01-02 to Today
                mock_fetch.assert_called_once()
                
                if not mock_fetch.called:
                    # If today is same as last_end, it might not be called.
                    # Adjust test data to ensure it is called.
                    self.skipTest("Skipping strict check if today is close to mocked last_end")
                
                args, _ = mock_fetch.call_args
                
                expected_start = "2023-01-02"
                self.assertEqual(args[1], expected_start)
                self.assertEqual(args[2], date.today().isoformat())

if __name__ == '__main__':
    unittest.main()
