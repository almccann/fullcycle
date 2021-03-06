'''Application Service layer for Full Cycle Mining
Gateway into most of application functionality'''
import sys
import os
import datetime
import logging
import json
from collections import defaultdict
import pika
from colorama import init, Fore
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import domain.minerstatistics
import domain.minerpool
from domain.mining import Miner, AvailablePool, MinerStatus
from domain.rep import MinerRepository, PoolRepository, LoginRepository, RuleParametersRepository, BaseRepository
#from domain.miningrules import RuleParameters
from messaging.messages import MinerMessageSchema, ConfigurationMessageSchema
from messaging.sensormessages import SensorValueSchema
from messaging.schema import MinerSchema, MinerStatsSchema, MinerCurrentPoolSchema
from helpers.queuehelper import QueueName, Queue, QueueEntry, QueueType
import backend.fcmutils as utils
#import backend.fcmcamera
from backend.fcmcamera import CameraService
from backend.fcmcache import Cache, CacheKeys
from backend.fcmbus import Bus
from backend.fcmcomponent import ComponentName
from backend.fcmservice import BaseService, PoolService, ServiceName, InfrastructureService, Configuration, Telegram
from backend.fcmminer import Antminer
from backend.fcmsensors import SensorService

class Component(object):
    '''A component is a unit of execution of FCM'''
    def __init__(self, componentname, option=''):
        self.app = ApplicationService(component=componentname, option=option)
        #was a queue, now its a channel
        self.listeningqueue = None

    def listen(self):
        if self.listeningqueue:
            self.app.bus.listen(self.listeningqueue)

class ApplicationService(BaseService):
    '''Application Services'''
    programnamefull = ''
    programname = ''
    component = ComponentName.fullcycle
    loglevel = 0
    #true if user passed in -now command line argument
    isrunnow = False
    #dictionary of queues managed by this app
    _queues = {}
    _channels = []
    #the startup directory
    __logger = None
    __logger_debug = None
    __logger_error = None

    def __init__(self, component=ComponentName.fullcycle, option=None, announceyourself=False):
        #TODO: call the one in parent before this
        self.homedirectory = os.path.dirname(__file__)
        self.initcache()
        self.setup_configuration()
        super().__init__(self.configuration, self.__cache)
        #for some reason super fails

        self.component = component
        if self.component == ComponentName.fullcycle:
            self.print('Starting FCM Init')
        self.initargs(option)
        self.startupstuff()
        if self.component == ComponentName.fullcycle:
            self.print('Starting FCM Configuration')
        self.initlogger()
        self.initmessaging()
        #this is slow. should be option to opt out of cache?
        if self.component == ComponentName.fullcycle:
            self.loginfo('Starting FCM Cache')
        self.initbus()
        self.init_application()
        self.init_sensors()

        if announceyourself:
            self.sendqueueitem(QueueEntry(QueueName.Q_LOG, self.stamp('Started {0}'.format(self.component)), QueueType.broadcast))

    def initargs(self, option):
        '''process command line arguments'''
        if sys.argv:
            self.programnamefull = sys.argv[0]
            self.programname = os.path.basename(self.programnamefull)
        firstarg = option
        if len(sys.argv) > 1:
            firstarg = sys.argv[1]
        if firstarg is not None:
            if firstarg == '-now':
                self.isrunnow = True

    def startupstuff(self):
        #used with colorama on windows
        init(autoreset=True)

    def initcache(self):
        '''start up cache'''
        try:
            cachelogin = self.getservice(ServiceName.cache)
            self.__cache = Cache(cachelogin)
        except Exception as ex:
            #cache is offline. try to run in degraded mode
            self.logexception(ex)

    def startup(self):
        self.initminercache()
        self.initpoolcache()

    def initbus(self):
        '''start up message bus'''
        login = self.getservice(ServiceName.messagebus)
        self.__bus = Bus(login)

    def init_sensors(self):
        self.sensors = SensorService(self.configuration, self.__cache)

    def init_application(self):
        self.antminer = Antminer(self.configuration, self.sshlogin())
        self.telegram = Telegram(self.configuration, self.getservice(ServiceName.telegram))
        self.pools = PoolService(self.configuration, self.__cache)
        self.camera = CameraService(self.configuration, self.__cache)

    @property
    def bus(self):
        return self.__bus

    #@property
    #def cache(self):
    #    return self.__cache

    @property
    def isdebug(self):
        return sys.flags.debug

    def setup_configuration(self):
        '''configuration is loaded once at startup'''
        raw = BaseRepository().readrawfile(self.getconfigfilename('config/fullcycle.conf'))
        config = json.loads(raw)

        self.configuration = Configuration(config)
        self.applicationid = self.configuration.get('applicationid')
        self.loglevel = self.configuration.get('loglevel')

    def initpoolcache(self):
        if self.__cache.get(CacheKeys.pools) is None:
            spools = PoolRepository().readrawfile(self.getconfigfilename('config/pools.conf'))
            self.__cache.tryputcache(CacheKeys.pools, spools)
        for pool in self.pools.get_all_pools():
            #pool isinstance of Pool
            availablepool = AvailablePool(pool.pool_type, pool, pool.url, pool.user, pool.password, pool.priority)
            minerpool = domain.minerpool.MinerPool(miner=None, priority=0, pool=availablepool)
            self.pools.putpool(pool)
            self.pools.add_pool(minerpool)

    def initminercache(self):
        '''put known miners into cache'''
        if self.__cache.get(CacheKeys.miners) is None:
            sminers = MinerRepository().readrawfile(self.getconfigfilename('config/miners.conf'))
            self.__cache.tryputcache(CacheKeys.miners, sminers)

        for miner in self.miners():
            #status is not persisted yet so init from name
            if miner.is_manually_disabled():
                miner.status = MinerStatus.Disabled
            if self.getminer(miner) is None:
                self.putminer(miner)

    def initlogger(self):
        '''set up logging application info'''
        self.__logger = self.setup_logger('fcmapp', 'fcm.log', logging.INFO)

        self.__logger_debug = self.setup_logger('fcmdebug', 'fcm.bug', logging.DEBUG)

        self.__logger_error = self.setup_logger('fcmerror', 'fcm.err', logging.ERROR)

    def setup_logger(self, logger_name, log_file, level=logging.INFO):
        '''start logger'''
        logr = logging.getLogger(logger_name)
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        #by default will append. use mode='w' to overwrite
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logr.addHandler(file_handler)
        # is setting stream necessary
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logr.setLevel(level)
        return logr

    def loginfo(self, message):
        '''log informational message'''
        logmsg = '{0}: {1}'.format(self.programname, message)
        self.__logger.info(logmsg)
        print(message)

    def logerror(self, message):
        '''log error'''
        logmsg = '{0}: {1}'.format(self.programname, message)
        self.__logger_error.error(logmsg)
        print(Fore.RED+logmsg)

    def logdebug(self, message):
        '''log debug message'''
        if not self.loglevel or self.loglevel == 0:
            return
        logmsg = '{0}: {1}'.format(self.programname, message)
        self.__logger_debug.debug(logmsg)
        print(Fore.GREEN+logmsg)

    def print(self, message):
        '''echo message to screen'''
        print('{0}: {1}'.format(self.now(), message))

    def now(self):
        '''current time formatted as friendly string'''
        return utils.formattime(datetime.datetime.now())

    #region lookups
    #todo: move to configurations section
    def miners(self):
        '''configured miners'''
        customerid = self.configuration.get('customerid')
        miners = MinerRepository().readminers(self.getconfigfilename('config/miners.conf'))
        for miner in miners:
            miner.customerid = customerid
        return miners

    def knownminers(self):
        '''for now just return a list of miners
        later should consider returning a list that is easily searched and filtered
        '''
        dknownminers = self.__cache.gethashset(CacheKeys.knownminers)
        if dknownminers is not None and dknownminers:
            #get list of miners from cache
            return utils.deserializelistofstrings(list(dknownminers.values()), MinerSchema())
        knownminers = self.miners()
        return knownminers

    def allminers(self):
        '''combined list of discovered miners and configured miners'''
        allminers = self.knownminers()
        for miner in self.miners():
            foundminer = [x for x in allminers if x.key() == miner.key()]
            if not foundminer:
                allminers.append(miner)
        return allminers

    def minersummary(self, max_number=10):
        '''show a summary of known miners
        '''
        mode = self.configuration.get('summary')
        if not mode:
            mode = 'auto'
        knownminers = self.knownminers()
        if len(knownminers) <= max_number:
            return '\n'.join([m.summary() for m in knownminers])
        groupbystatus = defaultdict(list)
        for miner in knownminers:
            groupbystatus[miner.status].append(miner)
        return '\n'.join(['{0}: {1}'.format(s, self.summary_by_status(s, groupbystatus[s])) for s in groupbystatus])

    def summary_by_status(self, key, minerlist):
        if key == 'online':
            return '{0} miners hash {1}'.format(self.summarize_count(minerlist), self.summarize_hash(minerlist))
        return self.summarize_count(minerlist)

    def summarize_count(self, minerlist):
        return len(minerlist)

    def summarize_hash(self, minerlist):
        return sum(miner.minerstats.currenthash for miner in minerlist)

    def addknownminer(self, miner):
        '''add miner to known miners list'''
        val = self.serialize(miner)
        self.__cache.putinhashset(CacheKeys.knownminers, miner.key(), val)

    def updateknownminer(self, miner):
        '''update known miner in cache'''
        if miner.is_key_updated:
            self.logdebug('delete cache: {0}'.format(miner.key_original))
            self.__cache.hdel(CacheKeys.knownminers, miner.key_original)
        sminer = self.__cache.getfromhashset(CacheKeys.knownminers, miner.key())
        memminer = utils.deserialize(MinerSchema(), utils.safestring(sminer))
        if memminer is None:
            memminer = miner
        else:
            #merge new values
            memminer.updatefrom(miner)
        val = self.serialize(memminer)
        self.logdebug('put cache: {0}'.format(miner.key()))
        self.__cache.putinhashset(CacheKeys.knownminers, miner.key(), val)

    def sshlogin(self):
        '''return contents of login file'''
        return self.readlogin('ftp.conf')

    def readlogin(self, filename):
        '''read login file configuration'''
        login = LoginRepository().readlogins(self.getconfigfilename('config/'+filename))
        return login

    def ruleparameters(self):
        '''rules parameters'''
        return RuleParametersRepository().readrules(self.getconfigfilename('config/'+'rules.conf'))

    def getservice(self, servicename):
        '''get a service by name. should be repository'''
        file_name = self.getconfigfilename('config/services.conf')
        with open(file_name, encoding='utf-8-sig') as config_file:
            content = json.loads(config_file.read())
        svc = None #dummy initializer to make scrutinize happy
        services = [InfrastructureService(**s) for s in content]
        return next((svc for svc in services if svc.name == servicename), None)

    def getservice_useroverride(self, servicename):
        service = self.getservice(servicename)
        service.user = self.component
        return service
    #endregion lookups

    def listen(self, qlisten):
        """Goes into listening mode on a queue"""
        try:
            self.bus.listen(qlisten)
        except KeyboardInterrupt:
            self.shutdown()
        except BaseException as unhandled:
            self.unhandledexception(unhandled)

    def registerqueue(self, qregister: Queue):
        '''register a queue'''
        self.logdebug(self.stamp('Registered queue {0}'.format(qregister.queue_name)))
        if qregister.queue_name not in self._queues.keys():
            self._queues[qregister.queue_name] = qregister

    def shutdown(self, exitstatus=0):
        '''shut down app services'''
        self.loginfo('Shutting down fcm app...')
        self.close_channels()
        self.closequeues()
        if self.__bus:
            self.bus.close()
        if self.__cache is not None:
            self.__cache.close()
        sys.exit(exitstatus)

    def closequeue(self, thequeue):
        '''close the queue'''
        if not thequeue:
            return
        try:
            if thequeue is not None:
                self.logdebug(self.stamp('closing queue {0}'.format(thequeue.queue_name)))
                thequeue.close()
            del self._queues[thequeue.queue_name]
        except Exception as ex:
            self.logexception(ex)

    def closequeues(self):
        '''close a bunch of queues'''
        for k in list(self._queues):
            self.closequeue(self._queues[k])

    def close_channel(self, chan):
        if not chan:
            return
        try:
            if chan.name in self._channels:
                self.logdebug(self.stamp('closing channel {0}'.format(chan.name)))
                chan.close()
                del self._channels[chan.name]
        except Exception as ex:
            self.logexception(ex)

    def close_channels(self):
        '''close all channels'''
        for chan in list(self._channels):
            self.close_channel(self._channels[chan])

    def unhandledexception(self, unhandled):
        '''what to do when there is an exception that app cannot handle'''
        self.logexception(unhandled)

    def exceptionmessage(self, ex):
        '''gets exception message even when it doesnt have one'''
        exc_type, _, exc_tb = sys.exc_info()
        exmsg = getattr(ex, 'message', repr(ex))
        return '{0}:{1}:{2}'.format(exc_type, exc_tb.tb_lineno, exmsg)

    def logexception(self, ex):
        '''log an exception'''
        self.logerror(self.exceptionmessage(ex))

    def sendlog(self, logmessage):
        '''send message to log queue'''
        item = QueueEntry(QueueName.Q_LOG, logmessage, 'broadcast')
        self.sendqueueitem(item)
        print(logmessage)

    def subscribe(self, name, callback, no_acknowledge=True, prefetch=1):
        '''subscribe to a queue'''
        chan = self.bus.subscribe(name, callback, no_acknowledge=no_acknowledge, prefetch_count=prefetch)
        print('Waiting for messages on {0}. To exit press CTRL+C'.format(name))
        return chan

    def listen_to_broadcast(self, broadcast_name, callback, no_acknowledge=True):
        thebroadcast = self.bus.subscribe_broadcast(broadcast_name, callback, no_acknowledge)
        print('Waiting for messages on {0}. To exit press CTRL+C'.format(broadcast_name))
        self.bus.listen(thebroadcast)
        #never returns becuase listen is blocking
        return thebroadcast

    def trypublish(self, queue_name, msg: str):
        '''publish a message to the queue'''
        try:
            self.bus.publish(queue_name, msg)
            return True
        except pika.exceptions.ConnectionClosed as ex:
            logmessage = 'Error publishing to {0} {1}'.format(queue_name, self.exceptionmessage(ex))
            self.logerror(logmessage)
            return False

    def trybroadcast(self, exchange_name, msg):
        '''broadcast a message to all queue listeners'''
        try:
            self.bus.broadcast(exchange_name, msg)
            return True
        except pika.exceptions.ConnectionClosed as conxex:
            self.logerror('Error broadcasting to {0} {1}'.format(exchange_name, self.exceptionmessage(conxex)))
            return False

    def putminer(self, miner: Miner):
        '''put miner in cache'''
        if miner and miner.key() and not miner.is_unknown:
            valu = self.serialize(miner)
            self.__cache.tryputcache('miner.{0}'.format(miner.key()), valu)

    def getminer(self, miner: Miner) -> Miner:
        '''strategies for getting miner from cache
        originally was key=miner.name but that was not good
        changed to key='miner.'+minerid
        '''
        valu = self.cache.trygetvaluefromcache('miner.{0}'.format(miner.key()))
        if valu is None:
            return None
        minerfromstore = utils.deserialize(MinerSchema(), utils.safestring(valu))
        if not minerfromstore.key():
            #do not allow entry with no key
            return None
        minerfromstore.store = 'mem'
        return minerfromstore

    def getknownminer(self, miner: Miner) -> Miner:
        '''get a known miner'''
        return self.getknownminerbykey(miner.key())

    def getminerbyname(self, minername):
        filtered = [x for x in self.miners() if x.name == minername]
        if filtered: return filtered[0]
        return None

    def getknownminerbykey(self, minername):
        str_miner = self.__cache.getfromhashset(CacheKeys.knownminers, minername)
        if str_miner is None:
            return None
        return utils.deserialize(MinerSchema(), utils.safestring(str_miner))

    def getknownminerbyname(self, minername):
        '''this could be slow if there are lots of miners'''
        known = self.knownminers()
        for miner in known:
            if miner.name == minername:
                return miner
        return None

    def putminerandstats(self, miner: Miner, minerstats, minerpool):
        '''put miner and status in cache'''
        self.putminer(miner)
        schema = MinerStatsSchema()
        valstats = schema.dumps(minerstats).data
        self.__cache.tryputcache(miner.key() + '.stats', valstats)
        schema = MinerCurrentPoolSchema()
        valpool = schema.dumps(minerpool).data
        self.__cache.tryputcache(miner.key() + '.pool', valpool)

    def getstats(self, miner: Miner):
        '''get stats entity'''
        valu = self.cache.trygetvaluefromcache(miner.name + '.stats')
        if valu is None: return None
        entity = domain.minerstatistics.MinerStatistics(miner, **utils.deserialize(MinerStatsSchema(), valu))
        return entity

    def getminerstatscached(self):
        '''iterator for cached stats'''
        for miner in self.miners():
            yield (self.getminer(miner), self.getstats(miner), self.pools.getpool(miner))

    def messagedecodeminer(self, body) -> Miner:
        '''deserialize a miner message'''
        message_envelope = super().deserializemessageenvelope(utils.safestring(body))
        schema = MinerMessageSchema()
        minermessage_dict = schema.load(message_envelope.bodyjson()).data
        minermessage_entity = schema.make_minermessage(minermessage_dict)
        miner = minermessage_entity.miner
        return miner

    def messagedecodeminerstats(self, body):
        '''deserialize miner stats'''
        message_envelope = super().deserializemessageenvelope(utils.safestring(body))
        schema = MinerMessageSchema()
        minermessage_dict = schema.load(message_envelope.bodyjson()).data
        minermessage_entity = schema.make_minermessage(minermessage_dict)
        return minermessage_entity

    def messagedecodeminercommand(self, body):
        '''deserialize  miner command'''
        message_envelope = super().deserializemessageenvelope(utils.safestring(body))
        schema = MinerMessageSchema()
        minermessage_dict = schema.load(message_envelope.bodyjson()).data
        minermessage_entity = schema.make_minermessage(minermessage_dict)
        return minermessage_entity

    def messagedecodesensor(self, body):
        '''deserialize sensor value '''
        message_envelope = super().deserializemessageenvelope(utils.safestring(body))
        schema = SensorValueSchema()
        #minermessage_dict = schema.load(message_envelope.bodyjson()).data
        entity = schema.load(message_envelope.bodyjson()).data
        return message_envelope, entity

    def messagedecode_configuration(self, body):
        '''deserialize  configuration command'''
        message_envelope = super().deserializemessageenvelope(utils.safestring(body))
        schema = ConfigurationMessageSchema()
        configurationmessage_dict = schema.load(message_envelope.bodyjson()).data
        configurationmessage_entity = schema.make_configurationmessage(configurationmessage_dict)
        return configurationmessage_entity

    def createmessagestats(self, miner, minerstats, minerpool):
        #always save the miner so the next guy can get latest changes
        #only put in cache if it came from cache
        if miner.store == 'mem':
            self.putminer(miner)
        message = super().createmessageenvelope()
        message = message.make_minerstats(miner, minerstats, minerpool)
        return super().serializemessageenvelope(message)

    def createmessagecommand(self, miner, command):
        '''create message command'''
        if miner.store == 'mem':
            self.putminer(miner)
        message = super().createmessageenvelope()
        message = message.make_minercommand(miner, command)
        return super().serializemessageenvelope(message)

    def messageencode(self, miner: Miner):
        '''command is optional, however should convert this call into minercommand'''
        #always save the miner so the next guy can get latest changes
        if miner.store == 'mem':
            self.putminer(miner)
        message = super().createmessageenvelope()
        message = message.make_minerstats(miner, minerstats=None, minerpool=None)
        return self._schemamsg.dumps(message).data

    def stamp(self, message):
        return '{0}:{1}: {2}'.format(self.now(), self.applicationid, message)

    def alert(self, message):
        '''send alert message'''
        return self.sendqueueitem(QueueEntry(QueueName.Q_ALERT, self.stamp(message), QueueType.broadcast))

    def send(self, q_name, message):
        '''send message to queue'''
        success = self.trypublish(q_name, message)
        return success

    def enqueue(self, queuelist):
        '''send a list of queue messages'''
        if queuelist is None:
            return
        if not queuelist.hasentries():
            return
        #todo: group by queuename
        for entry in queuelist.entries:
            self.sendqueueitem(entry)

    def sendqueueitem(self, entry):
        '''send one queue item'''
        if entry.eventtype == 'broadcast':
            send_result = self.trybroadcast(entry.queuename, entry.message)
            return send_result
        return self.send(entry.queuename, entry.message)

    def readtemperature(self):
        temp, humid = self.sensors.readtemperature()
        self.sendsensor(temp)
        self.sendsensor(humid)
        return temp, humid

    def sendsensor(self, reading):
        if not reading:
            return
        message = super().createmessageenvelope()
        sensorjson = message.jsonserialize(SensorValueSchema(), reading)
        self.sendqueueitem(QueueEntry(QueueName.Q_SENSOR, super().serializemessageenvelope(message.make_any('sensorvalue', sensorjson)), QueueType.broadcast))

    def getsession(self):
        service = self.getservice(ServiceName.database)
        engine = create_engine(service.connection, echo=False)
        Session = sessionmaker(bind=engine)
        return Session()

    def log_mineractivity(self, minerlog):
        try:
            session = self.getsession()
            session.add(minerlog)
            session.commit()
            return True
        except BaseException as ex:
            self.logexception(ex)
        return False

    def save_miner(self, miner: Miner):
        found = self.getknownminer(miner)
        if found is None:
            self.addknownminer(miner)
            #miners = MinerRepository()
            #todo:add the miner to the json config
        else:
            found.updatefrom(miner)
            self.putminer(found)

def main():
    full_cycle = ApplicationService()
    full_cycle.loginfo('Full Cycle was run in a script')
    full_cycle.shutdown()

if __name__ == "__main__":
    main()
