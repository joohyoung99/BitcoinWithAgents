import sys
from unittest.mock import MagicMock

# Mock bitpy.rest_api before importing src.executor
sys.modules["bitpy.rest_api"] = MagicMock()
sys.modules["bitpy"] = MagicMock()

# Mock BitgetAPI
mock_bitget = MagicMock()
sys.modules["bitpy.rest_api"].BitgetAPI = MagicMock(return_value=mock_bitget)
