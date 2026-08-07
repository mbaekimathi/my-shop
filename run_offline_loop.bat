@echo off
REM Continuous online/offline test loop (Ctrl+C to stop)
python scripts/test_online_offline_loop.py --continuous --interval 10 --iterations 25
