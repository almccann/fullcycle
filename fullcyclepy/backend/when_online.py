'''what to do when miner becomes online
enable, provision and start monitoring
'''
from helpers.queuehelper import QueueName, QueueEntries
from backend.fcmapp import Component

ONLINE = Component('online')

def when_online(channel, method, properties, body):
    '''whan a miner is found to be online after being offline'''
    print("[{0}] Received miner online message".format(ONLINE.app.now()))
    try:
        miner = ONLINE.app.messagedecodeminer(body)
        entries = doonline(miner)
        ONLINE.app.enqueue(entries)
    except Exception as ex:
        ONLINE.app.logexception(ex)

def doonline(miner):
    '''then provision the miner'''
    entries = QueueEntries()
    savedminer = ONLINE.app.getminer(miner)
    if savedminer is None:
        savedminer = miner
    #update status
    savedminer.online_now()
    ONLINE.app.putminer(savedminer)
    ONLINE.app.updateknownminer(savedminer)
    #just provision the miner and start to monitor
    entries.add(QueueName.Q_PROVISION, ONLINE.app.messageencode(savedminer))
    #tell them something good happened
    msg = ONLINE.app.stamp('miner {0} is back online'.format(savedminer.name))
    entries.addalert(msg)
    print(msg)
    return entries

def main():
    ONLINE.listeningqueue = ONLINE.app.subscribe(QueueName.Q_ONLINE, when_online)
    ONLINE.listen()

if __name__ == "__main__":
    main()
