"""Bounded asynchronous local worker prefetch. No HTTP/process wait on World lock."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from .coordinator import GameplayCoordinator
from .server import serve
from .task import fingerprint,MAX_AGE

ROOT=Path(__file__).resolve().parents[1]

class ReadService:
    def __init__(self):
        self.stop=threading.Event();self.queue=queue.Queue(maxsize=1)
        self.lock=threading.Lock();self.cache=None;self.pending=set();self.metrics=Counter()
        self.retry_after=0.;self.closed=False
        self.thread=threading.Thread(target=self._run,name='read-worker-prefetch',daemon=True)
        self.thread.start()

    def candidates(self,snapshot):
        """Nonblocking request path; only fresh exact snapshot hits can filter rows."""
        key=fingerprint(snapshot);now=time.monotonic()
        if not self.lock.acquire(blocking=False):return None
        try:
            if self.closed:return None
            if self.cache and self.cache[0]==key and now-self.cache[1]<=MAX_AGE:
                self.metrics['worker_hits']+=1
                return set(self.cache[2])
            self.metrics['central_fallbacks']+=1
            if now>=self.retry_after and key not in self.pending:
                try:
                    self.queue.put_nowait((key,snapshot,now));self.pending.add(key);self.metrics['queued']+=1
                except queue.Full:self.metrics['queue_full']+=1
            return None
        finally:self.lock.release()

    def summary(self):
        with self.lock:return dict(self.metrics,enabled=True,closed=self.closed,thread_alive=self.thread.is_alive())

    def _run(self):
        store=None;server=None;server_thread=None
        try:
            with tempfile.TemporaryDirectory(prefix='digit-read-worker-') as directory:
                root=Path(directory);store=GameplayCoordinator(root/'coordinator.sqlite3')
                token=store.provision('read-worker');server=serve(store)
                server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
                config=root/'worker.json';fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as f:
                    json.dump({'url':'http://127.0.0.1:'+str(server.server_port),'node_id':'read-worker','credential':token},f)
                try:
                    while not self.stop.is_set():
                        try:key,snapshot,requested=self.queue.get(timeout=.1)
                        except queue.Empty:continue
                        try:
                            if time.monotonic()-requested>MAX_AGE:raise ValueError('Queued snapshot expired')
                            job=store.enqueue_snapshot(snapshot)
                            # Finite subprocess; never executes code supplied by a task.
                            process=subprocess.run([sys.executable,'-B','-m','gameplay_worker.worker_cli','--config',str(config)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8)
                            if process.returncode!=0:raise ValueError('Worker failed')
                            result=store.consume(job,snapshot)
                            if time.monotonic()-requested>MAX_AGE:raise ValueError('Result expired')
                            ids=frozenset((r['kind'],r['id']) for r in result['matches'])
                            with self.lock:
                                self.cache=(key,requested,ids);self.metrics['verified']+=1
                        except Exception:
                            with self.lock:self.metrics['worker_failures']+=1;self.retry_after=time.monotonic()+1
                        finally:
                            # This service needs only one transient job at a time; no history growth.
                            with store.transaction():
                                store.db.execute('DELETE FROM coord_results');store.db.execute('DELETE FROM coord_jobs')
                            with self.lock:self.pending.discard(key)
                            self.queue.task_done()
                finally:
                    server.shutdown();server.server_close();server_thread.join(timeout=6)
                    store.close();store=None;server=None
        except Exception:
            with self.lock:self.metrics['service_failures']+=1;self.closed=True
            if server:
                server.shutdown();server.server_close()
            if store:store.close()

    def close(self):
        with self.lock:self.closed=True;self.cache=None
        self.stop.set();self.thread.join(timeout=16)
        if self.thread.is_alive():raise RuntimeError('Read worker did not stop within shutdown bound')
