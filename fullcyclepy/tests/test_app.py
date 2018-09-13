import unittest
import backend.fcmutils as utils
from backend.fcmapp import ApplicationService
from domain.mining import AvailablePool
from messaging.schema import AvailablePoolSchema

class TestApp(unittest.TestCase):
    def test_app_json_serialize(self):
        pool = AvailablePool('S9', None, 'url', 'user', 'x', 0)
        strpool = utils.jsonserialize(AvailablePoolSchema(), pool)
        self.assertTrue(isinstance(strpool, str))
        self.assertFalse(strpool.startswith('['))

    def test_app_knownpools(self):
        app = ApplicationService(component='test')
        app.startup()
        pools = app.pools.knownpools()
        self.assertTrue(len(pools) > 0)
        for pool in pools:
            self.assertTrue(isinstance(pool, AvailablePool))

if __name__ == '__main__':
    unittest.main()
