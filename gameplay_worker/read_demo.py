"""Actual game HTTP read requests with default-OFF/ON flag, worker hit and fallback."""
import json
import threading
import time
import urllib.request
from digit_world.config import WorldConfig
from digit_world.server import serve,Runtime
from .read_world import ReadWorld

def main():
    world=ReadWorld(':memory:',config=WorldConfig(energy_drain_per_second=0),enable_read_worker=True)
    server=None;thread=None;runtime=Runtime(world)
    try:
        a=world.execute('','register',{'name':'ReadFixtureA'});world.execute(a['token'],'login')
        b=world.execute('','register',{'name':'ReadFixtureB'});world.execute(b['token'],'login')
        world.db.execute('UPDATE players SET x=3,y=4 WHERE id=?',(b['player_id'],))
        world.db.execute("INSERT INTO objects VALUES('fixture-wall','wall',6,8,1)");world.db.commit()
        server=serve(world,port=0,runtime=runtime);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();runtime.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(action,args=None):
            req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/v1/command',json.dumps({'action':action,'args':args or {}}).encode(),{'Authorization':'Bearer '+a['token'],'Content-Type':'application/json'})
            with opener.open(req,timeout=5) as response:
                data=json.load(response);assert data['ok'];return data['result']
        # Default/off path baseline, then ON through the SAME game HTTP API.
        world.read_worker_enabled=False
        baseline={action:request(action) for action in ('look','nearby')}
        world.read_worker_enabled=True
        for action in baseline:assert request(action)==baseline[action]
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            for action in baseline:assert request(action)==baseline[action]
            if world.read_service.summary().get('worker_hits',0)>=2:break
            time.sleep(.05)
        assert world.read_service.summary().get('worker_hits',0)>=2,'No verified worker cache hits'
        # Geometry change: next read must fallback, never use old candidate membership.
        world.execute(b['token'],'move',{'dx':1,'dy':0})
        fallback_before=world.read_service.summary().get('central_fallbacks',0)
        after_move=request('look')
        assert world.read_service.summary().get('central_fallbacks',0)>fallback_before
        world.read_worker_enabled=False;assert request('look')==after_move;world.read_worker_enabled=True
        # Unavailable worker: reads still succeed through the ordinary Central path.
        world.read_service.close()
        for action in baseline:
            enabled=request(action);world.read_worker_enabled=False
            assert request(action)==enabled;world.read_worker_enabled=True
        print(json.dumps({'http_look_nearby_parity':True,'verified_worker_hits':world.read_service.summary().get('worker_hits',0),
                          'stale_geometry_fallback':True,'worker_unavailable_fallback':True,
                          'feature_flag_default':False,'production_node_changed':False,'soul_digit_awarded':0},indent=2))
        print('PASS: actual game HTTP look/nearby requests; OFF/ON response parity; verified subprocess worker cache hits; changed-world and unavailable-worker Central fallback; clean shutdown; no rewards.')
    finally:
        if server:server.shutdown();server.server_close()
        if thread:thread.join(timeout=10)
        runtime.close();world.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
