"""Trusted Central adapter. Read-only World snapshot; never callable by remote workers."""
from .task import validate,MAX_ENTITIES
from coordination.contract import identifier

def capture(world,observer_id,world_id='digit-local',radius=30):
    identifier(observer_id);identifier(world_id)
    with world.lock:
        observer=world.db.execute('SELECT id,x,y FROM players WHERE id=?',(observer_id,)).fetchone()
        if observer is None:raise ValueError('Observer not found')
        session=world.db.execute('SELECT online FROM sessions WHERE owner=?',(observer_id,)).fetchone()
        if session is None or not session['online']:raise ValueError('Observer must be online')
        entities=[]
        queries=[('player','SELECT p.id,p.x,p.y FROM players p JOIN sessions s ON s.owner=p.id WHERE s.online=1 ORDER BY p.id LIMIT 65'),
                 ('object','SELECT id,x,y FROM objects ORDER BY id LIMIT 65'),
                 ('drop','SELECT id,x,y FROM drops ORDER BY id LIMIT 65')]
        for kind,sql in queries:
            for row in world.db.execute(sql):entities.append({'id':row['id'],'kind':kind,'x':row['x'],'y':row['y']})
            if len(entities)>MAX_ENTITIES:raise ValueError('World exceeds snapshot bound; partitioning not implemented')
        snapshot={'world_id':world_id,'observer':dict(observer),'radius':radius,'entities':entities}
        return validate(snapshot)

def enqueue_nearby(coordinator,world,observer_id,world_id='digit-local',radius=30):
    return coordinator.enqueue_snapshot(capture(world,observer_id,world_id,radius))

def consume_nearby(coordinator,world,observer_id,job_id,world_id='digit-local',radius=30):
    # Hold World lock through freshness check; caller must revalidate if applying later.
    with world.lock:
        return coordinator.consume(job_id,capture(world,observer_id,world_id,radius))
