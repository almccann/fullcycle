'''gets stats from a miner and serializes to disk'''
import asyncio
from concurrent.futures import ThreadPoolExecutor
from colorama import Fore
from backend.fcmapp import ApplicationService
from domain.mining import MinerApiCall
from helpers import antminerhelper

print('Starting...')
APP = ApplicationService(component='fullcycle')
APP.print('started app. getting known miners')
WORKER_THREADS = 1
MINER_MULTIPLIER = 1

#async def getstats_async(miner):
#    minerstats, minerinfo, statspolling, minerpool = await antminerhelper.stats(miner)
#    return minerstats, minerinfo, statspolling, minerpool

def getstats(miner):
    '''poll miner'''
    minerstats, minerinfo, statspolling, minerpool = antminerhelper.stats(miner)
    return miner, minerstats, minerinfo, statspolling, minerpool

def process_results(results):
    '''process all results'''
    totaltime = 0
    for miner, minerstats, minerinfo, statspolling, minerpool in results:
        totaltime += statspolling.elapsed() * 1000
        process_result(miner, minerstats, minerinfo, statspolling, minerpool)
    return totaltime

def process_result(miner, minerstats, minerinfo, statspolling, minerpool):
    '''process results from one polling'''
    if minerstats is None:
        APP.logerror('{0} Offline? {1}'.format(miner.name, miner.ipaddress))
    else:
        savedminer = APP.getminer(miner)
        if not savedminer:
            print('Could not find saved miner {0}'.format(miner.name))
            savedminer = miner
        poolname = '{0} {1}'.format(minerpool.currentpool, minerpool.currentworker)
        foundpool = APP.pools.findpool(minerpool)
        if foundpool is not None:
            minerpool.poolname = foundpool.name
        savedminer.monitored(minerstats, minerpool, minerinfo, statspolling.elapsed())
        print('{0} mining at {1}({2})'.format(savedminer.name, minerpool.poolname, poolname))

        print(Fore.CYAN + str(APP.now()), miner.name, miner.status, \
            str(minerstats.currenthash), str(minerstats.minercount), \
            'temp=' + str(minerstats.tempboardmax()), \
            savedminer.uptime(minerstats.elapsed), \
            '{0:d}ms'.format(int(savedminer.monitorresponsetime() * 1000)))

        ##switches miner to default pool
        #if miner.defaultpool:
        #    founddefault = next((p for p in POOLS if p.name == miner.defaultpool), None)
        #    if founddefault is not None:
        #        #minerpool = antminerhelper.pools(miner)
        #        if minerpool is not None:
        #            #find pool number of default pool and switch to it
        #            switchtopoolnumber = minerpool.findpoolnumberforpool(founddefault.url,
        #founddefault.user)
        #            if switchtopoolnumber is not None and switchtopoolnumber > 0:
        #                antminerhelper.switch(miner, switchtopoolnumber)
        #                print(Fore.YELLOW + str(APP.now()), miner.name, 'switched to',
        #miner.defaultpool)

    #APP.putminerandstats(savedminer, minerstats, minerpool)
    #APP.updateknownminer(savedminer)
    if not statspolling:
        return 0
    return statspolling.elapsed() * 1000

def getminers(miners):
    '''get list of miners to poll'''
    listofminers = []
    cnt = MINER_MULTIPLIER
    while cnt > 0:
        for miner in miners:
            listofminers.append(miner)
        cnt -= 1
    return listofminers

async def run_tasks(cutor, miners):
    '''poll miners concurrently'''
    listofminers = getminers(miners)
    calltime = MinerApiCall(None)
    calltime.start()
    totalpolling = 0
    lop = asyncio.get_event_loop()
    tasks = [lop.run_in_executor(cutor, getstats, miner) for miner in listofminers]

    for fut in asyncio.as_completed(tasks, loop=lop):
        results = await fut
        totalpolling += process_result(*results)

    calltime.stop()

    totalms = int(calltime.elapsed()*1000)
    print('{0} api calls in {1}ms. Avg={2}ms' \
        .format(len(listofminers), totalms, totalms/len(listofminers)))
    timesavings = totalpolling - totalms
    print('Concurrency saved {}ms - {}ms = {}ms ({}%)' \
        .format(totalpolling, totalms, timesavings, int(timesavings/totalpolling*100)))


if __name__ == '__main__':
    MINERS = APP.knownminers()
    APP.print("{0} miners configured".format(len(MINERS)))

    CUTOR = ThreadPoolExecutor(max_workers=WORKER_THREADS)
    LOOP = asyncio.get_event_loop()
    LOOP.run_until_complete(run_tasks(CUTOR, MINERS))
    LOOP.close()
    APP.shutdown()
    #WHATISAID = input('done')
