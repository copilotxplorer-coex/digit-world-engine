"""Key-only authentication. No username/password/Email recovery implementation."""
import hashlib
import os
import secrets
import time
from collections import deque
from . import login_keys
from .worker_tickets import issue_worker_ticket
PUBLIC=login_keys.PUBLIC
ACTIONS=login_keys.ACTIONS
WARNING=login_keys.WARNING

class AccountMixin:
    def _account_tables(self):
        login_keys.tables(self)
        self.db.execute('CREATE TABLE IF NOT EXISTS control_sessions(owner TEXT PRIMARY KEY,expires REAL NOT NULL)')
    def _account(self,pid):
        return self.db.execute('SELECT * FROM login_keys WHERE owner=?',(pid,)).fetchone()
    def _auth_limit(self,action,args,token):
        if action not in ACTIONS:return
        now=time.monotonic()
        identity=str(args.get('key',''))[:256] if action in PUBLIC else str(token)[:256]
        key=(action,hashlib.sha256(identity.encode()).hexdigest())
        if not hasattr(self,'auth_attempts'):self.auth_attempts={}
        if len(self.auth_attempts)>4096:self.auth_attempts.clear()
        attempts=self.auth_attempts.setdefault(key,deque())
        while attempts and now-attempts[0]>60:attempts.popleft()
        if len(attempts)>=(60 if action=='key_status' else 5):raise ValueError('Key request limit; wait 60 seconds')
        attempts.append(now)
    def _control_allowed(self,pid):
        s=self.db.execute('SELECT * FROM sessions WHERE owner=?',(pid,)).fetchone()
        if s['online'] and s['logout_requested']:raise ValueError('Wait for logout to finish before switching Client')
        if self._combat(pid):raise ValueError('Cannot switch Client during Combat')
        if self._actor(pid)['state'] in ('stun','stagger','freeze'):raise ValueError('Cannot switch Client in current state')
    def _rotate_control(
        self,
        pid,
        online=True,
    ):
        tok = secrets.token_urlsafe(
            32
        )

        now = self.clock()

        self.db.execute(
            (
                "UPDATE players "
                "SET token=? "
                "WHERE id=?"
            ),
            (
                tok,
                pid,
            ),
        )

        self.db.execute(
            (
                "INSERT OR REPLACE "
                "INTO control_sessions "
                "VALUES(?,?)"
            ),
            (
                pid,
                now + 86400,
            ),
        )

        self.db.execute(
            (
                "DELETE FROM receipt_v2 "
                "WHERE owner=?"
            ),
            (
                pid,
            ),
        )

        if online:
            self.db.execute(
                (
                    "UPDATE sessions "
                    "SET online=1,"
                    "last_seen=?,"
                    "logout_at=NULL,"
                    "logout_requested=0 "
                    "WHERE owner=?"
                ),
                (
                    now,
                    pid,
                ),
            )

            self.db.execute(
                (
                    "UPDATE vitals "
                    "SET updated_at=? "
                    "WHERE owner=?"
                ),
                (
                    now,
                    pid,
                ),
            )

        player = self._p(
            pid
        )

        result = {
            "token": tok,
            "player_id": pid,
            "name": player["name"],
            "online": online,
            "event_cursor":
                self.db.execute(
                    (
                        "SELECT "
                        "COALESCE(MAX(seq),0) "
                        "FROM events"
                    )
                ).fetchone()[0],
            "session_seconds": 86400,
        }

        worker_secret = os.environ.get(
            "DIGIT_WORLD_WORKER_TICKET_SECRET"
        )

        if worker_secret:
            result["worker_ticket"] = (
                issue_worker_ticket(
                    player_id=pid,
                    session_token=tok,
                    secret=worker_secret,
                    ttl_seconds=60,
                )
            )

            result[
                "worker_ticket_expires_seconds"
            ] = 60

        else:
            #
            # Safe rollout:
            # Login continues to work before the VPS
            # secret is installed. Worker ticket based
            # attach remains unavailable.
            #
            result["worker_ticket"] = None

            result[
                "worker_ticket_expires_seconds"
            ] = 0

        return result

    def _account_action(self,token,action,a):return login_keys.action(self,token,action,a)
