import subprocess as sp
from threading import Lock
import logging
import xmlrpc.client
import random
import time
import pytest
import concurrent.futures as futures


baseAddr = "http://localhost:"
baseClientPort = 7000
baseFrontendPort = 8001
baseServerPort = 9000

def makeClient():
  return xmlrpc.client.ServerProxy(baseAddr + str(baseClientPort))

def makeFrontend():
  return xmlrpc.client.ServerProxy(baseAddr + str(baseFrontendPort))

@pytest.fixture(scope="session")
def client_proc():
    client = sp.Popen(["python", "client.py", "-i", str(0)])
    time.sleep(1)
    yield makeClient 
    client.kill()

@pytest.fixture(scope="session")
def frontend_proc():
    front_thread = sp.Popen(["python", "frontend.py"])
    time.sleep(1)
    yield makeFrontend
    front_thread.kill()

class ServerList:
  def __init__(self):
    self.map = dict()
    self.next = 0


@pytest.fixture(scope="function")
def servers():
  s = ServerList()
  yield s
  for v in s.map.values():
    v.kill()
  for v in s.map.values():
    v.wait()

def addServer(frontend, servers):
    id = servers.next
    servers.next += 1
    servers.map[id] = sp.Popen(["python", "server.py", "-i", str(id)])
    time.sleep(1)
    frontend.addServer(id)

def listServer(frontend):
    return frontend.listServer()

def killServer(servers, serverId):
    servers.map[serverId].kill()
    time.sleep(1)

def shutdownServer(frontend, serverId):
    frontend.shutdownServer(serverId)

def printKVPairs(frontend, serverId):
    return frontend.printKVPairs(serverId)

def runWorkload(thread_id, client_proc, frontend_proc, servers, client_locks,
                keys, load_vals, run_vals, num_threads, num_requests,
                put_ratio, test_consistency, key_range_duplication, mode, per_key_locks, per_key_vals):
    client = client_proc()
    frontend = frontend_proc()
    request_count = 0
    start_idx = int((len(keys) / num_threads) * thread_id)
    end_idx = int(start_idx + (int((len(keys) / num_threads))))

    if test_consistency == 1:
      if key_range_duplication == 0:
        while num_requests > request_count:
            idx = random.randint(start_idx, end_idx - 1)
            if thread_id == 0 and request_count == int(num_requests / 2):
                if mode == "crash":
                    killServer(servers, 0)
                elif mode == "add":
                    addServer(frontend, servers)
                elif mode == "remove":
                    shutdownServer(frontend, 0)
            newval = random.randint(0, 1000000)
            with client_locks[thread_id]:
              client.put(keys[idx], newval)
              result = client.get(keys[idx])
            result = result.split(':')
            if int(result[0]) != keys[idx] or int(result[1]) != newval:
              print("[Error] request = (%d, %d), return = (%d, %d)" % (keys[idx], newval, int(result[0]), int(result[1])))
              return
            request_count += 1
            if thread_id == 0:
              print("Request count = " + str(request_count))
      else:
        while num_requests > request_count:
            idx = random.randint(0, key_range_duplication - 1)
            if thread_id == 0 and request_count == int(num_requests / 2):
                if mode == "crash":
                    killServer(servers, 0)
                elif mode == "add":
                    addServer(frontend, servers)
                elif mode == "remove":
                    shutdownServer(frontend, 0)
            with per_key_locks[idx]:
              newval = per_key_vals[idx]
              per_key_vals[idx] += 1
              with client_locks[thread_id]:
                client.put(keys[idx], newval)

            with client_locks[thread_id]:
              result = client.get(keys[idx])
            result = result.split(':')
            if int(result[0]) != keys[idx] or int(result[1]) < newval:
                print("[Error] request = (%d, %d), return = (%d, %d)" % (keys[idx], newval, int(result[0]), int(result[1])))
                return
            request_count += 1
            if thread_id == 0:
                print("Request count = " + str(request_count))
    else:
        optype = []
        for i in range(0, 100):
            if (i % 100) < put_ratio:
                optype.append("Put")
            else:
                optype.append("Get")
        random.shuffle(optype)

        while num_requests > request_count:
            for idx in range(start_idx, end_idx):
                if request_count == num_requests:
                    break
                if optype[idx % 100] == "Put":
                    with client_locks[thread_id]:
                      result = client.put(keys[idx], run_vals[idx])
                elif optype[idx % 100] == "Get":
                    with client_locks[thread_id]:
                      result = client.get(keys[idx])
                    if result == "ERR_KEY":
                      assert keys[idx] == None
                    result = result.split(':')
                    if int(result[0]) != keys[idx] or int(result[1]) != load_vals[idx]:
                        print("[Error] request = (%d, %d), return = (%d, %d)" % (keys[idx], load_vals[idx], int(result[0]), int(result[1])))
                        return
                else:
                    print("[Error] unknown operation type")
                    return
                request_count += 1

def loadDataset(thread_id, client, keys, load_vals, num_threads):
    start_idx = int((len(keys) / num_threads) * thread_id)
    end_idx = int(start_idx + (int((len(keys) / num_threads))))

    for idx in range(start_idx, end_idx):
        result = client.put(keys[idx], load_vals[idx])

@pytest.mark.parametrize("mode", ["crash", "add", "remove"])
@pytest.mark.parametrize("dup", [0, 1])
@pytest.mark.parametrize("consist", [0, 1])
def test_KVS(client_proc, servers, frontend_proc, consist, dup, mode):
    frontend = frontend_proc()
    client = client_proc()

    for _ in range(4):
      addServer(frontend, servers)
    num_keys = 1000
    num_requests = 1000
    num_threads = 6

    keys = list(range(0, num_keys))
    load_vals = list(range(0, num_keys))
    run_vals = list(range(num_keys, num_keys * 2))

    per_key_locks = []
    per_key_vals = []
    if dup != 0:
        for i in range(0, num_keys):
            per_key_locks.append(Lock())
            per_key_vals.append(0)

    random.shuffle(keys)
    random.shuffle(load_vals)
    random.shuffle(run_vals)

    futs = []
    with futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
      start = time.time()
      for thread_id in range(0, num_threads):
          futs.append(pool.submit(loadDataset,
            thread_id, client_proc(), keys, load_vals, num_threads))
    for fut in futures.as_completed(futs):
      fut.result()
    end = time.time()
    print("Load throughput = " + str(round(num_keys/(end - start), 1)) + "ops/sec")

    client_locks = [Lock() for _ in range(num_threads)]
    futs = []
    with futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
      start = time.time()
      for thread_id in range(0, num_threads):
          futs.append(pool.submit(runWorkload, thread_id, client_proc, frontend_proc, servers,
                      client_locks, keys, load_vals, run_vals,
                      num_threads, int(num_requests / num_threads), 50, consist, dup, mode, per_key_locks, per_key_vals))
    for fut in futures.as_completed(futs):
      fut.result()
    end = time.time()
    print("Run throughput = " + str(round(num_requests/(end - start), 1)) + "ops/sec")
