'''# test json message format
# scenarios:
# - Miner with miner stats
# - Miner with command
# - command by itself
# - sensor
'''
import unittest
import datetime
import domain.minerstatistics
from domain.mining import Miner, MinerStatus, MinerInfo, MinerCurrentPool
from domain.sensors import SensorValue, Sensor
from messaging.messages import MinerSchema, ConfigurationMessage, ConfigurationMessageSchema
from messaging.sensormessages import SensorValueSchema

class TestSerialization(unittest.TestCase):
    def test_minerserialization(self):
        sch = MinerSchema()
        miner = Miner('test')
        miner.status = MinerStatus.Offline
        miner.status = MinerStatus.Online
        miner.minerinfo = MinerInfo('Antminer S9', '123')
        miner.minerpool = MinerCurrentPool(miner, 'test pool', 'test worker', allpools={})
        miner.minerpool.poolname = 'unittest'
        miner.minerstats = domain.minerstatistics.MinerStatistics(miner, datetime.datetime.utcnow(), 0, 1, 0, 99, 98, 97, 123, '', '', '')
        miner.minerstats.boardstatus1 = 'o'
        miner.minerstats.boardstatus2 = 'oo'
        miner.minerstats.boardstatus3 = 'ooo'
        jminer = sch.dumps(miner).data

        #rehydrate miner
        reminer = MinerSchema().loads(jminer).data
        self.assertTrue(isinstance(reminer.minerinfo, MinerInfo))
        self.assertTrue(isinstance(reminer.minerpool, MinerCurrentPool))
        self.assertTrue(reminer.minerpool.poolname == 'unittest')
        self.assertTrue(isinstance(reminer.minerstats, domain.minerstatistics.MinerStatistics))
        self.assertTrue(reminer.laststatuschanged)
        self.assertTrue(reminer.minerstats.boardstatus1 == 'o')
        self.assertTrue(reminer.minerstats.boardstatus2 == 'oo')
        self.assertTrue(reminer.minerstats.boardstatus3 == 'ooo')

    def test_sensorvalue_serialization(self):
        '''on windows the deserialization seems to lose the fractions of seconds
        so this test is only for seconds'''
        sch = SensorValueSchema()
        sensorvalue = SensorValue('testid', '99.9', 'temperature')
        sensorvalue.sensor = Sensor('testid', 'temperature', 'controller')
        sensortime = sensorvalue.sensortime
        jsensor = sch.dumps(sensorvalue).data

        #rehydrate sensor
        resensor = SensorValueSchema().loads(jsensor).data
        self.assertTrue(isinstance(resensor, SensorValue))
        self.assertTrue(resensor.sensortime.day == sensortime.day)
        self.assertTrue(resensor.sensortime.hour == sensortime.hour)
        self.assertTrue(resensor.sensortime.minute == sensortime.minute)
        self.assertTrue(resensor.sensortime.second == sensortime.second)
        self.assertTrue(isinstance(resensor.sensor, Sensor))
        self.assertTrue(resensor.sensorid == resensor.sensor.sensorid)

    def test_config_serialization(self):
        sch = ConfigurationMessageSchema()
        msg = ConfigurationMessage('save', '', 'pool', {"configuration_message_id":"name"}, [{"name":"my pool"}])
        jconfig = sch.dumps(msg).data
        reconfig = sch.loads(jconfig).data
        self.assertTrue(isinstance(reconfig, ConfigurationMessage))
        self.assertTrue(isinstance(reconfig.command, str))
        self.assertTrue(isinstance(reconfig.parameter, str))
        self.assertTrue(isinstance(reconfig.configuration_message_id, dict))
        self.assertTrue(isinstance(reconfig.values, list))

if __name__ == '__main__':
    unittest.main()
